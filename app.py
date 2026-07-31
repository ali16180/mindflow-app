import json

import streamlit as st
import streamlit.components.v1 as components

from db import database_url
from services import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    add_entry,
    change_password,
    delete_all_entries,
    export_entries,
    get_user,
    import_entries,
    is_valid_token,
    load_entries,
    log_in,
    log_out,
    new_device_token,
    resolve_device_user,
    sign_up,
    validate_credentials,
)

st.set_page_config(page_title="MindFlow", page_icon="🧠", layout="centered")

HISTORY_LIMIT = 100


# -----------------------------------
# IDENTITY
# -----------------------------------


def read_device_cookie() -> str | None:
    """Cookies from the initial request — present or absent, never 'pending'."""
    try:
        value = st.context.cookies.get(COOKIE_NAME)
    except Exception:
        return None
    return value if is_valid_token(value) else None


def write_device_cookie(token: str) -> None:
    """Best-effort write from the component iframe onto the parent document."""
    components.html(
        f"""
        <script>
        (function () {{
            var value = "{COOKIE_NAME}=" + {json.dumps(token)}
                + "; path=/; max-age={COOKIE_MAX_AGE}; SameSite=Lax";
            try {{
                (window.parent && window.parent.document
                    ? window.parent.document : document).cookie = value;
            }} catch (e) {{
                document.cookie = value;
            }}
        }})();
        </script>
        """,
        height=0,
    )


def bootstrap_identity() -> int:
    if "device_token" not in st.session_state:
        token = read_device_cookie()
        # A token is minted only when the request carried no cookie at all.
        st.session_state.cookie_present = token is not None
        st.session_state.device_token = token or new_device_token()

    if not st.session_state.cookie_present:
        write_device_cookie(st.session_state.device_token)

    if "user_id" not in st.session_state:
        st.session_state.user_id = resolve_device_user(st.session_state.device_token)

    return st.session_state.user_id


def flash(tone: str, message: str) -> None:
    st.session_state.flash = (tone, message)


def render_flash() -> None:
    entry = st.session_state.pop("flash", None)
    if entry:
        getattr(st, entry[0])(entry[1])


def show(tone: str, message: str) -> None:
    getattr(st, tone)(message)


user_id = bootstrap_identity()
account = get_user(user_id)

if account is None:
    # The identity row vanished under us (database reset) — rebuild rather than crash.
    st.session_state.pop("user_id", None)
    user_id = bootstrap_identity()
    account = get_user(user_id)


# -----------------------------------
# SIDEBAR: ACCOUNT
# -----------------------------------

with st.sidebar:
    st.subheader("Account")

    if account and account.is_registered:
        st.caption(f"Signed in as **{account.email}**")

        if st.button("Sign out on this device", width="stretch"):
            st.session_state.user_id = log_out(st.session_state.device_token)
            st.session_state.pop("last_mood", None)
            flash("info", "Signed out. This browser is back to a private local journal.")
            st.rerun()

        with st.expander("Change password"):
            with st.form("change_password", clear_on_submit=True):
                current = st.text_input("Current password", type="password")
                updated = st.text_input("New password", type="password")
                if st.form_submit_button("Update password"):
                    error = change_password(user_id, current, updated)
                    flash(*(("error", error) if error else ("success", "Password updated.")))
                    st.rerun()
    else:
        st.caption(
            "You're journaling anonymously on this browser. "
            "Add an account to reach these entries from anywhere — "
            "everything you've already written comes with you."
        )

        login_tab, signup_tab = st.tabs(["Log in", "Create account"])

        with login_tab:
            with st.form("login"):
                email = st.text_input("Email", key="login_email")
                password = st.text_input("Password", type="password", key="login_password")
                if st.form_submit_button("Log in"):
                    new_id, error = log_in(st.session_state.device_token, email, password)
                    if error:
                        flash("error", error)
                    else:
                        st.session_state.user_id = new_id
                        st.session_state.pop("last_mood", None)
                        flash("success", "Welcome back.")
                    st.rerun()

        with signup_tab:
            with st.form("signup"):
                email = st.text_input("Email", key="signup_email")
                password = st.text_input("Password", type="password", key="signup_password")
                if st.form_submit_button("Create account"):
                    error = validate_credentials(email, password)
                    if not error:
                        new_id, error = sign_up(st.session_state.device_token, email, password)
                        if not error:
                            st.session_state.user_id = new_id
                    if error:
                        flash("error", error)
                    else:
                        flash("success", "Account created — your entries are saved to it.")
                    st.rerun()


# -----------------------------------
# HEADER
# -----------------------------------

st.title("🧠 MindFlow")
st.write("Tell me how your day's been. Let your thoughts flow out for a bit.")

render_flash()


# -----------------------------------
# JOURNAL INPUT
# -----------------------------------

st.session_state.setdefault("journal_input", "")


def submit_entry() -> None:
    text = st.session_state.journal_input.strip()
    if not text:
        flash("warning", "Write something first before analyzing your mood.")
        return

    mood = add_entry(st.session_state.user_id, text)
    st.session_state.last_mood = (mood.tone, f"{mood.message} (Score: {mood.score:.2f})")
    # Clearing here is what prevents a second click re-submitting the same entry.
    st.session_state.journal_input = ""


st.text_area(
    "What's on your mind today?",
    height=180,
    placeholder="Write whatever comes to mind...",
    key="journal_input",
)

st.button("Analyze My Mood", on_click=submit_entry)

if last_mood := st.session_state.get("last_mood"):
    show(*last_mood)


# -----------------------------------
# MOOD JOURNEY
# -----------------------------------

entries = load_entries(user_id)

st.divider()
st.subheader("📈 Your Mood Journey")

if entries.empty:
    st.write("Your mood history will start showing up here once you add entries.")
else:
    st.line_chart(entries.set_index("created_at")[["score"]])

    with st.expander(f"📖 View Journal History ({len(entries)})"):
        recent = entries.iloc[::-1]
        if len(recent) > HISTORY_LIMIT:
            st.caption(
                f"Showing the {HISTORY_LIMIT} most recent entries. "
                "Download a backup below for the full history."
            )
            recent = recent.head(HISTORY_LIMIT)

        for row in recent.itertuples():
            with st.container(border=True):
                st.caption(
                    f"{row.created_at:%d %b %Y, %H:%M} UTC · {row.label} · {row.score:+.2f}"
                )
                st.write(row.text)

    st.divider()

    st.session_state.setdefault("confirm_delete", False)

    if not st.session_state.confirm_delete:
        if st.button("🗑 Delete All Entries"):
            st.session_state.confirm_delete = True
            st.rerun()
    else:
        st.warning("Are you sure? This will permanently delete your journal history.")
        col1, col2 = st.columns(2)

        with col1:
            if st.button("Yes, Delete Everything"):
                removed = delete_all_entries(user_id)
                st.session_state.confirm_delete = False
                st.session_state.pop("last_mood", None)
                flash("success", f"Deleted {removed} entries.")
                st.rerun()

        with col2:
            if st.button("Cancel"):
                st.session_state.confirm_delete = False
                st.rerun()


# -----------------------------------
# BACKUP
# -----------------------------------

with st.expander("💾 Backup & restore"):
    st.caption(
        "Keep your own copy of your journal. Worth doing regularly if you're on "
        "Streamlit Community Cloud — see the note in the README about storage."
    )

    st.download_button(
        "Download my entries (JSON)",
        data=export_entries(user_id, entries),
        file_name="mindflow-backup.json",
        mime="application/json",
        disabled=entries.empty,
    )

    upload = st.file_uploader("Restore from a backup file", type="json")
    if upload is not None and st.button("Restore entries"):
        added, error = import_entries(user_id, upload.getvalue())
        flash(*(("error", error) if error else ("success", f"Restored {added} entries.")))
        st.rerun()

    if database_url().startswith("sqlite"):
        st.caption(f"Storage: local SQLite file (`{database_url()}`).")
    else:
        st.caption("Storage: external database from `MINDFLOW_DB_URL`.")
