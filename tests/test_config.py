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


def test_preference_defaults_subset() -> None:
    config_mod = load_addon_module("config", "config.py")
    defaults = config_mod.preference_defaults()
    assert set(defaults) <= config_mod.PREFERENCE_KEYS
    assert "seconds_per_card" in defaults
