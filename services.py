"""Identity, accounts, entries, and mood analysis."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import delete, select
from textblob import TextBlob

from db import Device, Entry, User, session_scope, utcnow

COOKIE_NAME = "mindflow_device"
COOKIE_MAX_AGE = 60 * 60 * 24 * 730  # two years

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{22,128}")
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
MIN_PASSWORD_LEN = 8


# --------------------------------------------------------------------------
# Mood
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Mood:
    score: float
    label: str
    tone: str
    message: str


def analyze_mood(text: str) -> Mood:
    score = TextBlob(text).sentiment.polarity
    if score > 0.3:
        return Mood(
            score,
            "Feeling Good ✨",
            "success",
            "You seem to be in a pretty positive headspace today.",
        )
    if score < -0.3:
        return Mood(
            score,
            "Emotionally Heavy 😔",
            "error",
            "Looks like today's been weighing on you a bit.",
        )
    return Mood(score, "Balanced 🌿", "info", "Your mood feels fairly steady today.")


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    salt_b64 = base64.b64encode(salt).decode()
    dk_b64 = base64.b64encode(dk).decode()
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt_b64}${dk_b64}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(dk_b64)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, actual)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_credentials(email: str, password: str) -> str | None:
    """Return an error message, or None if the pair is acceptable."""
    if not _EMAIL_RE.match(normalize_email(email)):
        return "That doesn't look like a valid email address."
    if len(password) < MIN_PASSWORD_LEN:
        return f"Password needs to be at least {MIN_PASSWORD_LEN} characters."
    return None


# --------------------------------------------------------------------------
# Device identity
# --------------------------------------------------------------------------


def new_device_token() -> str:
    return secrets.token_urlsafe(32)


def is_valid_token(value: object) -> bool:
    """Guard the identity lookup against a malformed or hand-edited cookie."""
    return isinstance(value, str) and _TOKEN_RE.fullmatch(value) is not None


def resolve_device_user(token: str) -> int:
    """Map a device token to a user id, creating an anonymous user on first sight.

    Keyed entirely by the token, so repeated calls are idempotent — an identity is
    only ever minted for a token the database has genuinely never seen.
    """
    with session_scope() as s:
        device = s.get(Device, token)
        if device is not None:
            device.last_seen_at = utcnow()
            return device.user_id

        user = User()
        s.add(user)
        s.flush()
        s.add(Device(token=token, user_id=user.id))
        return user.id


def get_user(user_id: int) -> User | None:
    with session_scope() as s:
        return s.get(User, user_id)


def _rebind_device(s, token: str, user_id: int) -> None:
    device = s.get(Device, token)
    if device is None:
        s.add(Device(token=token, user_id=user_id))
    else:
        device.user_id = user_id
        device.last_seen_at = utcnow()


def _drop_if_empty_anonymous(s, user_id: int) -> None:
    user = s.get(User, user_id)
    if user is None or user.is_registered:
        return
    has_entries = s.scalar(select(Entry.id).where(Entry.user_id == user_id).limit(1))
    has_devices = s.scalar(select(Device.token).where(Device.user_id == user_id).limit(1))
    if not has_entries and not has_devices:
        s.delete(user)


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


def sign_up(token: str, email: str, password: str) -> tuple[int | None, str | None]:
    """Upgrade the device's anonymous identity into a real account, keeping its entries."""
    email = normalize_email(email)
    with session_scope() as s:
        if s.scalar(select(User).where(User.email == email)):
            return None, "An account with that email already exists — log in instead."

        device = s.get(Device, token)
        user = s.get(User, device.user_id) if device else None

        if user is None:
            user = User()
            s.add(user)
            s.flush()
            _rebind_device(s, token, user.id)
        elif user.is_registered:
            # Device already belongs to someone else's account; start a fresh one.
            user = User()
            s.add(user)
            s.flush()
            _rebind_device(s, token, user.id)

        user.email = email
        user.password_hash = hash_password(password)
        return user.id, None


def log_in(token: str, email: str, password: str) -> tuple[int | None, str | None]:
    """Authenticate and claim this device's anonymous entries for the account."""
    email = normalize_email(email)
    with session_scope() as s:
        account = s.scalar(select(User).where(User.email == email))
        if account is None or not verify_password(password, account.password_hash):
            return None, "Email or password is incorrect."

        device = s.get(Device, token)
        previous_id = device.user_id if device else None

        if previous_id is not None and previous_id != account.id:
            previous = s.get(User, previous_id)
            # Only absorb entries from an anonymous identity — never from another account.
            if previous is not None and not previous.is_registered:
                for entry in s.scalars(
                    select(Entry).where(Entry.user_id == previous_id)
                ):
                    entry.user_id = account.id

        _rebind_device(s, token, account.id)
        s.flush()
        if previous_id is not None and previous_id != account.id:
            _drop_if_empty_anonymous(s, previous_id)

        return account.id, None


def log_out(token: str) -> int:
    """Detach this device from the account by giving it a fresh anonymous identity."""
    with session_scope() as s:
        user = User()
        s.add(user)
        s.flush()
        _rebind_device(s, token, user.id)
        return user.id


def change_password(user_id: int, current: str, new: str) -> str | None:
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None or not user.is_registered:
            return "You need an account before you can change its password."
        if not verify_password(current, user.password_hash):
            return "Current password is incorrect."
        if len(new) < MIN_PASSWORD_LEN:
            return f"Password needs to be at least {MIN_PASSWORD_LEN} characters."
        user.password_hash = hash_password(new)
        return None


# --------------------------------------------------------------------------
# Entries
# --------------------------------------------------------------------------


def add_entry(user_id: int, text: str) -> Mood:
    mood = analyze_mood(text)
    with session_scope() as s:
        s.add(
            Entry(
                user_id=user_id,
                created_at=utcnow(),
                text=text,
                score=mood.score,
                label=mood.label,
            )
        )
    return mood


def load_entries(user_id: int) -> pd.DataFrame:
    with session_scope() as s:
        rows = s.scalars(
            select(Entry).where(Entry.user_id == user_id).order_by(Entry.created_at)
        ).all()

    frame = pd.DataFrame(
        [
            {
                "created_at": r.created_at,
                "text": r.text,
                "score": r.score,
                "label": r.label,
            }
            for r in rows
        ],
        columns=["created_at", "text", "score", "label"],
    )
    # Real datetimes so the chart spaces points by elapsed time, not row order.
    frame["created_at"] = pd.to_datetime(frame["created_at"], utc=True, errors="coerce")
    return frame


def delete_all_entries(user_id: int) -> int:
    with session_scope() as s:
        result = s.execute(delete(Entry).where(Entry.user_id == user_id))
        return result.rowcount or 0


def export_entries(user_id: int, frame: pd.DataFrame | None = None) -> bytes:
    frame = load_entries(user_id) if frame is None else frame
    payload = {
        "app": "mindflow",
        "version": 1,
        "exported_at": utcnow().isoformat(),
        "entries": [
            {
                "created_at": row.created_at.isoformat(),
                "text": row.text,
                "score": float(row.score),
                "label": row.label,
            }
            for row in frame.itertuples()
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False).encode()


def import_entries(user_id: int, raw: bytes) -> tuple[int, str | None]:
    """Merge an exported backup into this account, skipping entries already present."""
    try:
        payload = json.loads(raw.decode("utf-8"))
        incoming = payload["entries"]
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
        return 0, "That file isn't a MindFlow export."

    with session_scope() as s:
        existing = {
            (r.created_at.replace(tzinfo=timezone.utc), r.text)
            for r in s.scalars(select(Entry).where(Entry.user_id == user_id))
        }
        added = 0
        for item in incoming:
            try:
                created = datetime.fromisoformat(item["created_at"])
                text = str(item["text"])
            except (KeyError, TypeError, ValueError):
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            created = created.astimezone(timezone.utc)
            if (created, text) in existing:
                continue
            mood = analyze_mood(text)
            s.add(
                Entry(
                    user_id=user_id,
                    created_at=created,
                    text=text,
                    score=float(item.get("score", mood.score)),
                    label=str(item.get("label", mood.label)),
                )
            )
            existing.add((created, text))
            added += 1

    return added, None
