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
    config = config_mod.get_config("AnkiTube")
    assert config["seconds_per_card"] == config_mod.DEFAULTS["seconds_per_card"]
    assert config["config_version"] == config_mod.DEFAULTS["config_version"]
    assert config["media_mode"] == config_mod.MEDIA_MODE_SYSTEM
    assert config["auto_resume_on_budget"] is False
    assert config["system_media_poll_ms"] == 500
    assert config["show_menubar_watch_time"] is True


def test_save_preferences_updates_only_preference_keys(mock_mw) -> None:
    config_mod = load_addon_module("config", "config.py")
    config_mod.save_preferences(
        "AnkiTube", {"seconds_per_card": 20, "queue": [{"x": 1}]}
    )
    config = config_mod.get_config("AnkiTube")
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
    assert defaults["show_menubar_watch_time"] is True


def test_migrate_config_normalizes_show_menubar_watch_time() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config({"show_menubar_watch_time": 0})
    assert migrated["show_menubar_watch_time"] is False
    migrated2 = config_mod.migrate_config({})
    assert migrated2["show_menubar_watch_time"] is True


def test_migrate_config_renames_toolbar_key_to_menubar() -> None:
    config_mod = load_addon_module("config", "config.py")
    migrated = config_mod.migrate_config({"show_toolbar_watch_time": False})
    assert migrated["show_menubar_watch_time"] is False
    assert "show_toolbar_watch_time" not in migrated
