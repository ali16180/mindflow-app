"""End-to-end checks: run with `pytest test_mindflow.py`."""

import os
import tempfile
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp())
os.environ["MINDFLOW_DB_URL"] = f"sqlite:///{TMP / 'test.db'}"

from streamlit.testing.v1 import AppTest

import services

APP = str(Path(__file__).parent / "app.py")


def fresh_app(token=None, timeout=30):
    at = AppTest.from_file(APP, default_timeout=timeout)
    if token is not None:
        at.session_state["device_token"] = token
        at.session_state["cookie_present"] = True
    return at


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------


def test_password_roundtrip():
    stored = services.hash_password("correct horse battery")
    assert services.verify_password("correct horse battery", stored)
    assert not services.verify_password("wrong", stored)
    assert not services.verify_password("x", None)
    assert not services.verify_password("x", "garbage")


def test_hashes_are_salted():
    assert services.hash_password("same") != services.hash_password("same")


# --------------------------------------------------------------------------
# Device identity — the churn bug
# --------------------------------------------------------------------------


def test_device_token_maps_to_one_stable_identity():
    token = services.new_device_token()
    ids = {services.resolve_device_user(token) for _ in range(5)}
    assert len(ids) == 1


def test_only_well_formed_tokens_are_accepted():
    assert services.is_valid_token(services.new_device_token())
    for junk in [None, "", "short", 12345, object(), "has spaces", "x" * 500]:
        assert not services.is_valid_token(junk)


def test_distinct_tokens_are_distinct_users():
    a = services.resolve_device_user(services.new_device_token())
    b = services.resolve_device_user(services.new_device_token())
    assert a != b


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


def test_signup_keeps_anonymous_entries():
    token = services.new_device_token()
    uid = services.resolve_device_user(token)
    services.add_entry(uid, "wrote this before making an account")

    account_id, error = services.sign_up(token, "Keep@Example.com ", "hunter2hunter2")
    assert error is None
    assert account_id == uid  # same row, upgraded in place
    assert len(services.load_entries(account_id)) == 1


def test_duplicate_email_rejected():
    token = services.new_device_token()
    services.resolve_device_user(token)
    services.sign_up(token, "dupe@example.com", "hunter2hunter2")

    other = services.new_device_token()
    services.resolve_device_user(other)
    _, error = services.sign_up(other, "DUPE@example.com", "hunter2hunter2")
    assert error is not None


def test_login_claims_entries_from_this_device():
    # Account created on device 1.
    device1 = services.new_device_token()
    services.resolve_device_user(device1)
    account_id, _ = services.sign_up(device1, "claim@example.com", "hunter2hunter2")
    services.add_entry(account_id, "entry from my first device")

    # Device 2 journals anonymously, then logs in.
    device2 = services.new_device_token()
    anon_id = services.resolve_device_user(device2)
    services.add_entry(anon_id, "entry written before logging in")

    logged_in, error = services.log_in(device2, "claim@example.com", "hunter2hunter2")
    assert error is None
    assert logged_in == account_id

    texts = set(services.load_entries(account_id)["text"])
    assert texts == {"entry from my first device", "entry written before logging in"}
    assert services.load_entries(anon_id).empty


def test_login_rejects_bad_password():
    token = services.new_device_token()
    services.resolve_device_user(token)
    services.sign_up(token, "bad@example.com", "hunter2hunter2")
    assert services.log_in(token, "bad@example.com", "nope")[0] is None
    assert services.log_in(token, "missing@example.com", "hunter2hunter2")[0] is None


def test_login_does_not_steal_from_another_account():
    d1 = services.new_device_token()
    services.resolve_device_user(d1)
    first, _ = services.sign_up(d1, "first@example.com", "hunter2hunter2")
    services.add_entry(first, "belongs to first account")

    d2 = services.new_device_token()
    services.resolve_device_user(d2)
    second, _ = services.sign_up(d2, "second@example.com", "hunter2hunter2")

    # Log into the second account from the first account's device.
    services.log_in(d1, "second@example.com", "hunter2hunter2")
    assert set(services.load_entries(first)["text"]) == {"belongs to first account"}
    assert services.load_entries(second).empty


def test_logout_gives_device_a_clean_identity():
    token = services.new_device_token()
    services.resolve_device_user(token)
    account_id, _ = services.sign_up(token, "out@example.com", "hunter2hunter2")
    services.add_entry(account_id, "private thought")

    anon_id = services.log_out(token)
    assert anon_id != account_id
    assert services.load_entries(anon_id).empty
    assert len(services.load_entries(account_id)) == 1
    assert services.resolve_device_user(token) == anon_id


def test_change_password():
    token = services.new_device_token()
    services.resolve_device_user(token)
    uid, _ = services.sign_up(token, "pw@example.com", "hunter2hunter2")

    assert services.change_password(uid, "wrong", "newpassword") is not None
    assert services.change_password(uid, "hunter2hunter2", "short") is not None
    assert services.change_password(uid, "hunter2hunter2", "newpassword1") is None
    assert services.log_in(token, "pw@example.com", "newpassword1")[0] == uid


# --------------------------------------------------------------------------
# Entries
# --------------------------------------------------------------------------


def test_mood_bands():
    assert services.analyze_mood("this is wonderful and lovely").tone == "success"
    assert services.analyze_mood("this is awful, I hate everything").tone == "error"
    assert services.analyze_mood("I went to the shop").tone == "info"


def test_entries_have_real_datetimes():
    uid = services.resolve_device_user(services.new_device_token())
    services.add_entry(uid, "a perfectly ordinary day")
    frame = services.load_entries(uid)
    import pandas as pd

    assert pd.api.types.is_datetime64_any_dtype(frame["created_at"])


def test_export_import_roundtrip():
    source = services.resolve_device_user(services.new_device_token())
    services.add_entry(source, "first backed-up entry")
    services.add_entry(source, "second backed-up entry")
    backup = services.export_entries(source)

    target = services.resolve_device_user(services.new_device_token())
    added, error = services.import_entries(target, backup)
    assert error is None and added == 2

    # Re-importing must not duplicate.
    again, _ = services.import_entries(target, backup)
    assert again == 0
    assert len(services.load_entries(target)) == 2


def test_import_rejects_junk():
    uid = services.resolve_device_user(services.new_device_token())
    assert services.import_entries(uid, b"not json")[1] is not None


def test_delete_all_is_scoped_to_one_user():
    mine = services.resolve_device_user(services.new_device_token())
    yours = services.resolve_device_user(services.new_device_token())
    services.add_entry(mine, "my entry")
    services.add_entry(yours, "your entry")

    services.delete_all_entries(mine)
    assert services.load_entries(mine).empty
    assert len(services.load_entries(yours)) == 1


# --------------------------------------------------------------------------
# App behaviour
# --------------------------------------------------------------------------


def test_app_first_load_does_not_hang():
    at = fresh_app(timeout=15)
    at.run()
    assert not at.exception
    assert "MindFlow" in at.title[0].value


def test_analyze_shows_result_and_clears_the_box():
    at = fresh_app(services.new_device_token())
    at.run()
    at.text_area[0].set_value("Today was absolutely wonderful, I feel great").run()
    at.button[0].click().run()

    assert not at.exception
    assert at.success, "mood message should be visible after analyzing"
    assert at.session_state["journal_input"] == ""


def test_second_click_does_not_duplicate():
    token = services.new_device_token()
    at = fresh_app(token)
    at.run()
    at.text_area[0].set_value("a genuinely lovely and delightful day").run()
    at.button[0].click().run()
    at.button[0].click().run()  # impatient double-click

    uid = services.resolve_device_user(token)
    assert len(services.load_entries(uid)) == 1
    assert at.warning, "second click should warn about the empty box"


def test_empty_submission_is_rejected():
    token = services.new_device_token()
    at = fresh_app(token)
    at.run()
    at.text_area[0].set_value("   ").run()
    at.button[0].click().run()

    assert services.load_entries(services.resolve_device_user(token)).empty
    assert at.warning


def test_reload_keeps_entries_for_the_same_device():
    token = services.new_device_token()
    at = fresh_app(token)
    at.run()
    at.text_area[0].set_value("something worth remembering tomorrow").run()
    at.button[0].click().run()

    reloaded = fresh_app(token)  # fresh session, same cookie
    reloaded.run()
    assert not reloaded.exception
    uid = reloaded.session_state["user_id"]
    assert len(services.load_entries(uid)) == 1


def test_restart_keeps_entries():
    """New engine + new session state, same database file."""
    token = services.new_device_token()
    at = fresh_app(token)
    at.run()
    at.text_area[0].set_value("this should survive a restart")
    at.run()
    at.button[0].click().run()

    import streamlit as st

    st.cache_resource.clear()  # simulates a process restart

    reloaded = fresh_app(token)
    reloaded.run()
    uid = reloaded.session_state["user_id"]
    assert len(services.load_entries(uid)) == 1


def test_app_recovers_if_the_identity_row_disappears():
    """A wiped database underneath a live session should self-heal, not crash."""
    from sqlalchemy import delete

    from db import Device, Entry, User, session_scope

    token = services.new_device_token()
    at = fresh_app(token)
    at.run()
    stale_id = at.session_state["user_id"]

    with session_scope() as s:
        s.execute(delete(Entry).where(Entry.user_id == stale_id))
        s.execute(delete(Device).where(Device.user_id == stale_id))
        s.execute(delete(User).where(User.id == stale_id))

    at.run()
    assert not at.exception
    # A usable identity exists again and the app still writes entries.
    healed_id = at.session_state["user_id"]
    assert services.get_user(healed_id) is not None
    at.text_area[0].set_value("still working after the wipe").run()
    at.button[0].click().run()
    assert not at.exception
    assert len(services.load_entries(healed_id)) == 1


def test_no_entries_shows_empty_state():
    at = fresh_app(services.new_device_token())
    at.run()
    assert any("start showing up here" in m.value for m in at.markdown)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
