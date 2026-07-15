# Contributing to AnkiTube

Thank you for helping improve AnkiTube!

## Before you report a bug

1. Restart Anki completely.
2. Confirm you are on the latest AnkiTube release (or latest `main` if testing from source).
3. Disable other add-ons temporarily to rule out conflicts.
4. Enable **Debug logging** in AnkiTube settings and attach relevant lines from `ankittube.log` when possible.

## Reporting issues

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md) and include:

- Anki version (Help → About)
- Operating system
- Steps to reproduce
- What you expected vs what happened

## Pull requests

1. Fork the repo and create a focused branch.
2. Keep changes scoped to the problem you are solving.
3. Run local checks before opening a PR:

   ```bash
   python -m compileall -q .
   python package.py --check
   pip install -r requirements-dev.txt
   pytest tests/ -q
   ```

4. Fill out the [pull request template](.github/PULL_REQUEST_TEMPLATE.md).

## Development install

Clone into your Anki add-ons folder and restart Anki. When packaging locally, use `python package.py --no-update-mod` to avoid bumping `manifest.json` during iteration.

### Runtime dependencies (`vendor/`)

Budget overlay physics needs [pymunk](https://www.pymunk.org/). Anki Media Timer (menu bar + Now Playing lockout) needs [rumps](https://github.com/jaredks/rumps) (and PyObjC). Install both into `vendor/` with the **same Python Anki ships** (Anki 25.x uses 3.13):

```bash
# Prefer Anki's uv-managed venv when present:
"$HOME/Library/Application Support/AnkiProgramFiles/.venv/bin/python" \
  -m pip install -r requirements.txt -t vendor
```

For tests/dev in a virtualenv:

```bash
pip install -r requirements-dev.txt
```

## Code style

- Match existing module layout and naming.
- Prefer `gui_hooks` over legacy `wrap` where Anki provides a hook.
- Keep user-visible strings ready for translation with `_()` where practical.
