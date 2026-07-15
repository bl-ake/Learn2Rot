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
    path = tmp_path / "ankitube_watch_state.json"
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
    path = tmp_path / "ankitube_watch_state.json"
    ws.write_exit(path)
    state = ws.read_state(path)
    assert state["exit"] is True


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
