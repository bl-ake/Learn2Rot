# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Register the media sentinel as a login item (macOS LaunchAgent / Windows Run).

Pure OS plumbing — no Anki imports, so it is exercised directly by tests.
The Anki-facing wiring lives in ``sentinel.py``.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

LAUNCH_AGENT_LABEL = "com.github.bl-ake.learn2rot.sentinel"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_RUN_VALUE = "Learn2Rot Media Sentinel"

# Process names shown by Activity Monitor / Force Quit / background-activity
# and energy notifications (see ensure_named_launcher).
LAUNCHERS_DIRNAME = "learn2rot_launchers"
HELPER_PROCESS_NAME = "Anki Media Timer"
SENTINEL_PROCESS_NAME = "Anki Media Timer Watcher"

_PROBE_TIMEOUT_SEC = 5.0
_LAUNCHCTL_TIMEOUT_SEC = 10.0
_VERSION_PROBE = "import sys;print('%d.%d' % sys.version_info[:2])"


def supports_autostart(platform_name: Optional[str] = None) -> bool:
    return (platform_name or sys.platform) in ("darwin", "win32")


# --------------------------------------------------------------------------
# Interpreter discovery
# --------------------------------------------------------------------------


def _is_packaged_anki_executable(executable: str) -> bool:
    """True when ``executable`` is Anki itself rather than a Python CLI.

    Mirrors ``watch_daemon._is_packaged_anki_executable``: handing a ``.py``
    path to Anki.exe launches a second Anki instead of running the script.
    """
    name = executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name in ("anki", "anki.exe", "ankiw.exe")


def _anki_launcher_pythons(platform_name: str) -> list[Path]:
    """Interpreters from Anki's uv-managed launcher venv (Anki 25.x+)."""
    if platform_name == "win32":
        base = os.environ.get("LOCALAPPDATA") or ""
        if not base:
            return []
        scripts = Path(base) / "AnkiProgramFiles" / ".venv" / "Scripts"
        return [scripts / "pythonw.exe", scripts / "python.exe"]
    venv_bin = (
        Path.home()
        / "Library"
        / "Application Support"
        / "AnkiProgramFiles"
        / ".venv"
        / "bin"
    )
    return [venv_bin / "python3", venv_bin / "python"]


def _windowless_sibling(executable: Path) -> Optional[Path]:
    """Prefer pythonw.exe so the login item never flashes a console window."""
    if sys.platform != "win32" or executable.name.lower() != "python.exe":
        return None
    candidate = executable.with_name("pythonw.exe")
    return candidate if candidate.is_file() else None


def python_candidates(
    *,
    executable: Optional[str] = None,
    platform_name: Optional[str] = None,
) -> list[str]:
    """Ordered interpreters that might be able to host the sentinel."""
    import shutil

    platform_name = platform_name or sys.platform
    executable = sys.executable if executable is None else executable
    candidates: list[str] = []

    def add(value: Any) -> None:
        if not value:
            return
        text = str(value)
        if text not in candidates:
            candidates.append(text)

    if executable and not _is_packaged_anki_executable(executable):
        windowless = _windowless_sibling(Path(executable))
        if windowless is not None:
            add(windowless)
        add(executable)

    for path in _anki_launcher_pythons(platform_name):
        if path.is_file():
            add(path)

    names = (
        ("pythonw", "python3.13", "python3", "python")
        if platform_name == "win32"
        else ("python3.13", "python3", "python")
    )
    for name in names:
        add(shutil.which(name))
    return candidates


def _probe_python_version(executable: str) -> Optional[tuple[int, int]]:
    try:
        result = subprocess.run(
            [executable, "-c", _VERSION_PROBE],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    parts = (result.stdout or "").strip().split(".")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def resolve_launch_python(
    *,
    executable: Optional[str] = None,
    platform_name: Optional[str] = None,
    required_version: Optional[tuple[int, int]] = None,
    probe: Any = None,
) -> Optional[str]:
    """Pick an interpreter for the login item, or None if none is usable.

    The sentinel spawns ``watch_helper.py``, which imports the vendored
    rumps/pystray/pyobjc natives. Those are built for one CPython ABI, so the
    login interpreter must match the minor version Anki itself runs on.
    """
    required = required_version or sys.version_info[:2]
    probe_fn = probe or _probe_python_version
    for candidate in python_candidates(
        executable=executable, platform_name=platform_name
    ):
        if probe_fn(candidate) == tuple(required):
            return candidate
    return None


# --------------------------------------------------------------------------
# Process identity
# --------------------------------------------------------------------------


def ensure_named_launcher(
    python: str, *, name: str, directory: Path, platform_name: Optional[str] = None
) -> str:
    """Return an executable path that makes ``name`` the process name shown
    by Activity Monitor, Force Quit, and background-activity/energy
    notifications.

    macOS reads a process's displayed name from the path passed to exec(),
    not argv[0] or the running script's filename — so launching through a
    friendly-named symlink to the real interpreter is enough on its own; no
    app bundle or code signing required. Falls back to returning ``python``
    unchanged wherever this doesn't apply (only verified on macOS) or if the
    symlink can't be created (e.g. a read-only profile folder).
    """
    if (platform_name or sys.platform) != "darwin":
        return python
    try:
        target = str(Path(python).resolve())
        directory.mkdir(parents=True, exist_ok=True)
        link = directory / name
        current = os.readlink(link) if link.is_symlink() else None
        if current != target:
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(target)
        return str(link)
    except OSError:
        return python


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def sentinel_command(
    *,
    python: str,
    script: Path,
    state_path: Path,
    sentinel_state_path: Path,
    poll_ms: int,
    log_path: Optional[Path] = None,
    spawn_on_start: bool = False,
    oneshot: bool = False,
    on_hours: Optional[tuple[str, str]] = None,
    on_days: Optional[Sequence[str]] = None,
) -> list[str]:
    argv = [
        str(python),
        str(script),
        "--state",
        str(state_path),
        "--sentinel-state",
        str(sentinel_state_path),
        "--poll-ms",
        str(int(poll_ms)),
    ]
    if log_path is not None:
        argv += ["--log", str(log_path)]
    if spawn_on_start:
        argv.append("--spawn-on-start")
    if oneshot:
        argv.append("--oneshot")
    if on_hours is not None:
        start, end = on_hours
        argv += ["--on-hours-start", str(start), "--on-hours-end", str(end)]
    if on_days is not None:
        argv += ["--on-days", ",".join(on_days)]
    return argv


def _quote_windows(argv: Sequence[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in argv)


# --------------------------------------------------------------------------
# macOS LaunchAgent
# --------------------------------------------------------------------------


def launch_agent_path(home: Optional[Path] = None) -> Path:
    base = Path.home() if home is None else Path(home)
    return base / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def launch_agent_plist(argv: Sequence[str]) -> dict[str, Any]:
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": list(argv),
        "RunAtLoad": True,
        # Restart on crash but honour a clean exit, so quitting Anki Media
        # Timer from the menu bar is not undone a second later.
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "LowPriorityIO": True,
    }


def _launchctl(args: list[str]) -> bool:
    try:
        result = subprocess.run(
            ["/bin/launchctl", *args],
            capture_output=True,
            text=True,
            timeout=_LAUNCHCTL_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _gui_domain() -> str:
    return f"gui/{os.getuid()}"


def _load_launch_agent(plist_path: Path) -> bool:
    # bootout first so a changed plist replaces the running definition.
    _launchctl(["bootout", f"{_gui_domain()}/{LAUNCH_AGENT_LABEL}"])
    if _launchctl(["bootstrap", _gui_domain(), str(plist_path)]):
        return True
    # Pre-Yosemite-style fallback, still accepted by current launchctl.
    return _launchctl(["load", "-w", str(plist_path)])


def _unload_launch_agent(plist_path: Path) -> None:
    if not _launchctl(["bootout", f"{_gui_domain()}/{LAUNCH_AGENT_LABEL}"]):
        _launchctl(["unload", "-w", str(plist_path)])


def install_launch_agent(argv: Sequence[str], *, home: Optional[Path] = None) -> bool:
    plist_path = launch_agent_path(home)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    desired = launch_agent_plist(argv)
    payload = plistlib.dumps(desired)
    unchanged = False
    try:
        unchanged = plist_path.read_bytes() == payload
    except OSError:
        unchanged = False
    if not unchanged:
        plist_path.write_bytes(payload)
    return _load_launch_agent(plist_path)


def remove_launch_agent(*, home: Optional[Path] = None) -> bool:
    plist_path = launch_agent_path(home)
    if plist_path.exists():
        _unload_launch_agent(plist_path)
        try:
            plist_path.unlink()
        except OSError:
            return False
    return True


def launch_agent_installed(*, home: Optional[Path] = None) -> bool:
    return launch_agent_path(home).is_file()


# --------------------------------------------------------------------------
# Windows Run key
# --------------------------------------------------------------------------


def _winreg() -> Any:
    import winreg

    return winreg


def install_run_key(argv: Sequence[str]) -> bool:
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(
                key, WINDOWS_RUN_VALUE, 0, winreg.REG_SZ, _quote_windows(argv)
            )
    except OSError:
        return False
    return True


def remove_run_key() -> bool:
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, WINDOWS_RUN_VALUE)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def run_key_installed() -> bool:
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, WINDOWS_RUN_VALUE)
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------
# Platform-dispatching front door
# --------------------------------------------------------------------------


def install(argv: Sequence[str], *, platform_name: Optional[str] = None) -> bool:
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        return install_launch_agent(argv)
    if platform_name == "win32":
        return install_run_key(argv)
    return False


def starts_immediately(platform_name: Optional[str] = None) -> bool:
    """True when installing also launches the job right away.

    launchd bootstraps a RunAtLoad agent on the spot; a Windows ``Run`` value
    is inert until the next sign-in.
    """
    return (platform_name or sys.platform) == "darwin"


def uninstall(*, platform_name: Optional[str] = None) -> bool:
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        return remove_launch_agent()
    if platform_name == "win32":
        return remove_run_key()
    return True


def is_installed(*, platform_name: Optional[str] = None) -> bool:
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        return launch_agent_installed()
    if platform_name == "win32":
        return run_key_installed()
    return False
