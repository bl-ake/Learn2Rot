# AnkiTube configuration

AnkiTube stores user preferences and runtime session state in the add-on config JSON.

## User preferences

These keys can be changed in **Tools → AnkiTube → Settings...** or edited here:

| Key | Default | Description |
|-----|---------|-------------|
| `seconds_per_card` | `15` | Watch seconds earned per reviewed card |
| `starting_budget_seconds` | `0` | Initial watch budget for new profiles |
| `max_budget_seconds` | `600` | Maximum banked watch time (10 minutes) |
| `dock_area` | `"right"` | Dock side: `"left"` or `"right"` |
| `show_dock_in_review_only` | `false` | Hide the dock outside review mode |
| `youtube_show_controls` | `true` | Show YouTube player controls |
| `youtube_show_fullscreen` | `true` | Show YouTube fullscreen button |
| `dock_show_playback_buttons` | `true` | Show Play/Pause/Next/Fullscreen below player |
| `debug_logging` | `false` | Write diagnostic log to `ankittube.log` |

## Runtime state (auto-managed)

| Key | Description |
|-----|-------------|
| `budget_seconds` | Current watch-time balance |
| `queue` | Saved video queue |
| `current_index` | Index of the current queue item |
| `positions` | Playback positions per video ID |
| `lifetime_earned_seconds` | Total seconds earned from reviews |
| `dock_panel_sizes` | Splitter sizes for queue vs player |
| `queue_visible` | Whether the queue list is visible |
| `dock_visible` | Whether the dock panel is open |
| `dock_width` | Saved dock width in pixels |
