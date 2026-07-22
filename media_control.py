# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""System-wide Now Playing observation and play/pause control."""

from __future__ import annotations

import asyncio
import ctypes
import json
import platform
import subprocess
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Protocol


_OSASCRIPT_TIMEOUT_SEC = 2.0
_MEDIAREMOTE_PATH = (
    "/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote"
)
_UNSUPPORTED_MESSAGE = (
    "System media control is only available on macOS and Windows"
)

# Now Playing is read via osascript (entitled); commands go through the C API —
# MRNowPlayingController.sendCommandOptionsCompletion via JXA is a no-op on
# recent macOS even though it appears to succeed.
_JXA_GET_NOW_PLAYING = """
ObjC.import('Foundation');
function run() {
  const MediaRemote = $.NSBundle.bundleWithPath(
    '/System/Library/PrivateFrameworks/MediaRemote.framework/'
  );
  MediaRemote.load;
  const MRNowPlayingRequest = $.NSClassFromString('MRNowPlayingRequest');
  if (!MRNowPlayingRequest) {
    return JSON.stringify({title: '', artist: '', isPlaying: false, error: 'MRNowPlayingRequest unavailable'});
  }
  let title = '';
  let artist = '';
  let isPlaying = false;
  try {
    try {
      isPlaying = !!MRNowPlayingRequest.localIsPlaying;
    } catch (e) {
      isPlaying = false;
    }
    const item = MRNowPlayingRequest.localNowPlayingItem;
    if (item) {
      const info = item.nowPlayingInfo;
      if (info) {
        const t = info.valueForKey('kMRMediaRemoteNowPlayingInfoTitle');
        const a = info.valueForKey('kMRMediaRemoteNowPlayingInfoArtist');
        const rate = info.valueForKey('kMRMediaRemoteNowPlayingInfoPlaybackRate');
        if (t) { title = t.js || ''; }
        if (a) { artist = a.js || ''; }
        if (!isPlaying && rate !== undefined && rate !== null) {
          const n = Number(rate.js !== undefined ? rate.js : rate);
          isPlaying = !isNaN(n) && n !== 0;
        }
      }
    }
  } catch (e) {
    return JSON.stringify({title: '', artist: '', isPlaying: false, error: String(e)});
  }
  return JSON.stringify({title: title, artist: artist, isPlaying: isPlaying});
}
"""

_CMD_PLAY = 0
_CMD_PAUSE = 1
_CMD_TOGGLE = 2
_CMD_STOP = 3

# SMTC playback status enum value for Playing (Windows.Media.Control).
_SMTC_PLAYING = 4

OsascriptRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
CommandSender = Callable[[int], bool]
AsyncRunner = Callable[[Awaitable[Any]], Any]
SmtcBackendFactory = Callable[[], Any]


@dataclass
class NowPlayingInfo:
    title: str = ""
    artist: str = ""
    is_playing: bool = False
    supported: bool = True
    error: str = ""

    def display_label(self) -> str:
        if not self.supported:
            return self.error or _UNSUPPORTED_MESSAGE
        if self.error and not self.title and not self.artist:
            return self.error
        title = (self.title or "").strip()
        artist = (self.artist or "").strip()
        if title and artist:
            return f"{title} — {artist}"
        if title:
            return title
        if artist:
            return artist
        if self.is_playing:
            return "Playing"
        return "Nothing playing"


class MediaController(Protocol):
    def get_now_playing(self) -> NowPlayingInfo: ...

    def play(self) -> bool: ...

    def pause(self) -> bool: ...

    def toggle(self) -> bool: ...


def _default_osascript_runner(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=_OSASCRIPT_TIMEOUT_SEC,
        check=False,
    )


_mediaremote_lib: Optional[ctypes.CDLL] = None


def _load_mediaremote() -> Optional[ctypes.CDLL]:
    global _mediaremote_lib
    if _mediaremote_lib is not None:
        return _mediaremote_lib
    try:
        _mediaremote_lib = ctypes.cdll.LoadLibrary(_MEDIAREMOTE_PATH)
    except OSError:
        return None
    return _mediaremote_lib


def _ctypes_send_command(command: int) -> bool:
    lib = _load_mediaremote()
    if lib is None:
        return False
    try:
        send = lib.MRMediaRemoteSendCommand
        send.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        send.restype = ctypes.c_bool
        return bool(send(int(command), None))
    except (AttributeError, OSError, TypeError):
        return False


def _ensure_coroutine(awaitable: Awaitable[Any]) -> Any:
    """Wrap non-coroutine awaitables for asyncio.run (e.g. PyWinRT IAsyncOperation)."""
    if asyncio.iscoroutine(awaitable):
        return awaitable

    async def _await_it() -> Any:
        return await awaitable

    return _await_it()


def _run_async(coro: Awaitable[Any]) -> Any:
    """Run an awaitable from sync code (helper timer / Anki main thread)."""
    to_run = _ensure_coroutine(coro)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(to_run)
    # Already inside a loop (unusual for this add-on): run in a fresh loop
    # on a throwaway thread so we never nest asyncio.run.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, to_run).result()


class UnsupportedMediaController:
    """Stub for platforms without system Now Playing control."""

    _MESSAGE = _UNSUPPORTED_MESSAGE

    def get_now_playing(self) -> NowPlayingInfo:
        return NowPlayingInfo(supported=False, error=self._MESSAGE)

    def play(self) -> bool:
        return False

    def pause(self) -> bool:
        return False

    def toggle(self) -> bool:
        return False


class DarwinMediaController:
    """macOS Now Playing: JXA observe + MediaRemote C API commands."""

    def __init__(
        self,
        runner: Optional[OsascriptRunner] = None,
        command_sender: Optional[CommandSender] = None,
    ) -> None:
        self._runner = runner or _default_osascript_runner
        self._command_sender = command_sender or _ctypes_send_command
        self._last: NowPlayingInfo = NowPlayingInfo()

    def get_now_playing(self) -> NowPlayingInfo:
        try:
            result = self._runner(
                ["/usr/bin/osascript", "-l", "JavaScript", "-e", _JXA_GET_NOW_PLAYING]
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            info = NowPlayingInfo(
                title=self._last.title,
                artist=self._last.artist,
                is_playing=False,
                error=str(exc),
            )
            self._last = info
            return info

        stdout = (result.stdout or "").strip()
        if result.returncode != 0 or not stdout:
            err = (result.stderr or "").strip() or f"osascript exit {result.returncode}"
            info = NowPlayingInfo(
                title=self._last.title,
                artist=self._last.artist,
                is_playing=False,
                error=err,
            )
            self._last = info
            return info

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            info = NowPlayingInfo(
                title=self._last.title,
                artist=self._last.artist,
                is_playing=False,
                error="invalid now-playing JSON",
            )
            self._last = info
            return info

        info = NowPlayingInfo(
            title=str(payload.get("title") or ""),
            artist=str(payload.get("artist") or ""),
            is_playing=bool(payload.get("isPlaying")),
            error=str(payload.get("error") or ""),
        )
        self._last = info
        return info

    def play(self) -> bool:
        return self._send_command(_CMD_PLAY)

    def pause(self) -> bool:
        # Pause first; stop as a second slap for clients that ignore pause.
        paused = self._send_command(_CMD_PAUSE)
        if not paused:
            return self._send_command(_CMD_STOP)
        return True

    def toggle(self) -> bool:
        return self._send_command(_CMD_TOGGLE)

    def _send_command(self, command: int) -> bool:
        try:
            return bool(self._command_sender(command))
        except Exception:
            return False


def _default_smtc_backend_factory() -> Any:
    """Load GlobalSystemMediaTransportControlsSessionManager (PyWinRT)."""
    from winrt.windows.media.control import (  # type: ignore[import-untyped]
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    )

    return _run_async(SessionManager.request_async())


def _smtc_status_is_playing(status: Any) -> bool:
    try:
        value = int(status)
    except (TypeError, ValueError):
        value = getattr(status, "value", status)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return False
    return value == _SMTC_PLAYING


class WindowsMediaController:
    """Windows Now Playing via System Media Transport Controls (SMTC)."""

    def __init__(
        self,
        *,
        backend_factory: Optional[SmtcBackendFactory] = None,
        async_runner: Optional[AsyncRunner] = None,
    ) -> None:
        self._backend_factory = backend_factory or _default_smtc_backend_factory
        self._async_runner = async_runner or _run_async
        self._last: NowPlayingInfo = NowPlayingInfo()

    def get_now_playing(self) -> NowPlayingInfo:
        try:
            manager = self._backend_factory()
            if manager is None:
                info = NowPlayingInfo(
                    title=self._last.title,
                    artist=self._last.artist,
                    is_playing=False,
                    error="SMTC session manager unavailable",
                )
                self._last = info
                return info
            session = manager.get_current_session()
            if session is None:
                info = NowPlayingInfo(is_playing=False)
                self._last = info
                return info
            playback = session.get_playback_info()
            status = getattr(playback, "playback_status", None) if playback else None
            playing = _smtc_status_is_playing(status)
            title = ""
            artist = ""
            try:
                props = self._async_runner(session.try_get_media_properties_async())
            except Exception as exc:
                info = NowPlayingInfo(
                    title=self._last.title,
                    artist=self._last.artist,
                    is_playing=playing,
                    error=str(exc),
                )
                self._last = info
                return info
            if props is not None:
                title = str(getattr(props, "title", "") or "")
                artist = str(getattr(props, "artist", "") or "")
            info = NowPlayingInfo(title=title, artist=artist, is_playing=playing)
            self._last = info
            return info
        except Exception as exc:
            info = NowPlayingInfo(
                title=self._last.title,
                artist=self._last.artist,
                is_playing=False,
                error=str(exc),
            )
            self._last = info
            return info

    def play(self) -> bool:
        return self._with_current_session(
            lambda session: self._async_runner(session.try_play_async())
        )

    def pause(self) -> bool:
        """Pause all currently playing sessions (lockout parity with MediaRemote)."""
        try:
            manager = self._backend_factory()
            if manager is None:
                return False
            # get_sessions() needs winrt.windows.foundation.collections; if that
            # import fails (incomplete vendor), fall back to the current session.
            sessions: list[Any] = []
            try:
                sessions = list(manager.get_sessions() or [])
            except Exception:
                sessions = []
            if not sessions:
                current = manager.get_current_session()
                if current is None:
                    return False
                return self._pause_session(current)
            any_ok = False
            for session in sessions:
                playback = session.get_playback_info()
                status = getattr(playback, "playback_status", None) if playback else None
                if not _smtc_status_is_playing(status):
                    continue
                if self._pause_session(session):
                    any_ok = True
            return any_ok
        except Exception:
            return False

    def _pause_session(self, session: Any) -> bool:
        try:
            if self._async_runner(session.try_pause_async()):
                return True
            return bool(self._async_runner(session.try_stop_async()))
        except Exception:
            return False

    def toggle(self) -> bool:
        return self._with_current_session(
            lambda session: self._async_runner(session.try_toggle_play_pause_async())
        )

    def _with_current_session(self, action: Callable[[Any], Any]) -> bool:
        try:
            manager = self._backend_factory()
            if manager is None:
                return False
            session = manager.get_current_session()
            if session is None:
                return False
            return bool(action(session))
        except Exception:
            return False


def _is_windows(name: str) -> bool:
    return name in ("windows", "win32")


def create_media_controller(
    *,
    system: Optional[str] = None,
    runner: Optional[OsascriptRunner] = None,
    command_sender: Optional[CommandSender] = None,
    smtc_backend_factory: Optional[SmtcBackendFactory] = None,
    async_runner: Optional[AsyncRunner] = None,
) -> MediaController:
    name = (system or platform.system()).lower()
    if name == "darwin":
        return DarwinMediaController(runner=runner, command_sender=command_sender)
    if _is_windows(name):
        return WindowsMediaController(
            backend_factory=smtc_backend_factory,
            async_runner=async_runner,
        )
    return UnsupportedMediaController()
