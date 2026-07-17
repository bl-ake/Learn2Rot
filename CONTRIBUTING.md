# Contributing to Learn2Rot

Thank you for helping improve Learn2Rot!

## Before you report a bug

1. Restart Anki completely.
2. Confirm you are on the latest Learn2Rot release (or latest `main` if testing from source).
3. Disable other add-ons temporarily to rule out conflicts.
4. Enable **Debug logging** in Learn2Rot settings and attach relevant lines from `learn2rot.log` when possible.

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

Budget overlay physics needs [pymunk](https://www.pymunk.org/).

Anki Media Timer (menu bar / tray + system media lockout) needs platform packages:

- **macOS:** [rumps](https://github.com/jaredks/rumps) (and PyObjC)
- **Windows:** [pystray](https://github.com/moses-palmer/pystray), Pillow, and [PyWinRT](https://github.com/pywinrt/pywinrt) SMTC bindings (`winrt-Windows.Media.Control`, etc.)

Install into `vendor/` with the **same Python Anki ships** (Anki 25.x uses 3.13), on the **target OS** (macOS and Windows wheels differ):

```bash
# Prefer Anki's uv-managed venv when present (macOS example):
"$HOME/Library/Application Support/AnkiProgramFiles/.venv/bin/python" \
  -m pip install -r requirements.txt -t vendor
```

On Windows, use Anki’s bundled Python the same way (`…\AnkiProgramFiles\.venv\Scripts\python.exe -m pip install -r requirements.txt -t vendor`).

For tests/dev in a virtualenv:

```bash
pip install -r requirements-dev.txt
```
## Code style

- Match existing module layout and naming.
- Prefer `gui_hooks` over legacy `wrap` where Anki provides a hook.
- Keep user-visible strings ready for translation with `_()` where practical.
