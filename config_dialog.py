# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Learn2Rot settings dialog."""

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
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTime,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import askUser, qconnect, showInfo, tooltip

from . import sentinel, watch_daemon
from .budget import BudgetManager
from .config import (
    MEDIA_MODE_SYSTEM,
    MEDIA_MODE_YOUTUBE,
    get_config,
    is_system_media_mode,
    save_preferences,
)
from .utils import format_seconds
from .watch_state import WEEKDAYS

_WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _qtime_from_hhmm(value) -> QTime:
    text = str(value or "").strip()
    try:
        hours, minutes = text.split(":")
        return QTime(int(hours), int(minutes))
    except (TypeError, ValueError):
        return QTime(9, 0)


def _make_page() -> tuple[QWidget, QVBoxLayout, QFormLayout]:
    """A tab page: a field form up top, with room below for help text."""
    page = QWidget()
    page_layout = QVBoxLayout(page)
    form = QFormLayout()
    page_layout.addLayout(form)
    return page, page_layout, form


def _scrollable(widget: QWidget) -> QScrollArea:
    """Wrap a page so it scrolls instead of clipping on small screens."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(widget)
    return scroll


class ConfigDialog(QDialog):
    def __init__(
        self, addon_module: str, budget: BudgetManager, parent=None
    ) -> None:
        super().__init__(parent or mw)
        self._addon_module = addon_module
        self._budget = budget
        self.setWindowTitle("Learn2Rot Settings")

        config = get_config(addon_module)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # ---- Budget ---------------------------------------------------
        budget_page, budget_layout, form = _make_page()

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
        self.max_budget.setRange(0, 86400)
        self.max_budget.setSuffix(" sec")
        self.max_budget.setSpecialValueText("unlimited")
        self.max_budget.setValue(int(config.get("max_budget_seconds", 0)))
        form.addRow("Maximum watch budget (0 = unlimited):", self.max_budget)

        current_budget_row = QWidget()
        current_budget_layout = QHBoxLayout(current_budget_row)
        current_budget_layout.setContentsMargins(0, 0, 0, 0)
        self._current_budget_label = QLabel()
        self.decrement_budget_button = QPushButton("−")
        self.decrement_budget_button.setToolTip(
            "Remove one cube of watch time (seconds per card)"
        )
        qconnect(self.decrement_budget_button.clicked, self._decrement_current_budget)
        self.increment_budget_button = QPushButton("+")
        self.increment_budget_button.setToolTip(
            "Add one cube of watch time (seconds per card)"
        )
        qconnect(self.increment_budget_button.clicked, self._increment_current_budget)
        self.clear_budget_button = QPushButton("Clear")
        self.clear_budget_button.setToolTip("Set remaining watch time to zero")
        qconnect(self.clear_budget_button.clicked, self._clear_current_budget)
        current_budget_layout.addWidget(self._current_budget_label, 1)
        current_budget_layout.addWidget(self.decrement_budget_button)
        current_budget_layout.addWidget(self.increment_budget_button)
        current_budget_layout.addWidget(self.clear_budget_button)
        self._refresh_current_budget_label()
        form.addRow("Current watch budget:", current_budget_row)

        budget_layout.addWidget(
            QLabel(
                "Current watch budget is saved automatically. "
                "Use − / + to nudge by one cube (seconds per card), "
                "or Clear to reset it."
            )
        )

        self.auto_resume_on_budget = QCheckBox(
            "Auto-resume media when budget is restored (default off)"
        )
        self.auto_resume_on_budget.setChecked(
            bool(config.get("auto_resume_on_budget", False))
        )
        form.addRow("Auto-resume:", self.auto_resume_on_budget)

        self.require_cards_due = QCheckBox(_require_cards_due_checkbox_label())
        self.require_cards_due.setChecked(
            bool(config.get("require_cards_due", False))
        )
        form.addRow("Only when cards are due:", self.require_cards_due)

        cards_due_help = QLabel(_require_cards_due_help_text())
        cards_due_help.setWordWrap(True)
        budget_layout.addWidget(cards_due_help)

        tabs.addTab(_scrollable(budget_page), "Budget")

        # ---- Display ----------------------------------------------------
        display_page, display_layout, form = _make_page()

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

        self.dock_show_playback_buttons = QCheckBox(
            "Show Play and Pause controls on the dock"
        )
        self.dock_show_playback_buttons.setChecked(
            bool(config.get("dock_show_playback_buttons", True))
        )
        form.addRow("Dock playback buttons:", self.dock_show_playback_buttons)

        self.show_overlay_timer = QCheckBox(
            "Show watch timer in the top-left of the Anki window (default on)"
        )
        self.show_overlay_timer.setChecked(
            bool(config.get("show_overlay_timer", True))
        )
        form.addRow("Overlay timer:", self.show_overlay_timer)

        self.show_menubar_watch_time = QCheckBox(
            _menubar_watch_time_checkbox_label()
        )
        self.show_menubar_watch_time.setChecked(
            bool(config.get("show_menubar_watch_time", True))
        )
        form.addRow(_menubar_watch_time_form_label(), self.show_menubar_watch_time)

        self.show_budget_cubes = QCheckBox(
            "Show falling budget cubes over the Anki window (default off)"
        )
        self.show_budget_cubes.setChecked(
            bool(config.get("show_budget_cubes", False))
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

        cubes_help = QLabel(
            "When enabled, budget is shown as cubes that fall over the Anki window "
            "(one cube per “seconds per card”). They pile above the review bottom "
            "bar; cubes disappear as watch time is spent. "
            "Left/Right bounds are percentages of the window width — cubes drop "
            "randomly between them. Uncheck Budget cubes to hide them completely."
        )
        cubes_help.setWordWrap(True)
        display_layout.addWidget(cubes_help)

        tabs.addTab(_scrollable(display_page), "Display")

        # ---- Media --------------------------------------------------------
        media_page, media_layout, form = _make_page()

        self.legacy_youtube = QCheckBox(
            "Use embedded YouTube player (legacy)"
        )
        self.legacy_youtube.setChecked(
            str(config.get("media_mode", MEDIA_MODE_SYSTEM)).lower()
            == MEDIA_MODE_YOUTUBE
        )
        form.addRow("Media mode:", self.legacy_youtube)

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

        self.quit_with_anki = QCheckBox(
            "Quit Anki Media Timer when Anki quits "
            "(uncheck to keep pausing media after Anki closes)"
        )
        self.quit_with_anki.setChecked(bool(config.get("quit_with_anki", True)))
        form.addRow("Quit with Anki:", self.quit_with_anki)

        media_help = QLabel(_system_media_help_text())
        media_help.setWordWrap(True)
        media_layout.addWidget(media_help)

        tabs.addTab(_scrollable(media_page), "Media")

        # ---- Background & login --------------------------------------
        background_page, background_layout, form = _make_page()

        self.open_timer_at_login = QCheckBox(_open_timer_checkbox_label())
        self.open_timer_at_login.setChecked(
            bool(config.get("open_timer_at_login", False))
        )
        self.open_timer_at_login.setEnabled(sentinel.supported())
        form.addRow("Open at login:", self.open_timer_at_login)

        self.sentinel_at_login = QCheckBox(_sentinel_checkbox_label())
        self.sentinel_at_login.setChecked(
            bool(config.get("sentinel_at_login", False))
        )
        self.sentinel_at_login.setEnabled(sentinel.supported())
        form.addRow("Persist in background:", self.sentinel_at_login)

        qconnect(self.sentinel_at_login.toggled, self._on_persist_toggled)

        self.sentinel_on_hours_enabled = QCheckBox(_on_hours_checkbox_label())
        self.sentinel_on_hours_enabled.setChecked(
            bool(config.get("sentinel_on_hours_enabled", False))
        )
        self.sentinel_on_hours_enabled.setEnabled(sentinel.supported())
        form.addRow("Restrict to hours:", self.sentinel_on_hours_enabled)

        on_hours_row = QWidget()
        on_hours_layout = QHBoxLayout(on_hours_row)
        on_hours_layout.setContentsMargins(0, 0, 0, 0)
        self.sentinel_on_hours_start = QTimeEdit()
        self.sentinel_on_hours_start.setDisplayFormat("HH:mm")
        self.sentinel_on_hours_start.setTime(
            _qtime_from_hhmm(config.get("sentinel_on_hours_start", "09:00"))
        )
        self.sentinel_on_hours_end = QTimeEdit()
        self.sentinel_on_hours_end.setDisplayFormat("HH:mm")
        self.sentinel_on_hours_end.setTime(
            _qtime_from_hhmm(config.get("sentinel_on_hours_end", "21:00"))
        )
        on_hours_layout.addWidget(QLabel("From:"))
        on_hours_layout.addWidget(self.sentinel_on_hours_start)
        on_hours_layout.addWidget(QLabel("To:"))
        on_hours_layout.addWidget(self.sentinel_on_hours_end)
        on_hours_layout.addStretch(1)
        form.addRow("On-hours window:", on_hours_row)

        on_days_row = QWidget()
        on_days_layout = QHBoxLayout(on_days_row)
        on_days_layout.setContentsMargins(0, 0, 0, 0)
        on_days_selected = set(config.get("sentinel_on_days", list(WEEKDAYS)))
        self.sentinel_on_days: list[QCheckBox] = []
        for key, label in zip(WEEKDAYS, _WEEKDAY_LABELS):
            checkbox = QCheckBox(label)
            checkbox.setChecked(key in on_days_selected)
            self.sentinel_on_days.append(checkbox)
            on_days_layout.addWidget(checkbox)
        on_days_layout.addStretch(1)
        form.addRow("On-hours days:", on_days_row)

        qconnect(self.sentinel_on_hours_enabled.toggled, self._on_on_hours_toggled)
        self._on_on_hours_toggled(self.sentinel_on_hours_enabled.isChecked())
        self._on_persist_toggled(self.sentinel_at_login.isChecked())

        self._sentinel_status = QLabel(sentinel.describe_status(addon_module))
        self._sentinel_status.setWordWrap(True)
        form.addRow("", self._sentinel_status)

        background_buttons_row = QWidget()
        background_buttons_layout = QHBoxLayout(background_buttons_row)
        background_buttons_layout.setContentsMargins(0, 0, 0, 0)
        start_background_button = QPushButton("Start Background Processes")
        start_background_button.setToolTip(
            "Start whichever of Anki Media Timer and its login watcher should "
            "be running per the settings above (as last saved) but aren't."
        )
        qconnect(start_background_button.clicked, self._start_background_processes)
        quit_background_button = QPushButton("Quit Background Processes")
        quit_background_button.setToolTip(
            "Stop Anki Media Timer and its login watcher right away, without "
            "changing the settings above — they'll start again the next time "
            "something here reconciles them (Save, media playing, or login)."
        )
        qconnect(quit_background_button.clicked, self._quit_background_processes)
        background_buttons_layout.addWidget(start_background_button)
        background_buttons_layout.addWidget(quit_background_button)
        background_buttons_layout.addStretch(1)
        form.addRow("", background_buttons_row)

        sentinel_help = QLabel(_sentinel_help_text())
        sentinel_help.setWordWrap(True)
        background_layout.addWidget(sentinel_help)

        tabs.addTab(_scrollable(background_page), "Background")

        # ---- Advanced -------------------------------------------------
        advanced_page, advanced_layout, form = _make_page()

        self.debug_logging = QCheckBox(
            "Write events to learn2rot.log in your Anki profile folder"
        )
        self.debug_logging.setChecked(bool(config.get("debug_logging", False)))
        form.addRow("Debug logging:", self.debug_logging)

        log_actions_row = QWidget()
        log_actions_layout = QHBoxLayout(log_actions_row)
        log_actions_layout.setContentsMargins(0, 0, 0, 0)
        view_log_button = QPushButton("View Log")
        qconnect(view_log_button.clicked, self._view_debug_log)
        clear_log_button = QPushButton("Clear Log")
        qconnect(clear_log_button.clicked, self._clear_debug_log)
        log_actions_layout.addWidget(view_log_button)
        log_actions_layout.addWidget(clear_log_button)
        log_actions_layout.addStretch(1)
        form.addRow("Debug log:", log_actions_row)

        advanced_layout.addWidget(
            QLabel(
                "View Log opens learn2rot.log from your Anki profile folder; "
                "Clear Log empties it. Both work once Debug logging is on."
            )
        )

        tabs.addTab(_scrollable(advanced_page), "Advanced")

        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(560, 640)

    def _on_persist_toggled(self, checked: bool) -> None:
        """Persisting in the background implies opening at login too — see
        the matching rule in config.migrate_config(). Force the checkbox to
        agree visually, so the dialog doesn't show a state that Save would
        silently overturn.

        Restrict to hours is left alone here — it also gates a currently
        running timer's own enforcement now, not just sentinel revival, so
        it's useful with Persist in background off too.
        """
        if checked:
            self.open_timer_at_login.setChecked(True)
        self.open_timer_at_login.setEnabled(not checked and sentinel.supported())

    def _on_on_hours_toggled(self, checked: bool) -> None:
        self.sentinel_on_hours_start.setEnabled(checked)
        self.sentinel_on_hours_end.setEnabled(checked)
        for checkbox in self.sentinel_on_days:
            checkbox.setEnabled(checked)

    def _refresh_current_budget_label(self) -> None:
        self._current_budget_label.setText(format_seconds(self._budget.seconds))
        self.decrement_budget_button.setEnabled(self._budget.seconds > 0)
        self.clear_budget_button.setEnabled(self._budget.seconds > 0)

    def _chunk_seconds(self) -> int:
        return max(1, int(self.seconds_per_card.value()))

    def _nudge_current_budget(self, delta_seconds: int) -> None:
        from . import hooks

        hooks.adjust_timer(delta_seconds, notify=False)
        self._refresh_current_budget_label()

    def _increment_current_budget(self) -> None:
        self._nudge_current_budget(self._chunk_seconds())

    def _decrement_current_budget(self) -> None:
        self._nudge_current_budget(-self._chunk_seconds())

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

    def _quit_background_processes(self) -> None:
        """Force-quit now, independent of Quit with Anki / Persist settings."""
        watch_daemon.shutdown_watch_daemon(quit_helper=True)
        sentinel.stop_now()
        self._sentinel_status.setText(sentinel.describe_status(self._addon_module))
        tooltip("Learn2Rot: background processes stopped.")

    def _start_background_processes(self) -> None:
        """Reconcile against last-saved settings, starting what's missing."""
        watch_daemon.refresh_watch_daemon(budget_seconds=self._budget.seconds)
        result = sentinel.sync(self._addon_module)
        self._sentinel_status.setText(sentinel.describe_status(self._addon_module))
        if result.error:
            showInfo(f"Learn2Rot: {result.error}")
        else:
            tooltip("Learn2Rot: background processes started.")

    def _view_debug_log(self) -> None:
        from . import hooks

        hooks.open_debug_log()

    def _clear_debug_log(self) -> None:
        from . import hooks

        hooks.clear_debug_log()

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
                "require_cards_due": self.require_cards_due.isChecked(),
                "sentinel_at_login": self.sentinel_at_login.isChecked(),
                "open_timer_at_login": self.open_timer_at_login.isChecked(),
                "sentinel_on_hours_enabled": self.sentinel_on_hours_enabled.isChecked(),
                "sentinel_on_hours_start": self.sentinel_on_hours_start.time().toString(
                    "HH:mm"
                ),
                "sentinel_on_hours_end": self.sentinel_on_hours_end.time().toString(
                    "HH:mm"
                ),
                "sentinel_on_days": [
                    key
                    for key, checkbox in zip(WEEKDAYS, self.sentinel_on_days)
                    if checkbox.isChecked()
                ],
            },
        )
        self._budget.seconds = self._budget.seconds
        self._budget.save()
        showInfo("Learn2Rot settings saved.")
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


def _require_cards_due_checkbox_label() -> str:
    return (
        "Don't lock media once budget runs out if you have no cards due "
        "(default off)"
    )


def _require_cards_due_help_text() -> str:
    icon = "system tray" if _is_windows() else "menu bar"
    return (
        "Only when cards are due changes what happens once your banked time "
        "runs out — not before. Budget still counts down normally either "
        "way; but if it hits zero while you have nothing left to review, "
        f"locking media would just strand you, so it backs off instead — no "
        f"pausing, {icon} icon hidden — until cards are due again. It checks "
        "again right after you answer a card, undo one, or sync, and "
        "otherwise about once a minute, so it comes back on its own once "
        "cards are due (e.g. the next day, or when a learning card's timer "
        "elapses) — no need to reopen Anki. Only Anki can check this "
        "directly, so while it's closed, Anki's last prediction of when the "
        "next card becomes due (from before it closed) is used instead — "
        "enforcement resumes once that time passes, even without reopening."
    )


def _open_timer_checkbox_label() -> str:
    return "Open Anki Media Timer when you log in (default off)"


def _sentinel_checkbox_label() -> str:
    return (
        "Keep watching for media in the background (default off) — "
        "revives the timer when media plays with Anki closed"
    )


def _on_hours_checkbox_label() -> str:
    return "Only revive the timer during a set window each day (default off)"


def _sentinel_help_text() -> str:
    icon = "system tray" if _is_windows() else "menu bar"
    mechanism = (
        "a Run entry for your user account"
        if _is_windows()
        else "a per-user LaunchAgent"
    )
    return (
        "Either setting registers a small login item via "
        f"{mechanism} and opens Anki Media Timer ({icon} countdown, budget "
        "drain and pause enforcement) even if Anki has not been opened since "
        "you turned on your computer. "
        "Open at login starts it once, right away, then the login item exits — "
        "quitting the timer (or letting it run out) stays quit until your next "
        "login, since nothing is left behind to revive it. "
        "Persist in background keeps that login item running afterward too, "
        "so it revives the timer whenever media starts — including after you "
        "quit it. Turning on Persist in background also turns on Open at "
        "login (and disables the checkbox, since a watcher that never shows "
        "itself right away isn't very useful); Open at login can still be "
        "used on its own. Turn both off to remove the login item entirely. "
        "Restrict to hours applies whenever it's checked, whether or not "
        "Persist in background is on. Outside the window (or on an unchecked "
        "day), Anki Media Timer backs off — same as running out of cards "
        "when Only when cards are due is on: no pausing, icon hidden, budget "
        "left untouched — even if it's already running, and it picks back up "
        "on its own once the window reopens, no restart needed. With Persist "
        "in background also on, the watcher keeps watching the whole time "
        "too, but won't revive the timer outside the window, so quitting it "
        "there stays quit until the window opens again (or you reopen Anki). "
        "The window can span midnight (e.g. 22:00 to 06:00); day checks use "
        "the calendar day the moment falls on, so an overnight window may "
        "cut off at midnight if the next day isn't also checked. Unchecking "
        "every day backs off entirely without turning the setting off."
    )


def _system_media_help_text() -> str:
    if _is_windows():
        return (
            "By default, Anki Media Timer meters and pauses Windows system media "
            "(Spotify, Music, browser tabs that report SMTC, etc.) "
            "in the background. Play/Pause: Tools → Learn2Rot or the P key. "
            "Lockout is best-effort for apps that publish to System Media Transport "
            "Controls. Uncheck “Quit with Anki” to keep Anki Media Timer running "
            "after Anki closes."
        )
    return (
        "By default, Anki Media Timer meters and pauses macOS Now Playing media "
        "(Spotify, Music, browser tabs that report Now Playing, etc.) "
        "in the background. Play/Pause: Tools → Learn2Rot or the P key. "
        "Lockout is best-effort for apps that publish to Now Playing. "
        "Uncheck “Quit with Anki” to keep Anki Media Timer running after Anki closes."
    )