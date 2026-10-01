# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Anki-side control of the login media sentinel.

Two independent preferences, both default off:

- ``open_timer_at_login``: open Anki Media Timer itself the moment you log
  in, instead of waiting for media to start.
- ``sentinel_at_login``: keep a background watcher running so the timer
  revives whenever media starts later, even if Anki is never opened. This is
  the "persistent" half — the module-level name predates the split.

Either one, on its own, needs a login item; this module keeps that item, and
the ``watch_sentinel.py`` process it launches, in agreement with both
preferences. Setting only ``open_timer_at_login`` produces a one-shot login
item (open once, then exit — see ``oneshot`` on SentinelEngine); setting
``sentinel_at_login`` keeps it running indefinitely.

A third preference, ``sentinel_on_hours_enabled`` (with
``sentinel_on_hours_start`` / ``sentinel_on_hours_end`` / ``sentinel_on_days``),
restricts *when* the persistent watcher is allowed to revive the timer.
Outside that window (or on an unchecked day) it still watches, but won't
spawn the helper — so quitting the timer outside on-hours stays quit instead
of coming back the moment media resumes.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import autostart, watch_daemon
from .config import get_config
from .logger import log, log_exception
from .watch_state import (
    SENTINEL_STATE_FILENAME,
    clamp_sentinel_poll_ms,
    pid_is_alive,
    read_sentinel_state,
    request_sentinel_exit,
    update_sentinel_state,
)

_SENTINEL_NAME = "watch_sentinel.py"
_SENTINEL_LOG_NAME = "learn2rot_sentinel.log"

_cached_python: Optional[str] = None
_python_lookup_done = False


@dataclass
class SyncResult:
    """Outcome of reconciling config with the OS, for surfacing in the UI."""

    enabled: bool
    installed: bool
    running: bool
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def supported() -> bool:
    return autostart.supports_autostart()


def sentinel_script() -> Path:
    return Path(__file__).resolve().parent / _SENTINEL_NAME


def state_path() -> Path:
    return watch_daemon.state_path()


def sentinel_state_path() -> Path:
    return state_path().parent / SENTINEL_STATE_FILENAME


def sentinel_log_path() -> Path:
    return state_path().parent / _SENTINEL_LOG_NAME


def _poll_ms(addon_module: str) -> int:
    return clamp_sentinel_poll_ms(get_config(addon_module).get("sentinel_poll_ms", 3000))


def _on_hours(addon_module: str) -> Optional[tuple[str, str]]:
    """(start, end) HH:MM to restrict background revival to, or None.

    Only meaningful for the persistent watcher (``sentinel_at_login``): a
    one-shot "open at login" item has nothing left behind to revive playback
    with, so there is nothing for hours to restrict.
    """
    config = get_config(addon_module)
    if not bool(config.get("sentinel_at_login", False)):
        return None
    if not bool(config.get("sentinel_on_hours_enabled", False)):
        return None
    return (
        str(config.get("sentinel_on_hours_start", "09:00")),
        str(config.get("sentinel_on_hours_end", "21:00")),
    )


def _on_days(addon_module: str) -> Optional[tuple[str, ...]]:
    """Weekday abbreviations (``get_config`` already normalized them) the
    on-hours window applies on, or None when the window itself is off."""
    config = get_config(addon_module)
    if not bool(config.get("sentinel_at_login", False)):
        return None
    if not bool(config.get("sentinel_on_hours_enabled", False)):
        return None
    return tuple(config.get("sentinel_on_days") or ())


def _launch_python() -> Optional[str]:
    """Resolve (once per session) an interpreter able to host the sentinel."""
    global _cached_python, _python_lookup_done
    if not _python_lookup_done:
        _python_lookup_done = True
        resolved = autostart.resolve_launch_python()
        if resolved is not None:
            resolved = autostart.ensure_named_launcher(
                resolved,
                name=autostart.SENTINEL_PROCESS_NAME,
                directory=state_path().parent / autostart.LAUNCHERS_DIRNAME,
            )
        _cached_python = resolved
        log(f"sentinel: resolved launch python={_cached_python!r}")
    return _cached_python


def _command(
    addon_module: str, python: str, *, spawn_on_start: bool = False, oneshot: bool = False
) -> list[str]:
    return autostart.sentinel_command(
        python=python,
        script=sentinel_script(),
        state_path=state_path(),
        sentinel_state_path=sentinel_state_path(),
        poll_ms=_poll_ms(addon_module),
        log_path=sentinel_log_path(),
        spawn_on_start=spawn_on_start,
        oneshot=oneshot,
        on_hours=_on_hours(addon_module),
        on_days=_on_days(addon_module),
    )


def is_running() -> bool:
    state = read_sentinel_state(sentinel_state_path())
    pid = int(state.get("pid", 0) or 0)
    return bool(pid) and not state.get("exit") and pid_is_alive(pid)


def _await_running(*, timeout: float = 2.0, interval: float = 0.25) -> bool:
    """Wait briefly for a just-installed login item to claim the state file."""
    deadline = time.monotonic() + timeout
    while True:
        if is_running():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _await_stopped(*, timeout: float = 2.0, interval: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if not is_running():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def start_now(
    addon_module: str, *, spawn_on_start: bool = False, oneshot: bool = False
) -> bool:
    """Launch the sentinel immediately so enabling does not require a reboot."""
    if not supported():
        return False
    if is_running():
        if not spawn_on_start:
            return True
        # A stale already-running watcher was started without spawn_on_start
        # (or with different settings); restart it so "open at login" takes
        # effect on this save instead of only at next login.
        stop_now()
        _await_stopped()
    python = _launch_python()
    if python is None:
        return False
    script = sentinel_script()
    if not script.is_file():
        log(f"sentinel: script missing at {script}")
        return False

    # A stale exit flag from a previous disable would stop the new process on
    # its first tick.
    try:
        update_sentinel_state(
            sentinel_state_path(), lambda state: state.update({"exit": False})
        )
    except OSError:
        log_exception("sentinel: could not clear exit flag")

    popen_kwargs: dict = {
        "args": _command(
            addon_module, python, spawn_on_start=spawn_on_start, oneshot=oneshot
        ),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "start_new_session": True,
    }
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if creationflags:
            popen_kwargs["creationflags"] = creationflags
    try:
        proc = subprocess.Popen(**popen_kwargs)
    except OSError:
        log_exception("sentinel: failed to start")
        return False
    log(f"sentinel: started pid={proc.pid} python={python!r}")
    return True


def stop_now() -> None:
    """Ask a running sentinel to quit (it may not be a child of this process)."""
    try:
        request_sentinel_exit(sentinel_state_path())
    except OSError:
        log_exception("sentinel: could not request exit")
        return
    log("sentinel: exit requested")


def _preferences(addon_module: str) -> tuple[bool, bool]:
    """Return (open_at_login, persist_in_background)."""
    config = get_config(addon_module)
    return (
        bool(config.get("open_timer_at_login", False)),
        bool(config.get("sentinel_at_login", False)),
    )


def sync(addon_module: str) -> SyncResult:
    """Reconcile the login item and running process with the preferences."""
    open_at_login, persist = _preferences(addon_module)
    enabled = open_at_login or persist
    if not supported():
        return SyncResult(
            enabled=enabled,
            installed=False,
            running=False,
            error=(
                "Starting at login is only available on macOS and Windows."
                if enabled
                else ""
            ),
        )

    if not enabled:
        if not autostart.is_installed() and not is_running():
            # Nothing registered and nothing running — stay off the disk (and
            # out of the interpreter probe) on every profile open.
            return SyncResult(enabled=False, installed=False, running=False)
        stop_now()
        removed = autostart.uninstall()
        log(f"sentinel: disabled (login item removed={removed})")
        return SyncResult(
            enabled=False,
            installed=autostart.is_installed(),
            running=False,
            error="" if removed else "Could not remove the login item.",
        )

    python = _launch_python()
    if python is None:
        return SyncResult(
            enabled=True,
            installed=False,
            running=False,
            error=(
                "Could not find a Python "
                f"{sys.version_info[0]}.{sys.version_info[1]} interpreter to run "
                "the background watcher at login."
            ),
        )
    script = sentinel_script()
    if not script.is_file():
        return SyncResult(
            enabled=True,
            installed=False,
            running=False,
            error=f"Missing {script.name} in the add-on folder.",
        )

    oneshot = not persist
    argv = _command(addon_module, python, spawn_on_start=open_at_login, oneshot=oneshot)
    installed = autostart.install(argv)

    if persist:
        # launchd starts a RunAtLoad agent itself; give it a moment to claim
        # the state file rather than racing it with a second process.
        running = (
            _await_running()
            if installed and autostart.starts_immediately()
            else False
        )
        if not running:
            running = start_now(addon_module, spawn_on_start=open_at_login)
    else:
        # One-shot (open-only, no persistence): nothing stays alive to poll
        # for. launchd already ran RunAtLoad; a Windows Run key only fires at
        # next sign-in, so run it once now too.
        running = bool(installed and autostart.starts_immediately())
        if installed and not autostart.starts_immediately():
            running = start_now(
                addon_module, spawn_on_start=open_at_login, oneshot=True
            )
    log(f"sentinel: enabled (login item installed={installed} running={running})")
    error = ""
    if not installed:
        error = "Could not register the background watcher to start at login."
    elif not running:
        error = "Registered for login, but the watcher could not be started now."
    return SyncResult(
        enabled=True, installed=installed, running=running, error=error
    )


def describe_status(addon_module: str) -> str:
    """Human-readable state for the settings dialog."""
    if not supported():
        return "Not available on this platform."
    open_at_login, persist = _preferences(addon_module)
    if not open_at_login and not persist:
        return "Off — media is only metered while Anki (or the timer) is open."
    parts = [
        "registered for login" if autostart.is_installed() else "not registered"
    ]
    if persist:
        parts.append("watching now" if is_running() else "not currently watching")
    else:
        parts.append("opens once at login, then exits")
    return f"On — {', '.join(parts)}."
