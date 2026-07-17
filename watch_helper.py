#!/usr/bin/env python3
# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Standalone helper for Anki Media Timer: tray/menubar + budget drain + pause.

Must run outside Anki's Qt process (menubar/tray hosts differ by OS).
Owns the watch-time clock while running; Anki only earns/undos via the state file.

Usage:
    python watch_helper.py --state /path/to/ankitube_watch_state.json
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parent
_VENDOR = _ROOT / "vendor"
for candidate in (_ROOT, _VENDOR):
    path = str(candidate)
    if candidate.is_dir() and path not in sys.path:
        sys.path.insert(0, path)

from media_control import MediaController, create_media_controller  # noqa: E402
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
_TICK_INTERVAL_SEC = 0.25


class MediaTimerEngine:
    """Platform-agnostic drain / pause / state ownership."""

    def __init__(
        self,
        state_path: Path,
        media: Optional[MediaController] = None,
    ) -> None:
        self._state_path = state_path
        self._media = media or create_media_controller()
        self._last_drain_mono = 0.0
        self._paused_for_budget = False

    def publish_pid(self) -> None:
        pid = os.getpid()

        def mutator(state: dict) -> None:
            state["pid"] = pid
            state["exit"] = False

        try:
            update_state(self._state_path, mutator)
        except OSError:
            pass

    def tick(self) -> tuple[bool, str, bool]:
        """Run one drain/pause tick.

        Returns (should_quit, label, show_icon).
        """
        try:
            snapshot = read_state(self._state_path)
        except OSError:
            return False, _DEFAULT_LABEL, True
        if snapshot.get("exit"):
            return True, _DEFAULT_LABEL, True

        prefs = snapshot.get("prefs") or {}
        enforce = bool(prefs.get("enforce", True))
        show_icon = bool(prefs.get("show_menubar_watch_time", True))
        auto_resume = bool(prefs.get("auto_resume_on_budget", False))
        was_paused = bool(snapshot.get("paused_for_budget", False)) or (
            self._paused_for_budget
        )

        self._tick_locked(
            enforce=enforce,
            auto_resume=auto_resume,
            was_paused=was_paused,
        )

        try:
            latest = read_state(self._state_path)
        except OSError:
            return False, _DEFAULT_LABEL, show_icon
        label = str(latest.get("label") or _DEFAULT_LABEL)
        return False, label, show_icon

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

    def write_exit(self) -> None:
        try:
            write_exit(self._state_path)
        except OSError:
            pass


class _DarwinShell:
    """rumps menubar host for macOS."""

    def __init__(self, engine: MediaTimerEngine) -> None:
        import rumps

        self._rumps = rumps
        self._engine = engine
        self._last_label = ""
        self._menubar_visible = True
        self._app = rumps.App(
            "Anki Media Timer",
            title=_DEFAULT_LABEL,
            quit_button=None,
        )
        self._app.menu = [
            rumps.MenuItem("Anki Media Timer", callback=None),
            None,
            rumps.MenuItem("Quit Anki Media Timer", callback=self._quit),
        ]
        self._timer = rumps.Timer(self._on_timer, _TICK_INTERVAL_SEC)

    def run(self) -> None:
        self._engine.publish_pid()
        self._apply_tick(force=True)
        self._timer.start()
        self._app.run()

    def _on_timer(self, _: Any) -> None:
        self._apply_tick()

    def _apply_tick(self, *, force: bool = False) -> None:
        should_quit, label, show_icon = self._engine.tick()
        if should_quit:
            self._quit(None)
            return
        self._apply_menubar_visibility(visible=show_icon, label=label, force=force)

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
                self._app.title = label
                self._last_label = label
                self._menubar_visible = True
            return
        if self._menubar_visible or force:
            self._app.title = label or _DEFAULT_LABEL
            self._set_status_item_visible(False)
            self._menubar_visible = False
            self._last_label = ""

    def _set_status_item_visible(self, visible: bool) -> None:
        try:
            nsapp = getattr(self._app, "_nsapp", None)
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

    def _quit(self, _: Any) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass
        self._engine.write_exit()
        self._rumps.quit_application()
        sys.exit(0)


def _make_tray_icon_image() -> Any:
    """Small solid icon for pystray (no bundled asset required)."""
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = 8
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(40, 40, 40, 255),
        outline=(220, 220, 220, 255),
        width=3,
    )
    # Simple clock hands
    cx = cy = size // 2
    draw.line([(cx, cy), (cx, margin + 14)], fill=(220, 220, 220, 255), width=3)
    draw.line([(cx, cy), (cx + 14, cy + 6)], fill=(220, 220, 220, 255), width=3)
    return image


class _WindowsShell:
    """pystray system-tray host for Windows."""

    def __init__(self, engine: MediaTimerEngine) -> None:
        import pystray
        from pystray import MenuItem as item

        self._pystray = pystray
        self._engine = engine
        self._last_label = ""
        self._icon_visible = True
        self._stop = threading.Event()
        self._icon = pystray.Icon(
            "Anki Media Timer",
            _make_tray_icon_image(),
            _DEFAULT_LABEL,
            menu=pystray.Menu(
                item("Anki Media Timer", None, enabled=False),
                item("Quit Anki Media Timer", self._on_quit),
            ),
        )

    def run(self) -> None:
        self._engine.publish_pid()
        should_quit, label, show_icon = self._engine.tick()
        if should_quit:
            self._engine.write_exit()
            return
        self._apply_icon(label=label, visible=show_icon, force=True)
        ticker = threading.Thread(target=self._tick_loop, name="ankitube-tick", daemon=True)
        ticker.start()
        self._icon.run()
        self._stop.set()

    def _tick_loop(self) -> None:
        while not self._stop.wait(_TICK_INTERVAL_SEC):
            should_quit, label, show_icon = self._engine.tick()
            if should_quit:
                self._stop.set()
                try:
                    self._icon.stop()
                except Exception:
                    pass
                return
            self._apply_icon(label=label, visible=show_icon, force=False)

    def _apply_icon(
        self, *, label: str, visible: bool, force: bool = False
    ) -> None:
        try:
            if visible:
                if force or label != self._last_label or not self._icon_visible:
                    self._icon.visible = True
                    self._icon.title = label
                    self._last_label = label
                    self._icon_visible = True
                return
            if self._icon_visible or force:
                self._icon.visible = False
                self._icon_visible = False
                self._last_label = ""
        except Exception:
            pass

    def _on_quit(self, icon: Any, _item: Any) -> None:
        self._stop.set()
        self._engine.write_exit()
        try:
            icon.stop()
        except Exception:
            pass


def _supported_helper_platform() -> bool:
    return sys.platform in ("darwin", "win32")


def main() -> None:
    if not _supported_helper_platform():
        raise SystemExit(
            "Anki Media Timer only runs on macOS and Windows."
        )
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
    engine = MediaTimerEngine(state_path)
    if sys.platform == "darwin":
        _DarwinShell(engine).run()
    else:
        _WindowsShell(engine).run()


if __name__ == "__main__":
    main()
