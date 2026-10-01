# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from addon_loader import load_addon_module

_DEAD_PID = 9_999_999


def _modules():
    config = load_addon_module("config", "config.py")
    ws = load_addon_module("watch_state", "watch_state.py")
    sentinel = load_addon_module("sentinel", "sentinel.py")
    return config, ws, sentinel


@pytest.fixture(autouse=True)
def _pretend_platform_supports_autostart():
    """CI runs on Linux; these tests are about the macOS/Windows behaviour."""
    _config, _ws, sentinel = _modules()
    with patch.object(sentinel, "supported", return_value=True):
        yield


def _setup(mock_mw, tmp_path):
    config, ws, sentinel = _modules()
    mock_mw.pm.profileFolder.return_value = str(tmp_path)
    # Interpreter discovery is a subprocess probe; pin it for every test.
    sentinel._cached_python = "/opt/py/bin/python3"
    sentinel._python_lookup_done = True
    return config, ws, sentinel


def test_paths_live_beside_the_profile(mock_mw, tmp_path) -> None:
    _config, ws, sentinel = _setup(mock_mw, tmp_path)
    assert sentinel.state_path() == tmp_path / ws.STATE_FILENAME
    assert sentinel.sentinel_state_path() == tmp_path / ws.SENTINEL_STATE_FILENAME
    assert sentinel.sentinel_log_path().parent == tmp_path


def test_disabled_and_absent_is_a_no_op(mock_mw, tmp_path) -> None:
    _config, _ws, sentinel = _setup(mock_mw, tmp_path)
    with patch.object(sentinel.autostart, "is_installed", return_value=False):
        with patch.object(sentinel.autostart, "uninstall") as uninstall:
            result = sentinel.sync("Learn2Rot")
    assert result.enabled is False
    assert result.ok is True
    # Profile open must not touch launchctl / the registry when nothing is on.
    assert uninstall.call_count == 0


def test_disabling_removes_the_login_item(mock_mw, tmp_path) -> None:
    _config, ws, sentinel = _setup(mock_mw, tmp_path)
    installed = {"value": True}
    with patch.object(
        sentinel.autostart, "is_installed", side_effect=lambda: installed["value"]
    ):
        with patch.object(
            sentinel.autostart,
            "uninstall",
            side_effect=lambda: installed.update(value=False) or True,
        ) as uninstall:
            result = sentinel.sync("Learn2Rot")
    assert uninstall.call_count == 1
    assert result.enabled is False
    assert result.installed is False
    # And the running sentinel is asked to stand down.
    assert ws.read_sentinel_state(sentinel.sentinel_state_path())["exit"] is True


def test_enabling_installs_and_starts(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences("Learn2Rot", {"sentinel_at_login": True})
    with patch.object(sentinel.autostart, "install", return_value=True) as install:
        with patch.object(sentinel.autostart, "starts_immediately", return_value=False):
            with patch.object(sentinel, "start_now", return_value=True) as start:
                result = sentinel.sync("Learn2Rot")
    assert result.enabled is True
    assert result.installed is True
    assert result.running is True
    assert result.ok is True
    assert start.call_count == 1
    argv = install.call_args[0][0]
    assert argv[0] == "/opt/py/bin/python3"
    assert argv[1].endswith("watch_sentinel.py")
    assert str(tmp_path) in argv[argv.index("--state") + 1]


def test_enabling_reports_registration_failure(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences("Learn2Rot", {"sentinel_at_login": True})
    with patch.object(sentinel.autostart, "install", return_value=False):
        with patch.object(sentinel, "start_now", return_value=True):
            result = sentinel.sync("Learn2Rot")
    assert result.ok is False
    assert "login" in result.error.lower()


def test_launchd_start_is_not_raced_with_a_second_process(mock_mw, tmp_path) -> None:
    """bootstrap already launched the agent; spawning again would duplicate it."""
    config, ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences("Learn2Rot", {"sentinel_at_login": True})

    def fake_install(_argv):
        # Stand in for launchd bootstrapping the RunAtLoad agent.
        ws.write_sentinel_state(sentinel.sentinel_state_path(), {"pid": os.getpid()})
        return True

    with patch.object(sentinel.autostart, "install", side_effect=fake_install):
        with patch.object(sentinel.autostart, "starts_immediately", return_value=True):
            with patch.object(sentinel, "start_now", side_effect=AssertionError):
                result = sentinel.sync("Learn2Rot")
    assert result.running is True
    assert result.ok is True


def test_falls_back_to_direct_start_when_launchd_does_not_come_up(
    mock_mw, tmp_path
) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences("Learn2Rot", {"sentinel_at_login": True})
    with patch.object(sentinel.autostart, "install", return_value=True):
        with patch.object(sentinel.autostart, "starts_immediately", return_value=True):
            with patch.object(sentinel, "_await_running", return_value=False):
                with patch.object(sentinel, "start_now", return_value=True) as start:
                    result = sentinel.sync("Learn2Rot")
    assert start.call_count == 1
    assert result.running is True


def test_enabling_without_an_interpreter_reports_why(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences("Learn2Rot", {"sentinel_at_login": True})
    sentinel._cached_python = None
    with patch.object(sentinel.autostart, "install", side_effect=AssertionError):
        result = sentinel.sync("Learn2Rot")
    assert result.ok is False
    assert "Python" in result.error


def test_unsupported_platform_reports_why(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences("Learn2Rot", {"sentinel_at_login": True})
    with patch.object(sentinel, "supported", return_value=False):
        result = sentinel.sync("Learn2Rot")
    assert result.ok is False
    assert "macOS and Windows" in result.error


def test_is_running_tracks_the_claim(mock_mw, tmp_path) -> None:
    _config, ws, sentinel = _setup(mock_mw, tmp_path)
    path = sentinel.sentinel_state_path()
    assert sentinel.is_running() is False

    ws.write_sentinel_state(path, {"pid": os.getpid()})
    assert sentinel.is_running() is True

    ws.write_sentinel_state(path, {"pid": os.getpid(), "exit": True})
    assert sentinel.is_running() is False

    ws.write_sentinel_state(path, {"pid": _DEAD_PID})
    with patch.object(sentinel, "pid_is_alive", return_value=False):
        assert sentinel.is_running() is False


def test_start_now_clears_a_stale_exit_flag(mock_mw, tmp_path) -> None:
    _config, ws, sentinel = _setup(mock_mw, tmp_path)
    ws.request_sentinel_exit(sentinel.sentinel_state_path())
    with patch.object(
        sentinel.subprocess, "Popen", return_value=SimpleNamespace(pid=4242)
    ) as popen:
        assert sentinel.start_now("Learn2Rot") is True
    assert popen.call_count == 1
    assert ws.read_sentinel_state(sentinel.sentinel_state_path())["exit"] is False
    argv = popen.call_args.kwargs["args"]
    assert argv[0] == "/opt/py/bin/python3"
    assert "--sentinel-state" in argv


def test_start_now_is_idempotent(mock_mw, tmp_path) -> None:
    _config, ws, sentinel = _setup(mock_mw, tmp_path)
    ws.write_sentinel_state(sentinel.sentinel_state_path(), {"pid": os.getpid()})
    with patch.object(sentinel.subprocess, "Popen", side_effect=AssertionError):
        assert sentinel.start_now("Learn2Rot") is True


def test_start_now_needs_an_interpreter(mock_mw, tmp_path) -> None:
    _config, _ws, sentinel = _setup(mock_mw, tmp_path)
    sentinel._cached_python = None
    with patch.object(sentinel.subprocess, "Popen", side_effect=AssertionError):
        assert sentinel.start_now("Learn2Rot") is False


def test_poll_interval_comes_from_config(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences("Learn2Rot", {"sentinel_poll_ms": 250})
    assert sentinel._poll_ms("Learn2Rot") == 1000  # clamped up
    config.save_preferences("Learn2Rot", {"sentinel_poll_ms": 8000})
    assert sentinel._poll_ms("Learn2Rot") == 8000


def test_describe_status(mock_mw, tmp_path) -> None:
    config, ws, sentinel = _setup(mock_mw, tmp_path)
    assert sentinel.describe_status("Learn2Rot").startswith("Off")

    config.save_preferences("Learn2Rot", {"sentinel_at_login": True})
    ws.write_sentinel_state(sentinel.sentinel_state_path(), {"pid": os.getpid()})
    with patch.object(sentinel.autostart, "is_installed", return_value=True):
        status = sentinel.describe_status("Learn2Rot")
    assert status.startswith("On")
    assert "registered for login" in status
    assert "watching now" in status


def test_open_only_installs_a_oneshot_login_item(mock_mw, tmp_path) -> None:
    """Open at login without persisting should not keep the watcher running."""
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot", {"open_timer_at_login": True, "sentinel_at_login": False}
    )
    with patch.object(sentinel.autostart, "install", return_value=True) as install:
        with patch.object(sentinel.autostart, "starts_immediately", return_value=True):
            result = sentinel.sync("Learn2Rot")
    argv = install.call_args[0][0]
    assert "--spawn-on-start" in argv
    assert "--oneshot" in argv
    assert result.enabled is True
    assert result.running is True  # launchd's RunAtLoad already fired it
    assert result.ok is True


def test_open_only_starts_immediately_on_windows_too(mock_mw, tmp_path) -> None:
    """Windows Run keys don't fire until next sign-in; start it now anyway."""
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot", {"open_timer_at_login": True, "sentinel_at_login": False}
    )
    with patch.object(sentinel.autostart, "install", return_value=True):
        with patch.object(sentinel.autostart, "starts_immediately", return_value=False):
            with patch.object(sentinel, "start_now", return_value=True) as start:
                result = sentinel.sync("Learn2Rot")
    assert start.call_args.kwargs == {"spawn_on_start": True, "oneshot": True}
    assert result.running is True


def test_persist_and_open_together_pass_both_flags(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot", {"open_timer_at_login": True, "sentinel_at_login": True}
    )
    with patch.object(sentinel.autostart, "install", return_value=True) as install:
        with patch.object(sentinel.autostart, "starts_immediately", return_value=False):
            with patch.object(sentinel, "start_now", return_value=True) as start:
                result = sentinel.sync("Learn2Rot")
    argv = install.call_args[0][0]
    assert "--spawn-on-start" in argv
    assert "--oneshot" not in argv  # persists, does not exit after one attempt
    assert start.call_args.kwargs == {"spawn_on_start": True}
    assert result.ok is True


def test_persist_forces_open_even_when_explicitly_unset(mock_mw, tmp_path) -> None:
    """config.migrate_config forces open_timer_at_login true whenever
    sentinel_at_login is true — even if the caller explicitly passed False —
    so a persistent watcher always opens the timer at login rather than
    sitting invisible until media happens to start."""
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot", {"open_timer_at_login": False, "sentinel_at_login": True}
    )
    assert config.get_config("Learn2Rot")["open_timer_at_login"] is True
    with patch.object(sentinel.autostart, "install", return_value=True) as install:
        with patch.object(sentinel.autostart, "starts_immediately", return_value=False):
            with patch.object(sentinel, "start_now", return_value=True) as start:
                sentinel.sync("Learn2Rot")
    argv = install.call_args[0][0]
    assert "--spawn-on-start" in argv
    assert "--oneshot" not in argv
    assert start.call_args.kwargs == {"spawn_on_start": True}


def test_both_off_uninstalls(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot", {"open_timer_at_login": False, "sentinel_at_login": False}
    )
    with patch.object(sentinel.autostart, "is_installed", return_value=True):
        with patch.object(sentinel.autostart, "uninstall", return_value=True) as uninstall:
            result = sentinel.sync("Learn2Rot")
    assert uninstall.call_count == 1
    assert result.enabled is False


def test_describe_status_open_only(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot", {"open_timer_at_login": True, "sentinel_at_login": False}
    )
    with patch.object(sentinel.autostart, "is_installed", return_value=True):
        status = sentinel.describe_status("Learn2Rot")
    assert status.startswith("On")
    assert "opens once at login, then exits" in status


def test_start_now_restarts_a_stale_watcher_to_apply_spawn_on_start(
    mock_mw, tmp_path
) -> None:
    """A persistent watcher already running (started before open-at-login was
    turned on) must be restarted, or the new "open now" request is dropped
    silently until next login."""
    _config, ws, sentinel = _setup(mock_mw, tmp_path)
    ws.write_sentinel_state(sentinel.sentinel_state_path(), {"pid": os.getpid()})
    stopped = {"value": False}

    def fake_stop_now() -> None:
        stopped["value"] = True

    with patch.object(sentinel, "stop_now", side_effect=fake_stop_now) as stop:
        with patch.object(sentinel, "_await_stopped", return_value=True):
            with patch.object(
                sentinel.subprocess, "Popen", return_value=SimpleNamespace(pid=555)
            ) as popen:
                assert sentinel.start_now("Learn2Rot", spawn_on_start=True) is True
    assert stop.call_count == 1
    assert popen.call_count == 1
    argv = popen.call_args.kwargs["args"]
    assert "--spawn-on-start" in argv


def test_start_now_skips_restart_when_spawn_on_start_not_requested(
    mock_mw, tmp_path
) -> None:
    _config, ws, sentinel = _setup(mock_mw, tmp_path)
    ws.write_sentinel_state(sentinel.sentinel_state_path(), {"pid": os.getpid()})
    with patch.object(sentinel, "stop_now", side_effect=AssertionError):
        assert sentinel.start_now("Learn2Rot") is True


def test_on_hours_absent_when_disabled(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot", {"sentinel_at_login": True, "sentinel_on_hours_enabled": False}
    )
    assert sentinel._on_hours("Learn2Rot") is None


def test_on_hours_absent_without_persist(mock_mw, tmp_path) -> None:
    """A one-shot open-at-login item has nothing left behind to revive
    playback with, so on-hours has nothing to restrict."""
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot",
        {
            "sentinel_at_login": False,
            "sentinel_on_hours_enabled": True,
            "sentinel_on_hours_start": "09:00",
            "sentinel_on_hours_end": "17:00",
        },
    )
    assert sentinel._on_hours("Learn2Rot") is None


def test_on_hours_present_when_persisting(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot",
        {
            "sentinel_at_login": True,
            "sentinel_on_hours_enabled": True,
            "sentinel_on_hours_start": "08:30",
            "sentinel_on_hours_end": "18:15",
        },
    )
    assert sentinel._on_hours("Learn2Rot") == ("08:30", "18:15")


def test_command_includes_on_hours_argv(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot",
        {
            "sentinel_at_login": True,
            "sentinel_on_hours_enabled": True,
            "sentinel_on_hours_start": "08:30",
            "sentinel_on_hours_end": "18:15",
        },
    )
    argv = sentinel._command("Learn2Rot", "/opt/py/bin/python3")
    assert argv[argv.index("--on-hours-start") + 1] == "08:30"
    assert argv[argv.index("--on-hours-end") + 1] == "18:15"


def test_on_days_absent_when_on_hours_disabled(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot", {"sentinel_at_login": True, "sentinel_on_hours_enabled": False}
    )
    assert sentinel._on_days("Learn2Rot") is None


def test_on_days_present_when_on_hours_enabled(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot",
        {
            "sentinel_at_login": True,
            "sentinel_on_hours_enabled": True,
            "sentinel_on_days": ["mon", "wed", "fri"],
        },
    )
    assert sentinel._on_days("Learn2Rot") == ("mon", "wed", "fri")


def test_on_days_preserves_explicit_empty_selection(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot",
        {
            "sentinel_at_login": True,
            "sentinel_on_hours_enabled": True,
            "sentinel_on_days": [],
        },
    )
    assert sentinel._on_days("Learn2Rot") == ()


def test_command_includes_on_days_argv(mock_mw, tmp_path) -> None:
    config, _ws, sentinel = _setup(mock_mw, tmp_path)
    config.save_preferences(
        "Learn2Rot",
        {
            "sentinel_at_login": True,
            "sentinel_on_hours_enabled": True,
            "sentinel_on_days": ["mon", "tue"],
        },
    )
    argv = sentinel._command("Learn2Rot", "/opt/py/bin/python3")
    assert argv[argv.index("--on-days") + 1] == "mon,tue"


def test_launch_python_wraps_resolution_in_a_named_launcher(mock_mw, tmp_path) -> None:
    _config, _ws, sentinel = _setup(mock_mw, tmp_path)
    sentinel._cached_python = None
    sentinel._python_lookup_done = False
    with patch.object(
        sentinel.autostart, "resolve_launch_python", return_value="/opt/py/bin/python3"
    ):
        with patch.object(
            sentinel.autostart,
            "ensure_named_launcher",
            return_value="/named/Anki Media Timer Watcher",
        ) as ensure_named:
            resolved = sentinel._launch_python()
    assert resolved == "/named/Anki Media Timer Watcher"
    assert ensure_named.call_args.kwargs["name"] == sentinel.autostart.SENTINEL_PROCESS_NAME
