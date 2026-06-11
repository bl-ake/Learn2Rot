from __future__ import annotations

import os
import traceback
from datetime import datetime

from aqt import mw

ADDON_MODULE: str | None = None


def set_addon_module(module: str) -> None:
    global ADDON_MODULE
    ADDON_MODULE = module


def _is_enabled() -> bool:
    if ADDON_MODULE is None:
        return False
    config = mw.addonManager.getConfig(ADDON_MODULE) or {}
    return bool(config.get("debug_logging", False))


def log_path() -> str:
    try:
        folder = mw.pm.profileFolder()
    except Exception:
        folder = os.path.expanduser("~")
    return os.path.join(folder, "ankittube.log")


def log(message: str) -> None:
    if not _is_enabled():
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}\n"
    try:
        with open(log_path(), "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def log_exception(context: str) -> None:
    if not _is_enabled():
        return
    log(f"{context}\n{traceback.format_exc()}")


def clear_log() -> None:
    path = log_path()
    try:
        with open(path, "w", encoding="utf-8"):
            pass
    except OSError:
        pass
