# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""AnkiTube dock widget — orchestrates queue, player, and budget."""

from __future__ import annotations

import json
from typing import Optional

from aqt import mw
from aqt.qt import (
    QAbstractItemView,
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QResizeEvent,
    QShowEvent,
    QSplitter,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import qconnect, showWarning
from aqt.webview import AnkiWebView, AnkiWebViewKind

from .budget import BudgetManager
from .config import get_config
from .logger import log
from .metadata import fetch_video_metadata_async
from .persistence import DockPersistence
from .player_bridge import PlayerBridge
from .queue import VideoQueue
from .utils import (
    extract_all_video_ids,
    extract_video_ids_from_mime,
    format_seconds,
    normalize_youtube_url,
)
from .widgets import (
    AnkiTubePanel,
    FullscreenPlayer,
    MainWindowResizeFilter,
    PlayerShortcutFilter,
    QueueListWidget,
)

_STARTUP_GRACE_MS = 2000
_RESIZE_SETTLE_MS = 400


class AnkiTubeDock(QDockWidget):
    def __init__(self, addon_module: str, budget: BudgetManager) -> None:
        super().__init__("AnkiTube", mw)
        self._addon_module = addon_module
        self._budget = budget
        self._queue = VideoQueue()
        self._persistence = DockPersistence(addon_module, budget)
        self._bridge = PlayerBridge(addon_module, self)
        self._is_playing = False
        self._paused_for_budget = False
        self._reward_history: list[int] = []
        self._positions_cache: dict[str, float] = {}
        self._lifetime_earned_seconds = 0
        self._position_save_ticks = 0
        self._hold_paused = False
        self._was_playing_before_hold = False
        self._player_shortcut_filter: PlayerShortcutFilter | None = None
        self._fullscreen: Optional[FullscreenPlayer] = None
        self._queue_visible = True
        self._queue_splitter_sizes: Optional[list[int]] = None
        self._layout_restored = False
        self._startup_complete = False
        self._target_dock_width: Optional[int] = None
        self._last_mw_width: Optional[int] = None
        self._last_dock_width: Optional[int] = None
        self._mw_resize_filter: MainWindowResizeFilter | None = None
        self._metadata_tokens: dict[str, int] = {}
        self._metadata_token = 0

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_timer_tick)

        log("AnkiTube dock initializing")
        self._build_ui()
        self._load_state()
        self._update_budget_ui()
        QTimer.singleShot(0, self._finish_initial_layout)

        package = mw.addonManager.addonFromModule(addon_module)
        player_url = self._bridge.player_url()
        log(f"addon package={package!r} module={addon_module!r}")
        log(f"player url={player_url.toString()}")
        log(
            f"queue size={len(self._queue.items)} budget={self._budget.seconds}s "
            f"current_index={self._queue.current_index}"
        )

        self._bridge.register_exports()
        self._bridge.setup_webview(self._web)
        self._bridge.setup_player_webview(self._web)
        qconnect(self._web.loadFinished, self._bridge.on_web_load_finished)
        self._web.load_url(player_url)

        self._player_shortcut_filter = PlayerShortcutFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._player_shortcut_filter)

        self._target_dock_width = self._persistence.load_target_dock_width()
        self._mw_resize_filter = MainWindowResizeFilter(self)
        mw.installEventFilter(self._mw_resize_filter)
        self._startup_grace_timer.start()

    def _build_ui(self) -> None:
        container = AnkiTubePanel(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)

        self._top_panel = QWidget()
        top_panel = self._top_panel
        top_layout = QVBoxLayout(top_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)

        self._budget_label = QLabel()
        self._budget_bar = QProgressBar()
        self._budget_bar.setRange(0, 300)
        self._budget_bar.setTextVisible(False)

        queue_button_size = 22
        self._add_button = QPushButton("+")
        self._remove_button = QPushButton("x")
        self._up_button = QPushButton("↑")
        self._down_button = QPushButton("↓")
        self._toggle_queue_button = QPushButton("▾")
        for button, tooltip in (
            (self._add_button, "Add URL"),
            (self._remove_button, "Remove"),
            (self._up_button, "Move up"),
            (self._down_button, "Move down"),
            (self._toggle_queue_button, "Hide queue"),
        ):
            button.setFixedSize(queue_button_size, queue_button_size)
            button.setToolTip(tooltip)

        budget_row = QHBoxLayout()
        budget_row.addWidget(self._budget_label)
        budget_row.addStretch(1)
        queue_buttons = QHBoxLayout()
        queue_buttons.setSpacing(2)
        queue_buttons.addWidget(self._add_button)
        queue_buttons.addWidget(self._remove_button)
        queue_buttons.addWidget(self._up_button)
        queue_buttons.addWidget(self._down_button)
        queue_buttons.addWidget(self._toggle_queue_button)
        budget_row.addLayout(queue_buttons)
        top_layout.addLayout(budget_row)
        top_layout.addWidget(self._budget_bar)

        self._queue_list = QueueListWidget(self)
        self._queue_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        top_layout.addWidget(self._queue_list, stretch=1)
        top_panel.setMinimumHeight(120)

        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        self._web = AnkiWebView(kind=AnkiWebViewKind.EDITOR)
        self._web.requiresCol = False
        self._web.setMinimumHeight(160)
        self._bridge.setup_player_webview(self._web)
        bottom_layout.addWidget(self._web, stretch=1)

        controls = QHBoxLayout()
        self._play_button = QPushButton("Play")
        self._pause_button = QPushButton("Pause")
        self._next_button = QPushButton("Next")
        self._fullscreen_button = QPushButton("Fullscreen")
        controls.addWidget(self._play_button)
        controls.addWidget(self._pause_button)
        controls.addWidget(self._next_button)
        controls.addWidget(self._fullscreen_button)
        bottom_layout.addLayout(controls)
        bottom_panel.setMinimumHeight(200)

        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.addWidget(top_panel)
        self._splitter.addWidget(bottom_panel)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        layout.addWidget(self._splitter, stretch=1)

        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.setInterval(250)
        self._splitter_save_timer.timeout.connect(self._save_state)

        self._width_save_timer = QTimer(self)
        self._width_save_timer.setSingleShot(True)
        self._width_save_timer.setInterval(250)
        self._width_save_timer.timeout.connect(self._save_state)

        self._layout_settle_timer = QTimer(self)
        self._layout_settle_timer.setSingleShot(True)
        self._layout_settle_timer.setInterval(_RESIZE_SETTLE_MS)
        self._layout_settle_timer.timeout.connect(self._on_layout_resize_settled)

        self._startup_grace_timer = QTimer(self)
        self._startup_grace_timer.setSingleShot(True)
        self._startup_grace_timer.setInterval(_STARTUP_GRACE_MS)
        self._startup_grace_timer.timeout.connect(self._on_startup_grace_elapsed)

        self._add_button.clicked.connect(self._prompt_add_video)
        self._remove_button.clicked.connect(self._remove_selected)
        self._up_button.clicked.connect(lambda: self._move_selected(-1))
        self._down_button.clicked.connect(lambda: self._move_selected(1))
        self._toggle_queue_button.clicked.connect(self._toggle_queue_visibility)
        self._play_button.clicked.connect(self._play_current)
        self._pause_button.clicked.connect(self._pause_playback)
        self._next_button.clicked.connect(self._play_next)
        self._fullscreen_button.clicked.connect(self._toggle_fullscreen)
        self._queue_list.itemDoubleClicked.connect(self._play_selected_item)
        self._apply_playback_button_visibility()

        self.setWidget(container)
        self.setObjectName("AnkiTubeDock")
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        area = self._dock_area()
        mw.addDockWidget(area, self)

    def _dock_area(self) -> Qt.DockWidgetArea:
        if self._persistence.dock_area_name() == "left":
            return Qt.DockWidgetArea.LeftDockWidgetArea
        return Qt.DockWidgetArea.RightDockWidgetArea

    def _config(self) -> dict:
        return get_config(self._addon_module)

    def _apply_playback_button_visibility(self) -> None:
        show = bool(self._config().get("dock_show_playback_buttons", True))
        for button in (
            self._play_button,
            self._pause_button,
            self._next_button,
            self._fullscreen_button,
        ):
            button.setVisible(show)

    def apply_settings(self) -> None:
        self._apply_playback_button_visibility()
        was_playing = self._is_playing
        self._bridge.player_ready = False
        if was_playing:
            self._bridge.pending_play = True
        player_url = self._bridge.player_url()
        self._web.load_url(player_url)
        if self._fullscreen is not None:
            self._fullscreen._web.load_url(player_url)

    def _pause_dock_player(self) -> None:
        self._bridge.pause()

    def _toggle_fullscreen(self) -> None:
        if self._fullscreen and self._fullscreen.isVisible():
            self._close_fullscreen()
        else:
            self._open_fullscreen()

    def _close_fullscreen(self) -> None:
        player = self._fullscreen
        if player is None or not player.isVisible() or player._closing:
            return
        player._web.eval("window.ankittube.pause();")
        player._web.evalWithCallback(
            "window.ankittube ? window.ankittube.getCurrentTime() : 0",
            lambda result: self._finish_close_fullscreen(player, result),
        )

    def _finish_close_fullscreen(
        self, player: FullscreenPlayer, result: object
    ) -> None:
        if self._fullscreen is not player:
            return
        self._fullscreen = None
        self._restore_dock_after_fullscreen(result)
        player._closing = True
        player.close()

    def _restore_dock_after_fullscreen(self, result: object) -> None:
        self._web.eval(
            "if (document.fullscreenElement) { document.exitFullscreen(); }"
        )
        item = self._current_item()
        if not item:
            return
        start_seconds = self._start_seconds_for(item.video_id)
        if result is not None:
            try:
                start_seconds = float(result)
                self._update_position_cache(item.video_id, start_seconds)
            except (TypeError, ValueError):
                pass
        self._bridge.load_video(item.video_id, start_seconds, autoplay=False)
        if self._is_playing and self._budget.has_time():
            self._bridge.eval_js("window.ankittube.play();")

    def _restore_splitter_sizes(self) -> None:
        sizes = self._persistence.load_splitter_sizes()
        if sizes:
            try:
                top, bottom = sizes
                total = self._splitter.height()
                if total > 0:
                    saved_total = top + bottom
                    if saved_total > 0 and abs(total - saved_total) > 20:
                        ratio = top / saved_total
                        top = max(self._top_panel.minimumHeight(), int(total * ratio))
                        bottom = max(200, total - top)
                        if top + bottom > total:
                            top = max(self._top_panel.minimumHeight(), total - bottom)
                    self._splitter.setSizes([top, bottom])
                    return
            except (TypeError, ValueError):
                pass
        total = max(self._splitter.height(), 400)
        self._splitter.setSizes([total // 4, (total * 3) // 4])

    def _restore_dock_width(self) -> None:
        width = self._target_dock_width
        if width is None or width < 200:
            return
        try:
            mw.resizeDocks([self], [width], Qt.Orientation.Horizontal)
        except Exception:
            pass

    def _apply_target_layout(self) -> None:
        self._restore_dock_width()
        if self._queue_visible and self._splitter.height() > 50:
            self._restore_splitter_sizes()
        self._layout_restored = True

    def _schedule_layout_settle(self) -> None:
        self._layout_settle_timer.start()

    def _on_layout_resize_settled(self) -> None:
        if not self._startup_complete:
            self._restore_dock_width()
            if self._queue_visible and self._splitter.height() > 50:
                self._restore_splitter_sizes()
            return
        if not self._layout_restored:
            self._apply_target_layout()
            return
        target = self._target_dock_width
        if target and self.width() < target - 10:
            self._restore_dock_width()

    def _on_startup_grace_elapsed(self) -> None:
        self._startup_complete = True
        self._apply_target_layout()

    def _finish_initial_layout(self) -> None:
        if not self._queue_visible:
            self._apply_queue_visibility()
        self._schedule_layout_settle()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._schedule_layout_settle()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        old_w = event.oldSize().width()
        new_w = event.size().width()
        mw_w = mw.width()

        if old_w != new_w:
            if self._startup_complete and self._last_mw_width is not None:
                mw_changed = abs(mw_w - self._last_mw_width) > 8
                if mw_changed and new_w < old_w - 2:
                    self._schedule_layout_settle()
                elif not mw_changed and abs(new_w - old_w) > 2:
                    self._target_dock_width = new_w
                    self._width_save_timer.start()

        self._last_mw_width = mw_w
        self._last_dock_width = new_w

        if not self._layout_restored:
            self._schedule_layout_settle()

    def _toggle_queue_visibility(self) -> None:
        if self._queue_visible:
            self._queue_splitter_sizes = self._splitter.sizes()
        self._queue_visible = not self._queue_visible
        self._apply_queue_visibility()
        self._save_state()

    def _apply_queue_visibility(self) -> None:
        visible = self._queue_visible
        self._queue_list.setVisible(visible)
        self._remove_button.setEnabled(visible)
        self._up_button.setEnabled(visible)
        self._down_button.setEnabled(visible)
        self._toggle_queue_button.setText("▾" if visible else "▸")
        self._toggle_queue_button.setToolTip(
            "Hide queue" if visible else "Show queue"
        )
        self._top_panel.setMinimumHeight(120 if visible else 0)

        def adjust_splitter() -> None:
            if visible:
                if self._queue_splitter_sizes:
                    self._splitter.setSizes(self._queue_splitter_sizes)
                else:
                    self._restore_splitter_sizes()
            else:
                top = self._top_panel.sizeHint().height()
                total = max(sum(self._splitter.sizes()), top + 200)
                self._splitter.setSizes([top, total - top])

        QTimer.singleShot(0, adjust_splitter)

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        self._splitter_save_timer.start()

    def _load_state(self) -> None:
        positions, lifetime_earned, queue_visible = self._persistence.load_queue(
            self._queue
        )
        self._positions_cache = positions
        self._lifetime_earned_seconds = lifetime_earned
        self._queue_visible = queue_visible
        self._refresh_queue_ui()
        QTimer.singleShot(0, self._backfill_missing_durations)
        log(
            f"loaded queue: {[item.video_id for item in self._queue.items]} "
            f"index={self._queue.current_index} positions={self._positions_cache}"
        )

    def _start_seconds_for(self, video_id: str) -> float:
        return float(self._positions_cache.get(video_id, 0))

    def _update_position_cache(self, video_id: str, seconds: float) -> None:
        rounded = round(max(0.0, seconds), 1)
        if self._positions_cache.get(video_id) == rounded:
            return
        if not self._queue.labels_changed_for_position(
            video_id,
            rounded,
            self._positions_cache,
            seconds_per_card=self._persistence.seconds_per_card(),
            budget_seconds=self._budget.seconds,
        ):
            self._positions_cache[video_id] = rounded
            return
        self._positions_cache[video_id] = rounded
        self._refresh_queue_ui()

    def _poll_playback_position(self) -> None:
        item = self._current_item()
        if item is None or not self._is_playing:
            return

        def on_time(result: object) -> None:
            if result is None:
                return
            try:
                seconds = float(result)
            except (TypeError, ValueError):
                return
            self._update_position_cache(item.video_id, seconds)

        self._bridge.eval_with_callback(
            "window.ankittube ? window.ankittube.getCurrentTime() : 0",
            on_time,
        )

    def _capture_playback_position(self, callback=None) -> None:
        def on_time(result: object) -> None:
            item = self._current_item()
            if item is not None and result is not None:
                try:
                    self._update_position_cache(item.video_id, float(result))
                except (TypeError, ValueError):
                    pass
            if callback:
                callback()

        self._bridge.eval_with_callback(
            "window.ankittube ? window.ankittube.getCurrentTime() : 0",
            on_time,
        )

    def _save_state(self) -> None:
        panel_sizes = None
        if hasattr(self, "_splitter") and self._queue_visible:
            panel_sizes = self._splitter.sizes()
        self._persistence.save_state(
            self._queue,
            positions=self._positions_cache,
            lifetime_earned_seconds=self._lifetime_earned_seconds,
            queue_visible=self._queue_visible,
            dock_panel_sizes=panel_sizes,
            target_dock_width=self._target_dock_width,
        )

    def _remove_player_shortcut_filter(self) -> None:
        app = QApplication.instance()
        if app is not None and self._player_shortcut_filter is not None:
            app.removeEventFilter(self._player_shortcut_filter)
            self._player_shortcut_filter._dock = None
        self._player_shortcut_filter = None

    def _remove_mw_resize_filter(self) -> None:
        if self._mw_resize_filter is not None:
            mw.removeEventFilter(self._mw_resize_filter)
            self._mw_resize_filter = None

    def shutdown(self) -> None:
        log("AnkiTube dock shutting down")
        self._timer.stop()
        self._startup_grace_timer.stop()
        self._layout_settle_timer.stop()
        self._remove_player_shortcut_filter()
        self._remove_mw_resize_filter()

        def finish_shutdown() -> None:
            if self._hold_paused:
                self.hold_pause_end()
            self._save_state()
            self._bridge.unregister()
            if self._fullscreen:
                self._fullscreen._closing = True
                self._fullscreen.close()

        if self._bridge.player_ready:
            self._capture_playback_position(finish_shutdown)
        else:
            finish_shutdown()

    def on_budget_changed(self) -> None:
        self._update_budget_ui()
        if not self._budget.has_time() and self._is_playing:
            self._handle_budget_exhausted()

    def on_card_answered(self) -> None:
        reward = self._persistence.seconds_per_card()
        self._budget.add_seconds(reward)
        self._reward_history.append(reward)
        self._lifetime_earned_seconds += reward
        self._budget.save()
        self._refresh_queue_ui()
        self._save_state()
        log(
            f"card answered: +{reward}s "
            f"(history={self._reward_history}, earned={self._lifetime_earned_seconds})"
        )

    def on_review_undo(self) -> None:
        if self._reward_history:
            reward = self._reward_history.pop()
        else:
            reward = self._persistence.seconds_per_card()
        self._budget.subtract_seconds(reward)
        self._lifetime_earned_seconds = max(0, self._lifetime_earned_seconds - reward)
        self._budget.save()
        self._refresh_queue_ui()
        self._save_state()
        log(
            f"review undo: -{reward}s "
            f"(history={self._reward_history}, earned={self._lifetime_earned_seconds})"
        )

    def _update_budget_ui(self) -> None:
        seconds = self._budget.seconds
        cap = self._budget.max_seconds()
        self._budget_label.setText(f"Watch time remaining: {format_seconds(seconds)}")
        self._budget_bar.setRange(0, cap)
        self._budget_bar.setValue(min(seconds, cap))

    def _refresh_queue_ui(self) -> None:
        selected_row = self._queue_list.currentRow()
        self._queue_list.clear()
        for label in self._queue.item_labels(
            seconds_per_card=self._persistence.seconds_per_card(),
            budget_seconds=self._budget.seconds,
            positions=self._positions_cache,
            is_playing=self._is_playing,
        ):
            QListWidgetItem(label, self._queue_list)
        if 0 <= selected_row < len(self._queue.items):
            self._queue_list.setCurrentRow(selected_row)

    def _backfill_missing_durations(self) -> None:
        for item in self._queue.items_missing_duration():
            self._fetch_metadata_for_item(item.video_id)

    def _fetch_metadata_for_item(self, video_id: str) -> None:
        self._metadata_token += 1
        token = self._metadata_token
        self._metadata_tokens[video_id] = token

        def on_complete(title: str, duration: Optional[int]) -> None:
            if self._metadata_tokens.get(video_id) != token:
                return
            self._metadata_tokens.pop(video_id, None)
            changed = self._queue.update_metadata(
                video_id, title=title, duration_seconds=duration
            )
            if changed:
                self._refresh_queue_ui()
                self._save_state()

        fetch_video_metadata_async(video_id, on_complete)

    def _update_duration_from_player(self) -> None:
        item = self._current_item()
        if item is None or item.duration_seconds is not None:
            return

        def on_duration(result: object) -> None:
            if result is None:
                return
            try:
                seconds = int(float(result))
            except (TypeError, ValueError):
                return
            if seconds <= 0 or item.duration_seconds is not None:
                return
            item.duration_seconds = seconds
            self._refresh_queue_ui()
            self._save_state()

        self._bridge.eval_with_callback(
            "window.ankittube ? window.ankittube.getDuration() : 0",
            on_duration,
        )

    def _prompt_add_video(self) -> None:
        url, accepted = QInputDialog.getText(
            mw,
            "Add YouTube Video",
            "Paste a YouTube URL or 11-character video ID:",
        )
        if not accepted:
            return
        self.add_video_url(url)

    def add_urls_from_mime(self, mime, *, at_top: bool = False) -> bool:
        video_ids = extract_video_ids_from_mime(mime)
        if at_top:
            video_ids = list(reversed(video_ids))

        added = 0
        for video_id in video_ids:
            insert_index = 0 if at_top else len(self._queue.items)
            if self._add_video_by_id(
                video_id, warn_on_duplicate=False, insert_index=insert_index
            ):
                added += 1
        if added:
            where = "top" if at_top else "bottom"
            log(f"drop added {added} video(s) at {where} of queue")
        return added > 0

    def add_video_url(self, url: str) -> bool:
        text = normalize_youtube_url(url)
        video_ids = extract_all_video_ids(text)
        if not video_ids:
            showWarning("That does not look like a valid YouTube URL or video ID.")
            return False

        for video_id in video_ids:
            if self._add_video_by_id(video_id, warn_on_duplicate=False):
                return True

        showWarning("That video is already in the queue.")
        log(f"add_video_url duplicate for {video_ids!r} in {text!r}")
        return False

    def _add_video_by_id(
        self,
        video_id: str,
        *,
        warn_on_duplicate: bool,
        insert_index: int | None = None,
    ) -> bool:
        if self._queue.contains(video_id):
            if warn_on_duplicate:
                showWarning("That video is already in the queue.")
            return False

        was_empty = not self._queue.items
        insert_index = self._queue.insert(
            video_id,
            title=video_id,
            duration_seconds=None,
            insert_index=insert_index,
            metadata_pending=True,
        )
        self._refresh_queue_ui()
        self._save_state()
        self._fetch_metadata_for_item(video_id)
        if was_empty:
            self._queue.current_index = 0
            self._load_current_video(autoplay=False)
        log(f"added video {video_id!r} at index {insert_index}")
        return True

    def _remove_finished_current(self) -> bool:
        removed = self._queue.remove_current_finished()
        if removed:
            self._positions_cache.pop(removed.video_id, None)
            log(f"auto-removed finished video {removed.video_id!r}")
            return True
        return False

    def _remove_selected(self) -> None:
        row = self._queue_list.currentRow()
        if row < 0 or row >= len(self._queue.items):
            return
        removed = self._queue.remove_at(row)
        if removed is None:
            return
        if not self._queue.items:
            self._queue.current_index = -1
            self._pause_playback()
            self._bridge.eval_js("window.ankittube.clear();")
        elif row == self._queue.current_index:
            self._queue.current_index = min(row, len(self._queue.items) - 1)
            self._load_current_video(autoplay=self._is_playing)
        elif row < self._queue.current_index:
            self._queue.current_index -= 1
        self._refresh_queue_ui()
        self._save_state()

    def _move_selected(self, delta: int) -> None:
        row = self._queue_list.currentRow()
        new_row = self._queue.move(row, delta)
        if new_row is None:
            return
        self._refresh_queue_ui()
        self._queue_list.setCurrentRow(new_row)
        self._save_state()

    def _play_selected_item(self, _item: QListWidgetItem) -> None:
        row = self._queue_list.currentRow()
        if row < 0:
            return
        self._queue.current_index = row
        self._play_current()

    def _current_item(self):
        return self._queue.current_item()

    def _load_current_video(self, autoplay: bool = False) -> None:
        item = self._current_item()
        if not item:
            return
        start = self._start_seconds_for(item.video_id)
        self._bridge.load_video(item.video_id, start, autoplay=autoplay)
        self._refresh_queue_ui()

    def _sync_player_with_queue(self) -> None:
        item = self._current_item()
        if not item:
            log("sync_player_with_queue: no current item")
            return
        log(
            f"sync_player_with_queue: video_id={item.video_id} "
            f"player_ready={self._bridge.player_ready} "
            f"pending_play={self._bridge.pending_play}"
        )
        if self._bridge.player_ready:
            self._load_current_video(autoplay=self._bridge.pending_play)

    def _play_current(self) -> None:
        if not self._current_item():
            showWarning("Add at least one video to the queue first.")
            return
        if not self._budget.has_time():
            self._paused_for_budget = True
            showWarning(
                "Your watch-time budget is empty.\n\n"
                "Review flashcards to earn more time."
            )
            return
        self._paused_for_budget = False
        self._bridge.pending_play = True
        item = self._current_item()
        assert item is not None
        self._bridge.eval_js(
            f"window.ankittube.resumeOrPlay({json.dumps(item.video_id)});"
        )
        self._refresh_queue_ui()

    def _pause_playback(self) -> None:
        self._is_playing = False
        self._timer.stop()
        self._position_save_ticks = 0
        self._bridge.pause()
        self._refresh_queue_ui()

    def _toggle_playback(self) -> None:
        if self._is_playing:
            self._pause_playback()
        else:
            self._play_current()

    def _seek_relative(self, delta_seconds: float) -> None:
        if not self._current_item():
            return
        self._bridge.eval_js(f"window.ankittube.seekBy({delta_seconds});")

    def _adjust_volume(self, delta: int) -> None:
        if not self._current_item():
            return
        self._bridge.eval_js(f"window.ankittube.adjustVolume({delta});")

    def _toggle_captions(self) -> None:
        if not self._current_item():
            return
        self._bridge.eval_js("window.ankittube.toggleCaptions();")

    def hold_pause_begin(self) -> None:
        if self._hold_paused:
            return
        if not self._current_item():
            return
        self._hold_paused = True
        self._was_playing_before_hold = self._is_playing
        if self._is_playing:
            self._is_playing = False
            self._timer.stop()
            self._position_save_ticks = 0
            self._bridge.pause()
            self._refresh_queue_ui()
        log("hold pause begin")

    def hold_pause_end(self) -> None:
        if not self._hold_paused:
            return
        self._hold_paused = False
        should_resume = (
            self._was_playing_before_hold
            and self._budget.has_time()
            and not self._paused_for_budget
        )
        self._was_playing_before_hold = False
        if should_resume:
            item = self._current_item()
            if item:
                self._bridge.pending_play = True
                self._bridge.eval_js(
                    f"window.ankittube.resumeOrPlay({json.dumps(item.video_id)});"
                )
                self._refresh_queue_ui()
        log("hold pause end")

    def _play_next(self) -> None:
        if not self._queue.items:
            return
        if self._queue.current_index < len(self._queue.items) - 1:
            self._queue.current_index += 1
            self._save_state()
            self._play_current()
        else:
            self._pause_playback()

    def _open_fullscreen(self) -> None:
        if self._fullscreen and self._fullscreen.isVisible():
            self._fullscreen.showFullScreen()
            self._fullscreen.raise_()
            self._fullscreen.activateWindow()
            return

        item = self._current_item()
        if not item:
            return

        self._pause_dock_player()

        def show_fullscreen(start_seconds: float) -> None:
            self._fullscreen = FullscreenPlayer(self)
            self._update_position_cache(item.video_id, start_seconds)
            self._fullscreen.prepare_video(
                item.video_id,
                start_seconds,
                autoplay=self._is_playing and self._budget.has_time(),
            )
            self._fullscreen.showFullScreen()
            self._fullscreen.raise_()
            self._fullscreen.activateWindow()

        def on_time(result: object) -> None:
            try:
                start_seconds = float(result) if result is not None else 0.0
            except (TypeError, ValueError):
                start_seconds = self._start_seconds_for(item.video_id)
            show_fullscreen(start_seconds)

        self._web.evalWithCallback(
            "window.ankittube ? window.ankittube.getCurrentTime() : 0",
            on_time,
        )

    def _on_timer_tick(self) -> None:
        if not self._is_playing:
            return
        if not self._budget.consume_second():
            self._handle_budget_exhausted()
            return
        self._budget.save()
        self._update_budget_ui()
        self._poll_playback_position()
        self._position_save_ticks += 1
        if self._position_save_ticks >= 10:
            self._position_save_ticks = 0
            self._capture_playback_position(self._save_state)

    def _handle_budget_exhausted(self) -> None:
        self._paused_for_budget = True
        self._is_playing = False
        self._timer.stop()
        self._bridge.pause()
        self._refresh_queue_ui()
