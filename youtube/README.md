# YouTube MCP

Read-only YouTube tools — metadata, transcripts, search, channel videos.
See [root README](../README.md) for architecture, deployment, and decision records.

## Run

```bash
python youtube_mcp.py --port 8002 --host 127.0.0.1
```

## Tools

- `youtube_get_metadata(url)` — title, channel, duration, views, likes, upload date, tags
- `youtube_get_transcript(url, lang='en')` — full transcript text, with fallback to any available language
- `youtube_get_available_languages(url)` — list all transcript languages a video offers
- `youtube_search(query, max_results=10)` — yt-dlp search, no API quota cost
- `youtube_get_channel_videos(channel_url, max_videos=20)` — recent uploads from a channel

URL parsing accepts every YouTube URL form: `youtube.com/watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`, `/v/`, or a bare 11-character video ID.
