# Self-Hosted MCP Server Stack

Two [Model Context Protocol](https://modelcontextprotocol.io) servers built on [FastMCP](https://github.com/jlowin/fastmcp): read-only YouTube tools and separate Google OAuth playlist management. Clients must support the required bearer authentication.

**Example endpoints (replace with your own hostnames)**
- YouTube MCP — `https://youtube.example.com/mcp`
- YouTube Playlist MCP — `https://playlist.example.com/mcp` (OAuth)

**Status (2026-10-15)** — YouTube runs on an independent ARM64 VM behind its
own Cloudflare tunnel with mandatory bearer authentication. Claude connects
and discovers all five tools; public SDK search/transcript checks passed.
Claude conversation and Android/PC-off acceptance remain pending. Playlist
remains on its existing deployment; its source authentication changes are not
yet deployed. See [VM operations](deploy/README.md) and [authentication](AUTH-ROLLOUT.md).

---

## What it does

### `youtube/` — read-only YouTube tools

No Google account credentials required. MCP access requires a signed bearer
token. Captions/languages use anonymous FreeTranscriptAPI and persistent cache;
see [VM operations](deploy/README.md) for free-tier limits and isolation.

| Tool | Purpose |
|---|---|
| `youtube_get_metadata` | Title, channel, duration, views, likes, upload date, tags |
| `youtube_get_transcript` | Full transcript in the requested language, with fallback to any available |
| `youtube_get_available_languages` | List every transcript language a video offers |
| `youtube_search` | YouTube search (yt-dlp under the hood — no API quota cost) |
| `youtube_get_channel_videos` | Recent uploads for a channel |

### `youtube-playlist/` — authenticated playlist management

Uses the server owner's Google OAuth 2.0 account (shared by authorized clients):

| Tool | Purpose |
|---|---|
| `youtube_playlist_create` | Create a playlist (private / unlisted / public) |
| `youtube_playlist_list` | List all playlists owned by the authenticated user |
| `youtube_playlist_add_video` | Add a single video |
| `youtube_playlist_search_and_add` | Search + bulk-add top N results in one call |
| `youtube_playlist_remove_video` | Remove a playlist item by ID |
| `youtube_playlist_get_items` | List videos in a playlist |

---

## Architecture

```text
Claude cloud / bearer-capable MCP client
                | HTTPS + Authorization: Bearer ...
                v
Dedicated YouTube Cloudflare tunnel (VM, outbound connection)
                |
                v
127.0.0.1:8002 -> mcp-youtube.service
                  |-- public YouTube search/metadata/channel data
                  `-- FreeTranscriptAPI -> persistent SQLite cache

Separate existing Windows deployment: Playlist + Google OAuth
Shared Windows tunnel also carries Ollama; it is not moved or stopped.
```

YouTube has its own code, virtualenv, users, units, configuration, signing key
and state, independent of Early Signals. Removing the Early Signals application
does not remove this MCP. Rebuilding the entire VM still affects both.

### Why this shape

**HTTP transport, not stdio.**
stdio MCP only works for a client on the same machine. HTTP transport means any MCP client — on another laptop, in a browser, on a teammate's setup — reaches the same server. The cost is needing TLS and an inbound path; Cloudflare Tunnel solves both without exposing a port.

**Cloudflare Tunnel over port forwarding.**
No router config, no static IP, automatic TLS via Cloudflare's cert, free tier. The host never accepts inbound connections — it dials out to the edge. For a home-hosted service that authenticates to Google OAuth, this is the cleanest secure path.

**Separate services and tunnels.**
The read-only YouTube service has its own VM tunnel and no Google OAuth tokens.
Playlist remains separate on Windows. Neither service depends on Early Signals.

**yt-dlp for search, Google API only for mutations.**
YouTube Data API quota is 10,000 units/day. Search costs 100 units; insert costs 50. By using yt-dlp for the search half of `search_and_add`, a single tool call can search-and-add 20+ videos without burning the daily budget. The API is invoked only where there's no alternative.

**Defensive tool design.**
Every tool returns either a result dict or `{"error": "..."}` — never throws across the MCP boundary. This keeps the surface predictable for an LLM consuming these tools and avoids opaque MCP-level failures.

**OAuth tokens cached server-side.**
The MCP client never sees Google credentials. Refresh handled automatically on token expiry; the user authorises once via local browser flow.

---

## Stack

- **Language** — Python 3.12
- **MCP framework** — [FastMCP](https://github.com/jlowin/fastmcp) 3.x (Streamable HTTP transport)
- **YouTube data** — `yt-dlp` (search, metadata, channel videos), anonymous FreeTranscriptAPI (transcripts/languages with persistent cache), Google API client (playlist mutations)
- **OAuth** — `google-auth-oauthlib` (Installed App flow, local browser redirect)
- **HTTP runtime** — Uvicorn / Starlette (via FastMCP)
- **Tunnel** — dedicated `cloudflared` under systemd for VM YouTube; existing Windows tunnel kept separate
- **Process supervision** — systemd for VM YouTube; existing NSSM for Windows Playlist


---

## Quickstart

### Run locally

```bash
# 1. Clone + install
git clone https://github.com/<your-username>/mcp-youtube-stack.git
cd mcp-youtube-stack
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt

# 2. Playlist server only: add Google OAuth credentials
#    Get a Desktop OAuth client from https://console.cloud.google.com/apis/credentials
#    Save the downloaded JSON as `youtube-playlist/config/google_oauth_client.json`
#    The first authenticated call will open a browser for authorisation.

# 3. Set MCP_AUTH_PUBLIC_KEY_FILE to your RSA public PEM file in the environment.
#    See AUTH-ROLLOUT.md for token audiences and client configuration.
# 4. Run either server
python youtube/youtube_mcp.py --port 8002
python youtube-playlist/youtube_playlist_mcp.py --port 8003
```

### Connect from a client

For Claude web/mobile, add a custom web connector with the public YouTube MCP
URL and a fixed `authorization` request header whose value is `Bearer <token>`.
Use the same Claude account on Android. The VM server and tunnel run without
the PC; the actual Android/PC-off acceptance test is still pending.

For other clients, use their documented remote Streamable HTTP configuration
and secret storage to supply this header on every request. Do not put a real
token in a checked-in JSON configuration. A generic remote URL stanza is not a
verified Claude Desktop local-config integration.

The Playlist source requires a separate `youtube-playlist` audience bearer.
Its Google OAuth flow still authorizes the server owner's Google account.
Do not replace the existing Playlist service until its clients are validated.

---

## Architecture Decision Records

**ADR-001 — HTTP transport over stdio.**
The whole point of a personal MCP stack is reuse across machines and clients. HTTP adds operational cost (TLS, hosting, monitoring) but removes the per-machine setup tax permanently. Worth it the first time the second client connects.

**ADR-002 — Cloudflare Tunnel over reverse-proxy + port forward.**
A VPS-hosted reverse proxy costs £5–10/month plus cert management. Port forwarding requires a static IP and exposes the host. Tunnel: free, no inbound port, automatic TLS, single config file. The right default until traffic demands otherwise.

**ADR-003 — Split read-only and write servers.**
The metadata / transcript server doesn't need credentials. Keeping it separate from the OAuth-bearing Playlist server means a compromise of one doesn't escalate to the other, and restarts are independent.

**ADR-004 — yt-dlp for search, Google API only for mutations.**
YouTube Data API quota is 10k units/day. Search = 100 units, insert = 50. Using yt-dlp for the search half of `search_and_add` lets a single tool call search-and-add 20+ videos without burning the daily budget. The Google API is invoked only where there's no alternative (authenticated mutations).

**ADR-005 — Pickled token storage, not encrypted vault.**
The threat model for a single-user service running on the owner's machine is "filesystem access = full compromise." A vault adds complexity without raising the security floor. To revisit if this ever becomes multi-user.

**ADR-006 — Errors as return values, not exceptions.**
Every tool returns either a success dict or `{"error": "..."}`. This keeps the surface predictable for the LLM consuming these tools. Internal exceptions are caught and serialised with their type name and message.
