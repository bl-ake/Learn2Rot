# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from addon_loader import load_addon_module


def test_create_media_controller_unsupported() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    controller = media_mod.create_media_controller(system="Windows")
    info = controller.get_now_playing()
    assert info.supported is False
    assert "macOS" in info.display_label()
    assert controller.play() is False
    assert controller.pause() is False


def test_create_media_controller_darwin() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    controller = media_mod.create_media_controller(system="Darwin")
    assert isinstance(controller, media_mod.DarwinMediaController)


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
