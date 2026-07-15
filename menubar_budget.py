# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Optional macOS menu bar item showing remaining watch time.

Spawns a standalone rumps helper process (see menubar_helper.py). Creating an
NSStatusItem inside Anki's Qt process leaves the button occluded on modern
macOS; a separate AppKit app — the same approach as OpenConnect's menubar.py —
shows up correctly.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from aqt import mw

from .config import get_config
from .logger import log, log_exception
from .utils import format_seconds

_addon_module: str = ""
_seconds_provider: Optional[Callable[[], int]] = None
_controller: Optional["MenuBarBudgetController"] = None

_STATE_FILENAME = "ankitube_menubar_state.json"
_EXIT_SENTINEL = "__EXIT__"
_HELPER_NAME = "menubar_helper.py"


def set_addon_module(module: str) -> None:
    global _addon_module
    _addon_module = module
    log(f"menubar: set_addon_module module={module!r}")


def set_seconds_provider(provider: Callable[[], int]) -> None:
    global _seconds_provider
    _seconds_provider = provider


def set_menu_callbacks(
    *,
    play: Callable[[], None],
    pause: Callable[[], None],
    settings: Callable[[], None],
) -> None:
    # Menu actions live in Anki's Tools menu; the helper is display-only for now.
    _ = (play, pause, settings)


def label_for_seconds(seconds: int) -> str:
    return format_seconds(seconds)


def tooltip_for_seconds(seconds: int) -> str:
    return f"AnkiTube watch time remaining: {format_seconds(seconds)}"


def _enabled(*, quiet: bool = False) -> bool:
    if platform.system().lower() != "darwin":
        if not quiet:
            log("menubar: disabled (not macOS)")
        return False
    if not _addon_module:
        return True
    enabled = bool(get_config(_addon_module).get("show_menubar_watch_time", True))
    if not quiet:
        log(f"menubar: show_menubar_watch_time={enabled}")
    return enabled


def _seconds() -> int:
    if _seconds_provider is None:
        return 0
    return max(0, int(_seconds_provider()))


def _state_path() -> Path:
    try:
        folder = mw.pm.profileFolder()
    except Exception:
        folder = str(Path.home())
    return Path(folder) / _STATE_FILENAME


def _helper_script() -> Path:
    return Path(__file__).resolve().parent / _HELPER_NAME


def _vendor_dir() -> Path:
    return Path(__file__).resolve().parent / "vendor"


class MenuBarBudgetController:
    """Writes budget labels to a state file and keeps the rumps helper running."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._last_label: Optional[str] = None
        log("menubar: rumps helper controller created")

    def sync(self, *, force: bool = False) -> None:
        try:
            self._sync(force=force)
        except Exception:
            log_exception("menubar: sync failed")

    def _sync(self, *, force: bool = False) -> None:
        quiet = not force and self._proc_alive()
        if not _enabled(quiet=quiet):
            self.hide()
            return
        seconds = _seconds()
        label = label_for_seconds(seconds)
        tip = tooltip_for_seconds(seconds)
        if force or label != self._last_label:
            self._write_state({"label": label, "tip": tip})
            self._last_label = label
            if force or not quiet:
                log(f"menubar: state written label={label!r}")
        self._ensure_helper(force=force)

    def hide(self) -> None:
        self._stop_helper()
        self._last_label = None

    def shutdown(self) -> None:
        log("menubar: shutdown")
        self._stop_helper()
        self._last_label = None

    def _proc_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _write_state(self, payload: dict | str) -> None:
        path = _state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            text = (
                payload
                if isinstance(payload, str)
                else json.dumps(payload, ensure_ascii=True)
            )
            path.write_text(text + "\n", encoding="utf-8")
        except OSError:
            log_exception(f"menubar: failed writing state {path}")

    def _ensure_helper(self, *, force: bool = False) -> None:
        if self._proc_alive():
            return
        script = _helper_script()
        if not script.is_file():
            log(f"menubar: helper missing at {script}")
            return
        state = _state_path()
        vendor = _vendor_dir()
        env = os.environ.copy()
        if vendor.is_dir():
            vendor_s = str(vendor)
            env["PYTHONPATH"] = (
                vendor_s
                if not env.get("PYTHONPATH")
                else vendor_s + os.pathsep + env["PYTHONPATH"]
            )
        # Detach so Anki's quit doesn't leave a blocked child; helper watches state.
        log_file = None
        popen_kwargs: dict = {
            "args": [sys.executable, str(script), "--state", str(state)],
            "env": env,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
        }
        if bool(get_config(_addon_module).get("debug_logging", False)):
            try:
                helper_log = state.parent / "ankitube_menubar_helper.log"
                log_file = helper_log.open("a", encoding="utf-8")
                popen_kwargs["stdout"] = log_file
                popen_kwargs["stderr"] = subprocess.STDOUT
            except OSError:
                log_exception("menubar: could not open helper log")
        try:
            self._proc = subprocess.Popen(**popen_kwargs)
        except OSError:
            log_exception("menubar: failed to start rumps helper")
            self._proc = None
            if log_file is not None:
                log_file.close()
            return
        # Child owns the fd after dup; parent can close its copy.
        if log_file is not None:
            log_file.close()
        log(
            f"menubar: started rumps helper pid={self._proc.pid} "
            f"python={sys.executable!r} state={str(state)!r} force={force}"
        )

    def _stop_helper(self) -> None:
        self._write_state(_EXIT_SENTINEL)
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            log(f"menubar: stopped helper pid={proc.pid}")


def _get_controller() -> MenuBarBudgetController:
    global _controller
    if _controller is None:
        _controller = MenuBarBudgetController()
    return _controller


def update_menubar_watch_time(*, force: bool = False) -> None:
    if platform.system().lower() != "darwin":
        return
    try:
        _get_controller().sync(force=force)
    except Exception:
        log_exception("menubar: update_menubar_watch_time failed")


def shutdown_menubar_watch_time() -> None:
    global _controller
    if _controller is not None:
        _controller.shutdown()
        _controller = None
        log("menubar: controller cleared")
