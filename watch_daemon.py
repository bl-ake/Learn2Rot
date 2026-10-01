# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Anki-side controller for the watch daemon (menubar/tray + media lockout).

Spawns watch_helper.py. The helper owns budget drain and Now Playing pause;
this module pushes credits/prefs and polls for UI sync.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from aqt import mw
from aqt.qt import QTimer

from . import autostart
from .config import get_config, is_system_media_mode, write_config
from .logger import log, log_exception
from .watch_state import (
    STATE_FILENAME,
    format_seconds,
    pid_is_alive,
    prefs_from_config,
    read_state,
    terminate_pid,
    update_state,
    write_exit,
)

_addon_module: str = ""
_on_budget_sync: Optional[Callable[[int, bool], None]] = None
_controller: Optional["WatchDaemonController"] = None

_HELPER_NAME = "watch_helper.py"
_POLL_MS = 1000
# cards_due only changes on review/sync/undo (handled explicitly by callers)
# or when time passes a day rollover / a learning card's interval elapses —
# neither needs sub-minute precision, so this rides the existing 1s poll
# timer via a tick counter instead of adding another QTimer.
_CARDS_DUE_CHECK_EVERY_TICKS = 60


def set_addon_module(module: str) -> None:
    global _addon_module
    _addon_module = module
    log(f"watch_daemon: set_addon_module module={module!r}")


def set_budget_sync_callback(callback: Callable[[int, bool], None]) -> None:
    """callback(seconds, is_playing) when daemon state changes."""
    global _on_budget_sync
    _on_budget_sync = callback


def label_for_seconds(seconds: int) -> str:
    return format_seconds(seconds)


def tooltip_for_seconds(seconds: int) -> str:
    return f"Anki Media Timer — time remaining: {format_seconds(seconds)}"


def _supports_watch_daemon() -> bool:
    name = platform.system().lower()
    return name in ("darwin", "windows")


def _state_path() -> Path:
    try:
        folder = mw.pm.profileFolder()
    except Exception:
        folder = str(Path.home())
    return Path(folder) / STATE_FILENAME


def state_path() -> Path:
    """Shared state file path (also used by the login sentinel)."""
    return _state_path()


def _helper_script() -> Path:
    return Path(__file__).resolve().parent / _HELPER_NAME


def _vendor_dir() -> Path | None:
    from .vendor_paths import resolve_vendor_dir

    return resolve_vendor_dir(Path(__file__).resolve().parent)


def _is_packaged_anki_executable(executable: str) -> bool:
    """True when sys.executable is Anki itself (not a Python CLI).

    Briefcase Windows builds ship ``Anki.exe`` as the interpreter host. Passing
    a ``.py`` path to it opens that path as a deck file and starts a second
    Anki instance — which surfaces as "Unsupported file type" plus the
    single-instance warning.
    """
    # Split on both separators so Windows paths still match on Linux CI.
    name = executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name in ("anki", "anki.exe")


def _helper_python() -> Optional[str]:
    """Return a Python executable safe for running watch_helper.py, or None.

    Packaged Anki's ``sys.executable`` is Anki itself (not a Python CLI), so
    spawning ``watch_helper.py`` with it would reopen Anki as a deck. Fall
    back to the same AnkiProgramFiles / PATH discovery the login sentinel
    uses so macOS can still restart the menubar timer after it dies.
    """
    executable = sys.executable
    if executable and not _is_packaged_anki_executable(executable):
        return executable
    return autostart.resolve_launch_python(executable=executable)


def _compute_cards_due() -> Optional[tuple[bool, int]]:
    """(anything due now, unix time of the next predicted due card).

    ``due`` mirrors the deck list's own due counts (respects deck limits,
    filtered decks, etc.) via the same aggregated tree Anki's UI reads from.
    The predicted time is collection-wide, not tied to whichever deck happens
    to be selected: the sooner of the next learning-queue card's absolute due
    time (``queue = 1`` cards carry one directly) and the next day rollover,
    when new-card limits reset and review cards become due — the same cutoff
    ``col.sched.day_cutoff`` exposes. It lets the helper tell a still-valid
    "not due" apart from one that's gone stale once Anki closes and nothing
    is left to recompute it. Returns None if it can't be determined (no
    collection open, backend error) so callers can leave the last-known
    values alone rather than guessing.
    """
    try:
        col = mw.col
        if col is None:
            return None
        tree = col.sched.deck_due_tree()
        due = bool(tree.new_count or tree.learn_count or tree.review_count)
        next_learn = col.db.scalar("select min(due) from cards where queue = 1")
        day_cutoff = int(col.sched.day_cutoff)
        candidates = [c for c in (next_learn, day_cutoff) if c]
        next_due_at = min(candidates) if candidates else 0
        return due, next_due_at
    except Exception:
        log_exception("watch_daemon: cards-due check failed")
        return None


def _should_run_daemon() -> bool:
    """Daemon runs on macOS/Windows for system enforcement and/or tray display."""
    if not _supports_watch_daemon():
        return False
    if not _addon_module:
        return True
    config = get_config(_addon_module)
    if is_system_media_mode(config):
        return True
    return bool(config.get("show_menubar_watch_time", True))


class WatchDaemonController:
    """Keeps the watch helper process running and syncs shared state."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._inproc = False
        self._last_seconds: Optional[int] = None
        self._last_playing: Optional[bool] = None
        self._poll_tick_count = 0
        self._last_cards_due: Optional[bool] = None
        self._last_cards_due_at: Optional[int] = None
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(_POLL_MS)
        self._poll_timer.timeout.connect(self._on_poll)
        log("watch_daemon: controller created")

    def start(
        self,
        *,
        budget_seconds: int,
        force: bool = False,
    ) -> None:
        try:
            self._start(budget_seconds=budget_seconds, force=force)
        except Exception:
            log_exception("watch_daemon: start failed")

    def _start(self, *, budget_seconds: int, force: bool = False) -> None:
        if not _should_run_daemon():
            self.shutdown(quit_helper=True)
            return
        self._push_seed_and_prefs(budget_seconds=budget_seconds, anki_alive=True)
        self._ensure_helper(force=force)
        if not self._poll_timer.isActive():
            self._poll_timer.start()
        self._refresh_cards_due()
        self._on_poll()

    def credit(self, seconds: int) -> None:
        if seconds <= 0 or not _supports_watch_daemon():
            return

        def mutator(state: dict) -> None:
            state["credits"] = int(state.get("credits", 0) or 0) + int(seconds)
            state["anki_alive"] = True

        try:
            update_state(_state_path(), mutator)
            log(f"watch_daemon: credited +{seconds}s")
        except OSError:
            log_exception("watch_daemon: credit failed")

    def subtract(self, seconds: int) -> None:
        if seconds <= 0 or not _supports_watch_daemon():
            return

        def mutator(state: dict) -> None:
            state["subtracts"] = int(state.get("subtracts", 0) or 0) + int(seconds)
            state["anki_alive"] = True

        try:
            update_state(_state_path(), mutator)
            log(f"watch_daemon: subtract -{seconds}s")
        except OSError:
            log_exception("watch_daemon: subtract failed")

    def push_prefs(self, *, budget_seconds: Optional[int] = None) -> None:
        if not _supports_watch_daemon() or not _addon_module:
            return
        seconds = budget_seconds
        if seconds is None:
            try:
                seconds = int(read_state(_state_path()).get("budget_seconds", 0))
            except OSError:
                seconds = 0
        self._push_seed_and_prefs(budget_seconds=int(seconds), anki_alive=True)
        if _should_run_daemon():
            self._ensure_helper(force=True)
            if not self._poll_timer.isActive():
                self._poll_timer.start()
        else:
            self.shutdown(quit_helper=True)

    def read_budget(self) -> tuple[int, bool]:
        try:
            state = read_state(_state_path())
        except OSError:
            return 0, False
        return max(0, int(state.get("budget_seconds", 0) or 0)), bool(
            state.get("is_playing", False)
        )

    def shutdown(self, *, quit_helper: Optional[bool] = None) -> None:
        log(f"watch_daemon: shutdown quit_helper={quit_helper}")
        self._poll_timer.stop()
        if quit_helper is None:
            quit_helper = True
            if _addon_module:
                quit_helper = bool(
                    get_config(_addon_module).get("quit_with_anki", True)
                )
        try:
            self._persist_budget_to_config()
        except Exception:
            log_exception("watch_daemon: persist on shutdown failed")

        def mark_anki_gone(state: dict) -> None:
            state["anki_alive"] = False
            state["anki_pid"] = 0

        try:
            update_state(_state_path(), mark_anki_gone)
        except OSError:
            pass

        if quit_helper:
            self._stop_helper()
        else:
            # Detach: leave helper running; drop our handle.
            self._proc = None
            log("watch_daemon: leaving helper running (quit_with_anki=false)")

        self._last_seconds = None
        self._last_playing = None

    def _persist_budget_to_config(self) -> None:
        if not _addon_module:
            return
        seconds, _ = self.read_budget()
        config = get_config(_addon_module)
        config["budget_seconds"] = max(0, int(seconds))
        write_config(_addon_module, config)
        log(f"watch_daemon: persisted budget_seconds={seconds}")

    def _push_seed_and_prefs(self, *, budget_seconds: int, anki_alive: bool) -> None:
        config = get_config(_addon_module) if _addon_module else {}
        enforce = is_system_media_mode(config) if config else True
        prefs = prefs_from_config(config, enforce=enforce)

        def mutator(state: dict) -> None:
            helper_alive = pid_is_alive(int(state.get("pid", 0) or 0))
            # While the daemon is running it owns budget_seconds; only seed when
            # starting fresh so we don't clobber an in-flight countdown.
            if not helper_alive:
                state["budget_seconds"] = max(0, int(budget_seconds))
            state["prefs"] = prefs
            state["anki_alive"] = anki_alive
            # Lets the login sentinel tell "Anki owns the helper" apart from a
            # stale anki_alive left behind by a crash.
            state["anki_pid"] = os.getpid() if anki_alive else 0
            state["exit"] = False
            state["label"] = format_seconds(int(state.get("budget_seconds", 0) or 0))

        try:
            update_state(_state_path(), mutator)
            log(
                f"watch_daemon: prefs pushed enforce={prefs.get('enforce')} "
                f"quit_with_anki={prefs.get('quit_with_anki')} "
                f"seed={budget_seconds}"
            )
        except OSError:
            log_exception("watch_daemon: failed pushing prefs")

    def _refresh_cards_due(self) -> None:
        """Push cards_due (+ its next-due prediction) when either changes.

        No-op unless require_cards_due is on, so nobody else pays for this
        (no scheduler query, no state write) unless they opted in.
        """
        if not _addon_module:
            return
        config = get_config(_addon_module)
        if not bool(config.get("require_cards_due", False)):
            return
        result = _compute_cards_due()
        if result is None:
            return
        due, due_at = result
        if due == self._last_cards_due and due_at == self._last_cards_due_at:
            return
        self._last_cards_due = due
        self._last_cards_due_at = due_at

        def mutator(state: dict) -> None:
            state["cards_due"] = due
            state["cards_due_at"] = due_at

        try:
            update_state(_state_path(), mutator)
            log(f"watch_daemon: cards_due={due} cards_due_at={due_at}")
        except OSError:
            log_exception("watch_daemon: failed pushing cards_due")

    def _on_poll(self) -> None:
        if not _should_run_daemon():
            return
        self._poll_tick_count += 1
        if self._poll_tick_count % _CARDS_DUE_CHECK_EVERY_TICKS == 1:
            self._refresh_cards_due()
        try:
            state = read_state(_state_path())
        except OSError:
            return
        if state.get("exit"):
            self._proc = None
            return
        seconds = max(0, int(state.get("budget_seconds", 0) or 0))
        playing = bool(state.get("is_playing", False))
        if seconds != self._last_seconds or playing != self._last_playing:
            self._last_seconds = seconds
            self._last_playing = playing
            if _on_budget_sync is not None:
                try:
                    _on_budget_sync(seconds, playing)
                except Exception:
                    log_exception("watch_daemon: budget sync callback failed")
        # Reattach if helper died unexpectedly while we expect it.
        if not self._helper_alive() and not self._external_helper_alive(
            int(state.get("pid", 0) or 0)
        ):
            self._ensure_helper(force=False)

    def _proc_alive(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        return False

    def _inproc_alive(self) -> bool:
        if not self._inproc:
            return False
        try:
            from .watch_helper import windows_helper_inprocess_alive

            return windows_helper_inprocess_alive()
        except Exception:
            return False

    def _helper_alive(self) -> bool:
        return self._proc_alive() or self._inproc_alive()

    def _external_helper_alive(self, pid: int) -> bool:
        """True if a *separate* helper process owns ``pid``."""
        if pid <= 0 or not pid_is_alive(pid):
            return False
        # In-process Windows helper publishes Anki's own PID; that must not
        # count as an external helper still running.
        if pid == os.getpid():
            return self._inproc_alive()
        return True

    def _ensure_helper(self, *, force: bool = False) -> None:
        state = read_state(_state_path())
        existing_pid = int(state.get("pid", 0) or 0)
        if self._external_helper_alive(existing_pid):
            if existing_pid == os.getpid():
                log(f"watch_daemon: in-process helper already running pid={existing_pid}")
            else:
                log(f"watch_daemon: reattached to helper pid={existing_pid}")
                self._proc = None
                self._inproc = False
            return
        if self._helper_alive():
            return
        script = _helper_script()
        if not script.is_file():
            log(f"watch_daemon: helper missing at {script}")
            return
        state_path = _state_path()
        python = _helper_python()
        if python is None:
            self._start_helper_inprocess(state_path=state_path, force=force)
            return
        vendor = _vendor_dir()
        env = os.environ.copy()
        # Helper imports sibling modules + vendor packages.
        path_parts = [str(_helper_script().parent)]
        if vendor is not None and vendor.is_dir():
            path_parts.insert(0, str(vendor))
        env["PYTHONPATH"] = (
            os.pathsep.join(path_parts)
            if not env.get("PYTHONPATH")
            else os.pathsep.join(path_parts) + os.pathsep + env["PYTHONPATH"]
        )
        named_python = autostart.ensure_named_launcher(
            python,
            name=autostart.HELPER_PROCESS_NAME,
            directory=state_path.parent / autostart.LAUNCHERS_DIRNAME,
        )
        log_file = None
        popen_kwargs: dict = {
            "args": [named_python, str(script), "--state", str(state_path)],
            "env": env,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
        }
        if sys.platform == "win32":
            # Avoid a flashing console window for the tray helper.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if creationflags:
                popen_kwargs["creationflags"] = creationflags
        if _addon_module and bool(
            get_config(_addon_module).get("debug_logging", False)
        ):
            try:
                helper_log = state_path.parent / "learn2rot_watch_helper.log"
                log_file = helper_log.open("a", encoding="utf-8")
                popen_kwargs["stdout"] = log_file
                popen_kwargs["stderr"] = subprocess.STDOUT
            except OSError:
                log_exception("watch_daemon: could not open helper log")
        try:
            self._proc = subprocess.Popen(**popen_kwargs)
            self._inproc = False
        except OSError:
            log_exception("watch_daemon: failed to start helper")
            self._proc = None
            if log_file is not None:
                log_file.close()
            if sys.platform == "win32":
                self._start_helper_inprocess(state_path=state_path, force=force)
            return
        if log_file is not None:
            log_file.close()
        log(
            f"watch_daemon: started helper pid={self._proc.pid} "
            f"python={python!r} state={str(state_path)!r} force={force}"
        )

    def _start_helper_inprocess(self, *, state_path: Path, force: bool) -> None:
        if sys.platform != "win32":
            log(
                "watch_daemon: packaged Anki has no Python CLI; "
                "cannot spawn watch_helper subprocess on this platform"
            )
            return
        try:
            from .watch_helper import start_windows_helper_inprocess

            start_windows_helper_inprocess(state_path)
            self._proc = None
            self._inproc = True
            log(
                "watch_daemon: started in-process Windows helper "
                f"(Anki executable is not a Python CLI) state={str(state_path)!r} "
                f"force={force}"
            )
        except Exception:
            log_exception("watch_daemon: failed to start in-process helper")
            self._inproc = False

    def _stop_helper(self) -> None:
        state_path = _state_path()
        write_exit(state_path)
        proc = self._proc
        inproc = self._inproc
        self._proc = None
        self._inproc = False
        if inproc:
            try:
                from .watch_helper import stop_windows_helper_inprocess

                stop_windows_helper_inprocess(state_path)
                log("watch_daemon: stopped in-process helper")
            except Exception:
                log_exception("watch_daemon: failed stopping in-process helper")
            return
        # Also signal any reattached orphan via exit file (already written).
        state = read_state(state_path)
        pid = int(state.get("pid", 0) or 0)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            log(f"watch_daemon: stopped helper pid={proc.pid}")
        elif pid > 0 and pid != os.getpid() and pid_is_alive(pid):
            terminate_pid(pid)
            log(f"watch_daemon: signaled helper pid={pid}")


def _get_controller() -> WatchDaemonController:
    global _controller
    if _controller is None:
        _controller = WatchDaemonController()
    return _controller


def start_watch_daemon(*, budget_seconds: int, force: bool = False) -> None:
    if not _supports_watch_daemon():
        return
    try:
        _get_controller().start(budget_seconds=budget_seconds, force=force)
    except Exception:
        log_exception("watch_daemon: start_watch_daemon failed")


def refresh_cards_due() -> None:
    """Recheck cards_due now instead of waiting for the next poll tick.

    Called after events that can change what's due (answering a card,
    undoing one, or syncing in reviews from another device) so the icon
    hides/reappears right away rather than up to a minute late. A no-op if
    the daemon was never started — nothing to push into yet.
    """
    if not _supports_watch_daemon() or _controller is None:
        return
    try:
        _controller._refresh_cards_due()
    except Exception:
        log_exception("watch_daemon: refresh_cards_due failed")


def credit_watch_time(seconds: int) -> None:
    if not _supports_watch_daemon():
        return
    try:
        _get_controller().credit(seconds)
    except Exception:
        log_exception("watch_daemon: credit_watch_time failed")


def subtract_watch_time(seconds: int) -> None:
    if not _supports_watch_daemon():
        return
    try:
        _get_controller().subtract(seconds)
    except Exception:
        log_exception("watch_daemon: subtract_watch_time failed")


def publish_budget(seconds: int) -> None:
    """Push absolute budget for display when Anki owns the clock (YouTube mode)."""
    if not _supports_watch_daemon():
        return

    def mutator(state: dict) -> None:
        prefs = state.get("prefs") or {}
        if prefs.get("enforce", True):
            return
        state["budget_seconds"] = max(0, int(seconds))
        state["label"] = format_seconds(int(state["budget_seconds"]))
        state["anki_alive"] = True

    try:
        update_state(_state_path(), mutator)
    except OSError:
        log_exception("watch_daemon: publish_budget failed")


def refresh_watch_daemon(*, budget_seconds: Optional[int] = None) -> None:
    if not _supports_watch_daemon():
        return
    try:
        _get_controller().push_prefs(budget_seconds=budget_seconds)
    except Exception:
        log_exception("watch_daemon: refresh_watch_daemon failed")


def shutdown_watch_daemon(*, quit_helper: Optional[bool] = None) -> None:
    global _controller
    if _controller is not None:
        _controller.shutdown(quit_helper=quit_helper)
        _controller = None
        log("watch_daemon: controller cleared")
