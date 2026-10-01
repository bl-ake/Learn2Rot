# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from addon_loader import load_addon_module


def test_normalize_and_format() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.format_seconds(65) == "1:05"
    assert ws.format_seconds(3661) == "1:01:01"
    state = ws.normalize_state({"budget_seconds": 90, "credits": 5})
    assert state["budget_seconds"] == 90
    assert state["credits"] == 5
    assert state["prefs"]["quit_with_anki"] is True


def test_apply_pending_adjustments_clamps() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    state = ws.normalize_state(
        {
            "budget_seconds": 100,
            "credits": 50,
            "subtracts": 0,
            "prefs": {"max_budget_seconds": 120},
        }
    )
    applied = ws.apply_pending_adjustments(state)
    assert applied["budget_seconds"] == 120
    assert applied["credits"] == 0
    assert applied["subtracts"] == 0
    assert applied["label"] == "2:00"

    with_subtract = ws.apply_pending_adjustments(
        {
            "budget_seconds": 100,
            "credits": 50,
            "subtracts": 20,
            "prefs": {"max_budget_seconds": 120},
        }
    )
    # credits then subtracts: min(100+50, 120) - 20 = 100
    assert with_subtract["budget_seconds"] == 100


def test_apply_pending_adjustments_unlimited_max() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    applied = ws.apply_pending_adjustments(
        {
            "budget_seconds": 100,
            "credits": 500,
            "subtracts": 0,
            "prefs": {"max_budget_seconds": 0},
        }
    )
    assert applied["budget_seconds"] == 600
    assert applied["prefs"]["max_budget_seconds"] == 0


def test_drain_one_second() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    remaining, has_time = ws.drain_one_second(3)
    assert remaining == 2
    assert has_time is True
    remaining, has_time = ws.drain_one_second(1)
    assert remaining == 0
    assert has_time is False
    remaining, has_time = ws.drain_one_second(0)
    assert remaining == 0
    assert has_time is False


def test_update_state_credits_roundtrip(tmp_path) -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(path, {"budget_seconds": 10, "credits": 0})

    def add_credit(state: dict) -> None:
        state["credits"] = int(state.get("credits", 0) or 0) + 15

    ws.update_state(path, add_credit)
    mid = ws.read_state(path)
    assert mid["credits"] == 15
    assert mid["budget_seconds"] == 10

    def daemon_apply(state: dict) -> None:
        applied = ws.apply_pending_adjustments(state)
        state.clear()
        state.update(applied)

    ws.update_state(path, daemon_apply)
    final = ws.read_state(path)
    assert final["budget_seconds"] == 25
    assert final["credits"] == 0


def test_write_exit_sentinel(tmp_path) -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_exit(path)
    state = ws.read_state(path)
    assert state["exit"] is True


def test_write_exit_preserves_earned_budget(tmp_path) -> None:
    """Regression: quitting the helper must not zero out earned watch time.

    write_exit() used to overwrite the whole file with the bare "__EXIT__"
    string, which read_state() turns into a fresh default_state() (budget
    0). That was invisible while Anki stayed open (it reseeds from its own
    config on next start), but a sentinel-revived helper with no Anki around
    would read the wiped budget as 0:00.
    """
    ws = load_addon_module("watch_state", "watch_state.py")
    path = tmp_path / "learn2rot_watch_state.json"
    ws.write_state(
        path,
        {
            "budget_seconds": 340,
            "prefs": {**ws.DEFAULT_PREFS, "max_budget_seconds": 600},
            "pid": 4242,
        },
    )
    ws.write_exit(path)
    state = ws.read_state(path)
    assert state["exit"] is True
    assert state["budget_seconds"] == 340
    assert state["prefs"]["max_budget_seconds"] == 600


def test_read_state_still_understands_legacy_exit_sentinel_files(tmp_path) -> None:
    """Old, already-corrupted files (bare "__EXIT__") must not crash."""
    ws = load_addon_module("watch_state", "watch_state.py")
    path = tmp_path / "learn2rot_watch_state.json"
    path.write_text(ws.EXIT_SENTINEL + "\n", encoding="utf-8")
    state = ws.read_state(path)
    assert state["exit"] is True
    assert state["budget_seconds"] == 0


def test_prefs_from_config() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    prefs = ws.prefs_from_config(
        {
            "system_media_poll_ms": 100,
            "quit_with_anki": False,
            "show_menubar_watch_time": False,
            "max_budget_seconds": 300,
            "auto_resume_on_budget": True,
        },
        enforce=True,
    )
    assert prefs["system_media_poll_ms"] == 200
    assert prefs["quit_with_anki"] is False
    assert prefs["enforce"] is True
    assert prefs["show_menubar_watch_time"] is False
    assert prefs["max_budget_seconds"] == 300


def test_prefs_from_config_default_max_unlimited() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    prefs = ws.prefs_from_config({}, enforce=True)
    assert prefs["max_budget_seconds"] == 0
    assert prefs["quit_with_anki"] is True


def test_pid_is_alive_rejects_invalid() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.pid_is_alive(0) is False
    assert ws.pid_is_alive(-1) is False


def test_terminate_pid_noop_for_invalid() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    ws.terminate_pid(0)
    ws.terminate_pid(-5)


def test_anki_pid_survives_normalization() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.default_state()["anki_pid"] == 0
    assert ws.normalize_state({"anki_pid": 4242})["anki_pid"] == 4242
    assert ws.normalize_state({"anki_pid": "nope"})["anki_pid"] == 0
    assert ws.normalize_state({"anki_pid": -7})["anki_pid"] == 0


def test_sentinel_state_roundtrip(tmp_path) -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    path = tmp_path / ws.SENTINEL_STATE_FILENAME
    assert ws.read_sentinel_state(path) == ws.default_sentinel_state()

    ws.write_sentinel_state(path, {"pid": 99, "exit": True, "poll_ms": 5000})
    state = ws.read_sentinel_state(path)
    assert state == {"pid": 99, "exit": True, "poll_ms": 5000}


def test_sentinel_state_lives_apart_from_watch_state() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    # The watch state file gets replaced wholesale by __EXIT__, which would
    # destroy the sentinel's own pid/exit bookkeeping.
    assert ws.SENTINEL_STATE_FILENAME != ws.STATE_FILENAME


def test_sentinel_state_ignores_garbage(tmp_path) -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    path = tmp_path / ws.SENTINEL_STATE_FILENAME
    path.write_text("{not json", encoding="utf-8")
    assert ws.read_sentinel_state(path) == ws.default_sentinel_state()
    path.write_text("", encoding="utf-8")
    assert ws.read_sentinel_state(path) == ws.default_sentinel_state()


def test_clamp_sentinel_poll_ms() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.clamp_sentinel_poll_ms(10) == ws.MIN_SENTINEL_POLL_MS
    assert ws.clamp_sentinel_poll_ms(10**9) == ws.MAX_SENTINEL_POLL_MS
    assert ws.clamp_sentinel_poll_ms("junk") == ws.DEFAULT_SENTINEL_POLL_MS
    assert ws.clamp_sentinel_poll_ms(4000) == 4000


def test_update_sentinel_state_is_read_modify_write(tmp_path) -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    path = tmp_path / ws.SENTINEL_STATE_FILENAME
    ws.update_sentinel_state(path, lambda state: state.update({"pid": 11}))
    ws.update_sentinel_state(path, lambda state: state.update({"poll_ms": 2000}))
    state = ws.read_sentinel_state(path)
    assert state["pid"] == 11
    assert state["poll_ms"] == 2000


def test_request_sentinel_exit_preserves_pid(tmp_path) -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    path = tmp_path / ws.SENTINEL_STATE_FILENAME
    ws.write_sentinel_state(path, {"pid": 77})
    ws.request_sentinel_exit(path)
    state = ws.read_sentinel_state(path)
    assert state["exit"] is True
    assert state["pid"] == 77


def test_default_state_fails_open_on_cards_due() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.default_state()["cards_due"] is True
    assert ws.default_state()["cards_due_at"] == 0


def test_normalize_state_coerces_cards_due() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.normalize_state({"cards_due": False})["cards_due"] is False
    assert ws.normalize_state({"cards_due": True})["cards_due"] is True
    # Missing/garbage falls open rather than silently suppressing the timer.
    assert ws.normalize_state({})["cards_due"] is True
    assert ws.normalize_state({"cards_due": "nonsense"})["cards_due"] is True


def test_normalize_state_coerces_cards_due_at() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.normalize_state({"cards_due_at": 1700000000})["cards_due_at"] == 1700000000
    assert ws.normalize_state({})["cards_due_at"] == 0
    assert ws.normalize_state({"cards_due_at": -5})["cards_due_at"] == 0
    assert ws.normalize_state({"cards_due_at": "nonsense"})["cards_due_at"] == 0


def test_default_prefs_require_cards_due_off() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.DEFAULT_PREFS["require_cards_due"] is False


def test_normalize_prefs_coerces_require_cards_due() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.normalize_prefs({"require_cards_due": True})["require_cards_due"] is True
    assert ws.normalize_prefs({})["require_cards_due"] is False
    assert ws.normalize_prefs(None)["require_cards_due"] is False


def test_prefs_from_config_includes_require_cards_due() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    prefs = ws.prefs_from_config({"require_cards_due": True}, enforce=True)
    assert prefs["require_cards_due"] is True
    prefs_default = ws.prefs_from_config({}, enforce=True)
    assert prefs_default["require_cards_due"] is False


def test_default_prefs_on_hours_off() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.DEFAULT_PREFS["on_hours_enabled"] is False
    assert ws.DEFAULT_PREFS["on_hours_start"] == "09:00"
    assert ws.DEFAULT_PREFS["on_hours_end"] == "21:00"
    assert ws.DEFAULT_PREFS["on_days"] == list(ws.WEEKDAYS)


def test_normalize_prefs_coerces_on_hours() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    prefs = ws.normalize_prefs(
        {
            "on_hours_enabled": True,
            "on_hours_start": "7:30",
            "on_hours_end": "22",
            "on_days": ["mon", "wed", "bogus"],
        }
    )
    assert prefs["on_hours_enabled"] is True
    assert prefs["on_hours_start"] == "07:30"
    # Garbage falls back to the default rather than crashing the helper.
    assert prefs["on_hours_end"] == "21:00"
    assert prefs["on_days"] == ["mon", "wed"]
    assert ws.normalize_prefs({})["on_hours_enabled"] is False
    assert ws.normalize_prefs(None)["on_hours_enabled"] is False


def test_prefs_from_config_includes_on_hours() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    prefs = ws.prefs_from_config(
        {
            "sentinel_on_hours_enabled": True,
            "sentinel_on_hours_start": "10:00",
            "sentinel_on_hours_end": "18:00",
            "sentinel_on_days": ["tue", "thu"],
        },
        enforce=True,
    )
    assert prefs["on_hours_enabled"] is True
    assert prefs["on_hours_start"] == "10:00"
    assert prefs["on_hours_end"] == "18:00"
    assert prefs["on_days"] == ["tue", "thu"]
    prefs_default = ws.prefs_from_config({}, enforce=True)
    assert prefs_default["on_hours_enabled"] is False


def test_normalize_hhmm_accepts_valid_times() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.normalize_hhmm("9:00") == "09:00"
    assert ws.normalize_hhmm("09:00") == "09:00"
    assert ws.normalize_hhmm("23:59") == "23:59"
    assert ws.normalize_hhmm("00:00") == "00:00"


def test_normalize_hhmm_rejects_garbage() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.normalize_hhmm("24:00") is None
    assert ws.normalize_hhmm("9:60") is None
    assert ws.normalize_hhmm("not a time") is None
    assert ws.normalize_hhmm("") is None
    assert ws.normalize_hhmm(None) is None


def test_hhmm_to_minutes() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.hhmm_to_minutes("00:00") == 0
    assert ws.hhmm_to_minutes("09:30") == 570
    assert ws.hhmm_to_minutes("23:59") == 1439


def test_is_within_on_hours_same_day_window() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    start, end = "09:00", "21:00"
    assert ws.is_within_on_hours(ws.hhmm_to_minutes("09:00"), start, end) is True
    assert ws.is_within_on_hours(ws.hhmm_to_minutes("12:00"), start, end) is True
    assert ws.is_within_on_hours(ws.hhmm_to_minutes("20:59"), start, end) is True
    assert ws.is_within_on_hours(ws.hhmm_to_minutes("21:00"), start, end) is False
    assert ws.is_within_on_hours(ws.hhmm_to_minutes("08:59"), start, end) is False
    assert ws.is_within_on_hours(ws.hhmm_to_minutes("00:00"), start, end) is False


def test_is_within_on_hours_wraps_past_midnight() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    start, end = "22:00", "06:00"
    assert ws.is_within_on_hours(ws.hhmm_to_minutes("23:00"), start, end) is True
    assert ws.is_within_on_hours(ws.hhmm_to_minutes("00:30"), start, end) is True
    assert ws.is_within_on_hours(ws.hhmm_to_minutes("05:59"), start, end) is True
    assert ws.is_within_on_hours(ws.hhmm_to_minutes("06:00"), start, end) is False
    assert ws.is_within_on_hours(ws.hhmm_to_minutes("12:00"), start, end) is False


def test_is_within_on_hours_equal_bounds_means_unrestricted() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.is_within_on_hours(ws.hhmm_to_minutes("03:00"), "09:00", "09:00") is True


def test_weekdays_order_matches_tm_wday() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.WEEKDAYS == ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def test_normalize_on_days_defaults_to_every_day_when_not_a_list() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.normalize_on_days(None) == list(ws.WEEKDAYS)
    assert ws.normalize_on_days("mon") == list(ws.WEEKDAYS)
    assert ws.normalize_on_days(42) == list(ws.WEEKDAYS)


def test_normalize_on_days_preserves_explicit_empty_selection() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.normalize_on_days([]) == []


def test_normalize_on_days_filters_dedupes_and_orders() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.normalize_on_days(["Wed", "mon", "wed", "bogus", "FRI"]) == [
        "mon",
        "wed",
        "fri",
    ]


def test_is_on_day_checks_membership() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    on_days = ["mon", "wed", "fri"]
    assert ws.is_on_day(0, on_days) is True  # Monday
    assert ws.is_on_day(1, on_days) is False  # Tuesday
    assert ws.is_on_day(6, on_days) is False  # Sunday


def test_is_on_day_empty_selection_blocks_every_day() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    for weekday in range(7):
        assert ws.is_on_day(weekday, []) is False


def test_is_on_day_out_of_range_fails_open() -> None:
    ws = load_addon_module("watch_state", "watch_state.py")
    assert ws.is_on_day(-1, []) is True
    assert ws.is_on_day(7, []) is True
