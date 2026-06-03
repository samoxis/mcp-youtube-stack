# Self-Hosted MCP Server Stack

Two production [Model Context Protocol](https://modelcontextprotocol.io) servers built on [FastMCP](https://github.com/jlowin/fastmcp), running 24/7 behind a single Cloudflare Tunnel and reachable by any MCP-compatible client (Claude Desktop, Cursor, Cline, VS Code, etc.).

**Live endpoints**
- YouTube MCP — `https://yt.thevictoriacross.net/mcp`
- YouTube Playlist MCP — `https://pl.thevictoriacross.net/mcp` (OAuth)

**Status** — Production. In daily use across multiple MCP clients.

---

## What it does

### `youtube/` — read-only YouTube tools

Stateless, no credentials required:

| Tool | Purpose |
|---|---|
| `youtube_get_metadata` | Title, channel, duration, views, likes, upload date, tags |
| `youtube_get_transcript` | Full transcript in the requested language, with fallback to any available |
| `youtube_get_available_languages` | List every transcript language a video offers |
| `youtube_search` | YouTube search (yt-dlp under the hood — no API quota cost) |
| `youtube_get_channel_videos` | Recent uploads for a channel |

### `youtube-playlist/` — authenticated playlist management

Per-user, via Google OAuth 2.0:

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

```
   MCP-compatible client (Cursor, Cline, Claude Desktop, etc.)
                  │
                  │  HTTPS  (MCP Streamable HTTP transport)
                  ▼
        Cloudflare edge   ── TLS termination, no inbound port on host
                  │
                  │  Cloudflare Tunnel (outbound dial from host)
                  ▼
     Host (Windows, supervised by NSSM)
       ├── youtube_mcp.py            FastMCP · port 8002 · stateless
       └── youtube_playlist_mcp.py   FastMCP · port 8003 · OAuth + pickled tokens
```

### Why this shape

**HTTP transport, not stdio.**
stdio MCP only works for a client on the same machine. HTTP transport means any MCP client — on another laptop, in a browser, on a teammate's setup — reaches the same server. The cost is needing TLS and an inbound path; Cloudflare Tunnel solves both without exposing a port.

**Cloudflare Tunnel over port forwarding.**
No router config, no static IP, automatic TLS via Cloudflare's cert, free tier. The host never accepts inbound connections — it dials out to the edge. For a home-hosted service that authenticates to Google OAuth, this is the cleanest secure path.

**Two servers, two subdomains, one tunnel.**
A single `cloudflared` ingress block routes by hostname → local port. Responsibilities stay separated (the read-only YouTube server holds no credentials; the Playlist server is the only one with OAuth tokens) and each can be restarted independently.

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
- **YouTube data** — `yt-dlp` (search, metadata, channel videos), `youtube-transcript-api` (transcripts), Google API client (playlist mutations)
- **OAuth** — `google-auth-oauthlib` (Installed App flow, local browser redirect)
- **HTTP runtime** — Uvicorn / Starlette (via FastMCP)
- **Tunnel** — `cloudflared`, supervised by NSSM on Windows
- **Process supervision** — NSSM (Windows). Migration to systemd on Oracle Cloud ARM planned.


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

# 3. Run either server
python youtube/youtube_mcp.py --port 8002
python youtube-playlist/youtube_playlist_mcp.py --port 8003
```

### Connect from a client

Add to your MCP client config (the example below uses Claude Desktop's `claude_desktop_config.json` format — Cursor, Cline, and other clients use similar structures):

```json
{
  "mcpServers": {
    "youtube": {
      "url": "http://127.0.0.1:8002/mcp",
      "transport": "http"
    },
    "youtube-playlist": {
      "url": "http://127.0.0.1:8003/mcp",
      "transport": "http"
    }
  }
}
```

Restart the client. The Playlist server triggers OAuth on first authenticated call.

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

---

## Roadmap

- [ ] **Migrate to Oracle Cloud ARM Always Free** (4 vCPU / 24 GB) — frees the home machine and gives 24/7 uptime independent of local power. Process supervision moves from NSSM → systemd in the process.
- [ ] **Eval suite** — structured tests for tool correctness (transcript fidelity, playlist mutations idempotent under retry), regression-tracked across deployments. LLM-as-Judge for transcript-quality scoring.
- [ ] **RAG surface** — a third server adding transcript retrieval + synthesis, turning the stack into a working RAG-over-MCP demo.
- [ ] **First-class OAuth callback** — current flow relies on a locally bound port; move to a portable headless flow for cloud deployment.
- [ ] **Observability** — Cloudflare analytics on tunnel uptime + per-tool latency, exported.

---

## About

Built and operated by **Sorin Hinceanu** — thirteen years in telecoms infrastructure field engineering (Openreach FTTP, ranked top contractor of 200+), now building agentic AI tooling and self-hosted MCP infrastructure end-to-end.

- Location — London, UK
- Email — sorinbrocker@yahoo.com
