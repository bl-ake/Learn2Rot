# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from addon_loader import load_addon_module


def test_label_and_tooltip() -> None:
    menubar = load_addon_module("menubar_budget", "menubar_budget.py")
    assert menubar.label_for_seconds(65) == "1:05"
    assert "1:05" in menubar.tooltip_for_seconds(65)


def test_update_skips_non_darwin(mock_mw) -> None:
    menubar = load_addon_module("menubar_budget", "menubar_budget.py")
    with patch.object(menubar.platform, "system", return_value="Windows"):
        menubar.update_menubar_watch_time(force=True)
    assert menubar._controller is None


def test_enabled_reads_config(mock_mw) -> None:
    config_mod = load_addon_module("config", "config.py")
    menubar = load_addon_module("menubar_budget", "menubar_budget.py")
    menubar.set_addon_module("AnkiTube")
    with patch.object(menubar.platform, "system", return_value="Darwin"):
        assert menubar._enabled() is True
        config_mod.save_preferences("AnkiTube", {"show_menubar_watch_time": False})
        assert menubar._enabled() is False


def test_sync_writes_state_and_starts_helper(mock_mw, tmp_path) -> None:
    menubar = load_addon_module("menubar_budget", "menubar_budget.py")
    menubar.set_addon_module("AnkiTube")
    menubar.set_seconds_provider(lambda: 125)
    mock_mw.pm.profileFolder.return_value = str(tmp_path)

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.pid = 4242

    with patch.object(menubar.platform, "system", return_value="Darwin"), patch(
        "subprocess.Popen", return_value=fake_proc
    ) as popen:
        menubar.update_menubar_watch_time(force=True)

    state = tmp_path / "ankitube_menubar_state.json"
    assert state.exists()
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["label"] == "2:05"
    assert popen.called
    assert menubar._controller is not None
    assert menubar._controller._proc is fake_proc
