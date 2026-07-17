# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""AnkiTube settings dialog."""

from __future__ import annotations

import platform

from aqt import mw
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import askUser, qconnect, showInfo

from . import watch_daemon
from .budget import BudgetManager
from .config import (
    MEDIA_MODE_SYSTEM,
    MEDIA_MODE_YOUTUBE,
    get_config,
    is_system_media_mode,
    save_preferences,
)
from .utils import format_seconds


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
        form.addRow(
            "Seconds per card / cube (watch time earned):",
            self.seconds_per_card,
        )

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

        current_budget_row = QWidget()
        current_budget_layout = QHBoxLayout(current_budget_row)
        current_budget_layout.setContentsMargins(0, 0, 0, 0)
        self._current_budget_label = QLabel()
        self.clear_budget_button = QPushButton("Clear")
        self.clear_budget_button.setToolTip("Set remaining watch time to zero")
        qconnect(self.clear_budget_button.clicked, self._clear_current_budget)
        current_budget_layout.addWidget(self._current_budget_label, 1)
        current_budget_layout.addWidget(self.clear_budget_button)
        self._refresh_current_budget_label()
        form.addRow("Current watch budget:", current_budget_row)

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

        self.show_budget_cubes = QCheckBox(
            "Show falling budget cubes over the Anki window (default on)"
        )
        self.show_budget_cubes.setChecked(
            bool(config.get("show_budget_cubes", True))
        )
        form.addRow("Budget cubes:", self.show_budget_cubes)

        bounds_row = QWidget()
        bounds_layout = QHBoxLayout(bounds_row)
        bounds_layout.setContentsMargins(0, 0, 0, 0)
        self.cube_bounds_left = QSpinBox()
        self.cube_bounds_left.setRange(0, 95)
        self.cube_bounds_left.setSuffix("%")
        self.cube_bounds_left.setValue(int(config.get("cube_bounds_left_pct", 0)))
        self.cube_bounds_right = QSpinBox()
        self.cube_bounds_right.setRange(5, 100)
        self.cube_bounds_right.setSuffix("%")
        self.cube_bounds_right.setValue(int(config.get("cube_bounds_right_pct", 100)))
        bounds_layout.addWidget(QLabel("Left:"))
        bounds_layout.addWidget(self.cube_bounds_left)
        bounds_layout.addWidget(QLabel("Right:"))
        bounds_layout.addWidget(self.cube_bounds_right)
        bounds_layout.addStretch(1)
        form.addRow("Cube drop bounds:", bounds_row)

        self.show_overlay_timer = QCheckBox(
            "Show watch timer in the top-left of the Anki window (default on)"
        )
        self.show_overlay_timer.setChecked(
            bool(config.get("show_overlay_timer", True))
        )
        form.addRow("Overlay timer:", self.show_overlay_timer)

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

        self.show_menubar_watch_time = QCheckBox(
            _menubar_watch_time_checkbox_label()
        )
        self.show_menubar_watch_time.setChecked(
            bool(config.get("show_menubar_watch_time", True))
        )
        form.addRow(_menubar_watch_time_form_label(), self.show_menubar_watch_time)

        self.quit_with_anki = QCheckBox(
            "Quit Anki Media Timer when Anki quits "
            "(uncheck to keep pausing media after Anki closes)"
        )
        self.quit_with_anki.setChecked(bool(config.get("quit_with_anki", True)))
        form.addRow("Quit with Anki:", self.quit_with_anki)

        layout.addLayout(form)
        layout.addWidget(
            QLabel(
                "When enabled, budget is shown as cubes that fall over the Anki window "
                "(one cube per “seconds per card”). They bounce off the card "
                "and pile at the bottom; cubes disappear as watch time is spent. "
                "Left/Right bounds are percentages of the window width — cubes drop "
                "randomly between them. Uncheck Budget cubes to hide them completely."
            )
        )
        layout.addWidget(QLabel(_system_media_help_text()))
        layout.addWidget(
            QLabel(
                "Current watch budget is saved automatically. "
                "Review flashcards to earn more time, or use Clear to reset it."
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

    def _refresh_current_budget_label(self) -> None:
        self._current_budget_label.setText(format_seconds(self._budget.seconds))
        self.clear_budget_button.setEnabled(self._budget.seconds > 0)

    def _clear_current_budget(self) -> None:
        current = self._budget.seconds
        if current <= 0:
            showInfo("Watch budget is already empty.")
            return
        if not askUser(
            f"Clear {format_seconds(current)} of remaining watch time?",
            parent=self,
        ):
            return

        self._budget.seconds = 0
        self._budget.save()
        config = get_config(self._addon_module)
        if is_system_media_mode(config):
            watch_daemon.subtract_watch_time(current)
        else:
            watch_daemon.publish_budget(0)
        self._refresh_current_budget_label()
        try:
            from . import hooks

            hooks._sync_overlay(falling=False)
        except Exception:
            pass
        showInfo("Current watch budget cleared.")

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
                "show_menubar_watch_time": self.show_menubar_watch_time.isChecked(),
                "quit_with_anki": self.quit_with_anki.isChecked(),
                "media_mode": (
                    MEDIA_MODE_YOUTUBE
                    if self.legacy_youtube.isChecked()
                    else MEDIA_MODE_SYSTEM
                ),
                "auto_resume_on_budget": self.auto_resume_on_budget.isChecked(),
                "show_budget_cubes": self.show_budget_cubes.isChecked(),
                "cube_bounds_left_pct": self.cube_bounds_left.value(),
                "cube_bounds_right_pct": self.cube_bounds_right.value(),
                "show_overlay_timer": self.show_overlay_timer.isChecked(),
            },
        )
        self._budget.seconds = self._budget.seconds
        self._budget.save()
        showInfo("AnkiTube settings saved.")
        self.accept()


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def _menubar_watch_time_checkbox_label() -> str:
    if _is_windows():
        return "Show Anki Media Timer icon in the system tray (default on)"
    return "Show Anki Media Timer icon in the menu bar (default on)"


def _menubar_watch_time_form_label() -> str:
    if _is_windows():
        return "System tray icon:"
    return "Menu bar icon:"


def _system_media_help_text() -> str:
    if _is_windows():
        return (
            "By default, Anki Media Timer meters and pauses Windows system media "
            "(Spotify, Music, browser tabs that report SMTC, etc.) "
            "in the background. Play/Pause: Tools → AnkiTube or the P key. "
            "Lockout is best-effort for apps that publish to System Media Transport "
            "Controls. Uncheck “Quit with Anki” to keep Anki Media Timer running "
            "after Anki closes."
        )
    return (
        "By default, Anki Media Timer meters and pauses macOS Now Playing media "
        "(Spotify, Music, browser tabs that report Now Playing, etc.) "
        "in the background. Play/Pause: Tools → AnkiTube or the P key. "
        "Lockout is best-effort for apps that publish to Now Playing. "
        "Uncheck “Quit with Anki” to keep Anki Media Timer running after Anki closes."
    )