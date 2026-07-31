"""Real-browser checks for the parts unit tests structurally can't reach.

The device cookie is written by JavaScript and read back from a later HTTP request,
so only an actual browser proves that loop closes. Skipped unless playwright and a
chromium build are both available:

    pip install playwright && playwright install --with-deps chromium
    pytest test_browser.py
"""

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
sync_playwright = playwright_api.sync_playwright

REPO = Path(__file__).parent
ENTRY_A = "Today was absolutely wonderful and I feel great about it"
ENTRY_B = "a separate anonymous note from my other browser"
EMPTY_STATE = "Your mood history will start showing up here"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.port = free_port()
        self.url = f"http://localhost:{self.port}"
        self.proc = None

    def start(self):
        env = {**os.environ, "MINDFLOW_DB_URL": f"sqlite:///{self.db_path}"}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "app.py",
             "--server.headless", "true", "--server.port", str(self.port),
             "--browser.gatherUsageStats", "false"],
            cwd=REPO, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        for _ in range(80):
            try:
                urllib.request.urlopen(f"{self.url}/_stcore/health", timeout=1)
                return self
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("streamlit did not start")

    def stop(self):
        if self.proc:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            self.proc.wait(timeout=30)
            self.proc = None


def settle(page):
    page.wait_for_selector("h1", timeout=45000)
    page.wait_for_timeout(1500)


def token_of(context):
    return {c["name"]: c["value"] for c in context.cookies()}.get("mindflow_device")


def write_entry(page, text):
    box = page.locator("textarea").first
    box.click()
    box.fill(text)
    page.wait_for_timeout(400)
    page.get_by_role("button", name="Analyze My Mood").click()
    page.wait_for_timeout(2500)


def submit_credentials(page, tab, button, email, password):
    page.get_by_role("tab", name=tab).click()
    page.wait_for_timeout(600)
    page.locator('input[type="text"]:visible').last.fill(email)
    page.locator('input[type="password"]:visible').last.fill(password)
    page.get_by_role("button", name=button).click()
    page.wait_for_timeout(3500)


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    server = Server(tmp_path_factory.mktemp("browser") / "browser.db")
    server.start()
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:
                pytest.skip(f"chromium unavailable: {exc}")
            yield server, browser
            browser.close()
    finally:
        server.stop()


def test_full_journey(live):
    """One ordered pass through the behaviours that only a browser can confirm."""
    server, browser = live

    ctx_a = browser.new_context(viewport={"width": 1400, "height": 1000})
    page_a = ctx_a.new_page()
    page_a.goto(server.url)
    settle(page_a)

    assert "MindFlow" in page_a.inner_text("h1"), "first load hung or failed to render"

    token_a = token_of(ctx_a)
    assert token_a, "app did not write a device cookie"

    write_entry(page_a, ENTRY_A)
    assert "Score:" in page_a.inner_text("body"), "mood result was not displayed"
    assert page_a.locator("textarea").first.input_value().strip() == ""

    # An impatient second click must not write a duplicate.
    page_a.get_by_role("button", name="Analyze My Mood").click()
    page_a.wait_for_timeout(2500)
    assert "Write something first" in page_a.inner_text("body")

    # Reload keeps both the identity and the data.
    page_a.reload()
    settle(page_a)
    assert token_of(ctx_a) == token_a, "device token churned across reload"
    page_a.get_by_text("View Journal History").click()
    page_a.wait_for_timeout(1200)
    assert ENTRY_A[:20] in page_a.inner_text("body")

    # A second browser is a separate person.
    ctx_b = browser.new_context(viewport={"width": 1400, "height": 1000})
    page_b = ctx_b.new_page()
    page_b.goto(server.url)
    settle(page_b)
    assert token_of(ctx_b) and token_of(ctx_b) != token_a
    assert EMPTY_STATE in page_b.inner_text("body")

    # Restarting the process must not lose anything.
    server.stop()
    server.start()
    for page in (page_a, page_b):
        page.reload()
        settle(page)
    assert EMPTY_STATE not in page_a.inner_text("body"), "entries lost on restart"

    # Signing up keeps the entries written while anonymous.
    submit_credentials(page_a, "Create account", "Create account", "me@example.com", "hunter2hunter2")
    assert "me@example.com" in page_a.inner_text("body")
    assert EMPTY_STATE not in page_a.inner_text("body")

    # Logging in elsewhere claims that browser's anonymous entries.
    write_entry(page_b, ENTRY_B)
    submit_credentials(page_b, "Log in", "Log in", "me@example.com", "hunter2hunter2")
    page_b.get_by_text("View Journal History").click()
    page_b.wait_for_timeout(1500)
    body_b = page_b.inner_text("body")
    assert ENTRY_B[:20] in body_b, "anonymous entries were orphaned by login"
    assert ENTRY_A[:20] in body_b, "account entries missing after login"

    # Signing out hands the browser back a private, empty journal.
    page_b.get_by_role("button", name="Sign out on this device").click()
    page_b.wait_for_timeout(3000)
    after = page_b.inner_text("body")
    assert ENTRY_A[:20] not in after and "me@example.com" not in after
