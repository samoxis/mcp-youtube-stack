# YouTube Playlist MCP

Authenticated YouTube playlist management via Google OAuth 2.0.
See [root README](../README.md) for architecture and decision records.

## Setup

1. Create a Desktop OAuth client in [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
2. Save the downloaded JSON as `config/google_oauth_client.json` (next to this script).
3. Enable the **YouTube Data API v3** for the same Google Cloud project.

Override the config location with the `MCP_CONFIG_DIR` env var if needed.

## Run

```bash
python youtube_playlist_mcp.py --port 8003 --host 127.0.0.1
```

On the first authenticated call, a browser window opens for OAuth authorisation. The resulting token is cached at `config/youtube_token.pickle` and refreshed automatically.

## Tools

- `youtube_playlist_create(title, description='', privacy='private')` — privacy: private / unlisted / public
- `youtube_playlist_list()` — all playlists owned by the authenticated user
- `youtube_playlist_add_video(playlist_id, video_url_or_id)` — add a single video
- `youtube_playlist_search_and_add(playlist_id, query, max_videos=10)` — search + bulk-add in one call
- `youtube_playlist_remove_video(playlist_item_id)` — remove by playlist-item ID (from `get_items`)
- `youtube_playlist_get_items(playlist_id, max_results=50)` — list videos in a playlist

## API quota note

YouTube Data API has a daily quota (default 10,000 units). Search costs 100 units per call; inserts cost 50. `youtube_playlist_search_and_add` uses yt-dlp for the search step (zero quota cost) and the API only for the inserts — letting you search-and-add 20+ videos in one tool call without burning the daily budget.
