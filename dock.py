from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from aqt import gui_hooks, mw
from aqt.qt import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QDialog,
    QDockWidget,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QEvent,
    QHBoxLayout,
    QInputDialog,
    QKeyEvent,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QObject,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QResizeEvent,
    QShowEvent,
    QSplitter,
    Qt,
    QTimer,
    QTextEdit,
    QUrl,
    QUrlQuery,
    QVBoxLayout,
    QWidget,
    QWebEngineFullScreenRequest,
    QWebEngineSettings,
)
from aqt.utils import qconnect, showWarning
from aqt.webview import AnkiWebView, AnkiWebViewKind

from .budget import BudgetManager
from .logger import log
from .utils import (
    extract_all_video_ids,
    extract_video_id,
    extract_video_ids_from_mime,
    allocate_queue_card_progress,
    fetch_video_duration,
    fetch_video_title,
    format_queue_item_label,
    format_seconds,
    mime_has_youtube_url,
    normalize_youtube_url,
)

_STARTUP_GRACE_MS = 2000
_RESIZE_SETTLE_MS = 400


@dataclass
class QueueItem:
    video_id: str
    title: str
    duration_seconds: Optional[int] = None


class _UrlDropMixin:
    _dock: "AnkiTubeDock"

    def _accept_url_drag(self, event: QDragEnterEvent | QDropEvent) -> bool:
        if mime_has_youtube_url(event.mimeData()):
            event.acceptProposedAction()
            return True
        return False

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not self._accept_url_drag(event):
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if not self._accept_url_drag(event):
            super().dragMoveEvent(event)

    def _drop_at_top(self, event: QDropEvent) -> bool:
        height = self.height()
        if height <= 0:
            return False
        return event.position().y() < height / 2

    def dropEvent(self, event: QDropEvent) -> None:
        at_top = self._drop_at_top(event)
        if self._dock.add_urls_from_mime(event.mimeData(), at_top=at_top):
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class AnkiTubePanel(_UrlDropMixin, QWidget):
    def __init__(self, dock: "AnkiTubeDock") -> None:
        super().__init__()
        self._dock = dock
        self.setAcceptDrops(True)


class QueueListWidget(_UrlDropMixin, QListWidget):
    def __init__(self, dock: "AnkiTubeDock") -> None:
        super().__init__()
        self._dock = dock
        self.setAcceptDrops(True)


class _MainWindowResizeFilter(QObject):
    def __init__(self, dock: "AnkiTubeDock") -> None:
        super().__init__(dock)
        self._dock = dock

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is mw and event.type() == QEvent.Type.Resize:
            self._dock._schedule_layout_settle()
        return False


class _PlayerShortcutFilter(QObject):
    SEEK_SECONDS = 5
    VOLUME_STEP = 5

    def __init__(self, dock: "AnkiTubeDock") -> None:
        super().__init__(dock)
        self._dock = dock

    def _dock_alive(self) -> bool:
        dock = self._dock
        if dock is None:
            return False
        try:
            dock.isVisible()
        except RuntimeError:
            return False
        return True

    def _should_handle(self) -> bool:
        if not self._dock_alive():
            return False
        if not self._dock._current_item():
            return False
        fullscreen = self._dock._fullscreen
        if fullscreen is not None and fullscreen.isVisible():
            return True
        if not self._dock.isVisible():
            return False
        focus = QApplication.focusWidget()
        if focus is not None and isinstance(
            focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ):
            return False
        return True

    def _is_option_key(self, key: int) -> bool:
        return key in (Qt.Key.Key_Alt, Qt.Key.Key_AltGr)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        if not isinstance(event, QKeyEvent):
            return False
        try:
            if not self._should_handle():
                return False

            key_event = event
            key = key_event.key()

            if self._is_option_key(key):
                if event.type() == QEvent.Type.KeyPress:
                    if key_event.isAutoRepeat():
                        return self._dock._hold_paused
                    self._dock.hold_pause_begin()
                    return True
                if self._dock._hold_paused:
                    self._dock.hold_pause_end()
                return True

            if event.type() != QEvent.Type.KeyPress:
                return False
            if key_event.isAutoRepeat():
                return False

            modifiers = key_event.modifiers()
            if modifiers not in (
                Qt.KeyboardModifier.NoModifier,
                Qt.KeyboardModifier.KeypadModifier,
            ):
                return False

            if key == Qt.Key.Key_P:
                self._dock._toggle_playback()
                return True
            if key == Qt.Key.Key_G:
                self._dock._toggle_fullscreen()
                return True
            if key == Qt.Key.Key_C:
                self._dock._toggle_captions()
                return True
            if key == Qt.Key.Key_Left:
                self._dock._seek_relative(-self.SEEK_SECONDS)
                return True
            if key == Qt.Key.Key_Right:
                self._dock._seek_relative(self.SEEK_SECONDS)
                return True
            if key == Qt.Key.Key_Up:
                self._dock._adjust_volume(self.VOLUME_STEP)
                return True
            if key == Qt.Key.Key_Down:
                self._dock._adjust_volume(-self.VOLUME_STEP)
                return True
        except RuntimeError:
            return False

        return False


class FullscreenPlayer(QMainWindow):
    def __init__(self, dock: "AnkiTubeDock") -> None:
        super().__init__(parent=mw, flags=Qt.WindowType.Window)
        self._dock = dock
        self._closing = False
        self._pending_video_id: Optional[str] = None
        self._pending_start_seconds = 0.0
        self._pending_autoplay = False
        self.setWindowTitle("AnkiTube")
        self._web = AnkiWebView(kind=AnkiWebViewKind.EDITOR)
        self._web.requiresCol = False
        self.setCentralWidget(self._web)
        self._web.set_open_links_externally(False)
        self._web.set_bridge_command(self._dock._on_bridge_command, self)
        self._web.load_url(dock._player_url())
        dock._setup_player_webview(self._web)
        qconnect(self._web.loadFinished, self._on_load_finished)

    def prepare_video(self, video_id: str, start_seconds: float, autoplay: bool) -> None:
        self._pending_video_id = video_id
        self._pending_start_seconds = start_seconds
        self._pending_autoplay = autoplay
        self._sync_video()

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            self._sync_video()

    def _sync_video(self) -> None:
        if not self._pending_video_id:
            return
        video_id = self._pending_video_id
        start = self._pending_start_seconds
        autoplay = self._pending_autoplay
        self._web.eval(
            "window.ankittube.loadVideo("
            f"{json.dumps(video_id)}, {start});"
        )
        if autoplay:
            self._web.eval("window.ankittube.play();")

    def closeEvent(self, event) -> None:
        if self._closing:
            self._dock._fullscreen = None
            super().closeEvent(event)
            return
        event.ignore()
        self._dock._close_fullscreen()


class AnkiTubeDock(QDockWidget):
    CMD_PREFIX = "ankittube:"

    def __init__(self, addon_module: str, budget: BudgetManager) -> None:
        super().__init__("AnkiTube", mw)
        self._addon_module = addon_module
        self._budget = budget
        self._queue: list[QueueItem] = []
        self._current_index = -1
        self._is_playing = False
        self._paused_for_budget = False
        self._player_ready = False
        self._pending_play = False
        self._reward_history: list[int] = []
        self._positions_cache: dict[str, float] = {}
        self._lifetime_earned_seconds = 0
        self._position_save_ticks = 0
        self._hold_paused = False
        self._was_playing_before_hold = False
        self._player_shortcut_filter: _PlayerShortcutFilter | None = None
        self._fullscreen: Optional[FullscreenPlayer] = None
        self._queue_visible = True
        self._queue_splitter_sizes: Optional[list[int]] = None
        self._layout_restored = False
        self._startup_complete = False
        self._target_dock_width: Optional[int] = None
        self._last_mw_width: Optional[int] = None
        self._last_dock_width: Optional[int] = None
        self._mw_resize_filter: _MainWindowResizeFilter | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_timer_tick)

        log("AnkiTube dock initializing")
        self._build_ui()
        self._load_state()
        self._update_budget_ui()
        QTimer.singleShot(0, self._finish_initial_layout)

        package = mw.addonManager.addonFromModule(addon_module)
        player_url = self._player_url()
        log(f"addon package={package!r} module={addon_module!r}")
        log(f"player url={player_url.toString()}")
        log(
            f"queue size={len(self._queue)} budget={self._budget.seconds}s "
            f"current_index={self._current_index}"
        )

        mw.addonManager.setWebExports(addon_module, r"web/.*")
        self._web.set_open_links_externally(False)
        self._web.set_bridge_command(self._on_bridge_command, self)
        qconnect(self._web.loadFinished, self._on_web_load_finished)
        gui_hooks.webview_did_receive_js_message.append(self._on_js_message)
        self._web.load_url(player_url)

        self._player_shortcut_filter = _PlayerShortcutFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._player_shortcut_filter)

        self._target_dock_width = self._load_target_dock_width()
        self._mw_resize_filter = _MainWindowResizeFilter(self)
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
        self._setup_player_webview(self._web)
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
        config = mw.addonManager.getConfig(self._addon_module) or {}
        area_name = str(config.get("dock_area", "right")).lower()
        if area_name == "left":
            return Qt.DockWidgetArea.LeftDockWidgetArea
        return Qt.DockWidgetArea.RightDockWidgetArea

    def _player_url(self) -> QUrl:
        package = mw.addonManager.addonFromModule(self._addon_module)
        config = self._config()
        query = QUrlQuery()
        query.addQueryItem(
            "controls",
            "1" if config.get("youtube_show_controls", True) else "0",
        )
        query.addQueryItem(
            "fs",
            "1" if config.get("youtube_show_fullscreen", True) else "0",
        )
        url = QUrl(f"{mw.serverURL()}_addons/{package}/web/player.html")
        url.setQuery(query)
        return url

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
        self._player_ready = False
        if was_playing:
            self._pending_play = True
        player_url = self._player_url()
        self._web.load_url(player_url)
        if self._fullscreen is not None:
            self._fullscreen._web.load_url(player_url)

    def _setup_player_webview(self, web: AnkiWebView) -> None:
        web.settings().setAttribute(
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True
        )
        qconnect(
            web.page().fullScreenRequested,
            lambda request, player_web=web: self._on_fullscreen_requested(
                player_web, request
            ),
        )

    def _on_fullscreen_requested(
        self, web: AnkiWebView, request: QWebEngineFullScreenRequest
    ) -> None:
        log(
            "fullscreen requested "
            f"toggle_on={request.toggleOn()} dock={web is self._web}"
        )
        request.accept()
        if request.toggleOn():
            if web is self._web:
                self._open_fullscreen()
            return
        if (
            self._fullscreen is not None
            and self._fullscreen.isVisible()
            and web is self._fullscreen._web
        ):
            self._close_fullscreen()

    def _pause_dock_player(self) -> None:
        self._web.eval("window.ankittube.pause();")

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
        self._web.eval(
            "window.ankittube.loadVideo("
            f"{json.dumps(item.video_id)}, {start_seconds});"
        )
        if self._is_playing and self._budget.has_time():
            self._web.eval("window.ankittube.play();")

    def _active_web(self) -> AnkiWebView:
        if self._fullscreen and self._fullscreen.isVisible():
            return self._fullscreen._web
        return self._web

    def _eval_js(self, script: str) -> None:
        log(f"eval js: {script}")
        self._active_web().eval(script)

    def _eval_js_check(self, script: str, label: str) -> None:
        def on_result(result: object) -> None:
            log(f"eval result ({label}): {result!r}")

        self._active_web().evalWithCallback(script, on_result)

    def _config(self) -> dict:
        return mw.addonManager.getConfig(self._addon_module) or {}

    def _load_target_dock_width(self) -> Optional[int]:
        width = self._config().get("dock_width")
        if isinstance(width, (int, float)):
            width = int(width)
            if width >= 200:
                return width
        return None

    def _restore_splitter_sizes(self) -> None:
        sizes = self._config().get("dock_panel_sizes")
        if isinstance(sizes, list) and len(sizes) == 2:
            try:
                top, bottom = int(sizes[0]), int(sizes[1])
                if top > 0 and bottom > 0:
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
        config = self._config()
        queue_data = config.get("queue", [])
        self._queue = []
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
                self._queue.append(
                    QueueItem(
                        video_id=video_id,
                        title=str(title) if title else video_id,
                        duration_seconds=duration_seconds,
                    )
                )
        self._refresh_queue_ui()
        QTimer.singleShot(0, self._backfill_missing_durations)
        saved_index = config.get("current_index", 0)
        if self._queue:
            if isinstance(saved_index, int) and 0 <= saved_index < len(self._queue):
                self._current_index = saved_index
            else:
                self._current_index = 0
        positions = config.get("positions", {})
        if isinstance(positions, dict):
            self._positions_cache = {
                str(video_id): float(seconds)
                for video_id, seconds in positions.items()
                if isinstance(video_id, str)
            }
        earned = config.get("lifetime_earned_seconds", 0)
        self._lifetime_earned_seconds = max(0, int(earned)) if isinstance(
            earned, (int, float)
        ) else 0
        queue_visible = config.get("queue_visible", True)
        self._queue_visible = queue_visible if isinstance(queue_visible, bool) else True
        log(
            f"loaded queue: {[item.video_id for item in self._queue]} "
            f"index={self._current_index} positions={self._positions_cache}"
        )

    def _start_seconds_for(self, video_id: str) -> float:
        return float(self._positions_cache.get(video_id, 0))

    def _update_position_cache(self, video_id: str, seconds: float) -> None:
        rounded = round(max(0.0, seconds), 1)
        if self._positions_cache.get(video_id) == rounded:
            return
        new_positions = dict(self._positions_cache)
        new_positions[video_id] = rounded
        if self._queue_item_labels(new_positions) == self._queue_item_labels():
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

        self._active_web().evalWithCallback(
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

        self._active_web().evalWithCallback(
            "window.ankittube ? window.ankittube.getCurrentTime() : 0",
            on_time,
        )

    def _save_state(self) -> None:
        config = self._config()
        config["queue"] = [
            {
                "video_id": item.video_id,
                "title": item.title,
                "duration_seconds": item.duration_seconds,
            }
            for item in self._queue
        ]
        config["current_index"] = self._current_index
        config["positions"] = dict(self._positions_cache)
        config["lifetime_earned_seconds"] = self._lifetime_earned_seconds
        config["queue_visible"] = self._queue_visible
        if hasattr(self, "_splitter") and self._queue_visible:
            config["dock_panel_sizes"] = self._splitter.sizes()
        if self._target_dock_width and self._target_dock_width >= 200:
            config["dock_width"] = self._target_dock_width
        mw.addonManager.writeConfig(self._addon_module, config)
        self._budget.save()

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
            gui_hooks.webview_did_receive_js_message.remove(self._on_js_message)
            if self._fullscreen:
                self._fullscreen._closing = True
                self._fullscreen.close()

        if self._player_ready:
            self._capture_playback_position(finish_shutdown)
        else:
            finish_shutdown()

    def on_budget_changed(self) -> None:
        self._update_budget_ui()
        if not self._budget.has_time() and self._is_playing:
            self._handle_budget_exhausted()

    def _seconds_per_card(self) -> int:
        return int(self._config().get("seconds_per_card", 15))

    def on_card_answered(self) -> None:
        reward = self._seconds_per_card()
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
            reward = self._seconds_per_card()
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

    def _queue_item_labels(
        self, playback_positions: dict[str, float] | None = None
    ) -> list[str]:
        positions = (
            self._positions_cache
            if playback_positions is None
            else playback_positions
        )
        seconds_per_card = self._seconds_per_card()
        card_progress = allocate_queue_card_progress(
            [item.duration_seconds for item in self._queue],
            self._budget.seconds,
            seconds_per_card,
            video_ids=[item.video_id for item in self._queue],
            playback_positions=positions,
        )
        labels: list[str] = []
        for index, (item, progress) in enumerate(zip(self._queue, card_progress)):
            playing_prefix = "▶ " if index == self._current_index and self._is_playing else ""
            labels.append(
                format_queue_item_label(
                    item.title,
                    progress.cards_done,
                    progress.cards_total,
                    playing_prefix=playing_prefix,
                )
            )
        return labels

    def _refresh_queue_ui(self) -> None:
        selected_row = self._queue_list.currentRow()
        self._queue_list.clear()
        for label in self._queue_item_labels():
            QListWidgetItem(label, self._queue_list)
        if 0 <= selected_row < len(self._queue):
            self._queue_list.setCurrentRow(selected_row)

    def _backfill_missing_durations(self) -> None:
        updated = False
        for item in self._queue:
            if item.duration_seconds is not None:
                continue
            duration = fetch_video_duration(item.video_id)
            if duration is None:
                continue
            item.duration_seconds = duration
            updated = True
        if updated:
            self._refresh_queue_ui()
            self._save_state()

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

        self._active_web().evalWithCallback(
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
            insert_index = 0 if at_top else len(self._queue)
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
        if any(item.video_id == video_id for item in self._queue):
            if warn_on_duplicate:
                showWarning("That video is already in the queue.")
            return False

        was_empty = not self._queue
        if insert_index is None:
            insert_index = len(self._queue)
        insert_index = max(0, min(insert_index, len(self._queue)))

        title = fetch_video_title(video_id)
        duration_seconds = fetch_video_duration(video_id)
        self._queue.insert(
            insert_index,
            QueueItem(
                video_id=video_id,
                title=title,
                duration_seconds=duration_seconds,
            ),
        )
        if self._current_index < 0:
            self._current_index = 0
        elif insert_index <= self._current_index:
            self._current_index += 1
        self._refresh_queue_ui()
        self._save_state()
        if was_empty:
            self._current_index = 0
            self._load_current_video(autoplay=False)
        log(f"added video {video_id!r} at index {insert_index}")
        return True

    def _remove_finished_current(self) -> bool:
        """Remove the currently playing video after it finishes. Returns True if removed."""
        if not self._current_item():
            return False
        was_last = self._current_index >= len(self._queue) - 1
        removed = self._queue.pop(self._current_index)
        self._positions_cache.pop(removed.video_id, None)
        if not self._queue:
            self._current_index = -1
        elif was_last:
            self._current_index = len(self._queue) - 1
        log(f"auto-removed finished video {removed.video_id!r}")
        return True

    def _remove_selected(self) -> None:
        row = self._queue_list.currentRow()
        if row < 0 or row >= len(self._queue):
            return
        removed = self._queue.pop(row)
        if not self._queue:
            self._current_index = -1
            self._pause_playback()
            self._eval_js("window.ankittube.clear();")
        elif row == self._current_index:
            self._current_index = min(row, len(self._queue) - 1)
            self._load_current_video(autoplay=self._is_playing)
        elif row < self._current_index:
            self._current_index -= 1
        self._refresh_queue_ui()
        self._save_state()

    def _move_selected(self, delta: int) -> None:
        row = self._queue_list.currentRow()
        new_row = row + delta
        if row < 0 or new_row < 0 or new_row >= len(self._queue):
            return
        self._queue[row], self._queue[new_row] = self._queue[new_row], self._queue[row]
        if self._current_index == row:
            self._current_index = new_row
        elif self._current_index == new_row:
            self._current_index = row
        self._refresh_queue_ui()
        self._queue_list.setCurrentRow(new_row)
        self._save_state()

    def _play_selected_item(self, _item: QListWidgetItem) -> None:
        row = self._queue_list.currentRow()
        if row < 0:
            return
        self._current_index = row
        self._play_current()

    def _current_item(self) -> Optional[QueueItem]:
        if self._current_index < 0 or self._current_index >= len(self._queue):
            return None
        return self._queue[self._current_index]

    def _load_current_video(self, autoplay: bool = False) -> None:
        item = self._current_item()
        if not item:
            return
        start = self._start_seconds_for(item.video_id)
        if autoplay:
            self._pending_play = True
            self._eval_js(
                f"window.ankittube.resumeOrPlay({json.dumps(item.video_id)});"
            )
        else:
            self._pending_play = False
            self._eval_js(
                f"window.ankittube.loadVideo({json.dumps(item.video_id)}, {start});"
            )
        self._refresh_queue_ui()

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
        self._pending_play = True
        item = self._current_item()
        assert item is not None
        self._eval_js(f"window.ankittube.resumeOrPlay({json.dumps(item.video_id)});")
        self._refresh_queue_ui()

    def _pause_playback(self) -> None:
        self._is_playing = False
        self._timer.stop()
        self._position_save_ticks = 0
        self._eval_js("window.ankittube.pause();")
        self._refresh_queue_ui()

    def _toggle_playback(self) -> None:
        if self._is_playing:
            self._pause_playback()
        else:
            self._play_current()

    def _seek_relative(self, delta_seconds: float) -> None:
        if not self._current_item():
            return
        self._eval_js(f"window.ankittube.seekBy({delta_seconds});")

    def _adjust_volume(self, delta: int) -> None:
        if not self._current_item():
            return
        self._eval_js(f"window.ankittube.adjustVolume({delta});")

    def _toggle_captions(self) -> None:
        if not self._current_item():
            return
        self._eval_js("window.ankittube.toggleCaptions();")

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
            self._eval_js("window.ankittube.pause();")
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
                self._pending_play = True
                self._eval_js(
                    f"window.ankittube.resumeOrPlay({json.dumps(item.video_id)});"
                )
                self._refresh_queue_ui()
        log("hold pause end")

    def _play_next(self) -> None:
        if not self._queue:
            return
        if self._current_index < len(self._queue) - 1:
            self._current_index += 1
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
        self._eval_js("window.ankittube.pause();")
        self._refresh_queue_ui()

    def _on_web_load_finished(self, ok: bool) -> None:
        log(f"webview loadFinished ok={ok} url={self._player_url().toString()}")
        if not ok:
            return
        self._eval_js_check("typeof window.ankittube", "ankittube type")
        self._eval_js_check("document.title", "document title")
        self._sync_player_with_queue()

    def _sync_player_with_queue(self) -> None:
        item = self._current_item()
        if not item:
            log("sync_player_with_queue: no current item")
            return
        log(
            f"sync_player_with_queue: video_id={item.video_id} "
            f"player_ready={self._player_ready} pending_play={self._pending_play}"
        )
        if self._player_ready:
            self._load_current_video(autoplay=self._pending_play)

    def _on_bridge_command(self, cmd: str) -> None:
        log(f"bridge cmd: {cmd!r}")
        if not cmd.startswith(self.CMD_PREFIX):
            return
        self._handle_player_message(cmd[len(self.CMD_PREFIX) :])

    def _on_js_message(self, handled: tuple[bool, object], message: str, context) -> tuple:
        if not message.startswith(self.CMD_PREFIX):
            return handled
        log(f"js message hook: {message!r}")
        self._handle_player_message(message[len(self.CMD_PREFIX) :])
        return (True, None)

    def _handle_player_message(self, message: str) -> None:
        log(f"player message: {message!r}")

        if message == "page_ready":
            return

        if message == "ready":
            self._player_ready = True
            if self._fullscreen and self._fullscreen.isVisible():
                self._fullscreen._sync_video()
                return
            item = self._current_item()
            if item:
                self._load_current_video(autoplay=self._pending_play)
            return

        if message == "player_ready":
            self._update_duration_from_player()
            if self._pending_play and self._budget.has_time():
                self._eval_js("window.ankittube.play();")
            return

        if message.startswith("position:"):
            try:
                seconds = float(message.split(":", 1)[1])
            except ValueError:
                return
            item = self._current_item()
            if item:
                self._update_position_cache(item.video_id, seconds)
            return

        if message.startswith("state:"):
            try:
                state = int(message.split(":", 1)[1])
            except ValueError:
                return
            # 1 = playing, 2 = paused, 0 = ended
            if state == 1:
                if not self._budget.has_time():
                    self._handle_budget_exhausted()
                    return
                self._is_playing = True
                if not self._timer.isActive():
                    self._timer.start()
                self._refresh_queue_ui()
            elif state in (0, 2, 5):
                self._is_playing = False
                self._timer.stop()
                self._refresh_queue_ui()
            return

        if message == "ended":
            self._is_playing = False
            self._timer.stop()
            was_last = (
                self._current_index >= 0
                and self._current_index >= len(self._queue) - 1
            )
            self._remove_finished_current()
            self._refresh_queue_ui()
            self._save_state()
            if not self._queue:
                self._pause_playback()
                self._eval_js("window.ankittube.clear();")
            elif not was_last and self._budget.has_time():
                self._play_current()
            elif self._budget.has_time():
                self._pause_playback()
            else:
                self._handle_budget_exhausted()
            return

        if message.startswith("error"):
            self._is_playing = False
            self._timer.stop()
            detail = message.split(":", 1)[-1]
            log(f"playback error: {detail}")
            self._refresh_queue_ui()
