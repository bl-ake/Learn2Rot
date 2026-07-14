# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""AnkiTube configuration defaults and accessors."""

from __future__ import annotations

from typing import Any

from aqt import mw

from ._version import __version__  # noqa: F401 — exposed for packaging

CONFIG_VERSION = 1

PREFERENCE_KEYS = frozenset(
    {
        "config_version",
        "seconds_per_card",
        "starting_budget_seconds",
        "max_budget_seconds",
        "dock_area",
        "show_dock_in_review_only",
        "youtube_show_controls",
        "youtube_show_fullscreen",
        "dock_show_playback_buttons",
        "debug_logging",
    }
)

RUNTIME_KEYS = frozenset(
    {
        "budget_seconds",
        "queue",
        "current_index",
        "positions",
        "lifetime_earned_seconds",
        "dock_panel_sizes",
        "queue_visible",
        "dock_visible",
        "dock_width",
    }
)

DEFAULTS: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "seconds_per_card": 15,
    "starting_budget_seconds": 0,
    "max_budget_seconds": 600,
    "dock_area": "right",
    "show_dock_in_review_only": False,
    "youtube_show_controls": True,
    "youtube_show_fullscreen": True,
    "dock_show_playback_buttons": True,
    "debug_logging": False,
}


def get_config(addon_module: str) -> dict[str, Any]:
    stored = mw.addonManager.getConfig(addon_module) or {}
    merged = dict(DEFAULTS)
    merged.update(stored)
    return merged


def write_config(addon_module: str, config: dict[str, Any]) -> None:
    mw.addonManager.writeConfig(addon_module, config)


def migrate_config(config: dict[str, Any]) -> dict[str, Any]:
    version = int(config.get("config_version", 0) or 0)
    if version < CONFIG_VERSION:
        config["config_version"] = CONFIG_VERSION
    return config


def save_preferences(addon_module: str, preferences: dict[str, Any]) -> None:
    config = get_config(addon_module)
    for key, value in preferences.items():
        if key in PREFERENCE_KEYS:
            config[key] = value
    config = migrate_config(config)
    write_config(addon_module, config)


def preference_defaults() -> dict[str, Any]:
    return {key: DEFAULTS[key] for key in PREFERENCE_KEYS if key in DEFAULTS}
