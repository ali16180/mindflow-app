# mindflow-app

A small journalling app: write about your day, get a sentiment read on it, watch the
trend over time.

🔗 **Try it:** [mindflow-app-gidfdrgldaspkjjercqyvm.streamlit.app](https://mindflow-app-gidfdrgldaspkjjercqyvm.streamlit.app/)

No account needed to start — open the link and write. See below for how identity and
storage work if you want the details.

## How identity works

You can use the app without an account. On first visit the app checks the request for a
`mindflow_device` cookie; if there genuinely isn't one, it mints a device token, stores
it in a cookie, and creates an anonymous user row keyed to that token. The read is
synchronous — it comes from the HTTP request headers via `st.context.cookies`, so
"absent" and "not read yet" are never confused, and a token is minted exactly once per
browser.

Creating an account attaches an email and password to the anonymous identity you're
already using, so everything written beforehand stays with you. Logging in from a
different browser pulls that browser's anonymous entries into the account. Logging in
never absorbs entries that already belong to another account.

Signing in persists on that device until you sign out, the same as most apps — so
anyone with access to your browser can read your journal. Passwords are hashed with
scrypt (stdlib, salted per user).

## Storage

SQLite through SQLAlchemy, at `mindflow.db` by default. No database server.

**On Streamlit Community Cloud the file does not survive a container restart.** The
container gets recycled on redeploy, on wake-from-sleep, and on resource limits, and the
filesystem goes with it. That is a property of the host, not something the app can fix
from inside. Two ways around it:

- Use **Backup & restore** in the app to download a JSON copy, and restore it after a
  reset. Worth doing periodically if you keep anything you'd miss.
- Point `MINDFLOW_DB_URL` at a hosted database (env var or `.streamlit/secrets.toml`).
  Because everything goes through SQLAlchemy, that's the only change needed.

Run locally and the file persists normally. Timestamps are stored and displayed in UTC.

## Running it locally

```bash
pip install -r requirements.txt
streamlit run app.py
python -m pytest test_mindflow.py     # 25 tests, a few seconds
```

`test_browser.py` drives a real Chromium against a live server and covers what the
unit tests structurally can't — the cookie is written by JavaScript and read back from
a later HTTP request, so only a browser proves that loop closes. It also restarts the
server mid-test to check nothing is lost. It skips itself unless you set it up:

```bash
pip install playwright && playwright install --with-deps chromium
python -m pytest test_browser.py      # ~40s
```
