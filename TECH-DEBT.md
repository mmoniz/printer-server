# Tech debt

This file lists shortcuts we took on purpose, problems we know about, and fixes we put off. Log them here instead of leaving TODOs in code, so they stay visible and can be prioritized.

**Adding an entry:** give it the next ID and fill in every column. **Impact** says what goes wrong, or what gets harder, if we leave it. **Area** is the skill or component it belongs to.

**Paying one off:** move the row to **Resolved** and add the PR link.

## Open

| ID | Area | Issue | Impact | Proposed fix | Logged |
|---|---|---|---|---|---|
| TD-1 | repo-standards | CI runs `ruff check` but no format check or typecheck (ENG-06). `ruff format` would reformat 19 files (about 1,000 changed lines), and mypy reports 10 errors in `mail.py`, `mailstore.py` and `normalize.py`. | A formatting or typing regression merges unnoticed. Folding the reformat into the standards PR would have buried the real changes. | A follow-up `refactor/` PR: format the repo, fix the 10 mypy errors, and add both steps to CI. | 2026-10-05 |
| TD-2 | pi-deployment | The Pi runs Python 3.11 from Raspberry Pi OS Bookworm, which Raspberry Pi now lists as Legacy. 3.11 gets security fixes only, until 2027-10 (devguide.python.org/versions). Trixie (Debian 13, Python 3.13) has a 32-bit Lite image. | The runtime ages out in October 2027, and numpy 2.5 and later need Python 3.12 or newer, so the Pi stays on older packages meanwhile. | Try Trixie on a spare SD card with the Pi 2, then move `.python-version`, `requires-python` and CI together in a `chore/` PR. | 2026-10-05 |
| TD-3 | pi-deployment | `requirements.txt` keeps `>=` floors so the Pi can reuse apt's numpy and Pillow, while CI tests the versions in `uv.lock`. | The Pi can run versions CI never tested. | Test the apt versions in CI (a Bookworm container with `python3-numpy`, `python3-pil`), or add constraints for the Pi install. | 2026-10-05 |
| TD-4 | label-web-app | `labelserver/app.py` ends with `app.run(host="0.0.0.0", port=8080, debug=True)` behind `if __name__ == "__main__"`. | Running `python labelserver/app.py` exposes Flask's interactive debugger to the whole LAN. Nothing in the repo does this (the service uses gunicorn, the docs use `flask run`), but nothing prevents it either. | Bind to `127.0.0.1` and drop `debug=True`, with a test that reads the entry point. | 2026-10-05 |
| TD-5 | tests | `pytest -W default` shows `ResourceWarning`s: sockets left open in two `test_urlfetch.py` tests, and a sqlite connection left open. | Noise now. These could hide a real leak later. | Close the sockets and connections in the fixtures. | 2026-10-05 |

## Resolved

| ID | Area | Issue | Resolution | Resolved |
|---|---|---|---|---|
