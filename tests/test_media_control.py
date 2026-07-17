# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from addon_loader import load_addon_module


def test_create_media_controller_unsupported() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    controller = media_mod.create_media_controller(system="Linux")
    info = controller.get_now_playing()
    assert info.supported is False
    assert "macOS" in info.display_label()
    assert "Windows" in info.display_label()
    assert controller.play() is False
    assert controller.pause() is False


def test_create_media_controller_darwin() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    controller = media_mod.create_media_controller(system="Darwin")
    assert isinstance(controller, media_mod.DarwinMediaController)


def test_create_media_controller_windows() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    controller = media_mod.create_media_controller(system="Windows")
    assert isinstance(controller, media_mod.WindowsMediaController)
    controller_win32 = media_mod.create_media_controller(system="win32")
    assert isinstance(controller_win32, media_mod.WindowsMediaController)


def test_darwin_get_now_playing_parses_json() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    payload = {"title": "Song", "artist": "Artist", "isPlaying": True}

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    controller = media_mod.DarwinMediaController(
        runner=runner, command_sender=lambda _cmd: True
    )
    info = controller.get_now_playing()
    assert info.title == "Song"
    assert info.artist == "Artist"
    assert info.is_playing is True
    assert info.display_label() == "Song — Artist"


def test_darwin_pause_sends_command() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    seen: list[int] = []

    def sender(command: int) -> bool:
        seen.append(command)
        return True

    controller = media_mod.DarwinMediaController(
        runner=lambda _args: SimpleNamespace(returncode=0, stdout="{}", stderr=""),
        command_sender=sender,
    )
    assert controller.pause() is True
    assert seen == [media_mod._CMD_PAUSE]


def test_darwin_pause_falls_back_to_stop() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    seen: list[int] = []

    def sender(command: int) -> bool:
        seen.append(command)
        return command == media_mod._CMD_STOP

    controller = media_mod.DarwinMediaController(command_sender=sender)
    assert controller.pause() is True
    assert seen == [media_mod._CMD_PAUSE, media_mod._CMD_STOP]


def test_darwin_timeout_returns_cached_not_playing() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")

    def runner(_args: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=2)

    controller = media_mod.DarwinMediaController(
        runner=runner, command_sender=lambda _cmd: True
    )
    controller._last = media_mod.NowPlayingInfo(title="Cached", artist="Art")
    info = controller.get_now_playing()
    assert info.title == "Cached"
    assert info.is_playing is False
    assert info.error


def test_now_playing_display_label_empty() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    assert media_mod.NowPlayingInfo().display_label() == "Nothing playing"
    assert (
        media_mod.NowPlayingInfo(is_playing=True).display_label() == "Playing"
    )


class _FakeProps:
    def __init__(self, title: str = "", artist: str = "") -> None:
        self.title = title
        self.artist = artist


class _FakePlayback:
    def __init__(self, status: int) -> None:
        self.playback_status = status


class _FakeSession:
    def __init__(
        self,
        *,
        title: str = "",
        artist: str = "",
        status: int = 4,
        pause_ok: bool = True,
        stop_ok: bool = False,
    ) -> None:
        self._title = title
        self._artist = artist
        self._status = status
        self.pause_calls = 0
        self.stop_calls = 0
        self.play_calls = 0
        self.toggle_calls = 0
        self._pause_ok = pause_ok
        self._stop_ok = stop_ok

    def get_playback_info(self) -> _FakePlayback:
        return _FakePlayback(self._status)

    async def try_get_media_properties_async(self) -> _FakeProps:
        return _FakeProps(self._title, self._artist)

    async def try_pause_async(self) -> bool:
        self.pause_calls += 1
        return self._pause_ok

    async def try_stop_async(self) -> bool:
        self.stop_calls += 1
        return self._stop_ok

    async def try_play_async(self) -> bool:
        self.play_calls += 1
        return True

    async def try_toggle_play_pause_async(self) -> bool:
        self.toggle_calls += 1
        return True


class _FakeManager:
    def __init__(
        self,
        current: Optional[_FakeSession] = None,
        sessions: Optional[list[_FakeSession]] = None,
    ) -> None:
        self._current = current
        self._sessions = sessions if sessions is not None else (
            [current] if current is not None else []
        )

    def get_current_session(self) -> Optional[_FakeSession]:
        return self._current

    def get_sessions(self) -> list[_FakeSession]:
        return list(self._sessions)


def _async_runner(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def test_windows_get_now_playing() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    session = _FakeSession(title="Track", artist="Band", status=4)
    manager = _FakeManager(current=session)

    controller = media_mod.WindowsMediaController(
        backend_factory=lambda: manager,
        async_runner=_async_runner,
    )
    info = controller.get_now_playing()
    assert info.title == "Track"
    assert info.artist == "Band"
    assert info.is_playing is True
    assert info.display_label() == "Track — Band"


def test_windows_get_now_playing_nothing() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    manager = _FakeManager(current=None, sessions=[])
    controller = media_mod.WindowsMediaController(
        backend_factory=lambda: manager,
        async_runner=_async_runner,
    )
    info = controller.get_now_playing()
    assert info.is_playing is False
    assert info.title == ""


def test_windows_pause_all_playing_sessions() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    playing = _FakeSession(title="A", status=4)
    paused = _FakeSession(title="B", status=5)  # not playing
    also_playing = _FakeSession(title="C", status=4)
    manager = _FakeManager(
        current=playing,
        sessions=[playing, paused, also_playing],
    )
    controller = media_mod.WindowsMediaController(
        backend_factory=lambda: manager,
        async_runner=_async_runner,
    )
    assert controller.pause() is True
    assert playing.pause_calls == 1
    assert also_playing.pause_calls == 1
    assert paused.pause_calls == 0


def test_windows_pause_falls_back_to_stop() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    session = _FakeSession(status=4, pause_ok=False, stop_ok=True)
    manager = _FakeManager(current=session, sessions=[session])
    controller = media_mod.WindowsMediaController(
        backend_factory=lambda: manager,
        async_runner=_async_runner,
    )
    assert controller.pause() is True
    assert session.pause_calls == 1
    assert session.stop_calls == 1


def test_windows_play_and_toggle_current_session() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    session = _FakeSession(status=5)
    manager = _FakeManager(current=session, sessions=[session])
    controller = media_mod.WindowsMediaController(
        backend_factory=lambda: manager,
        async_runner=_async_runner,
    )
    assert controller.play() is True
    assert session.play_calls == 1
    assert controller.toggle() is True
    assert session.toggle_calls == 1
