# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Learn2Rot configuration defaults and accessors."""

from __future__ import annotations

from typing import Any

from aqt import mw

from ._version import __version__  # noqa: F401 — exposed for packaging

CONFIG_VERSION = 4

MEDIA_MODE_SYSTEM = "system"
MEDIA_MODE_YOUTUBE = "youtube"

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
        "show_menubar_watch_time",
        "quit_with_anki",
        "debug_logging",
        "media_mode",
        "auto_resume_on_budget",
        "show_budget_cubes",
        "cube_bounds_left_pct",
        "cube_bounds_right_pct",
        "show_overlay_timer",
        "system_media_poll_ms",
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
    "show_menubar_watch_time": True,
    "quit_with_anki": True,
    "debug_logging": False,
    "media_mode": MEDIA_MODE_SYSTEM,
    "auto_resume_on_budget": False,
    "show_budget_cubes": True,
    "cube_bounds_left_pct": 0,
    "cube_bounds_right_pct": 100,
    "show_overlay_timer": True,
    "system_media_poll_ms": 500,
}


def get_config(addon_module: str) -> dict[str, Any]:
    stored = mw.addonManager.getConfig(addon_module) or {}
    merged = dict(DEFAULTS)
    merged.update(stored)
    return migrate_config(merged)


def write_config(addon_module: str, config: dict[str, Any]) -> None:
    mw.addonManager.writeConfig(addon_module, config)


def migrate_config(config: dict[str, Any]) -> dict[str, Any]:
    version = int(config.get("config_version", 0) or 0)
    if version < CONFIG_VERSION:
        config["config_version"] = CONFIG_VERSION
    mode = str(config.get("media_mode", MEDIA_MODE_SYSTEM)).lower()
    if mode not in (MEDIA_MODE_SYSTEM, MEDIA_MODE_YOUTUBE):
        config["media_mode"] = MEDIA_MODE_SYSTEM
    else:
        config["media_mode"] = mode
    try:
        poll_ms = int(config.get("system_media_poll_ms", 500))
    except (TypeError, ValueError):
        poll_ms = 500
    config["system_media_poll_ms"] = max(200, min(5000, poll_ms))
    config["auto_resume_on_budget"] = bool(config.get("auto_resume_on_budget", False))
    config["show_budget_cubes"] = bool(config.get("show_budget_cubes", True))
    config["show_overlay_timer"] = bool(config.get("show_overlay_timer", True))
    if "show_menubar_watch_time" not in config and "show_toolbar_watch_time" in config:
        config["show_menubar_watch_time"] = config.pop("show_toolbar_watch_time")
    else:
        config.pop("show_toolbar_watch_time", None)
    config["show_menubar_watch_time"] = bool(
        config.get("show_menubar_watch_time", True)
    )
    config["quit_with_anki"] = bool(config.get("quit_with_anki", True))
    config["cube_bounds_left_pct"], config["cube_bounds_right_pct"] = (
        normalize_cube_bounds_pct(
            config.get("cube_bounds_left_pct", 0),
            config.get("cube_bounds_right_pct", 100),
        )
    )
    return config


def normalize_cube_bounds_pct(left: Any, right: Any) -> tuple[int, int]:
    """Clamp left/right percentages and ensure a usable gap."""
    try:
        left_i = int(left)
    except (TypeError, ValueError):
        left_i = 0
    try:
        right_i = int(right)
    except (TypeError, ValueError):
        right_i = 100
    left_i = max(0, min(100, left_i))
    right_i = max(0, min(100, right_i))
    if right_i <= left_i:
        right_i = min(100, left_i + 5)
        if right_i <= left_i:
            left_i = max(0, right_i - 5)
    return left_i, right_i


def save_preferences(addon_module: str, preferences: dict[str, Any]) -> None:
    config = get_config(addon_module)
    for key, value in preferences.items():
        if key in PREFERENCE_KEYS:
            config[key] = value
    config = migrate_config(config)
    write_config(addon_module, config)


def preference_defaults() -> dict[str, Any]:
    return {key: DEFAULTS[key] for key in PREFERENCE_KEYS if key in DEFAULTS}


def is_system_media_mode(config: dict[str, Any] | None = None) -> bool:
    if config is None:
        return True
    return str(config.get("media_mode", MEDIA_MODE_SYSTEM)).lower() == MEDIA_MODE_SYSTEM
