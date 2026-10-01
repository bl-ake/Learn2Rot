# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared JSON state protocol for the watch daemon.

Anki writes credits/subtracts/prefs/anki_alive; the daemon owns budget_seconds
while running and applies credits atomically under a file lock.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows
    msvcrt = None  # type: ignore[assignment]

STATE_FILENAME = "learn2rot_watch_state.json"
SENTINEL_STATE_FILENAME = "learn2rot_sentinel_state.json"
EXIT_SENTINEL = "__EXIT__"

DEFAULT_SENTINEL_POLL_MS = 3000
MIN_SENTINEL_POLL_MS = 1000
MAX_SENTINEL_POLL_MS = 60000

# Index order matches time.struct_time.tm_wday / datetime.weekday(): Mon=0..Sun=6.
WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

DEFAULT_PREFS: dict[str, Any] = {
    "system_media_poll_ms": 500,
    "auto_resume_on_budget": False,
    "show_menubar_watch_time": True,
    "max_budget_seconds": 0,
    "quit_with_anki": True,
    "enforce": True,
    "require_cards_due": False,
    # Same on-hours window the login sentinel uses to gate revival — also
    # applied by the running helper itself (see watch_helper.MediaTimerEngine)
    # so a helper that's already running backs off once the window ends
    # instead of only affecting whether a *new* helper gets spawned.
    "on_hours_enabled": False,
    "on_hours_start": "09:00",
    "on_hours_end": "21:00",
    "on_days": list(WEEKDAYS),
}


def default_state() -> dict[str, Any]:
    return {
        "budget_seconds": 0,
        "credits": 0,
        "subtracts": 0,
        "label": "0:00",
        "is_playing": False,
        "paused_for_budget": False,
        "pid": 0,
        "anki_pid": 0,
        "anki_alive": False,
        "exit": False,
        # Only Anki can compute this (needs collection access); fail open so
        # a helper that has never heard from Anki yet still enforces normally
        # instead of silently going quiet.
        "cards_due": True,
        # Unix time Anki last predicted the next card becomes due (day
        # rollover or a learning-queue card), so the helper can tell a
        # still-valid False apart from one that's gone stale while Anki is
        # closed. 0 means no prediction on record.
        "cards_due_at": 0,
        "prefs": dict(DEFAULT_PREFS),
    }


def format_seconds(total_seconds: int) -> str:
    seconds = max(0, int(total_seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _clamp_budget(value: int, max_budget: int) -> int:
    """Clamp budget to [0, max_budget]. max_budget <= 0 means unlimited."""
    value = max(0, int(value))
    max_budget = int(max_budget)
    if max_budget <= 0:
        return value
    return min(value, max_budget)


def normalize_prefs(raw: Any) -> dict[str, Any]:
    prefs = dict(DEFAULT_PREFS)
    if not isinstance(raw, dict):
        return prefs
    try:
        poll_ms = int(raw.get("system_media_poll_ms", prefs["system_media_poll_ms"]))
    except (TypeError, ValueError):
        poll_ms = 500
    prefs["system_media_poll_ms"] = max(200, min(5000, poll_ms))
    prefs["auto_resume_on_budget"] = bool(
        raw.get("auto_resume_on_budget", prefs["auto_resume_on_budget"])
    )
    prefs["show_menubar_watch_time"] = bool(
        raw.get("show_menubar_watch_time", prefs["show_menubar_watch_time"])
    )
    try:
        max_budget = int(raw.get("max_budget_seconds", prefs["max_budget_seconds"]))
    except (TypeError, ValueError):
        max_budget = 0
    prefs["max_budget_seconds"] = max(0, max_budget)
    prefs["quit_with_anki"] = bool(raw.get("quit_with_anki", prefs["quit_with_anki"]))
    prefs["enforce"] = bool(raw.get("enforce", prefs["enforce"]))
    prefs["require_cards_due"] = bool(
        raw.get("require_cards_due", prefs["require_cards_due"])
    )
    prefs["on_hours_enabled"] = bool(
        raw.get("on_hours_enabled", prefs["on_hours_enabled"])
    )
    prefs["on_hours_start"] = (
        normalize_hhmm(raw.get("on_hours_start", prefs["on_hours_start"]))
        or prefs["on_hours_start"]
    )
    prefs["on_hours_end"] = (
        normalize_hhmm(raw.get("on_hours_end", prefs["on_hours_end"]))
        or prefs["on_hours_end"]
    )
    prefs["on_days"] = normalize_on_days(raw.get("on_days", prefs["on_days"]))
    return prefs


def normalize_state(raw: Any) -> dict[str, Any]:
    state = default_state()
    if raw == EXIT_SENTINEL:
        # Legacy corrupted files: no other data to preserve.
        state["exit"] = True
        return state
    if not isinstance(raw, dict):
        return state
    # A dict with exit=True still carries a real budget_seconds/prefs/pid —
    # e.g. write_exit()'s read-modify-write — so it falls through to the
    # normal per-field parsing below instead of being collapsed to defaults.
    prefs = normalize_prefs(raw.get("prefs"))
    state["prefs"] = prefs
    try:
        budget = int(raw.get("budget_seconds", 0))
    except (TypeError, ValueError):
        budget = 0
    state["budget_seconds"] = _clamp_budget(budget, prefs["max_budget_seconds"])
    for key in ("credits", "subtracts", "pid", "anki_pid"):
        try:
            state[key] = max(0, int(raw.get(key, 0) or 0))
        except (TypeError, ValueError):
            state[key] = 0
    state["label"] = str(raw.get("label") or format_seconds(state["budget_seconds"]))
    state["is_playing"] = bool(raw.get("is_playing", False))
    state["paused_for_budget"] = bool(raw.get("paused_for_budget", False))
    state["anki_alive"] = bool(raw.get("anki_alive", False))
    state["exit"] = bool(raw.get("exit", False))
    state["cards_due"] = bool(raw.get("cards_due", True))
    try:
        state["cards_due_at"] = max(0, int(raw.get("cards_due_at", 0) or 0))
    except (TypeError, ValueError):
        state["cards_due_at"] = 0
    return state


def read_state(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return default_state()
    if not text:
        return default_state()
    if text == EXIT_SENTINEL:
        state = default_state()
        state["exit"] = True
        return state
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return default_state()
    return normalize_state(data)


def write_state(path: Path, state: dict[str, Any]) -> None:
    _write_json_atomic(Path(path), normalize_state(state))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".learn2rot_watch_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_exit(path: Path) -> None:
    """Flag the state as exited without discarding budget_seconds/prefs.

    Historically this overwrote the whole file with the bare ``__EXIT__``
    string, which read_state() turns into a fresh default_state() — silently
    zeroing earned watch time. While Anki is open that was invisible: its own
    poll loop skips syncing while exit is set, and reopening Anki reseeds the
    file from its own (untouched) config. A sentinel-revived helper has no
    Anki around to do that reseed, so it read the wiped budget as 0. Reading
    old ``__EXIT__`` files is still supported below for compatibility.
    """

    def mutator(state: dict[str, Any]) -> None:
        state["exit"] = True

    update_state(Path(path), mutator)


def apply_pending_adjustments(state: dict[str, Any]) -> dict[str, Any]:
    """Apply credits/subtracts to budget_seconds and clear the queues."""
    state = normalize_state(state)
    prefs = state["prefs"]
    max_budget = prefs["max_budget_seconds"]
    credits = int(state.get("credits", 0) or 0)
    subtracts = int(state.get("subtracts", 0) or 0)
    budget = int(state.get("budget_seconds", 0) or 0)
    if credits:
        budget = _clamp_budget(budget + credits, max_budget)
    if subtracts:
        budget = max(0, budget - subtracts)
    state["budget_seconds"] = _clamp_budget(budget, max_budget)
    state["credits"] = 0
    state["subtracts"] = 0
    state["label"] = format_seconds(state["budget_seconds"])
    return state


def drain_one_second(budget_seconds: int) -> tuple[int, bool]:
    """Consume one second. Returns (remaining, still_has_time)."""
    if budget_seconds <= 0:
        return 0, False
    remaining = budget_seconds - 1
    return remaining, remaining > 0


def _lock_acquire(lock_handle: Any) -> None:
    if fcntl is not None:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        return
    if msvcrt is not None:
        # Ensure the lock file has at least one byte to lock.
        lock_handle.seek(0)
        if lock_handle.read(1) == "":
            lock_handle.write("0")
            lock_handle.flush()
        lock_handle.seek(0)
        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)


def _lock_release(lock_handle: Any) -> None:
    if fcntl is not None:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:
        try:
            lock_handle.seek(0)
            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


def _update_locked(
    path: Path,
    mutator: Callable[[dict[str, Any]], None],
    *,
    reader: Callable[[Path], dict[str, Any]],
    writer: Callable[[Path, dict[str, Any]], None],
    seed: Callable[[], dict[str, Any]],
    normalizer: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """Read-modify-write a state file under an exclusive lock when possible."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        writer(path, seed())

    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_handle: Optional[Any] = None
    try:
        lock_handle = open(lock_path, "a+", encoding="utf-8")
        _lock_acquire(lock_handle)
        state = reader(path)
        mutator(state)
        state = normalizer(state)
        writer(path, state)
        return state
    finally:
        if lock_handle is not None:
            try:
                _lock_release(lock_handle)
            except OSError:
                pass
            lock_handle.close()


def update_state(
    path: Path,
    mutator: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Read-modify-write the watch state file under an exclusive lock."""
    return _update_locked(
        path,
        mutator,
        reader=read_state,
        writer=write_state,
        seed=default_state,
        normalizer=normalize_state,
    )


def default_sentinel_state() -> dict[str, Any]:
    """State owned by the login sentinel (separate file: watch state gets
    replaced wholesale by the ``__EXIT__`` sentinel, which would eat these)."""
    return {
        "pid": 0,
        "exit": False,
        "poll_ms": DEFAULT_SENTINEL_POLL_MS,
    }


def normalize_sentinel_state(raw: Any) -> dict[str, Any]:
    state = default_sentinel_state()
    if not isinstance(raw, dict):
        return state
    try:
        state["pid"] = max(0, int(raw.get("pid", 0) or 0))
    except (TypeError, ValueError):
        state["pid"] = 0
    try:
        poll_ms = int(raw.get("poll_ms", DEFAULT_SENTINEL_POLL_MS))
    except (TypeError, ValueError):
        poll_ms = DEFAULT_SENTINEL_POLL_MS
    state["poll_ms"] = clamp_sentinel_poll_ms(poll_ms)
    state["exit"] = bool(raw.get("exit", False))
    return state


def clamp_sentinel_poll_ms(value: Any) -> int:
    try:
        poll_ms = int(value)
    except (TypeError, ValueError):
        poll_ms = DEFAULT_SENTINEL_POLL_MS
    return max(MIN_SENTINEL_POLL_MS, min(MAX_SENTINEL_POLL_MS, poll_ms))


_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def normalize_hhmm(value: Any) -> Optional[str]:
    """Return a zero-padded 24h "HH:MM" string, or None if value isn't one."""
    text = str(value).strip() if value is not None else ""
    match = _HHMM_RE.match(text)
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def hhmm_to_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def is_within_on_hours(now_minutes: int, start: str, end: str) -> bool:
    """Whether ``now_minutes`` (0-1439) falls in the [start, end) window.

    ``end <= start`` means the window spans midnight (e.g. 22:00-06:00). An
    equal start/end is a degenerate "always on" window rather than zero-width
    — nobody setting on-hours means to lock themselves out entirely.
    """
    start_m = hhmm_to_minutes(start)
    end_m = hhmm_to_minutes(end)
    if start_m == end_m:
        return True
    if start_m < end_m:
        return start_m <= now_minutes < end_m
    return now_minutes >= start_m or now_minutes < end_m


def normalize_on_days(raw: Any) -> list[str]:
    """Validate a day-of-week selection down to WEEKDAYS abbreviations.

    Anything that isn't a list/tuple (missing key, wrong type) defaults to
    every day — "no restriction", the same fallback an unset on-hours window
    gets. An explicit empty list is preserved as "no days selected" rather
    than treated as invalid, since a user may deliberately want the schedule
    to never trigger while leaving the watcher itself running.
    """
    if not isinstance(raw, (list, tuple)):
        return list(WEEKDAYS)
    seen: list[str] = []
    for item in raw:
        name = str(item).strip().lower()
        if name in WEEKDAYS and name not in seen:
            seen.append(name)
    return sorted(seen, key=WEEKDAYS.index)


def is_on_day(weekday: int, on_days: Sequence[str]) -> bool:
    """``weekday`` follows tm_wday/``date.weekday()``: Mon=0..Sun=6."""
    if not (0 <= weekday <= 6):
        return True
    return WEEKDAYS[weekday] in on_days


def read_sentinel_state(path: Path) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return default_sentinel_state()
    if not text:
        return default_sentinel_state()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return default_sentinel_state()
    return normalize_sentinel_state(data)


def write_sentinel_state(path: Path, state: dict[str, Any]) -> None:
    _write_json_atomic(Path(path), normalize_sentinel_state(state))


def update_sentinel_state(
    path: Path,
    mutator: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Read-modify-write the sentinel state file under an exclusive lock."""
    return _update_locked(
        path,
        mutator,
        reader=read_sentinel_state,
        writer=write_sentinel_state,
        seed=default_sentinel_state,
        normalizer=normalize_sentinel_state,
    )


def request_sentinel_exit(path: Path) -> None:
    """Ask a running sentinel to quit on its next tick."""

    def mutator(state: dict[str, Any]) -> None:
        state["exit"] = True

    update_sentinel_state(path, mutator)


def prefs_from_config(config: dict[str, Any], *, enforce: bool) -> dict[str, Any]:
    return normalize_prefs(
        {
            "system_media_poll_ms": config.get("system_media_poll_ms", 500),
            "auto_resume_on_budget": config.get("auto_resume_on_budget", False),
            "show_menubar_watch_time": config.get("show_menubar_watch_time", True),
            "max_budget_seconds": config.get("max_budget_seconds", 0),
            "quit_with_anki": config.get("quit_with_anki", True),
            "enforce": enforce,
            "require_cards_due": config.get("require_cards_due", False),
            "on_hours_enabled": config.get("sentinel_on_hours_enabled", False),
            "on_hours_start": config.get("sentinel_on_hours_start", "09:00"),
            "on_hours_end": config.get("sentinel_on_hours_end", "21:00"),
            "on_days": config.get("sentinel_on_days", list(WEEKDAYS)),
        }
    )


def _pid_is_alive_windows(pid: int) -> bool:
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
            handle, ctypes.byref(exit_code)
        )
        if not ok:
            return False
        return int(exit_code.value) == STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            return _pid_is_alive_windows(pid)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def terminate_pid(pid: int) -> None:
    """Best-effort terminate for a helper process we do not own via Popen."""
    if pid <= 0:
        return
    if sys.platform == "win32":
        import ctypes

        PROCESS_TERMINATE = 0x0001
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            PROCESS_TERMINATE, False, int(pid)
        )
        if not handle:
            return
        try:
            ctypes.windll.kernel32.TerminateProcess(handle, 1)  # type: ignore[attr-defined]
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass
