#!/usr/bin/env python3
# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Standalone rumps menu-bar helper for AnkiTube watch time.

Must run outside Anki's Qt process — in-process NSStatusItem ends up occluded.
Mirrors the OpenConnect menubar.py pattern (rumps.App + timer + title updates).

Usage:
    python menubar_helper.py --state /path/to/ankitube_menubar_state.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Prefer bundled vendor/ (pymunk, rumps, PyObjC) when launched by the add-on.
_VENDOR = Path(__file__).resolve().parent / "vendor"
if _VENDOR.is_dir():
    path = str(_VENDOR)
    if path not in sys.path:
        sys.path.insert(0, path)

import rumps  # noqa: E402


_EXIT_SENTINEL = "__EXIT__"
_DEFAULT_LABEL = "0:00"


def _read_state(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    if not text:
        return {}
    if text == _EXIT_SENTINEL:
        return {"exit": True}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"label": text}
    if isinstance(data, dict):
        return data
    return {}


class AnkiTubeWatchApp(rumps.App):
    def __init__(self, state_path: Path) -> None:
        super().__init__(
            "AnkiTube",
            title=_DEFAULT_LABEL,
            quit_button=None,
        )
        self._state_path = state_path
        self._last_label = ""
        self.menu = [
            rumps.MenuItem("AnkiTube watch time", callback=None),
            None,
            rumps.MenuItem("Quit menu bar icon", callback=self.quit_app),
        ]
        self._apply_state(force=True)
        self._timer = rumps.Timer(self._poll_state, 0.5)
        self._timer.start()

    def _poll_state(self, _: rumps.Timer) -> None:
        self._apply_state()

    def _apply_state(self, *, force: bool = False) -> None:
        data = _read_state(self._state_path)
        if data.get("exit"):
            self.quit_app(None)
            return
        label = str(data.get("label") or _DEFAULT_LABEL)
        if force or label != self._last_label:
            self.title = label
            self._last_label = label

    def quit_app(self, _: rumps.MenuItem | None) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass
        rumps.quit_application()
        sys.exit(0)


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("AnkiTube menu bar helper only runs on macOS.")
    parser = argparse.ArgumentParser(description="AnkiTube menu bar watch-time helper")
    parser.add_argument(
        "--state",
        required=True,
        help="Path to JSON state file written by the AnkiTube add-on",
    )
    args = parser.parse_args()
    state_path = Path(args.state).expanduser()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if not state_path.exists():
        state_path.write_text(
            json.dumps({"label": _DEFAULT_LABEL}),
            encoding="utf-8",
        )
    AnkiTubeWatchApp(state_path).run()


if __name__ == "__main__":
    main()
