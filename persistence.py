# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Persist and restore dock queue and layout state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from aqt import mw

from .config import get_config, write_config
from .queue import QueueItem, VideoQueue

if TYPE_CHECKING:
    from .budget import BudgetManager


class DockPersistence:
    def __init__(self, addon_module: str, budget: "BudgetManager") -> None:
        self._addon_module = addon_module
        self._budget = budget

    def config(self) -> dict[str, Any]:
        return get_config(self._addon_module)

    def load_queue(self, queue: VideoQueue) -> tuple[dict[str, float], int, bool]:
        config = self.config()
        queue_data = config.get("queue", [])
        queue.items = []
        for item in queue_data:
            if not isinstance(item, dict):
                continue
            video_id = item.get("video_id")
            title = item.get("title")
            if isinstance(video_id, str) and video_id:
                duration = item.get("duration_seconds")
                duration_seconds = (
                    int(duration) if isinstance(duration, (int, float)) else None
                )
                queue.items.append(
                    QueueItem(
                        video_id=video_id,
                        title=str(title) if title else video_id,
                        duration_seconds=duration_seconds,
                    )
                )

        saved_index = config.get("current_index", 0)
        if queue.items:
            if isinstance(saved_index, int) and 0 <= saved_index < len(queue.items):
                queue.current_index = saved_index
            else:
                queue.current_index = 0
        else:
            queue.current_index = -1

        positions: dict[str, float] = {}
        raw_positions = config.get("positions", {})
        if isinstance(raw_positions, dict):
            positions = {
                str(video_id): float(seconds)
                for video_id, seconds in raw_positions.items()
                if isinstance(video_id, str)
            }

        earned = config.get("lifetime_earned_seconds", 0)
        lifetime_earned = (
            max(0, int(earned)) if isinstance(earned, (int, float)) else 0
        )

        queue_visible = config.get("queue_visible", True)
        visible = queue_visible if isinstance(queue_visible, bool) else True
        return positions, lifetime_earned, visible

    def load_dock_visible(self) -> bool:
        dock_visible = self.config().get("dock_visible", True)
        return dock_visible if isinstance(dock_visible, bool) else True

    def save_state(
        self,
        queue: VideoQueue,
        *,
        positions: dict[str, float],
        lifetime_earned_seconds: int,
        queue_visible: bool,
        dock_visible: bool,
        dock_panel_sizes: Optional[list[int]] = None,
        target_dock_width: Optional[int] = None,
    ) -> None:
        config = self.config()
        config["queue"] = [
            {
                "video_id": item.video_id,
                "title": item.title,
                "duration_seconds": item.duration_seconds,
            }
            for item in queue.items
        ]
        config["current_index"] = queue.current_index
        config["positions"] = dict(positions)
        config["lifetime_earned_seconds"] = lifetime_earned_seconds
        config["queue_visible"] = queue_visible
        config["dock_visible"] = dock_visible
        if dock_panel_sizes is not None and queue_visible:
            config["dock_panel_sizes"] = dock_panel_sizes
        if target_dock_width and target_dock_width >= 200:
            config["dock_width"] = target_dock_width
        write_config(self._addon_module, config)
        self._budget.save()

    def load_target_dock_width(self) -> Optional[int]:
        width = self.config().get("dock_width")
        if isinstance(width, (int, float)):
            width = int(width)
            if width >= 200:
                return width
        return None

    def load_splitter_sizes(self) -> Optional[list[int]]:
        sizes = self.config().get("dock_panel_sizes")
        if isinstance(sizes, list) and len(sizes) == 2:
            try:
                top, bottom = int(sizes[0]), int(sizes[1])
                if top > 0 and bottom > 0:
                    return [top, bottom]
            except (TypeError, ValueError):
                pass
        return None

    def dock_area_name(self) -> str:
        return str(self.config().get("dock_area", "right")).lower()

    def seconds_per_card(self) -> int:
        return int(self.config().get("seconds_per_card", 15))
