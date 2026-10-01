# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
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


def test_helper_python_rejects_packaged_anki() -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    assert daemon._is_packaged_anki_executable(r"C:\Program Files\Anki\Anki.exe")
    assert daemon._is_packaged_anki_executable("/Applications/Anki.app/Contents/MacOS/anki")
    assert not daemon._is_packaged_anki_executable(sys.executable)
    with patch.object(daemon.sys, "executable", sys.executable):
        assert daemon._helper_python() == sys.executable


def test_helper_python_falls_back_when_packaged_anki() -> None:
    """Packaged Anki.exe/anki is not a CLI; resolve AnkiProgramFiles instead."""
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    with patch.object(daemon.sys, "executable", r"C:\Programs\Anki\Anki.exe"):
        with patch.object(
            daemon.autostart,
            "resolve_launch_python",
            return_value=r"C:\AnkiProgramFiles\.venv\Scripts\pythonw.exe",
        ) as resolve:
            assert daemon._helper_python() == (
                r"C:\AnkiProgramFiles\.venv\Scripts\pythonw.exe"
            )
        resolve.assert_called_once_with(executable=r"C:\Programs\Anki\Anki.exe")
    with patch.object(daemon.sys, "executable", "/Applications/Anki.app/Contents/MacOS/anki"):
        with patch.object(
            daemon.autostart, "resolve_launch_python", return_value=None
        ):
            assert daemon._helper_python() is None


def test_supports_watch_daemon_platforms() -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    with patch.object(daemon.platform, "system", return_value="Darwin"):
        assert daemon._supports_watch_daemon() is True
    with patch.object(daemon.platform, "system", return_value="Windows"):
        assert daemon._supports_watch_daemon() is True
    with patch.object(daemon.platform, "system", return_value="Linux"):
        assert daemon._supports_watch_daemon() is False


def test_start_publishes_anki_pid(mock_mw, tmp_path) -> None:
    """The sentinel uses anki_pid to tell a live Anki from a crashed one."""
    import os

    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    daemon.set_addon_module("Learn2Rot")
    mock_mw.pm.profileFolder.return_value = str(tmp_path)

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.pid = 4242
    with patch.object(daemon, "_supports_watch_daemon", return_value=True), patch(
        "subprocess.Popen", return_value=fake_proc
    ):
        daemon.start_watch_daemon(budget_seconds=60, force=True)

    state = tmp_path / "learn2rot_watch_state.json"
    assert json.loads(state.read_text(encoding="utf-8"))["anki_pid"] == os.getpid()

    # Detaching leaves the helper running and the state file intact.
    daemon.shutdown_watch_daemon(quit_helper=False)
    payload = json.loads(state.read_text(encoding="utf-8"))
    # Cleared on shutdown so the sentinel takes over instead of standing down.
    assert payload["anki_pid"] == 0
    assert payload["anki_alive"] is False


def test_state_path_is_public_for_the_sentinel(mock_mw, tmp_path) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    mock_mw.pm.profileFolder.return_value = str(tmp_path)
    assert daemon.state_path() == tmp_path / "learn2rot_watch_state.json"


def test_ensure_helper_uses_the_named_launcher(mock_mw, tmp_path) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    daemon.set_addon_module("Learn2Rot")
    mock_mw.pm.profileFolder.return_value = str(tmp_path)

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.pid = 4321

    with patch.object(daemon, "_supports_watch_daemon", return_value=True):
        with patch.object(
            daemon.autostart,
            "ensure_named_launcher",
            return_value="/named/Anki Media Timer",
        ) as ensure_named:
            with patch("subprocess.Popen", return_value=fake_proc) as popen:
                daemon.start_watch_daemon(budget_seconds=60, force=True)

    assert ensure_named.call_args.kwargs["name"] == daemon.autostart.HELPER_PROCESS_NAME
    assert popen.call_args.kwargs["args"][0] == "/named/Anki Media Timer"
    daemon.shutdown_watch_daemon(quit_helper=True)


def _due_tree(new=0, learn=0, review=0):
    return SimpleNamespace(new_count=new, learn_count=learn, review_count=review)


def _set_due_tree(mock_mw, tree) -> None:
    """Set deck_due_tree's return value, clearing any side_effect a prior
    test left behind — mock_mw is a process-wide singleton the fixture
    doesn't fully reset, and side_effect otherwise silently wins over a
    freshly assigned return_value."""
    mock_mw.col.sched.deck_due_tree.side_effect = None
    mock_mw.col.sched.deck_due_tree.return_value = tree


def test_compute_cards_due_true_when_any_count_nonzero(mock_mw) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    _set_due_tree(mock_mw, _due_tree(review=1))
    assert daemon._compute_cards_due() == (True, 0)


def test_compute_cards_due_false_when_all_zero(mock_mw) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    _set_due_tree(mock_mw, _due_tree())
    assert daemon._compute_cards_due() == (False, 0)


def test_compute_cards_due_at_uses_day_cutoff(mock_mw) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    _set_due_tree(mock_mw, _due_tree())
    mock_mw.col.sched.day_cutoff = 1700003600
    mock_mw.col.db.scalar.return_value = None
    try:
        assert daemon._compute_cards_due() == (False, 1700003600)
    finally:
        mock_mw.col.sched.day_cutoff = 0


def test_compute_cards_due_at_prefers_sooner_learning_card(mock_mw) -> None:
    """The next learning-queue card can be due earlier today than the next
    day rollover — the sooner of the two should win."""
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    _set_due_tree(mock_mw, _due_tree())
    mock_mw.col.sched.day_cutoff = 1700003600
    mock_mw.col.db.scalar.return_value = 1700001000
    try:
        assert daemon._compute_cards_due() == (False, 1700001000)
    finally:
        mock_mw.col.sched.day_cutoff = 0
        mock_mw.col.db.scalar.return_value = None


def test_compute_cards_due_none_without_collection(mock_mw) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    original_col = mock_mw.col
    mock_mw.col = None
    try:
        assert daemon._compute_cards_due() is None
    finally:
        # mock_mw is a process-wide singleton the fixture doesn't fully
        # reset; leaving col=None here would break every later test.
        mock_mw.col = original_col


def test_compute_cards_due_none_on_backend_error(mock_mw) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    mock_mw.col.sched.deck_due_tree.side_effect = RuntimeError("boom")
    try:
        assert daemon._compute_cards_due() is None
    finally:
        mock_mw.col.sched.deck_due_tree.side_effect = None


def test_refresh_cards_due_noop_when_setting_off(mock_mw, tmp_path) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    ws = load_addon_module("watch_state", "watch_state.py")
    daemon.set_addon_module("Learn2Rot")
    mock_mw.pm.profileFolder.return_value = str(tmp_path)
    state_path = tmp_path / ws.STATE_FILENAME
    ws.write_state(state_path, {"cards_due": True})
    _set_due_tree(mock_mw, _due_tree())
    mock_mw.col.sched.deck_due_tree.reset_mock()

    controller = daemon.WatchDaemonController()
    controller._refresh_cards_due()

    # require_cards_due defaults off: no query performed, state untouched.
    assert mock_mw.col.sched.deck_due_tree.call_count == 0
    assert ws.read_state(state_path)["cards_due"] is True


def test_refresh_cards_due_pushes_when_changed(mock_mw, tmp_path) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    config_mod = load_addon_module("config", "config.py")
    ws = load_addon_module("watch_state", "watch_state.py")
    daemon.set_addon_module("Learn2Rot")
    mock_mw.pm.profileFolder.return_value = str(tmp_path)
    config_mod.save_preferences("Learn2Rot", {"require_cards_due": True})
    state_path = tmp_path / ws.STATE_FILENAME
    ws.write_state(state_path, {"cards_due": True})
    _set_due_tree(mock_mw, _due_tree())
    mock_mw.col.sched.day_cutoff = 1700003600

    controller = daemon.WatchDaemonController()
    try:
        controller._refresh_cards_due()
    finally:
        mock_mw.col.sched.day_cutoff = 0

    assert ws.read_state(state_path)["cards_due"] is False
    assert ws.read_state(state_path)["cards_due_at"] == 1700003600
    assert controller._last_cards_due is False
    assert controller._last_cards_due_at == 1700003600


def test_refresh_cards_due_skips_redundant_writes(mock_mw, tmp_path) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    config_mod = load_addon_module("config", "config.py")
    ws = load_addon_module("watch_state", "watch_state.py")
    daemon.set_addon_module("Learn2Rot")
    mock_mw.pm.profileFolder.return_value = str(tmp_path)
    config_mod.save_preferences("Learn2Rot", {"require_cards_due": True})
    state_path = tmp_path / ws.STATE_FILENAME
    ws.write_state(state_path, {"cards_due": False})
    _set_due_tree(mock_mw, _due_tree())

    controller = daemon.WatchDaemonController()
    controller._refresh_cards_due()
    with patch.object(daemon, "update_state") as update_state:
        controller._refresh_cards_due()
    assert update_state.call_count == 0


def test_refresh_cards_due_skips_on_backend_error(mock_mw, tmp_path) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    config_mod = load_addon_module("config", "config.py")
    ws = load_addon_module("watch_state", "watch_state.py")
    daemon.set_addon_module("Learn2Rot")
    mock_mw.pm.profileFolder.return_value = str(tmp_path)
    config_mod.save_preferences("Learn2Rot", {"require_cards_due": True})
    state_path = tmp_path / ws.STATE_FILENAME
    ws.write_state(state_path, {"cards_due": True})
    mock_mw.col.sched.deck_due_tree.side_effect = RuntimeError("boom")

    try:
        controller = daemon.WatchDaemonController()
        controller._refresh_cards_due()
        # Last-known value preserved rather than guessed when the check fails.
        assert ws.read_state(state_path)["cards_due"] is True
    finally:
        mock_mw.col.sched.deck_due_tree.side_effect = None


def test_module_refresh_cards_due_is_noop_without_controller(mock_mw) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    with patch.object(daemon, "_supports_watch_daemon", return_value=True):
        daemon._controller = None
        daemon.refresh_cards_due()  # must not raise / must not create one


def test_on_poll_rechecks_cards_due_every_60_ticks(mock_mw, tmp_path) -> None:
    daemon = load_addon_module("watch_daemon", "watch_daemon.py")
    ws = load_addon_module("watch_state", "watch_state.py")
    daemon.set_addon_module("Learn2Rot")
    mock_mw.pm.profileFolder.return_value = str(tmp_path)
    ws.write_state(tmp_path / ws.STATE_FILENAME, {})

    controller = daemon.WatchDaemonController()
    calls = {"n": 0}
    with patch.object(
        controller, "_refresh_cards_due", side_effect=lambda: calls.update(n=calls["n"] + 1)
    ):
        with patch.object(daemon, "_supports_watch_daemon", return_value=True):
            for _ in range(121):
                controller._on_poll()
    assert calls["n"] == 3  # ticks 1, 61, 121
