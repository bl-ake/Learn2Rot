# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

ADDON_ROOT = Path(__file__).resolve().parents[1]
_PKG = "learn2rot_test"


def _ensure_aqt_mock() -> MagicMock:
    mw = MagicMock()
    stored: dict = {}
    mw._learn2rot_test_config = stored  # type: ignore[attr-defined]

    def get_config(_module: str) -> dict:
        return dict(stored)

    def write_config(_module: str, data: dict) -> None:
        stored.clear()
        stored.update(data)

    mw.addonManager.getConfig.side_effect = get_config
    mw.addonManager.writeConfig.side_effect = write_config
    mw.state = "review"
    mw.pm.profileFolder.return_value = str(ADDON_ROOT)
    mw.serverURL.return_value = "http://127.0.0.1:12345/"
    mw.addonManager.addonFromModule.return_value = "Learn2Rot"
    mw.taskman.run_on_main.side_effect = lambda fn: fn()

    aqt = ModuleType("aqt")
    aqt.mw = mw
    sys.modules["aqt"] = aqt
    for submodule in ("qt", "utils", "webview", "gui_hooks"):
        sys.modules[f"aqt.{submodule}"] = MagicMock()
    return mw


def _ensure_anki_mock() -> None:
    if "anki" not in sys.modules:
        sys.modules["anki"] = MagicMock()
        sys.modules["anki.collection"] = MagicMock()


def _ensure_pkg() -> None:
    if _PKG not in sys.modules:
        pkg = ModuleType(_PKG)
        pkg.__path__ = [str(ADDON_ROOT)]
        sys.modules[_PKG] = pkg


_ensure_aqt_mock()
_ensure_anki_mock()


def load_addon_module(name: str, filename: str) -> ModuleType:
    full_name = f"{_PKG}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    _ensure_pkg()
    path = ADDON_ROOT / filename
    spec = importlib.util.spec_from_file_location(
        full_name,
        path,
        submodule_search_locations=[str(ADDON_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")

    module = importlib.util.module_from_spec(spec)
    module.__package__ = _PKG
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module
