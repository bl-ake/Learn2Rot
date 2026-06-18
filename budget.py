# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Watch-time budget manager."""

from __future__ import annotations

from typing import Callable, Optional

from aqt import mw

from .config import get_config, write_config


class BudgetManager:
    """Tracks watch-time budget earned from reviewing cards."""

    def __init__(
        self,
        addon_module: str,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self._addon_module = addon_module
        self._on_change = on_change
        self._seconds = 0

    @property
    def seconds(self) -> int:
        return self._seconds

    @seconds.setter
    def seconds(self, value: int) -> None:
        self._seconds = self._clamp(int(value))
        self._notify()

    def max_seconds(self) -> int:
        return self._max_budget_seconds()

    def load(self) -> None:
        config = self._config()
        if "budget_seconds" in config:
            self._seconds = self._clamp(int(config["budget_seconds"]))
        else:
            self._seconds = self._clamp(int(config.get("starting_budget_seconds", 0)))

    def save(self) -> None:
        config = self._config()
        config["budget_seconds"] = self._seconds
        write_config(self._addon_module, config)

    def add_seconds(self, amount: int) -> None:
        if amount <= 0:
            return
        self.seconds = min(self._seconds + amount, self._max_budget_seconds())

    def subtract_seconds(self, amount: int) -> None:
        if amount <= 0:
            return
        self.seconds = max(0, self._seconds - amount)

    def consume_second(self) -> bool:
        if self._seconds <= 0:
            return False
        self._seconds -= 1
        self._notify()
        return True

    def has_time(self) -> bool:
        return self._seconds > 0

    def _config(self) -> dict:
        return get_config(self._addon_module)

    def _max_budget_seconds(self) -> int:
        return max(1, int(self._config().get("max_budget_seconds", 600)))

    def _clamp(self, value: int) -> int:
        return max(0, min(int(value), self._max_budget_seconds()))

    def _notify(self) -> None:
        if self._on_change:
            self._on_change()
