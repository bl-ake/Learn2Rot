# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Video queue model and queue UI labels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .utils import (
    allocate_queue_card_progress,
    format_queue_item_label,
)


@dataclass
class QueueItem:
    video_id: str
    title: str
    duration_seconds: Optional[int] = None
    metadata_pending: bool = False


class VideoQueue:
    def __init__(self) -> None:
        self.items: list[QueueItem] = []
        self.current_index = -1

    def contains(self, video_id: str) -> bool:
        return any(item.video_id == video_id for item in self.items)

    def insert(
        self,
        video_id: str,
        *,
        title: str,
        duration_seconds: Optional[int],
        insert_index: int | None = None,
        metadata_pending: bool = False,
    ) -> int:
        if insert_index is None:
            insert_index = len(self.items)
        insert_index = max(0, min(insert_index, len(self.items)))
        self.items.insert(
            insert_index,
            QueueItem(
                video_id=video_id,
                title=title,
                duration_seconds=duration_seconds,
                metadata_pending=metadata_pending,
            ),
        )
        if self.current_index < 0:
            self.current_index = 0
        elif insert_index <= self.current_index:
            self.current_index += 1
        return insert_index

    def update_metadata(
        self,
        video_id: str,
        *,
        title: str,
        duration_seconds: Optional[int],
    ) -> bool:
        for item in self.items:
            if item.video_id != video_id:
                continue
            changed = (
                item.title != title
                or item.duration_seconds != duration_seconds
                or item.metadata_pending
            )
            item.title = title
            item.duration_seconds = duration_seconds
            item.metadata_pending = False
            return changed
        return False

    def current_item(self) -> Optional[QueueItem]:
        if self.current_index < 0 or self.current_index >= len(self.items):
            return None
        return self.items[self.current_index]

    def item_labels(
        self,
        *,
        seconds_per_card: int,
        budget_seconds: int,
        positions: dict[str, float],
        is_playing: bool,
    ) -> list[str]:
        card_progress = allocate_queue_card_progress(
            [item.duration_seconds for item in self.items],
            budget_seconds,
            seconds_per_card,
            video_ids=[item.video_id for item in self.items],
            playback_positions=positions,
        )
        labels: list[str] = []
        for index, (item, progress) in enumerate(zip(self.items, card_progress)):
            playing_prefix = (
                "▶ " if index == self.current_index and is_playing else ""
            )
            title = item.title
            if item.metadata_pending:
                title = f"{title} (fetching…)"
            labels.append(
                format_queue_item_label(
                    title,
                    progress.cards_done,
                    progress.cards_total,
                    playing_prefix=playing_prefix,
                )
            )
        return labels

    def labels_changed_for_position(
        self,
        video_id: str,
        seconds: float,
        positions: dict[str, float],
        *,
        seconds_per_card: int,
        budget_seconds: int,
    ) -> bool:
        new_positions = dict(positions)
        new_positions[video_id] = seconds
        return self.item_labels(
            seconds_per_card=seconds_per_card,
            budget_seconds=budget_seconds,
            positions=new_positions,
            is_playing=False,
        ) != self.item_labels(
            seconds_per_card=seconds_per_card,
            budget_seconds=budget_seconds,
            positions=positions,
            is_playing=False,
        )

    def remove_at(self, row: int) -> Optional[QueueItem]:
        if row < 0 or row >= len(self.items):
            return None
        return self.items.pop(row)

    def remove_current_finished(self) -> Optional[QueueItem]:
        if not self.current_item():
            return None
        was_last = self.current_index >= len(self.items) - 1
        removed = self.items.pop(self.current_index)
        if not self.items:
            self.current_index = -1
        elif was_last:
            self.current_index = len(self.items) - 1
        return removed

    def move(self, row: int, delta: int) -> Optional[int]:
        new_row = row + delta
        if row < 0 or new_row < 0 or new_row >= len(self.items):
            return None
        self.items[row], self.items[new_row] = self.items[new_row], self.items[row]
        if self.current_index == row:
            self.current_index = new_row
        elif self.current_index == new_row:
            self.current_index = row
        return new_row

    def items_missing_duration(self) -> list[QueueItem]:
        return [item for item in self.items if item.duration_seconds is None]
