# Copyright (C) 2026 bl-ake
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Background video metadata fetching."""

from __future__ import annotations

import threading
from typing import Callable, Optional

from aqt import mw

from .utils import fetch_video_duration, fetch_video_title


def fetch_video_metadata_async(
    video_id: str,
    on_complete: Callable[[str, Optional[int]], None],
) -> None:
    """Fetch title and duration off the UI thread, then call back on main."""

    def worker() -> None:
        title = fetch_video_title(video_id)
        duration = fetch_video_duration(video_id)

        def deliver() -> None:
            on_complete(title, duration)

        mw.taskman.run_on_main(deliver)

    threading.Thread(target=worker, daemon=True).start()
