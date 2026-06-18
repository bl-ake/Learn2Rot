# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""YouTube URL parsing, metadata fetch, and queue helpers."""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from aqt.qt import QMimeData

YOUTUBE_PATTERNS = (
    re.compile(
        r"(?:https?://)?(?:www\.)?youtube\.com/watch\?(?:[^&\s]+&)*v=([a-zA-Z0-9_-]{11})"
    ),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})"),
    re.compile(r"^([a-zA-Z0-9_-]{11})$"),
)


def normalize_youtube_url(url: str) -> str:
    text = url.strip()
    if text.startswith("/watch") or text.startswith("/shorts/"):
        return f"https://www.youtube.com{text}"
    if text.startswith("watch?"):
        return f"https://www.youtube.com/{text}"
    return text


def extract_urls_from_mime(mime: QMimeData) -> list[str]:
    urls: list[str] = []

    if mime.hasUrls():
        for url in mime.urls():
            if url.isValid() and url.scheme() in ("http", "https"):
                urls.append(url.toString())

    if mime.hasText():
        for line in mime.text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)

    if mime.hasHtml():
        for match in re.finditer(r"""href=["']([^"']+)["']""", mime.html()):
            href = match.group(1)
            if "youtube.com" in href or "youtu.be" in href or href.startswith("/watch"):
                urls.append(href)

    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        normalized = normalize_youtube_url(url)
        if normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def extract_all_video_ids(text: str) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for pattern in YOUTUBE_PATTERNS:
        for match in pattern.finditer(text.strip()):
            video_id = match.group(1)
            if video_id not in seen:
                seen.add(video_id)
                ids.append(video_id)
    return ids


def extract_video_ids_from_mime(mime: QMimeData) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for url in extract_urls_from_mime(mime):
        for video_id in extract_all_video_ids(url):
            if video_id not in seen:
                seen.add(video_id)
                ids.append(video_id)
    return ids


def mime_has_youtube_url(mime: QMimeData) -> bool:
    return bool(extract_video_ids_from_mime(mime))


def extract_video_id(url: str) -> Optional[str]:
    ids = extract_all_video_ids(normalize_youtube_url(url))
    return ids[0] if ids else None


def fetch_video_duration(video_id: str) -> Optional[int]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    for pattern in (
        re.compile(r'"lengthSeconds"\s*:\s*"(\d+)"'),
        re.compile(r'"lengthSeconds"\s*:\s*(\d+)'),
    ):
        match = pattern.search(html)
        if match:
            return int(match.group(1))

    match = re.search(r'"approxDurationMs"\s*:\s*"(\d+)"', html)
    if match:
        return int(match.group(1)) // 1000
    return None


def cards_to_watch_video(
    duration_seconds: Optional[int], seconds_per_card: int
) -> Optional[int]:
    if duration_seconds is None or duration_seconds <= 0 or seconds_per_card <= 0:
        return None
    return math.ceil(duration_seconds / seconds_per_card)


@dataclass(frozen=True)
class VideoCardProgress:
    cards_done: Optional[int]
    cards_total: Optional[int]


def allocate_queue_card_progress(
    durations_seconds: list[Optional[int]],
    available_budget_seconds: int,
    seconds_per_card: int,
    *,
    video_ids: Optional[list[str]] = None,
    playback_positions: Optional[dict[str, float]] = None,
) -> list[VideoCardProgress]:
    """Allocate unspent review budget across the queue in order."""
    if seconds_per_card <= 0:
        return [VideoCardProgress(None, None) for _ in durations_seconds]

    remaining_budget = max(0, int(available_budget_seconds))
    progress: list[VideoCardProgress] = []

    for index, duration in enumerate(durations_seconds):
        cards_total = cards_to_watch_video(duration, seconds_per_card)
        if cards_total is None:
            progress.append(VideoCardProgress(None, None))
            continue

        watched_seconds = 0
        if (
            video_ids is not None
            and playback_positions is not None
            and index < len(video_ids)
        ):
            watched_seconds = int(playback_positions.get(video_ids[index], 0))

        duration_seconds = duration or 0
        if duration_seconds > 0 and watched_seconds >= duration_seconds:
            cards_from_watching = cards_total
        else:
            cards_from_watching = watched_seconds // seconds_per_card

        cards_still_needed = cards_total - cards_from_watching
        seconds_still_needed = cards_still_needed * seconds_per_card
        budget_allocated = min(remaining_budget, seconds_still_needed)
        cards_done = min(
            cards_total,
            cards_from_watching + budget_allocated // seconds_per_card,
        )
        remaining_budget -= budget_allocated

        progress.append(VideoCardProgress(cards_done, cards_total))

    return progress


def format_queue_item_label(
    title: str,
    cards_done: Optional[int],
    cards_total: Optional[int],
    *,
    playing_prefix: str = "",
) -> str:
    if cards_total is None:
        card_label = "(?)"
    else:
        done = cards_done if cards_done is not None else 0
        card_label = f"({done}/{cards_total})"
    return f"{playing_prefix}{card_label} {title}"


def fetch_video_title(video_id: str) -> str:
    endpoint = (
        "https://www.youtube.com/oembed?"
        f"url=https://www.youtube.com/watch?v={video_id}&format=json"
    )
    try:
        with urllib.request.urlopen(endpoint, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
            title = payload.get("title")
            if isinstance(title, str) and title.strip():
                return title.strip()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        pass
    return video_id


def format_seconds(total_seconds: int) -> str:
    seconds = max(0, int(total_seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
