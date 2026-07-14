# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""AnkiTube settings dialog."""

from __future__ import annotations

from aqt import mw
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)
from aqt.utils import showInfo

from .budget import BudgetManager
from .config import MEDIA_MODE_SYSTEM, MEDIA_MODE_YOUTUBE, get_config, save_preferences


class ConfigDialog(QDialog):
    def __init__(
        self, addon_module: str, budget: BudgetManager, parent=None
    ) -> None:
        super().__init__(parent or mw)
        self._addon_module = addon_module
        self._budget = budget
        self.setWindowTitle("AnkiTube Settings")

        config = get_config(addon_module)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.seconds_per_card = QSpinBox()
        self.seconds_per_card.setRange(1, 3600)
        self.seconds_per_card.setSuffix(" sec")
        self.seconds_per_card.setValue(int(config.get("seconds_per_card", 15)))
        form.addRow("Seconds earned per reviewed card:", self.seconds_per_card)

        self.starting_budget = QSpinBox()
        self.starting_budget.setRange(0, 86400)
        self.starting_budget.setSuffix(" sec")
        self.starting_budget.setValue(int(config.get("starting_budget_seconds", 0)))
        form.addRow("Starting budget for new profiles:", self.starting_budget)

        self.max_budget = QSpinBox()
        self.max_budget.setRange(1, 86400)
        self.max_budget.setSuffix(" sec")
        self.max_budget.setValue(int(config.get("max_budget_seconds", 600)))
        form.addRow("Maximum watch budget (default 10 min):", self.max_budget)

        self.show_dock_in_review_only = QCheckBox(
            "Hide the dock outside the review screen"
        )
        self.show_dock_in_review_only.setChecked(
            bool(config.get("show_dock_in_review_only", False))
        )
        form.addRow("Review only:", self.show_dock_in_review_only)

        self.dock_area = QComboBox()
        self.dock_area.addItem("Right", "right")
        self.dock_area.addItem("Left", "left")
        area = str(config.get("dock_area", "right")).lower()
        self.dock_area.setCurrentIndex(0 if area != "left" else 1)
        form.addRow("Dock side:", self.dock_area)

        self.legacy_youtube = QCheckBox(
            "Use embedded YouTube player (legacy)"
        )
        self.legacy_youtube.setChecked(
            str(config.get("media_mode", MEDIA_MODE_SYSTEM)).lower()
            == MEDIA_MODE_YOUTUBE
        )
        form.addRow("Media mode:", self.legacy_youtube)

        self.auto_resume_on_budget = QCheckBox(
            "Auto-resume media when budget is restored (default off)"
        )
        self.auto_resume_on_budget.setChecked(
            bool(config.get("auto_resume_on_budget", False))
        )
        form.addRow("Auto-resume:", self.auto_resume_on_budget)

        self.debug_logging = QCheckBox(
            "Write events to ankittube.log in your Anki profile folder"
        )
        self.debug_logging.setChecked(bool(config.get("debug_logging", False)))
        form.addRow("Debug logging:", self.debug_logging)

        self.youtube_show_controls = QCheckBox(
            "Show play bar and controls in the embedded player"
        )
        self.youtube_show_controls.setChecked(
            bool(config.get("youtube_show_controls", True))
        )
        form.addRow("YouTube controls:", self.youtube_show_controls)

        self.youtube_show_fullscreen = QCheckBox(
            "Show fullscreen button in the embedded player"
        )
        self.youtube_show_fullscreen.setChecked(
            bool(config.get("youtube_show_fullscreen", True))
        )
        form.addRow("YouTube fullscreen:", self.youtube_show_fullscreen)

        self.dock_show_playback_buttons = QCheckBox(
            "Show Play and Pause controls on the dock"
        )
        self.dock_show_playback_buttons.setChecked(
            bool(config.get("dock_show_playback_buttons", True))
        )
        form.addRow("Dock playback buttons:", self.dock_show_playback_buttons)

        layout.addLayout(form)
        layout.addWidget(
            QLabel(
                "By default, AnkiTube meters and pauses macOS Now Playing media "
                "(Spotify, Music, browser tabs that report Now Playing, etc.). "
                "Lockout is best-effort for apps that publish to Now Playing."
            )
        )
        layout.addWidget(
            QLabel(
                "Current watch budget is saved automatically. "
                "Review flashcards to earn more time."
            )
        )
        layout.addWidget(
            QLabel(
                "View the log from Tools → AnkiTube → View Debug Log after enabling debug logging."
            )
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        save_preferences(
            self._addon_module,
            {
                "seconds_per_card": self.seconds_per_card.value(),
                "starting_budget_seconds": self.starting_budget.value(),
                "max_budget_seconds": self.max_budget.value(),
                "show_dock_in_review_only": self.show_dock_in_review_only.isChecked(),
                "dock_area": self.dock_area.currentData(),
                "debug_logging": self.debug_logging.isChecked(),
                "youtube_show_controls": self.youtube_show_controls.isChecked(),
                "youtube_show_fullscreen": self.youtube_show_fullscreen.isChecked(),
                "dock_show_playback_buttons": self.dock_show_playback_buttons.isChecked(),
                "media_mode": (
                    MEDIA_MODE_YOUTUBE
                    if self.legacy_youtube.isChecked()
                    else MEDIA_MODE_SYSTEM
                ),
                "auto_resume_on_budget": self.auto_resume_on_budget.isChecked(),
            },
        )
        self._budget.seconds = self._budget.seconds
        self._budget.save()
        showInfo("AnkiTube settings saved.")
        self.accept()
