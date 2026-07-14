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

    controller = media_mod.DarwinMediaController(runner=runner)
    info = controller.get_now_playing()
    assert info.title == "Song"
    assert info.artist == "Artist"
    assert info.is_playing is True
    assert info.display_label() == "Song — Artist"


def test_darwin_pause_sends_command() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")
    seen: list[list[str]] = []

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    controller = media_mod.DarwinMediaController(runner=runner)
    assert controller.pause() is True
    assert seen
    assert seen[0][-1] == "1"


def test_darwin_timeout_returns_cached_not_playing() -> None:
    media_mod = load_addon_module("media_control", "media_control.py")

    def runner(_args: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=2)

    controller = media_mod.DarwinMediaController(runner=runner)
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
