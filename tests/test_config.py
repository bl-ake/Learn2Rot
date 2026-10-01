# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from addon_loader import load_addon_module


def test_get_config_merges_defaults(mock_mw) -> None:
    config_mod = load_addon_module("config", "config.py")
    config = config_mod.get_config("Learn2Rot")
    assert config["seconds_per_card"] == config_mod.DEFAULTS["seconds_per_card"]
    assert config["config_version"] == config_mod.DEFAULTS["config_version"]
    assert config["media_mode"] == config_mod.MEDIA_MODE_SYSTEM
    assert config["auto_resume_on_budget"] is False
    assert config["show_budget_cubes"] is False
    assert config["cube_bounds_left_pct"] == 0
    assert config["cube_bounds_right_pct"] == 100
    assert config["show_overlay_timer"] is True
    assert config["system_media_poll_ms"] == 500
    assert config["show_menubar_watch_time"] is True
    assert config["quit_with_anki"] is True
    assert config["debug_logging"] is False
    assert config["max_budget_seconds"] == 0


def test_save_preferences_updates_only_preference_keys(mock_mw) -> None:
    config_mod = load_addon_module("config", "config.py")
    config_mod.save_preferences(
        "Learn2Rot", {"seconds_per_card": 20, "queue": [{"x": 1}]}
    )
    config = config_mod.get_config("Learn2Rot")
    assert config["seconds_per_card"] == 20
    assert "queue" not in config


def test_migrate_config_sets_version() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config({})
    assert migrated["config_version"] == config_mod.CONFIG_VERSION


def test_migrate_config_normalizes_media_mode_and_poll() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config(
        {"media_mode": "invalid", "system_media_poll_ms": 50}
    )
    assert migrated["media_mode"] == config_mod.MEDIA_MODE_SYSTEM
    assert migrated["system_media_poll_ms"] == 200
    migrated2 = config_mod.migrate_config(
        {"media_mode": "youtube", "system_media_poll_ms": 9000}
    )
    assert migrated2["media_mode"] == config_mod.MEDIA_MODE_YOUTUBE
    assert migrated2["system_media_poll_ms"] == 5000


def test_is_system_media_mode() -> None:
    config_mod = load_addon_module("config", "config.py")
    assert config_mod.is_system_media_mode({"media_mode": "system"}) is True
    assert config_mod.is_system_media_mode({"media_mode": "youtube"}) is False


def test_preference_defaults_subset() -> None:
    config_mod = load_addon_module("config", "config.py")
    defaults = config_mod.preference_defaults()
    assert set(defaults) <= config_mod.PREFERENCE_KEYS
    assert "seconds_per_card" in defaults
    assert "media_mode" in defaults
    assert "auto_resume_on_budget" in defaults
    assert defaults["show_budget_cubes"] is False
    assert defaults["cube_bounds_left_pct"] == 0
    assert defaults["cube_bounds_right_pct"] == 100
    assert defaults["show_overlay_timer"] is True
    assert defaults["show_menubar_watch_time"] is True
    assert defaults["quit_with_anki"] is True
    assert defaults["debug_logging"] is False
    assert defaults["max_budget_seconds"] == 0


def test_migrate_config_normalizes_show_budget_cubes() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config({"show_budget_cubes": 0})
    assert migrated["show_budget_cubes"] is False
    migrated2 = config_mod.migrate_config({})
    assert migrated2["show_budget_cubes"] is False


def test_migrate_config_normalizes_cube_bounds() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config(
        {"cube_bounds_left_pct": 80, "cube_bounds_right_pct": 20}
    )
    assert migrated["cube_bounds_left_pct"] == 80
    assert migrated["cube_bounds_right_pct"] == 85
    migrated2 = config_mod.migrate_config(
        {"cube_bounds_left_pct": -10, "cube_bounds_right_pct": 200}
    )
    assert migrated2["cube_bounds_left_pct"] == 0
    assert migrated2["cube_bounds_right_pct"] == 100
    assert config_mod.normalize_cube_bounds_pct("bad", None) == (0, 100)


def test_migrate_config_normalizes_show_overlay_timer() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config({"show_overlay_timer": 0})
    assert migrated["show_overlay_timer"] is False
    migrated2 = config_mod.migrate_config({})
    assert migrated2["show_overlay_timer"] is True


def test_migrate_config_normalizes_show_menubar_watch_time() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config({"show_menubar_watch_time": 0})
    assert migrated["show_menubar_watch_time"] is False
    migrated2 = config_mod.migrate_config({})
    assert migrated2["show_menubar_watch_time"] is True


def test_migrate_config_normalizes_quit_with_anki() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config({"quit_with_anki": 0})
    assert migrated["quit_with_anki"] is False
    migrated2 = config_mod.migrate_config({})
    assert migrated2["quit_with_anki"] is True


def test_migrate_config_renames_toolbar_key_to_menubar() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config({"show_toolbar_watch_time": False})
    assert migrated["show_menubar_watch_time"] is False
    assert "show_toolbar_watch_time" not in migrated


def test_migrate_config_normalizes_sentinel_at_login() -> None:
    config_mod = load_addon_module("config", "config.py")
    assert config_mod.migrate_config({})["sentinel_at_login"] is False
    assert config_mod.migrate_config({"sentinel_at_login": 1})["sentinel_at_login"] is True


def test_migrate_config_clamps_sentinel_poll_ms() -> None:
    config_mod = load_addon_module("config", "config.py")
    assert config_mod.migrate_config({})["sentinel_poll_ms"] == 3000
    assert config_mod.migrate_config({"sentinel_poll_ms": 10})["sentinel_poll_ms"] == 1000
    assert (
        config_mod.migrate_config({"sentinel_poll_ms": 10**6})["sentinel_poll_ms"]
        == 60000
    )
    assert config_mod.migrate_config({"sentinel_poll_ms": "x"})["sentinel_poll_ms"] == 3000


def test_sentinel_keys_are_saveable_preferences() -> None:
    config_mod = load_addon_module("config", "config.py")
    assert "sentinel_at_login" in config_mod.PREFERENCE_KEYS
    assert "open_timer_at_login" in config_mod.PREFERENCE_KEYS
    assert "sentinel_poll_ms" in config_mod.PREFERENCE_KEYS
    assert config_mod.preference_defaults()["sentinel_at_login"] is False
    assert config_mod.preference_defaults()["open_timer_at_login"] is False


def test_persist_in_background_forces_open_at_login() -> None:
    """A watcher that stays resident but never shows itself isn't useful —
    see the matching UI wiring in config_dialog._on_persist_toggled."""
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config(
        {"sentinel_at_login": True, "open_timer_at_login": False}
    )
    assert migrated["sentinel_at_login"] is True
    assert migrated["open_timer_at_login"] is True


def test_open_at_login_alone_does_not_force_persist() -> None:
    """The reverse implication does not hold: opening once at login is a
    valid standalone choice that must not silently enable the watcher."""
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config(
        {"sentinel_at_login": False, "open_timer_at_login": True}
    )
    assert migrated["open_timer_at_login"] is True
    assert migrated["sentinel_at_login"] is False


def test_open_at_login_default_is_off() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config({})
    assert migrated["open_timer_at_login"] is False
    assert migrated["sentinel_at_login"] is False


def test_on_hours_defaults() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config({})
    assert migrated["sentinel_on_hours_enabled"] is False
    assert migrated["sentinel_on_hours_start"] == "09:00"
    assert migrated["sentinel_on_hours_end"] == "21:00"


def test_on_hours_keys_are_saveable_preferences() -> None:
    config_mod = load_addon_module("config", "config.py")
    assert "sentinel_on_hours_enabled" in config_mod.PREFERENCE_KEYS
    assert "sentinel_on_hours_start" in config_mod.PREFERENCE_KEYS
    assert "sentinel_on_hours_end" in config_mod.PREFERENCE_KEYS
    assert config_mod.preference_defaults()["sentinel_on_hours_enabled"] is False


def test_migrate_config_normalizes_on_hours_enabled() -> None:
    config_mod = load_addon_module("config", "config.py")
    assert (
        config_mod.migrate_config({"sentinel_on_hours_enabled": 1})[
            "sentinel_on_hours_enabled"
        ]
        is True
    )


def test_migrate_config_normalizes_on_hours_times() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config(
        {"sentinel_on_hours_start": "7:5", "sentinel_on_hours_end": "22:00"}
    )
    assert migrated["sentinel_on_hours_start"] == "09:00"  # invalid -> default
    assert migrated["sentinel_on_hours_end"] == "22:00"

    valid = config_mod.migrate_config(
        {"sentinel_on_hours_start": "7:05", "sentinel_on_hours_end": "23:59"}
    )
    assert valid["sentinel_on_hours_start"] == "07:05"
    assert valid["sentinel_on_hours_end"] == "23:59"


def test_on_days_default_is_every_day() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config({})
    assert migrated["sentinel_on_days"] == list(config_mod.WEEKDAYS)
    assert config_mod.preference_defaults()["sentinel_on_days"] == list(
        config_mod.WEEKDAYS
    )


def test_on_days_key_is_a_saveable_preference() -> None:
    config_mod = load_addon_module("config", "config.py")
    assert "sentinel_on_days" in config_mod.PREFERENCE_KEYS


def test_migrate_config_normalizes_on_days() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config(
        {"sentinel_on_days": ["Mon", "wed", "bogus", "mon"]}
    )
    assert migrated["sentinel_on_days"] == ["mon", "wed"]

    empty = config_mod.migrate_config({"sentinel_on_days": []})
    assert empty["sentinel_on_days"] == []  # explicit "no days" preserved

    garbage = config_mod.migrate_config({"sentinel_on_days": "mon"})
    assert garbage["sentinel_on_days"] == list(config_mod.WEEKDAYS)
