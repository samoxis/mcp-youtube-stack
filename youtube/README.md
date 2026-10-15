# YouTube MCP

Read-only YouTube tools — metadata, transcripts, search, channel videos.
See [root README](../README.md) for architecture, deployment, and decision records.
The prepared independent VM installation is documented in [VM operations](../deploy/README.md).
Captions/languages now use anonymous FreeTranscriptAPI with a persistent cache,
seven-day TTL and a durable local budget of 18 upstream requests/hour.

## Run

```bash
# Install requirements-youtube.txt in a dedicated environment first.
# This is the public verification key; never give the application a signing key.
export MCP_AUTH_PUBLIC_KEY_FILE=/etc/mcp-youtube/public.pem
export MCP_CACHE_DIR=/var/lib/mcp-youtube/cache
python youtube_mcp.py --port 8002 --host 127.0.0.1
```

Every HTTP client must send a valid RS256 Bearer JWT with issuer `samoxis-mcp`
and audience `youtube-readonly`. Missing key configuration refuses startup.
No OAuth login flow is provided by this verifier.

## Tools

- `youtube_get_metadata(url)` — rich metadata where available; otherwise basic
  YouTube oEmbed title/channel/thumbnail with `partial: true` and explicit missing fields
- `youtube_get_transcript(url, lang='en')` — full transcript text, with fallback to any available language
- `youtube_get_available_languages(url)` — list all transcript languages a video offers
- `youtube_search(query, max_results=10)` — yt-dlp search, no API quota cost
- `youtube_get_channel_videos(channel_url, max_videos=20)` — recent uploads from a channel

Video inputs accept canonical HTTPS YouTube watch/short/embed/v URLs, youtu.be
links, or a bare 11-character video ID. Other hosts and arbitrary URLs are
rejected. Channel listing accepts only canonical HTTPS YouTube @handle/channel
URLs and does not invoke generic website extraction.
