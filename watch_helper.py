#!/usr/bin/env python3
# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Standalone macOS helper for Anki Media Timer: menubar + budget drain + pause.

Must run outside Anki's Qt process — in-process NSStatusItem ends up occluded.
Owns the watch-time clock while running; Anki only earns/undos via the state file.

Usage:
    python watch_helper.py --state /path/to/ankitube_watch_state.json
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_VENDOR = _ROOT / "vendor"
for candidate in (_ROOT, _VENDOR):
    path = str(candidate)
    if candidate.is_dir() and path not in sys.path:
        sys.path.insert(0, path)

import rumps  # noqa: E402

from media_control import create_media_controller  # noqa: E402
from watch_state import (  # noqa: E402
    apply_pending_adjustments,
    default_state,
    drain_one_second,
    format_seconds,
    read_state,
    update_state,
    write_exit,
    write_state,
)

_DEFAULT_LABEL = "0:00"
_DRAIN_INTERVAL_SEC = 1.0


class MediaTimerApp(rumps.App):
    def __init__(self, state_path: Path) -> None:
        super().__init__(
            "Anki Media Timer",
            title=_DEFAULT_LABEL,
            quit_button=None,
        )
        self._state_path = state_path
        self._media = create_media_controller()
        self._last_label = ""
        self._menubar_visible = True
        self._last_drain_mono = 0.0
        self._paused_for_budget = False
        self.menu = [
            rumps.MenuItem("Anki Media Timer", callback=None),
            None,
            rumps.MenuItem("Quit Anki Media Timer", callback=self.quit_app),
        ]
        self._publish_pid()
        self._apply_tick(force=True)
        self._timer = rumps.Timer(self._on_timer, 0.25)
        self._timer.start()

    def _publish_pid(self) -> None:
        pid = os.getpid()

        def mutator(state: dict) -> None:
            state["pid"] = pid
            state["exit"] = False

        try:
            update_state(self._state_path, mutator)
        except OSError:
            pass

    def _on_timer(self, _: rumps.Timer) -> None:
        self._apply_tick()

    def _apply_tick(self, *, force: bool = False) -> None:
        try:
            snapshot = read_state(self._state_path)
        except OSError:
            return
        if snapshot.get("exit"):
            self.quit_app(None)
            return

        prefs = snapshot.get("prefs") or {}
        enforce = bool(prefs.get("enforce", True))
        show_menubar = bool(prefs.get("show_menubar_watch_time", True))
        auto_resume = bool(prefs.get("auto_resume_on_budget", False))
        was_paused = bool(snapshot.get("paused_for_budget", False)) or self._paused_for_budget

        self._tick_locked(
            enforce=enforce,
            auto_resume=auto_resume,
            was_paused=was_paused,
        )

        try:
            latest = read_state(self._state_path)
        except OSError:
            return
        label = str(latest.get("label") or _DEFAULT_LABEL)
        self._apply_menubar_visibility(visible=show_menubar, label=label, force=force)

    def _apply_menubar_visibility(
        self, *, visible: bool, label: str, force: bool = False
    ) -> None:
        """Show or fully hide the menu bar status item.

        rumps falls back to the app name when title is empty, so we must hide
        the NSStatusItem itself rather than clearing the title.
        """
        if visible:
            if force or label != self._last_label or not self._menubar_visible:
                self._set_status_item_visible(True)
                self.title = label
                self._last_label = label
                self._menubar_visible = True
            return
        if self._menubar_visible or force:
            # Keep a non-empty internal title so rumps won't swap in the app name
            # if something re-shows the item; the item itself is hidden.
            self.title = label or _DEFAULT_LABEL
            self._set_status_item_visible(False)
            self._menubar_visible = False
            self._last_label = ""

    def _set_status_item_visible(self, visible: bool) -> None:
        try:
            nsapp = getattr(self, "_nsapp", None)
            item = getattr(nsapp, "nsstatusitem", None) if nsapp is not None else None
            if item is None:
                return
            if hasattr(item, "setVisible_"):
                item.setVisible_(bool(visible))
                if visible and hasattr(item, "setLength_"):
                    item.setLength_(-1)  # NSVariableStatusItemLength
                return
            if hasattr(item, "setLength_"):
                item.setLength_(-1 if visible else 0)
        except Exception:
            pass

    def _tick_locked(
        self, *, enforce: bool, auto_resume: bool, was_paused: bool
    ) -> None:
        def mutator(current: dict) -> None:
            before_budget = int(current.get("budget_seconds", 0) or 0)
            applied = apply_pending_adjustments(current)
            current.clear()
            current.update(applied)
            after_credits = int(current.get("budget_seconds", 0) or 0)
            current["pid"] = os.getpid()
            current["exit"] = False

            if not enforce:
                current["is_playing"] = False
                current["paused_for_budget"] = False
                current["label"] = format_seconds(after_credits)
                self._paused_for_budget = False
                self._last_drain_mono = 0.0
                return

            info = self._media.get_now_playing()
            budget = after_credits
            playing = bool(info.supported and info.is_playing)

            if (
                auto_resume
                and (was_paused or self._paused_for_budget)
                and before_budget <= 0
                and budget > 0
            ):
                self._media.play()
                self._paused_for_budget = False
                playing = True

            if budget <= 0:
                self._paused_for_budget = True
                current["paused_for_budget"] = True
                if info.supported:
                    self._media.pause()
                playing = False
                self._last_drain_mono = 0.0
            elif playing:
                self._paused_for_budget = False
                current["paused_for_budget"] = False
                now = time.monotonic()
                if (
                    self._last_drain_mono <= 0
                    or now - self._last_drain_mono >= _DRAIN_INTERVAL_SEC
                ):
                    budget, has_time = drain_one_second(budget)
                    current["budget_seconds"] = budget
                    self._last_drain_mono = now
                    if not has_time:
                        self._paused_for_budget = True
                        current["paused_for_budget"] = True
                        self._media.pause()
                        playing = False
            else:
                self._last_drain_mono = 0.0
                current["paused_for_budget"] = bool(self._paused_for_budget)

            current["budget_seconds"] = max(
                0, int(current.get("budget_seconds", budget))
            )
            current["is_playing"] = bool(
                playing and current["budget_seconds"] > 0
            )
            current["label"] = format_seconds(int(current["budget_seconds"]))

        try:
            update_state(self._state_path, mutator)
        except OSError:
            pass

    def quit_app(self, _: rumps.MenuItem | None) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass
        try:
            write_exit(self._state_path)
        except OSError:
            pass
        rumps.quit_application()
        sys.exit(0)


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Anki Media Timer only runs on macOS.")
    parser = argparse.ArgumentParser(description="Anki Media Timer")
    parser.add_argument(
        "--state",
        required=True,
        help="Path to JSON state file shared with the Anki add-on",
    )
    args = parser.parse_args()
    state_path = Path(args.state).expanduser()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if not state_path.exists():
        write_state(state_path, default_state())
    MediaTimerApp(state_path).run()


if __name__ == "__main__":
    main()
