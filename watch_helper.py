#!/usr/bin/env python3
# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Standalone helper for Anki Media Timer: tray/menubar + budget drain + pause.

Must run outside Anki's Qt process (menubar/tray hosts differ by OS).
Owns the watch-time clock while running; Anki only earns/undos via the state file.

Usage:
    python watch_helper.py --state /path/to/learn2rot_watch_state.json
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from vendor_paths import resolve_vendor_dir  # noqa: E402

_VENDOR = resolve_vendor_dir(_ROOT)
if _VENDOR is not None:
    _vendor_path = str(_VENDOR)
    if _vendor_path not in sys.path:
        sys.path.insert(0, _vendor_path)

from media_control import MediaController, create_media_controller  # noqa: E402
from watch_state import (  # noqa: E402
    WEEKDAYS,
    apply_pending_adjustments,
    default_state,
    drain_one_second,
    format_seconds,
    is_on_day,
    is_within_on_hours,
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
        now_provider: Callable[[], time.struct_time] = time.localtime,
    ) -> None:
        self._state_path = state_path
        self._media = media or create_media_controller()
        self._last_drain_mono = 0.0
        self._paused_for_budget = False
        self._now_provider = now_provider

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
        # Only Anki can compute cards_due (needs collection access); it pushes
        # the value here. require_cards_due off (the default) makes gate_active
        # always False, so behavior is unchanged for everyone who hasn't
        # turned the setting on. Even when on, this only changes what happens
        # once budget actually reaches zero — banked time still counts down
        # normally either way, so nothing changes the instant cards stop
        # being due.
        require_due = bool(prefs.get("require_cards_due", False))
        cards_due = bool(snapshot.get("cards_due", True))
        if not cards_due and not snapshot.get("anki_alive", False):
            # Nothing is refreshing cards_due while Anki is closed. Anki
            # records when it expects the next card to become due (day
            # rollover or a learning-queue card); once that time has passed,
            # a leftover False is stale rather than still accurate, so stop
            # trusting it. No recorded time at all is treated the same way.
            due_at = int(snapshot.get("cards_due_at", 0) or 0)
            if due_at <= 0 or time.time() >= due_at:
                cards_due = True
        gate_active = require_due and not cards_due
        show_menubar = bool(prefs.get("show_menubar_watch_time", True))
        auto_resume = bool(prefs.get("auto_resume_on_budget", False))
        was_paused = bool(snapshot.get("paused_for_budget", False)) or (
            self._paused_for_budget
        )
        # Unlike require_cards_due, this applies for the whole window, not
        # just once budget hits zero — outside on-hours the timer backs off
        # entirely (no drain, no pause, icon hidden), and picks back up on
        # its own the next tick once the window reopens. Computed locally
        # from the system clock, so it works even with Anki closed.
        in_on_hours = self._in_on_hours(prefs)

        self._tick_locked(
            enforce=enforce and in_on_hours,
            auto_resume=auto_resume,
            was_paused=was_paused,
            gate_active=gate_active,
        )

        try:
            latest = read_state(self._state_path)
        except OSError:
            return False, _DEFAULT_LABEL, show_menubar and in_on_hours
        label = str(latest.get("label") or _DEFAULT_LABEL)
        # Nothing left to show/lock once we've backed off at zero — there's
        # no countdown running and no lockout to indicate.
        backed_off = gate_active and int(latest.get("budget_seconds", 0) or 0) <= 0
        show_icon = show_menubar and in_on_hours and not backed_off
        return False, label, show_icon

    def _in_on_hours(self, prefs: dict) -> bool:
        if not bool(prefs.get("on_hours_enabled", False)):
            return True
        start = prefs.get("on_hours_start")
        end = prefs.get("on_hours_end")
        if not start or not end:
            return True
        now = self._now_provider()
        on_days = prefs.get("on_days") or list(WEEKDAYS)
        if not is_on_day(now.tm_wday, on_days):
            return False
        now_minutes = now.tm_hour * 60 + now.tm_min
        return is_within_on_hours(now_minutes, start, end)

    def _tick_locked(
        self,
        *,
        enforce: bool,
        auto_resume: bool,
        was_paused: bool,
        gate_active: bool = False,
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
                if gate_active:
                    # Nothing due to review means no way to earn more time —
                    # locking media would just strand the user, so leave
                    # playback alone instead of pausing it.
                    self._paused_for_budget = False
                    current["paused_for_budget"] = False
                else:
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
                        if gate_active:
                            self._paused_for_budget = False
                            current["paused_for_budget"] = False
                        else:
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
            if gate_active and current["budget_seconds"] <= 0:
                # Backed off: we're not the one controlling playback anymore,
                # so report what's actually happening instead of forcing False.
                current["is_playing"] = bool(playing)
            else:
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


# Pre-baked multi-size ICO (16/32/64). pystray only needs an object with save().
# Avoids bundling Pillow (~14MB of native wheels) just for a tray glyph.
_TRAY_ICO_B64 = (
    "AAABAAMAEBAAAAAAIABTAgAANgAAACAgAAAAACAAvgQAAIkCAABAQAAAAAAgAKIBAABH"
    "BwAAiVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAACGklEQVR4nKWTPYsa"
    "QRjHn9mXI75BThYFdddGiCuohQiBlIKCRUBwQfELpEiRwta3ryAp8g0sYnWFoH3IgbCF"
    "jVy9WohLEMR179zdCc/enfFMIoQMLCzz//3/88zMMwBng1JKKKUcAJCTaXcOtXP+xTgH"
    "KKWv8bvEkFOBEFyI8rZtf5xOp8pisZBRi8Vi83w+/5Vl2c+EkMMzewygv8zB+Xx+MxgM"
    "3k0mE1iv16hBKBSCYrEI9Xr9myzL7wkhP44h9HHPDKX01Ww2+95oNGgwGHxIJBJ2Op12"
    "stmsg/84hxoyyD55CAMALCHEMQzjw3A4fDsajR4kSeJN02RyuRxRFIUYhsHE43EeNWSQ"
    "RQ96mW636+AJq6raGI/HjiAI7OFwcEvnOA54ngdCCFiWBaghgyx60Mv0ej1MCiyXyze6"
    "rjMcx2FpbgDLshAIBGC73YJpmhjGIIMsetDLvLzI442Az+cDVVVhv99Ds9mESCTiVoHV"
    "nA6m0+lgyDYajd4JguBYluU8l7/ZbKDf77tBlUoFRFF0/H6/I4riHXpcL33sOtjtdp/a"
    "7Ta9vr6+z2QyNJlMUlmWaSqVopIkuf/hcPi+1WpRZJ8q5XB1G6/E6/V+qVart+Vy+UrT"
    "tINt23g22BvU4/E4q9XqUCgUrhRFuUUWPegllxpJ13V3n4IgQKlUglqt9lsjXWxlTdPc"
    "VhZF8a+t/N+P6Y8h//KcfwJMW3f6Cb+vqAAAAABJRU5ErkJggolQTkcNChoKAAAADUlI"
    "RFIAAAAgAAAAIAgGAAAAc3p69AAABIVJREFUeJzVl01IK1cUx++dycckaZoQtSJuJOhC"
    "jaAmyBQEWxAerQWFko9loeBWcNlNFF2q4MaFUKwr60hRoY8iBNquGjURRVcKIi6LRmNM"
    "MtFkbvnP68gkL1/4PuQdGDKZe87vf+65H3OHkBc2+txAxlhRLKWUvZeMaonGYjFjpXa0"
    "lSZWy2g9ToCW9jAajX5+cnKiPvN4PFQUxbtaMc9KgDHGU0oLuD88PPzC4XD8aLPZhqxW"
    "65fZbFbBc4vFwmUymX/S6fTfyWTy597e3n9LYyuZoVqjJEkqQJIklyiKvzgcjq9lWf5s"
    "f38fFSBWq1X1y2QyRBTFVz6f75XL5frp8vLyz2g0+gOlNAFGIBComkRZ08b64ODg2+vr"
    "6+vT01M2MzPDvF7vo9vtzjc3NystLS3qhXs8Qxt84IsYxOpZdZskSTx+o9HoSCqVym9u"
    "brKurq7H1tZWpb29nXV2djKPx1N04Rna4ANfxCAWDD2z5hyQJIn3+/3s+Pj4m7a2tu1I"
    "JEInJycJz/OcxWIhhUIBY6v64ldRFLS9gVGq3mezWfgpCwsLZHh4mF1cXIz29PT8sbGx"
    "QUuHg9P/CYfDHByWl5eFpqYmKRKJ8BA3mUyc2Wwm+Xy+SNxkMhG73f4Uj2fwgS9iEAsG"
    "WGCCDY2KFWCMcfF4nG9sbPzt4eHhu7GxMSWXy/EAoqeaoZc3NzdkfHycBINB9UKvDQbD"
    "U4Icx5FcLodkCltbW5zJZPr96urqe6/XW6CUPsG4knWr3N7eusxm88j6+jpNJpNq2fXi"
    "+t6WVkBviEEsGGCBCTY09JsVp93E43F1SXZ0dIQMBgPb3t5+tFgsFGNeybSSVzLEggEW"
    "mGDrtYoSOD8/VxYXF82CIIzs7e3xiUSC05e0kmHiVUsQDLDABBsa0CpKgDGmzk5BEASe"
    "57/CJpNOpzltdlcTQKm12V/O8BwsMMGGBrS0YeD0zrIsM8ZYGjtctZ5p4picGGdZljHW"
    "6sTDVa5KYIINDX0bV1Wlhng8HiepVIosLS2RoaEhNQn8r5RIOeP0fwRBwEqwYW+vNvYo"
    "O3q0u7tLAoEAtmsyPT1NVlZWyODgILm7u0PZn5IAC0ywofFWApRShh1QlmW5UCj8JYoi"
    "sdlsSq0VgPJDaH5+noRCIXJ0dESmpqbI6uoq6e/vV4cGfmCBCTY0/n/JsaIKuN1ubmJi"
    "IifL8uuBgYGCy+VSsMSqzQVtG25oaCD39/dkbm6O+P1+rCjS19enbkToBFhggg0NaL01"
    "BF6vV13QZ2dnv+bzeTo6OmrMZrOsnpUAESw3JIJSh8Nhsra2RpxOJxJjYIEJtl6rKAGU"
    "BFux0+lM5HK518FgkDkcDgVbbD0TSkvEaDSqwjAMARhggQk2NPQnJU4Pwfj5fL7HnZ2d"
    "kN1uz87OzvJ4q2Eo6p3V2uRFMogFAywwwYbGe3sdl9pzXsfvdCDp7u5Wr3c5kLz4kYxW"
    "TuFN1ihZ6aE0FouVO5Ri/mAzu08mk+qhNBAI1DyUUvLCx3JaK4EP/WHyaXyakQrJFIE+"
    "xsfph7D/ACn4cdp0gqqeAAAAAElFTkSuQmCCiVBORw0KGgoAAAANSUhEUgAAAEAAAABA"
    "CAYAAACqaXHeAAABaUlEQVR4nO2a2w2DMAxF7TsL4zAu47BLKz4qRQi1eTixXft8p+B7"
    "lEAaTJQkSZIkSVR45c3O83zVjNu2bVldbCW0lgy2GHqlDF4Zft/3qmscx7FMAs8OXhu6"
    "VYaUCJ4VfjR4jQgJCSwdXjr4LxGjEuAp/NM9Rh+48BR+hgR4Cy8tAR7DS0qAZAEajNbA"
    "LYNLwxLhyye65H6h5c0A7e3tDFpqhdepL1UTKDjQWPuzKGurXQag4ICCg3+Z/r3LABQc"
    "UHBAwQEFB2SEbwehfytgv71VLgmrRYCUuSRoigAZQUsEyBirRXDNIM3d4FPwbzW0HoyA"
    "jPM0IyQBOWGWCK4d6OFPUc+5ICg48LRrm1ETageubFsZZcqx+P3ClmZB7zeB4WeABQmj"
    "NaD1B3fDmhIkegXQc2MLEqQaJdBbgKYEyS4RjBSiIUG6RYZJgNBNUh9Ct8mVhG2ULAnd"
    "KnsnZLO0p3b5JEmSJEkoLG+QMuQHUNyRtAAAAABJRU5ErkJggg=="
)
_TRAY_ICO_BYTES = base64.b64decode(_TRAY_ICO_B64)


class _StaticIcoImage:
    """Minimal stand-in for PIL.Image so pystray can write an ICO file."""

    def save(self, fp: Any, format: str | None = None, **_kwargs: Any) -> None:
        del format  # pystray always requests ICO on Windows
        fp.write(_TRAY_ICO_BYTES)


def _make_tray_icon_image() -> Any:
    """Tray icon for pystray without a Pillow dependency."""
    return _StaticIcoImage()


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
        ticker = threading.Thread(target=self._tick_loop, name="learn2rot-tick", daemon=True)
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


def _prepare_state_path(state_path: Path) -> Path:
    state_path = state_path.expanduser()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if not state_path.exists():
        write_state(state_path, default_state())
    return state_path


_inproc_thread: Optional[threading.Thread] = None
_inproc_shell: Optional[_WindowsShell] = None
_inproc_lock = threading.Lock()


def windows_helper_inprocess_alive() -> bool:
    thread = _inproc_thread
    return thread is not None and thread.is_alive()


def start_windows_helper_inprocess(state_path: Path) -> threading.Thread:
    """Run the Windows tray helper inside Anki's process.

    Packaged Windows Anki uses ``Anki.exe`` as ``sys.executable``, so spawning
    a subprocess re-launches Anki (single-instance + unsupported file type).
    pystray can host the tray icon from a background thread instead.
    """
    global _inproc_thread, _inproc_shell
    with _inproc_lock:
        if windows_helper_inprocess_alive():
            assert _inproc_thread is not None
            return _inproc_thread
        prepared = _prepare_state_path(Path(state_path))

        def target() -> None:
            global _inproc_shell
            engine = MediaTimerEngine(prepared)
            shell = _WindowsShell(engine)
            _inproc_shell = shell
            try:
                shell.run()
            finally:
                if _inproc_shell is shell:
                    _inproc_shell = None

        thread = threading.Thread(
            target=target,
            name="learn2rot-watch-helper",
            daemon=True,
        )
        _inproc_thread = thread
        thread.start()
        return thread


def stop_windows_helper_inprocess(state_path: Path) -> None:
    """Ask the in-process Windows helper to exit (never kill Anki's PID)."""
    global _inproc_thread, _inproc_shell
    write_exit(Path(state_path))
    shell = _inproc_shell
    if shell is not None:
        try:
            shell._stop.set()
            shell._icon.stop()
        except Exception:
            pass
    thread = _inproc_thread
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=2.0)
    with _inproc_lock:
        if _inproc_thread is thread:
            _inproc_thread = None
        if _inproc_shell is shell:
            _inproc_shell = None


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
    state_path = _prepare_state_path(Path(args.state))
    engine = MediaTimerEngine(state_path)
    if sys.platform == "darwin":
        _DarwinShell(engine).run()
    else:
        _WindowsShell(engine).run()


if __name__ == "__main__":
    main()
