#!/usr/bin/env python3
# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Login sentinel for Anki Media Timer: revive the helper when media starts.

Runs from a login item (see autostart.py) so watch-time enforcement survives
Anki being closed — or never opened since boot. It stays idle and cheap until
system media starts playing, then spawns watch_helper.py, which owns the
menu bar / tray icon, the budget drain, and pause enforcement.

Deliberately edge-triggered: it only spawns on a not-playing -> playing
transition, so "Quit Anki Media Timer" from the menu bar is not undone on the
next tick while the same track keeps playing.

Usage:
    python watch_sentinel.py --state /path/to/learn2rot_watch_state.json
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from vendor_paths import resolve_vendor_dir  # noqa: E402

_VENDOR = resolve_vendor_dir(_ROOT)
if _VENDOR is not None:
    _vendor_path = str(_VENDOR)
    if _vendor_path not in sys.path:
        sys.path.insert(0, _vendor_path)

import autostart  # noqa: E402
from media_control import MediaController, create_media_controller  # noqa: E402
from watch_state import (  # noqa: E402
    DEFAULT_SENTINEL_POLL_MS,
    SENTINEL_STATE_FILENAME,
    clamp_sentinel_poll_ms,
    is_on_day,
    is_within_on_hours,
    normalize_hhmm,
    normalize_on_days,
    pid_is_alive,
    read_sentinel_state,
    read_state,
    update_sentinel_state,
)

_HELPER_NAME = "watch_helper.py"
# How long to wait for a freshly spawned helper to publish its pid before we
# assume the spawn failed. Generous: importing rumps/pyobjc is not instant.
_HELPER_CLAIM_TIMEOUT_SEC = 10.0


_log_path: Optional[Path] = None


def set_log_path(path: Optional[Path]) -> None:
    global _log_path
    _log_path = Path(path) if path else None


def _log(message: str) -> None:
    """Record an event.

    There is no console to watch: launchd owns stdout on macOS and pythonw.exe
    discards it on Windows, so the optional ``--log`` file is the only trace a
    user can inspect.
    """
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] sentinel: {message}"
    print(line, flush=True)
    if _log_path is None:
        return
    try:
        with _log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


class SentinelEngine:
    """Watches for playback and keeps a helper alive while it lasts."""

    def __init__(
        self,
        *,
        state_path: Path,
        sentinel_state_path: Path,
        media: Optional[MediaController] = None,
        poll_ms: int = DEFAULT_SENTINEL_POLL_MS,
        spawn_on_start: bool = False,
        oneshot: bool = False,
        on_hours_start: Optional[str] = None,
        on_hours_end: Optional[str] = None,
        on_days: Optional[Sequence[str]] = None,
        now_provider: Callable[[], time.struct_time] = time.localtime,
    ) -> None:
        self._state_path = Path(state_path)
        self._sentinel_state_path = Path(sentinel_state_path)
        self._media = media if media is not None else create_media_controller()
        self._poll_ms = clamp_sentinel_poll_ms(poll_ms)
        # spawn_on_start: open the helper immediately rather than waiting for
        # a playback transition — "open at login" as opposed to "watch for
        # media". oneshot: exit after that one attempt instead of continuing
        # to watch — "don't persist in the background".
        self.spawn_on_start = spawn_on_start
        self.oneshot = oneshot
        # on_hours: a local-time [start, end) window (wraps past midnight
        # when end <= start) outside of which the sentinel keeps watching but
        # won't spawn the helper — invalid/partial input degrades to "no
        # restriction" so a bad config never strands the watcher permanently.
        start = normalize_hhmm(on_hours_start) if on_hours_start is not None else None
        end = normalize_hhmm(on_hours_end) if on_hours_end is not None else None
        self._on_hours: Optional[tuple[str, str]] = (start, end) if start and end else None
        # Only consulted when _on_hours is set — a day restriction with no
        # hour restriction has nothing to attach to.
        self._on_days: Optional[tuple[str, ...]] = (
            tuple(normalize_on_days(list(on_days))) if on_days is not None else None
        )
        self._now_provider = now_provider
        self._child: Optional[subprocess.Popen[bytes]] = None
        self._child_spawned_at = 0.0
        # None means "no baseline yet" — the next observation only establishes
        # one, so we never spawn for playback that was already underway.
        self._was_playing: Optional[bool] = None
        # spawn_on_start only ever gets one opportunity: the first tick.
        self._did_first_tick = False

    @property
    def poll_seconds(self) -> float:
        return self._poll_ms / 1000.0

    def _in_on_hours(self) -> bool:
        if self._on_hours is None:
            return True
        now = self._now_provider()
        if self._on_days is not None and not is_on_day(now.tm_wday, self._on_days):
            return False
        now_minutes = now.tm_hour * 60 + now.tm_min
        start, end = self._on_hours
        return is_within_on_hours(now_minutes, start, end)

    def claim(self) -> None:
        """Take ownership of the sentinel state file."""

        def mutator(state: dict) -> None:
            state["pid"] = os.getpid()
            state["exit"] = False
            state["poll_ms"] = self._poll_ms

        try:
            update_sentinel_state(self._sentinel_state_path, mutator)
        except OSError:
            pass

    def release(self) -> None:
        def mutator(state: dict) -> None:
            if int(state.get("pid", 0) or 0) == os.getpid():
                state["pid"] = 0

        try:
            update_sentinel_state(self._sentinel_state_path, mutator)
        except OSError:
            pass

    def tick(self) -> bool:
        """Run one poll. Returns True when the sentinel should quit."""
        is_first_tick = not self._did_first_tick
        self._did_first_tick = True

        control = read_sentinel_state(self._sentinel_state_path)
        if control.get("exit"):
            _log("exit requested")
            return True
        owner = int(control.get("pid", 0) or 0)
        if owner and owner != os.getpid() and pid_is_alive(owner):
            _log(f"another sentinel owns the state file (pid={owner}); exiting")
            return True
        self._poll_ms = clamp_sentinel_poll_ms(
            control.get("poll_ms", self._poll_ms)
        )

        if not self._state_path.is_file():
            # Anki has never seeded prefs/budget here. Spawning the helper now
            # would lock out all media against a default-zero budget.
            return False

        state = read_state(self._state_path)
        self._reconcile_child(state)
        if self._guardian_present(state):
            # Someone else owns enforcement; drop the baseline so the first
            # tick after they go away only re-observes, never spawns.
            self._was_playing = None
            return False

        info = self._media.get_now_playing()
        if not info.supported:
            _log(f"system media unsupported ({info.error}); exiting")
            return True
        playing = bool(info.is_playing)
        started = playing and self._was_playing is False
        self._was_playing = playing
        if is_first_tick and self.spawn_on_start:
            if self._in_on_hours():
                _log("opening Anki Media Timer at login")
                self._spawn_helper()
            else:
                _log("outside on-hours; not opening at login")
        elif started:
            if self._in_on_hours():
                _log(f"playback started ({info.display_label()}); starting helper")
                self._spawn_helper()
            else:
                _log(
                    f"playback started ({info.display_label()}) outside "
                    "on-hours; not starting helper"
                )
        return False

    def _guardian_present(self, state: dict) -> bool:
        """True when a helper — or the Anki that would spawn one — is alive."""
        if self._child is not None and self._child.poll() is None:
            return True
        helper_pid = int(state.get("pid", 0) or 0)
        if helper_pid and not state.get("exit") and pid_is_alive(helper_pid):
            return True
        if state.get("exit"):
            # The user quit the helper from its own menu. watch_daemon sees
            # this flag and deliberately will NOT respawn it (that's what
            # makes quitting stick) — even while Anki stays open. So once
            # this flag is set, the sentinel is the only thing left that can
            # revive playback, regardless of whether Anki is running.
            return False
        anki_pid = int(state.get("anki_pid", 0) or 0)
        if anki_pid and pid_is_alive(anki_pid):
            # Anki owns the helper lifecycle while it is running (and will
            # reattach it on a crash); racing it would leave two menu bar
            # icons draining the budget twice.
            return True
        return False

    def _reconcile_child(self, state: dict) -> None:
        """Drop a dead child, and stand down if another helper won the race."""
        child = self._child
        if child is None:
            return
        if child.poll() is not None:
            self._child = None
            return
        helper_pid = int(state.get("pid", 0) or 0)
        if helper_pid and helper_pid != child.pid and pid_is_alive(helper_pid):
            _log(f"helper pid={helper_pid} beat us; stopping child {child.pid}")
            self._terminate_child()
            return
        if (
            helper_pid != child.pid
            and time.monotonic() - self._child_spawned_at
            > _HELPER_CLAIM_TIMEOUT_SEC
        ):
            _log(f"child {child.pid} never claimed the state file; stopping it")
            self._terminate_child()

    def _terminate_child(self) -> None:
        child = self._child
        self._child = None
        if child is None or child.poll() is not None:
            return
        try:
            child.terminate()
            child.wait(timeout=2)
        except Exception:
            try:
                child.kill()
            except Exception:
                pass

    def _spawn_helper(self) -> None:
        script = _ROOT / _HELPER_NAME
        if not script.is_file():
            _log(f"helper missing at {script}")
            return
        env = os.environ.copy()
        path_parts = [str(_ROOT)]
        if _VENDOR is not None and _VENDOR.is_dir():
            path_parts.insert(0, str(_VENDOR))
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            os.pathsep.join(path_parts)
            if not existing
            else os.pathsep.join(path_parts) + os.pathsep + existing
        )
        python = autostart.ensure_named_launcher(
            sys.executable,
            name=autostart.HELPER_PROCESS_NAME,
            directory=self._state_path.parent / autostart.LAUNCHERS_DIRNAME,
        )
        popen_kwargs: dict = {
            "args": [python, str(script), "--state", str(self._state_path)],
            "env": env,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
        }
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if creationflags:
                popen_kwargs["creationflags"] = creationflags
        try:
            self._child = subprocess.Popen(**popen_kwargs)
        except OSError as exc:
            self._child = None
            _log(f"failed to start helper: {exc}")
            return
        self._child_spawned_at = time.monotonic()
        _log(f"started helper pid={self._child.pid}")

    def shutdown(self) -> None:
        """Leave the helper running; it is the user-visible half."""
        self._child = None
        self.release()


def _default_sentinel_state_path(state_path: Path) -> Path:
    return Path(state_path).parent / SENTINEL_STATE_FILENAME


def run(engine: SentinelEngine, *, sleep=time.sleep) -> None:
    engine.claim()
    try:
        first = True
        while True:
            try:
                should_quit = engine.tick()
            except Exception as exc:  # keep the login item alive through blips
                _log(f"tick failed: {exc}")
                should_quit = False
            if should_quit:
                return
            if first and engine.oneshot:
                # "Open at login" without "persist in the background": try
                # once, then get out of the way rather than polling forever.
                return
            first = False
            sleep(engine.poll_seconds)
    finally:
        engine.shutdown()


def main(argv: Optional[list[str]] = None) -> int:
    if sys.platform not in ("darwin", "win32"):
        _log("Anki Media Timer only runs on macOS and Windows")
        return 1
    parser = argparse.ArgumentParser(description="Anki Media Timer login sentinel")
    parser.add_argument(
        "--state",
        required=True,
        help="Path to the JSON state file shared with the Anki add-on",
    )
    parser.add_argument(
        "--sentinel-state",
        default=None,
        help="Path to the sentinel's own state file (default: next to --state)",
    )
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=DEFAULT_SENTINEL_POLL_MS,
        help="Milliseconds between playback checks",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Append events to this file (there is no console to watch)",
    )
    parser.add_argument(
        "--spawn-on-start",
        action="store_true",
        help="Open Anki Media Timer immediately instead of waiting for media to start",
    )
    parser.add_argument(
        "--oneshot",
        action="store_true",
        help="Exit after the first attempt instead of continuing to watch for playback",
    )
    parser.add_argument(
        "--on-hours-start",
        default=None,
        help="Only revive playback at/after this local HH:MM (with --on-hours-end)",
    )
    parser.add_argument(
        "--on-hours-end",
        default=None,
        help="Only revive playback before this local HH:MM (with --on-hours-start)",
    )
    parser.add_argument(
        "--on-days",
        default=None,
        help=(
            "Comma-separated weekdays (mon,tue,wed,thu,fri,sat,sun) the "
            "on-hours window applies on; empty string means no day is on"
        ),
    )
    args = parser.parse_args(argv)

    if args.log:
        set_log_path(Path(args.log).expanduser())
    state_path = Path(args.state).expanduser()
    sentinel_state_path = (
        Path(args.sentinel_state).expanduser()
        if args.sentinel_state
        else _default_sentinel_state_path(state_path)
    )
    on_days = tuple(args.on_days.split(",")) if args.on_days is not None else None
    engine = SentinelEngine(
        state_path=state_path,
        sentinel_state_path=sentinel_state_path,
        poll_ms=args.poll_ms,
        spawn_on_start=args.spawn_on_start,
        oneshot=args.oneshot,
        on_hours_start=args.on_hours_start,
        on_hours_end=args.on_hours_end,
        on_days=on_days,
    )
    _log(f"started pid={os.getpid()} poll={engine.poll_seconds:.1f}s state={state_path}")
    run(engine)
    _log("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
