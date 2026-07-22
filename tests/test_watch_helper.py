# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_TESTS_DIR = Path(__file__).resolve().parent
_ROOT = _TESTS_DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from vendor_paths import resolve_vendor_dir  # noqa: E402

_vendor = resolve_vendor_dir(_ROOT)
if _vendor is not None and str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

# watch_helper is a standalone script (not an Anki package module).
import watch_helper  # noqa: E402
import watch_state as ws  # noqa: E402


class _FakeMedia:
    def __init__(self, *, playing: bool = False, supported: bool = True) -> None:
        self.supported = supported
        self.is_playing = playing
        self.pause_calls = 0
        self.play_calls = 0

    def get_now_playing(self) -> SimpleNamespace:
        return SimpleNamespace(
            supported=self.supported,
            is_playing=self.is_playing,
            title="T",
            artist="A",
            error="",
        )

    def play(self) -> bool:
        self.play_calls += 1
        self.is_playing = True
        return True

    def pause(self) -> bool:
        self.pause_calls += 1
        self.is_playing = False
        return True


def test_engine_drains_while_playing(tmp_path) -> None:
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 3,
            "prefs": {"enforce": True, "show_menubar_watch_time": True},
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(path, media=media)
    engine._last_drain_mono = 0.0
    should_quit, label, show_icon = engine.tick()
    assert should_quit is False
    assert show_icon is True
    state = ws.read_state(path)
    assert state["budget_seconds"] == 2
    assert state["is_playing"] is True
    assert label == "0:02"


def test_engine_pauses_when_budget_empty(tmp_path) -> None:
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 0,
            "prefs": {"enforce": True},
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(path, media=media)
    should_quit, _label, _show = engine.tick()
    assert should_quit is False
    assert media.pause_calls >= 1
    state = ws.read_state(path)
    assert state["paused_for_budget"] is True
    assert state["is_playing"] is False


def test_engine_quit_on_exit_sentinel(tmp_path) -> None:
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_exit(path)
    engine = watch_helper.MediaTimerEngine(path, media=_FakeMedia())
    should_quit, _label, _show = engine.tick()
    assert should_quit is True
