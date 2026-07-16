# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""AnkiTube hook handlers and menu setup."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from anki.collection import OpChangesAfterUndo

from aqt import gui_hooks, mw, tr
from aqt.qt import QAction, QMenu, QTimer
from aqt.qt import QDesktopServices, QUrl
from aqt.utils import qconnect, showInfo

from . import watch_daemon
from .config import get_config, is_system_media_mode
from .config_dialog import ConfigDialog
from .logger import clear_log, log, log_path
from .utils import local_file_uri

if TYPE_CHECKING:
    from .budget import BudgetManager
    from .dock import AnkiTubeDock
    from .overlay import BudgetOverlayController

_dock: Optional["AnkiTubeDock"] = None
_budget: Optional["BudgetManager"] = None
_overlay: Optional["BudgetOverlayController"] = None
_addon_module: str = ""
_syncing_from_daemon = False


def set_addon_module(module: str) -> None:
    global _addon_module
    _addon_module = module
    watch_daemon.set_addon_module(module)
    watch_daemon.set_budget_sync_callback(_on_daemon_budget_sync)


def get_budget() -> "BudgetManager":
    global _budget
    from .budget import BudgetManager

    if _budget is None:
        _budget = BudgetManager(_addon_module, on_change=_on_budget_changed)
        _budget.load()
    return _budget


def get_dock() -> "AnkiTubeDock":
    global _dock
    from .dock import AnkiTubeDock

    if _dock is None:
        _dock = AnkiTubeDock(_addon_module, get_budget())
    return _dock


def get_overlay() -> "BudgetOverlayController":
    global _overlay
    from .overlay import BudgetOverlayController

    if _overlay is None:
        _overlay = BudgetOverlayController(_addon_module)
        _overlay.start()
    return _overlay


def _chunk_seconds() -> int:
    return max(1, int(get_config(_addon_module).get("seconds_per_card", 15)))


def _hydrate_overlay() -> None:
    budget = get_budget()
    get_overlay().hydrate_from_budget(
        budget.seconds,
        _chunk_seconds(),
        budget.max_seconds(),
    )


def _sync_overlay(*, falling: bool) -> None:
    budget = get_budget()
    get_overlay().on_budget_seconds(
        budget.seconds,
        _chunk_seconds(),
        budget.max_seconds(),
        falling=falling,
    )


def _on_budget_changed() -> None:
    if _dock is not None:
        _dock.on_budget_changed()


def _on_daemon_budget_sync(seconds: int, is_playing: bool) -> None:
    """Apply daemon-owned budget to Anki UI (overlay / dock)."""
    global _syncing_from_daemon
    if _budget is None:
        return
    _syncing_from_daemon = True
    try:
        if _budget.seconds != seconds:
            _budget.seconds = seconds
            _sync_overlay(falling=False)
        if _dock is not None:
            _dock.on_daemon_playback_state(is_playing)
    finally:
        _syncing_from_daemon = False


def show_dock() -> None:
    get_dock().show_dock()


def toggle_dock() -> None:
    dock = get_dock()
    if dock.isVisible():
        dock.hide_dock()
    else:
        dock.show_dock()


def play_media() -> None:
    get_dock()._play_current()


def pause_media() -> None:
    get_dock()._pause_playback()


def toggle_media() -> None:
    get_dock()._toggle_playback()


def open_settings() -> None:
    dialog = ConfigDialog(_addon_module, get_budget(), parent=mw)
    if dialog.exec():
        if _dock is not None:
            _dock.apply_settings()
        get_overlay().apply_settings()
        _sync_overlay(falling=False)
        get_overlay().ensure_raised()
        watch_daemon.refresh_watch_daemon(budget_seconds=get_budget().seconds)


def open_debug_log() -> None:
    path = log_path()
    if not os.path.exists(path):
        showInfo("No AnkiTube debug log exists yet.\n\nEnable debug logging in Settings first.")
        return
    if not QDesktopServices.openUrl(QUrl(local_file_uri(path))):
        showInfo(f"Could not open the debug log.\n\nPath:\n{path}")


def clear_debug_log() -> None:
    clear_log()
    log("debug log cleared")
    showInfo("AnkiTube debug log cleared.")


def on_profile_open() -> None:
    config = get_config(_addon_module)
    log(
        "profile open: hydrating dock/overlay/watch_daemon "
        f"(show_menubar_watch_time="
        f"{bool(config.get('show_menubar_watch_time', True))} "
        f"quit_with_anki={bool(config.get('quit_with_anki', True))})"
    )
    budget = get_budget()
    get_dock()
    _hydrate_overlay()
    get_overlay().set_review_active(mw.state == "review")
    watch_daemon.start_watch_daemon(budget_seconds=budget.seconds, force=True)


def on_profile_close() -> None:
    global _dock, _budget, _overlay
    quit_helper = bool(get_config(_addon_module).get("quit_with_anki", True))
    watch_daemon.shutdown_watch_daemon(quit_helper=quit_helper)
    if _overlay is not None:
        _overlay.shutdown()
        _overlay = None
    if _dock is not None:
        _dock.shutdown()
        _dock.deleteLater()
        _dock = None
    _budget = None


def on_answer_card(reviewer, card, ease) -> None:
    if mw.state != "review":
        return
    get_dock().on_card_answered()
    _sync_overlay(falling=True)
    QTimer.singleShot(0, get_overlay().refresh_card_colliders)


def _is_answer_card_undo(changes: OpChangesAfterUndo) -> bool:
    if changes.operation == tr.actions_answer_card():
        return True
    return "answer card" in changes.operation.lower()


def on_undo(changes: OpChangesAfterUndo) -> None:
    if not _is_answer_card_undo(changes):
        return
    log(f"undo detected for answer card: {changes.operation!r}")
    get_dock().on_review_undo()
    _sync_overlay(falling=False)


def on_show_question(card) -> None:
    overlay = get_overlay()
    overlay.set_review_active(True)
    QTimer.singleShot(0, overlay.refresh_card_colliders)
    QTimer.singleShot(80, overlay.refresh_card_colliders)


def on_show_answer(card) -> None:
    overlay = get_overlay()
    overlay.set_review_active(True)
    QTimer.singleShot(0, overlay.refresh_card_colliders)
    QTimer.singleShot(80, overlay.refresh_card_colliders)


def on_state_change(new_state, old_state) -> None:
    get_overlay().set_review_active(new_state == "review")
    config = get_config(_addon_module)
    if is_system_media_mode(config):
        # System mode keeps the dock hidden; overlay is the budget UI.
        return
    if not config.get("show_dock_in_review_only", False):
        return
    dock = get_dock()
    if new_state == "review":
        if dock._dock_visible:
            dock.show_dock()
    else:
        dock.hide()


def setup_menu() -> None:
    menu = QMenu("AnkiTube", mw)
    mw.form.menuTools.addMenu(menu)

    play_action = QAction("Play", mw)
    qconnect(play_action.triggered, play_media)
    menu.addAction(play_action)

    pause_action = QAction("Pause", mw)
    qconnect(pause_action.triggered, pause_media)
    menu.addAction(pause_action)

    toggle_media_action = QAction("Play/Pause", mw)
    qconnect(toggle_media_action.triggered, toggle_media)
    menu.addAction(toggle_media_action)

    menu.addSeparator()

    show_action = QAction("Show Player", mw)
    qconnect(show_action.triggered, show_dock)
    menu.addAction(show_action)

    toggle_action = QAction("Toggle Player", mw)
    qconnect(toggle_action.triggered, toggle_dock)
    menu.addAction(toggle_action)

    settings_action = QAction("Settings...", mw)
    qconnect(settings_action.triggered, open_settings)
    menu.addAction(settings_action)

    view_log_action = QAction("View Debug Log", mw)
    qconnect(view_log_action.triggered, open_debug_log)
    menu.addAction(view_log_action)

    clear_log_action = QAction("Clear Debug Log", mw)
    qconnect(clear_log_action.triggered, clear_debug_log)
    menu.addAction(clear_log_action)


def register_hooks() -> None:
    gui_hooks.profile_did_open.append(on_profile_open)
    gui_hooks.profile_will_close.append(on_profile_close)
    gui_hooks.reviewer_did_answer_card.append(on_answer_card)
    gui_hooks.reviewer_did_show_question.append(on_show_question)
    gui_hooks.reviewer_did_show_answer.append(on_show_answer)
    gui_hooks.state_did_undo.append(on_undo)
    gui_hooks.state_did_change.append(on_state_change)
    gui_hooks.main_window_did_init.append(setup_menu)
