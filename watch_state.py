# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared JSON state protocol for the macOS watch daemon.

Anki writes credits/subtracts/prefs/anki_alive; the daemon owns budget_seconds
while running and applies credits atomically under a file lock.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix
    fcntl = None  # type: ignore[assignment]

STATE_FILENAME = "ankitube_watch_state.json"
EXIT_SENTINEL = "__EXIT__"

DEFAULT_PREFS: dict[str, Any] = {
    "system_media_poll_ms": 500,
    "auto_resume_on_budget": False,
    "show_menubar_watch_time": True,
    "max_budget_seconds": 600,
    "quit_with_anki": True,
    "enforce": True,
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
        "anki_alive": False,
        "exit": False,
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
    return max(0, min(int(value), max(1, int(max_budget))))


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
        max_budget = 600
    prefs["max_budget_seconds"] = max(1, max_budget)
    prefs["quit_with_anki"] = bool(raw.get("quit_with_anki", prefs["quit_with_anki"]))
    prefs["enforce"] = bool(raw.get("enforce", prefs["enforce"]))
    return prefs


def normalize_state(raw: Any) -> dict[str, Any]:
    state = default_state()
    if raw == EXIT_SENTINEL or (
        isinstance(raw, dict) and raw.get("exit") is True
    ):
        state["exit"] = True
        return state
    if not isinstance(raw, dict):
        return state
    prefs = normalize_prefs(raw.get("prefs"))
    state["prefs"] = prefs
    try:
        budget = int(raw.get("budget_seconds", 0))
    except (TypeError, ValueError):
        budget = 0
    state["budget_seconds"] = _clamp_budget(budget, prefs["max_budget_seconds"])
    for key in ("credits", "subtracts", "pid"):
        try:
            state[key] = max(0, int(raw.get(key, 0) or 0))
        except (TypeError, ValueError):
            state[key] = 0
    state["label"] = str(raw.get("label") or format_seconds(state["budget_seconds"]))
    state["is_playing"] = bool(raw.get("is_playing", False))
    state["paused_for_budget"] = bool(raw.get("paused_for_budget", False))
    state["anki_alive"] = bool(raw.get("anki_alive", False))
    state["exit"] = bool(raw.get("exit", False))
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
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = normalize_state(state)
    text = json.dumps(payload, ensure_ascii=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".ankitube_watch_", suffix=".tmp"
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
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXIT_SENTINEL + "\n", encoding="utf-8")


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


def update_state(
    path: Path,
    mutator: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Read-modify-write the state file under an exclusive lock when possible."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        write_state(path, default_state())

    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_handle: Optional[Any] = None
    try:
        lock_handle = open(lock_path, "a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        state = read_state(path)
        mutator(state)
        state = normalize_state(state)
        write_state(path, state)
        return state
    finally:
        if lock_handle is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            lock_handle.close()


def prefs_from_config(config: dict[str, Any], *, enforce: bool) -> dict[str, Any]:
    return normalize_prefs(
        {
            "system_media_poll_ms": config.get("system_media_poll_ms", 500),
            "auto_resume_on_budget": config.get("auto_resume_on_budget", False),
            "show_menubar_watch_time": config.get("show_menubar_watch_time", True),
            "max_budget_seconds": config.get("max_budget_seconds", 600),
            "quit_with_anki": config.get("quit_with_anki", True),
            "enforce": enforce,
        }
    )


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
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
