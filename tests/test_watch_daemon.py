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
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    assert daemon.label_for_seconds(65) == "1:05"
    assert "1:05" in daemon.tooltip_for_seconds(65)
    assert "Anki Media Timer" in daemon.tooltip_for_seconds(65)


def test_start_skips_unsupported(mock_mw) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    with patch.object(daemon, "_supports_watch_daemon", return_value=False):
        daemon.start_watch_daemon(budget_seconds=30, force=True)
    assert daemon._controller is None


def test_should_run_for_system_mode(mock_mw) -> None:
    config_mod = load_addon_module("config", "config.py")
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    daemon.set_addon_module("Learn2Rot")
    with patch.object(daemon, "_supports_watch_daemon", return_value=True):
        assert daemon._should_run_daemon() is True
        config_mod.save_preferences(
            "Learn2Rot",
            {"media_mode": "youtube", "show_menubar_watch_time": False},
        )
        assert daemon._should_run_daemon() is False
        config_mod.save_preferences(
            "Learn2Rot",
            {"media_mode": "youtube", "show_menubar_watch_time": True},
        )
        assert daemon._should_run_daemon() is True


def test_credit_writes_state(mock_mw, tmp_path) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    ws = load_addon_module("watch_state", "watch_state.py")
    daemon.set_addon_module("Learn2Rot")
    mock_mw.pm.profileFolder.return_value = str(tmp_path)
    state_path = tmp_path / ws.STATE_FILENAME
    ws.write_state(state_path, {"budget_seconds": 10, "credits": 0})

    with patch.object(daemon, "_supports_watch_daemon", return_value=True):
        daemon.credit_watch_time(15)

    state = ws.read_state(state_path)
    assert state["credits"] == 15
    assert state["anki_alive"] is True


def test_start_writes_prefs_and_starts_helper(mock_mw, tmp_path) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    daemon.set_addon_module("Learn2Rot")
    mock_mw.pm.profileFolder.return_value = str(tmp_path)

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.pid = 4242

    with patch.object(daemon, "_supports_watch_daemon", return_value=True), patch(
        "subprocess.Popen", return_value=fake_proc
    ) as popen:
        daemon.start_watch_daemon(budget_seconds=125, force=True)

    state = tmp_path / "learn2rot_watch_state.json"
    assert state.exists()
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["budget_seconds"] == 125
    assert payload["prefs"]["enforce"] is True
    assert payload["prefs"]["quit_with_anki"] is True
    assert popen.called
    assert daemon._controller is not None
    daemon.shutdown_watch_daemon(quit_helper=True)


def test_supports_watch_daemon_platforms() -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    with patch.object(daemon.platform, "system", return_value="Darwin"):
        assert daemon._supports_watch_daemon() is True
    with patch.object(daemon.platform, "system", return_value="Windows"):
        assert daemon._supports_watch_daemon() is True
    with patch.object(daemon.platform, "system", return_value="Linux"):
        assert daemon._supports_watch_daemon() is False
