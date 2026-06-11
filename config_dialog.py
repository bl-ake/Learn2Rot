from __future__ import annotations

from aqt import mw
from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)
from aqt.utils import showInfo

from .budget import BudgetManager


class ConfigDialog(QDialog):
    def __init__(
        self, addon_module: str, budget: BudgetManager, parent=None
    ) -> None:
        super().__init__(parent or mw)
        self._addon_module = addon_module
        self._budget = budget
        self.setWindowTitle("AnkiTube Settings")

        config = mw.addonManager.getConfig(addon_module) or {}

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

        self.debug_logging = QCheckBox("Write events to ankittube.log in your Anki profile folder")
        self.debug_logging.setChecked(bool(config.get("debug_logging", False)))
        form.addRow("Debug logging:", self.debug_logging)

        self.youtube_show_controls = QCheckBox("Show play bar and controls in the embedded player")
        self.youtube_show_controls.setChecked(bool(config.get("youtube_show_controls", True)))
        form.addRow("YouTube controls:", self.youtube_show_controls)

        self.youtube_show_fullscreen = QCheckBox("Show fullscreen button in the embedded player")
        self.youtube_show_fullscreen.setChecked(bool(config.get("youtube_show_fullscreen", True)))
        form.addRow("YouTube fullscreen:", self.youtube_show_fullscreen)

        self.dock_show_playback_buttons = QCheckBox("Show Play, Pause, Next, and Fullscreen below the player")
        self.dock_show_playback_buttons.setChecked(
            bool(config.get("dock_show_playback_buttons", True))
        )
        form.addRow("Dock playback buttons:", self.dock_show_playback_buttons)

        layout.addLayout(form)
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
        config = mw.addonManager.getConfig(self._addon_module) or {}
        config["seconds_per_card"] = self.seconds_per_card.value()
        config["starting_budget_seconds"] = self.starting_budget.value()
        config["max_budget_seconds"] = self.max_budget.value()
        config["debug_logging"] = self.debug_logging.isChecked()
        config["youtube_show_controls"] = self.youtube_show_controls.isChecked()
        config["youtube_show_fullscreen"] = self.youtube_show_fullscreen.isChecked()
        config["dock_show_playback_buttons"] = self.dock_show_playback_buttons.isChecked()
        mw.addonManager.writeConfig(self._addon_module, config)
        self._budget.seconds = self._budget.seconds
        self._budget.save()
        showInfo("AnkiTube settings saved.")
        self.accept()
