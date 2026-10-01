# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sys
import time
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


def test_engine_drains_normally_with_no_cards_due_while_budget_remains(
    tmp_path,
) -> None:
    """require_cards_due only changes what happens once budget hits zero —
    while there's still banked time, cards_due has no effect at all."""
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 3,
            "cards_due": False,
            "prefs": {
                "enforce": True,
                "show_menubar_watch_time": True,
                "require_cards_due": True,
            },
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(path, media=media)
    engine._last_drain_mono = 0.0
    should_quit, _label, show_icon = engine.tick()
    assert should_quit is False
    assert show_icon is True  # still shown — nothing has run out yet
    state = ws.read_state(path)
    assert state["budget_seconds"] == 2  # drains exactly as if cards were due
    assert media.pause_calls == 0


def test_engine_backs_off_instead_of_pausing_once_budget_hits_zero(
    tmp_path,
) -> None:
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 0,
            "cards_due": False,
            "anki_alive": True,
            "prefs": {
                "enforce": True,
                "show_menubar_watch_time": True,
                "require_cards_due": True,
            },
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(path, media=media)
    should_quit, _label, show_icon = engine.tick()
    assert should_quit is False
    assert show_icon is False  # nothing to show/lock once backed off
    assert media.pause_calls == 0  # no point locking with nothing to review
    state = ws.read_state(path)
    assert state["paused_for_budget"] is False
    assert state["is_playing"] is True  # reports the real (untouched) state


def test_engine_still_pauses_at_zero_when_cards_are_due(tmp_path) -> None:
    """Sanity check: the gate never engages while cards ARE due, even with
    require_cards_due on — normal lockout-at-zero still applies."""
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 0,
            "cards_due": True,
            "prefs": {"enforce": True, "require_cards_due": True},
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(path, media=media)
    engine.tick()
    assert media.pause_calls >= 1
    state = ws.read_state(path)
    assert state["paused_for_budget"] is True
    assert state["is_playing"] is False


def test_engine_trusts_cards_due_false_until_predicted_time_arrives(tmp_path) -> None:
    """Anki recorded that cards won't be due again until some future time
    (day rollover or a learning card) before it closed — the gate should
    stay in effect while Anki is closed and that time hasn't arrived yet."""
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 0,
            "cards_due": False,
            "cards_due_at": int(time.time()) + 3600,
            "anki_alive": False,
            "prefs": {"enforce": True, "require_cards_due": True},
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(path, media=media)
    engine.tick()
    assert media.pause_calls == 0  # still backed off — prediction hasn't arrived
    state = ws.read_state(path)
    assert state["paused_for_budget"] is False


def test_engine_resumes_once_predicted_due_time_passes_while_anki_closed(
    tmp_path,
) -> None:
    """Once the recorded next-due time is in the past, a leftover False is
    stale rather than still accurate — enforcement should resume even
    without Anki reopening to confirm it."""
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 0,
            "cards_due": False,
            "cards_due_at": int(time.time()) - 60,
            "anki_alive": False,
            "prefs": {"enforce": True, "require_cards_due": True},
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(path, media=media)
    engine.tick()
    assert media.pause_calls >= 1  # predicted time has passed — enforce
    state = ws.read_state(path)
    assert state["paused_for_budget"] is True
    assert state["is_playing"] is False


def test_engine_fails_open_with_no_recorded_due_prediction(tmp_path) -> None:
    """cards_due_at=0 (never recorded, e.g. an older state file) is treated
    like an already-passed prediction rather than trusted indefinitely."""
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 0,
            "cards_due": False,
            "anki_alive": False,
            "prefs": {"enforce": True, "require_cards_due": True},
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(path, media=media)
    engine.tick()
    assert media.pause_calls >= 1
    assert ws.read_state(path)["paused_for_budget"] is True


def test_engine_trusts_fresh_cards_due_false_while_anki_is_alive(tmp_path) -> None:
    """A future cards_due_at must not matter while Anki is alive and
    actively reporting False right now — that's always the freshest signal."""
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 0,
            "cards_due": False,
            "cards_due_at": int(time.time()) - 60,
            "anki_alive": True,
            "prefs": {"enforce": True, "require_cards_due": True},
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(path, media=media)
    engine.tick()
    assert media.pause_calls == 0
    assert ws.read_state(path)["paused_for_budget"] is False


def test_engine_still_pauses_at_zero_when_setting_off(tmp_path) -> None:
    """require_cards_due defaults off — cards_due=False must not change
    anything for people who haven't opted in, even at zero budget."""
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 0,
            "cards_due": False,
            "prefs": {"enforce": True, "require_cards_due": False},
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(path, media=media)
    should_quit, _label, show_icon = engine.tick()
    assert should_quit is False
    assert show_icon is True
    assert media.pause_calls >= 1
    state = ws.read_state(path)
    assert state["paused_for_budget"] is True


def test_engine_pauses_at_the_exact_tick_budget_reaches_zero(tmp_path) -> None:
    """The gate must also cover the mid-tick 1 -> 0 drain crossing, not just
    budget that was already at zero going in."""
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 1,
            "cards_due": False,
            "anki_alive": True,
            "prefs": {"enforce": True, "require_cards_due": True},
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(path, media=media)
    engine._last_drain_mono = 0.0
    engine.tick()
    assert media.pause_calls == 0
    state = ws.read_state(path)
    assert state["budget_seconds"] == 0
    assert state["paused_for_budget"] is False
    assert state["is_playing"] is True


def test_engine_resumes_enforcement_once_cards_become_due_again(tmp_path) -> None:
    """Once Anki flips cards_due back to True, the very next tick resumes
    normal lockout-at-zero — no restart of the helper needed."""
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 0,
            "cards_due": False,
            "anki_alive": True,
            "prefs": {"enforce": True, "require_cards_due": True},
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(path, media=media)
    engine.tick()
    assert media.pause_calls == 0  # backed off

    def mark_due(state: dict) -> None:
        state["cards_due"] = True

    ws.update_state(path, mark_due)
    engine.tick()
    assert media.pause_calls >= 1  # enforcement resumes
    assert ws.read_state(path)["paused_for_budget"] is True


def test_engine_credits_lift_the_gate_and_resume_draining(tmp_path) -> None:
    """Reviewing cards still banks time while backed off, and as soon as it
    pushes budget back above zero, normal draining resumes — same tick."""
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 0,
            "credits": 15,
            "cards_due": False,
            "prefs": {"enforce": True, "require_cards_due": True},
        },
    )
    engine = watch_helper.MediaTimerEngine(path, media=_FakeMedia(playing=True))
    engine.tick()
    state = ws.read_state(path)
    assert state["credits"] == 0
    assert state["budget_seconds"] == 14  # 15 credited, then 1 drained
    assert state["is_playing"] is True


def _fake_now(*, hour: int, minute: int = 0, wday: int = 2) -> SimpleNamespace:
    """wday follows tm_wday: Mon=0..Sun=6 (default Wed, an on-day in tests)."""
    return SimpleNamespace(tm_wday=wday, tm_hour=hour, tm_min=minute)


def test_engine_backs_off_outside_on_hours_even_with_budget_left(tmp_path) -> None:
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 30,
            "prefs": {
                "enforce": True,
                "show_menubar_watch_time": True,
                "on_hours_enabled": True,
                "on_hours_start": "09:00",
                "on_hours_end": "21:00",
            },
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(
        path, media=media, now_provider=lambda: _fake_now(hour=22)
    )
    engine._last_drain_mono = 0.0
    should_quit, _label, show_icon = engine.tick()
    assert should_quit is False
    assert show_icon is False
    assert media.pause_calls == 0  # left alone, not locked
    state = ws.read_state(path)
    assert state["budget_seconds"] == 30  # frozen, not drained
    assert state["paused_for_budget"] is False


def test_engine_enforces_normally_inside_on_hours(tmp_path) -> None:
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 3,
            "prefs": {
                "enforce": True,
                "show_menubar_watch_time": True,
                "on_hours_enabled": True,
                "on_hours_start": "09:00",
                "on_hours_end": "21:00",
            },
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(
        path, media=media, now_provider=lambda: _fake_now(hour=12)
    )
    engine._last_drain_mono = 0.0
    should_quit, _label, show_icon = engine.tick()
    assert should_quit is False
    assert show_icon is True
    state = ws.read_state(path)
    assert state["budget_seconds"] == 2  # drains as usual inside the window


def test_engine_resumes_once_on_hours_window_reopens(tmp_path) -> None:
    """No restart needed: the very next tick after the window reopens picks
    enforcement back up on its own."""
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 3,
            "prefs": {
                "enforce": True,
                "on_hours_enabled": True,
                "on_hours_start": "09:00",
                "on_hours_end": "21:00",
            },
        },
    )
    media = _FakeMedia(playing=True)
    hour = {"value": 22}
    engine = watch_helper.MediaTimerEngine(
        path, media=media, now_provider=lambda: _fake_now(hour=hour["value"])
    )
    engine._last_drain_mono = 0.0
    engine.tick()
    assert ws.read_state(path)["budget_seconds"] == 3  # backed off, untouched

    hour["value"] = 12
    engine.tick()
    assert ws.read_state(path)["budget_seconds"] == 2  # enforcement resumed


def test_engine_ignores_on_hours_window_on_an_unchecked_day(tmp_path) -> None:
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 30,
            "prefs": {
                "enforce": True,
                "on_hours_enabled": True,
                "on_hours_start": "09:00",
                "on_hours_end": "21:00",
                "on_days": ["mon", "tue"],
            },
        },
    )
    media = _FakeMedia(playing=True)
    # Midday but Wednesday (2), which isn't in on_days — backs off anyway.
    engine = watch_helper.MediaTimerEngine(
        path, media=media, now_provider=lambda: _fake_now(hour=12, wday=2)
    )
    engine._last_drain_mono = 0.0
    should_quit, _label, show_icon = engine.tick()
    assert should_quit is False
    assert show_icon is False
    state = ws.read_state(path)
    assert state["budget_seconds"] == 30


def test_engine_ignores_on_hours_when_setting_off(tmp_path) -> None:
    """on_hours_enabled defaults off — an out-of-range clock must not change
    anything for people who haven't opted in."""
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 3,
            "prefs": {"enforce": True, "on_hours_enabled": False},
        },
    )
    media = _FakeMedia(playing=True)
    engine = watch_helper.MediaTimerEngine(
        path, media=media, now_provider=lambda: _fake_now(hour=22)
    )
    engine._last_drain_mono = 0.0
    should_quit, _label, show_icon = engine.tick()
    assert should_quit is False
    assert show_icon is True
    state = ws.read_state(path)
    assert state["budget_seconds"] == 2


def test_tray_icon_image_writes_ico_without_pillow(tmp_path) -> None:
    image = watch_helper._make_tray_icon_image()
    out = tmp_path / "tray.ico"
    with out.open("wb") as handle:
        image.save(handle, format="ICO")
    data = out.read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00"  # ICO header
    assert len(data) == len(watch_helper._TRAY_ICO_BYTES)
