# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path
from unittest.mock import patch

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from addon_loader import load_addon_module


def _autostart():
    return load_addon_module("autostart", "autostart.py")


def test_supports_autostart() -> None:
    autostart = _autostart()
    assert autostart.supports_autostart("darwin") is True
    assert autostart.supports_autostart("win32") is True
    assert autostart.supports_autostart("linux") is False


def test_packaged_anki_executable_detection() -> None:
    autostart = _autostart()
    assert autostart._is_packaged_anki_executable("/Applications/Anki.app/x/anki")
    assert autostart._is_packaged_anki_executable(r"C:\Program Files\Anki\anki.exe")
    assert not autostart._is_packaged_anki_executable("/usr/bin/python3")


def test_candidates_skip_packaged_anki() -> None:
    autostart = _autostart()
    candidates = autostart.python_candidates(
        executable="/Applications/Anki.app/Contents/MacOS/anki",
        platform_name="darwin",
    )
    assert "/Applications/Anki.app/Contents/MacOS/anki" not in candidates


def test_candidates_prefer_running_interpreter() -> None:
    autostart = _autostart()
    candidates = autostart.python_candidates(
        executable="/opt/py313/bin/python3", platform_name="darwin"
    )
    assert candidates[0] == "/opt/py313/bin/python3"


def test_resolve_launch_python_picks_first_abi_match() -> None:
    autostart = _autostart()
    seen: list[str] = []

    def probe(executable: str):
        seen.append(executable)
        return {"/py311": (3, 11), "/py313": (3, 13), "/py314": (3, 14)}.get(executable)

    with patch.object(
        autostart, "python_candidates", return_value=["/py311", "/py313", "/py314"]
    ):
        resolved = autostart.resolve_launch_python(
            required_version=(3, 13), probe=probe
        )
    assert resolved == "/py313"
    # Stops as soon as it matches — no wasted subprocess probes.
    assert seen == ["/py311", "/py313"]


def test_resolve_launch_python_returns_none_without_match() -> None:
    autostart = _autostart()
    with patch.object(autostart, "python_candidates", return_value=["/py311"]):
        resolved = autostart.resolve_launch_python(
            required_version=(3, 13), probe=lambda _executable: (3, 11)
        )
    assert resolved is None


def test_resolve_launch_python_tolerates_unprobeable_candidate() -> None:
    autostart = _autostart()
    with patch.object(autostart, "python_candidates", return_value=["/gone", "/py313"]):
        resolved = autostart.resolve_launch_python(
            required_version=(3, 13),
            probe=lambda executable: None if executable == "/gone" else (3, 13),
        )
    assert resolved == "/py313"


def test_sentinel_command_shape(tmp_path) -> None:
    autostart = _autostart()
    argv = autostart.sentinel_command(
        python="/opt/py/bin/python3",
        script=tmp_path / "watch_sentinel.py",
        state_path=tmp_path / "state.json",
        sentinel_state_path=tmp_path / "sentinel.json",
        poll_ms=3000,
        log_path=tmp_path / "sentinel.log",
    )
    assert argv[0] == "/opt/py/bin/python3"
    assert argv[1] == str(tmp_path / "watch_sentinel.py")
    assert argv[argv.index("--state") + 1] == str(tmp_path / "state.json")
    assert argv[argv.index("--poll-ms") + 1] == "3000"
    assert argv[argv.index("--log") + 1] == str(tmp_path / "sentinel.log")


def test_sentinel_command_omits_log_when_unset(tmp_path) -> None:
    autostart = _autostart()
    argv = autostart.sentinel_command(
        python="python3",
        script=tmp_path / "s.py",
        state_path=tmp_path / "state.json",
        sentinel_state_path=tmp_path / "sentinel.json",
        poll_ms=1000,
    )
    assert "--log" not in argv


def test_launch_agent_plist_contents() -> None:
    autostart = _autostart()
    plist = autostart.launch_agent_plist(["/py", "/script.py"])
    assert plist["Label"] == autostart.LAUNCH_AGENT_LABEL
    assert plist["ProgramArguments"] == ["/py", "/script.py"]
    assert plist["RunAtLoad"] is True
    # A clean exit (menu bar "Quit") must not be undone by launchd.
    assert plist["KeepAlive"] == {"SuccessfulExit": False}


def test_install_and_remove_launch_agent(tmp_path) -> None:
    autostart = _autostart()
    argv = ["/opt/py/bin/python3", "/addon/watch_sentinel.py"]
    with patch.object(autostart, "_load_launch_agent", return_value=True) as load:
        assert autostart.install_launch_agent(argv, home=tmp_path) is True
    plist_path = autostart.launch_agent_path(tmp_path)
    assert plist_path.is_file()
    assert load.call_count == 1
    with plist_path.open("rb") as handle:
        assert plistlib.load(handle)["ProgramArguments"] == argv
    assert autostart.launch_agent_installed(home=tmp_path) is True

    with patch.object(autostart, "_unload_launch_agent") as unload:
        assert autostart.remove_launch_agent(home=tmp_path) is True
    assert unload.call_count == 1
    assert not plist_path.exists()
    assert autostart.launch_agent_installed(home=tmp_path) is False


def test_remove_launch_agent_noop_when_absent(tmp_path) -> None:
    autostart = _autostart()
    with patch.object(autostart, "_unload_launch_agent") as unload:
        assert autostart.remove_launch_agent(home=tmp_path) is True
    assert unload.call_count == 0


def test_quote_windows() -> None:
    autostart = _autostart()
    quoted = autostart._quote_windows(
        [r"C:\Program Files\py\pythonw.exe", "--state", r"C:\a\b.json"]
    )
    assert quoted.startswith('"C:\\Program Files\\py\\pythonw.exe"')
    assert "--state" in quoted


def test_install_uninstall_unsupported_platform() -> None:
    autostart = _autostart()
    assert autostart.install(["x"], platform_name="linux") is False
    assert autostart.uninstall(platform_name="linux") is True
    assert autostart.is_installed(platform_name="linux") is False


def test_ensure_named_launcher_noop_off_darwin(tmp_path) -> None:
    autostart = _autostart()
    result = autostart.ensure_named_launcher(
        "/opt/py/bin/python3",
        name="Anki Media Timer",
        directory=tmp_path,
        platform_name="win32",
    )
    assert result == "/opt/py/bin/python3"
    assert list(tmp_path.iterdir()) == []


def test_ensure_named_launcher_creates_a_symlink_on_darwin(tmp_path) -> None:
    autostart = _autostart()
    real_python = tmp_path / "real_python3"
    real_python.write_text("#!/bin/sh\n", encoding="utf-8")
    directory = tmp_path / "launchers"

    result = autostart.ensure_named_launcher(
        str(real_python),
        name="Anki Media Timer",
        directory=directory,
        platform_name="darwin",
    )

    link = directory / "Anki Media Timer"
    assert result == str(link)
    assert link.is_symlink()
    assert Path(os.readlink(link)) == real_python.resolve()


def test_ensure_named_launcher_is_idempotent(tmp_path) -> None:
    autostart = _autostart()
    real_python = tmp_path / "real_python3"
    real_python.write_text("#!/bin/sh\n", encoding="utf-8")
    directory = tmp_path / "launchers"

    first = autostart.ensure_named_launcher(
        str(real_python), name="Anki Media Timer", directory=directory,
        platform_name="darwin",
    )
    link = Path(first)
    mtime_before = link.lstat().st_mtime_ns
    second = autostart.ensure_named_launcher(
        str(real_python), name="Anki Media Timer", directory=directory,
        platform_name="darwin",
    )
    assert second == first
    assert link.lstat().st_mtime_ns == mtime_before  # not recreated


def test_ensure_named_launcher_updates_a_stale_target(tmp_path) -> None:
    autostart = _autostart()
    old_python = tmp_path / "old_python3"
    old_python.write_text("#!/bin/sh\n", encoding="utf-8")
    new_python = tmp_path / "new_python3"
    new_python.write_text("#!/bin/sh\n", encoding="utf-8")
    directory = tmp_path / "launchers"

    autostart.ensure_named_launcher(
        str(old_python), name="Anki Media Timer", directory=directory,
        platform_name="darwin",
    )
    autostart.ensure_named_launcher(
        str(new_python), name="Anki Media Timer", directory=directory,
        platform_name="darwin",
    )
    link = directory / "Anki Media Timer"
    assert Path(os.readlink(link)) == new_python.resolve()


def test_ensure_named_launcher_falls_back_on_failure(tmp_path) -> None:
    autostart = _autostart()
    unwritable = tmp_path / "not_a_directory"
    unwritable.write_text("x", encoding="utf-8")  # a file, not a dir
    result = autostart.ensure_named_launcher(
        "/opt/py/bin/python3",
        name="Anki Media Timer",
        directory=unwritable / "launchers",
        platform_name="darwin",
    )
    assert result == "/opt/py/bin/python3"


def test_sentinel_command_spawn_on_start_and_oneshot_flags(tmp_path) -> None:
    autostart = _autostart()
    argv = autostart.sentinel_command(
        python="python3",
        script=tmp_path / "watch_sentinel.py",
        state_path=tmp_path / "state.json",
        sentinel_state_path=tmp_path / "sentinel.json",
        poll_ms=1000,
        spawn_on_start=True,
        oneshot=True,
    )
    assert "--spawn-on-start" in argv
    assert "--oneshot" in argv


def test_sentinel_command_omits_flags_by_default(tmp_path) -> None:
    autostart = _autostart()
    argv = autostart.sentinel_command(
        python="python3",
        script=tmp_path / "watch_sentinel.py",
        state_path=tmp_path / "state.json",
        sentinel_state_path=tmp_path / "sentinel.json",
        poll_ms=1000,
    )
    assert "--spawn-on-start" not in argv
    assert "--oneshot" not in argv


def test_sentinel_command_includes_on_hours(tmp_path) -> None:
    autostart = _autostart()
    argv = autostart.sentinel_command(
        python="python3",
        script=tmp_path / "watch_sentinel.py",
        state_path=tmp_path / "state.json",
        sentinel_state_path=tmp_path / "sentinel.json",
        poll_ms=1000,
        on_hours=("09:00", "21:00"),
    )
    assert argv[argv.index("--on-hours-start") + 1] == "09:00"
    assert argv[argv.index("--on-hours-end") + 1] == "21:00"


def test_sentinel_command_omits_on_hours_by_default(tmp_path) -> None:
    autostart = _autostart()
    argv = autostart.sentinel_command(
        python="python3",
        script=tmp_path / "watch_sentinel.py",
        state_path=tmp_path / "state.json",
        sentinel_state_path=tmp_path / "sentinel.json",
        poll_ms=1000,
    )
    assert "--on-hours-start" not in argv
    assert "--on-hours-end" not in argv


def test_sentinel_command_includes_on_days(tmp_path) -> None:
    autostart = _autostart()
    argv = autostart.sentinel_command(
        python="python3",
        script=tmp_path / "watch_sentinel.py",
        state_path=tmp_path / "state.json",
        sentinel_state_path=tmp_path / "sentinel.json",
        poll_ms=1000,
        on_days=("mon", "wed", "fri"),
    )
    assert argv[argv.index("--on-days") + 1] == "mon,wed,fri"


def test_sentinel_command_includes_empty_on_days_selection(tmp_path) -> None:
    """An explicit empty selection must still produce the flag (empty
    value), distinct from omitting it entirely (no restriction)."""
    autostart = _autostart()
    argv = autostart.sentinel_command(
        python="python3",
        script=tmp_path / "watch_sentinel.py",
        state_path=tmp_path / "state.json",
        sentinel_state_path=tmp_path / "sentinel.json",
        poll_ms=1000,
        on_days=(),
    )
    assert "--on-days" in argv
    assert argv[argv.index("--on-days") + 1] == ""


def test_sentinel_command_omits_on_days_by_default(tmp_path) -> None:
    autostart = _autostart()
    argv = autostart.sentinel_command(
        python="python3",
        script=tmp_path / "watch_sentinel.py",
        state_path=tmp_path / "state.json",
        sentinel_state_path=tmp_path / "sentinel.json",
        poll_ms=1000,
    )
    assert "--on-days" not in argv
