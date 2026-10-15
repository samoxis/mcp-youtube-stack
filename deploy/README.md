# Independent YouTube VM service

Deployment layout for an independent Ubuntu 24.04 ARM64 service with Python 3.12.3.
Paths below describe the service layout and contain no deployment credentials.

## Ownership and isolation

| Component | Path / identity |
| --- | --- |
| Service and non-login system user | `mcp-youtube` |
| Root-owned application and independent Python environment | `/opt/mcp-youtube/` and `/opt/mcp-youtube/venv/` |
| Unit | `/etc/systemd/system/mcp-youtube.service` |
| Service configuration and public key | `/etc/mcp-youtube/service.env`, `/etc/mcp-youtube/public.pem` |
| Root-only signing key | `/etc/mcp-youtube/admin/signing.pem` |
| Service-owned persistent SQLite cache and request budget | `/var/lib/mcp-youtube/cache/transcripts.sqlite3` |
| Logs | systemd journal for `mcp-youtube.service` |
| Bind | `127.0.0.1:8002`, never a public interface |

No source, virtualenv, credentials, cache, working directory, symlink, unit
dependency, or environment file is taken from another project.
Removing or changing unrelated projects and their units
does not remove this installation. Deleting/rebuilding the entire VM, changing
the system Python/OS/network, or broad root operations can affect both.

The unit uses `ProtectHome=yes`, `ProtectSystem=strict`, `NoNewPrivileges=yes`,
no Linux capabilities, private temporary files/devices, and a root-only key
directory explicitly hidden from the service. Only its own state directory is
writable in the normal filesystem. CPU is limited to one core and memory to
768 MiB. Application code is not writable by the service identity.

## Caption provider and cache

Both transcript and language tools use anonymous HTTPS requests to
FreeTranscriptAPI. No provider account, API key, paid endpoint, Google cookies,
proxy, or automatic paid fallback is configured. The provider receives the
requested public video ID and caption language.

Successful responses are schema-validated, cached for seven days, and reused
across calls and service restarts. Cache keys include operation, video ID and
requested language. Timestamps and text are validated before returning them.
The cache retains up to 1,000 objects and 64 MiB of JSON payload; SQLite pages
and indexes add disk overhead. SQLite reuses freed pages after eviction.

The durable local budget is 18 outgoing provider requests per rolling hour,
shared by transcript and language lookups. Failed requests also consume a local
reservation. Cache hits do not. This leaves a margin below the advertised 20
anonymous requests/hour/IP; other applications on the same public IP and the
provider's global anonymous limit may still exhaust its quota. HTTP 429 starts
a persisted cooldown. There are no automatic retries; valid cached captions
remain usable during cooldown. A missing transcript is reported explicitly.

Search and channel listing remain direct yt-dlp requests with bounded result
counts, socket timeouts and disabled automatic retries/cache/proxy discovery.
Channel inputs accept only canonical HTTPS YouTube handles/channel IDs and use
the YouTube-specific extractor, rejecting arbitrary/internal URLs.

Rich video metadata may be blocked by YouTube's bot checks. A public YouTube
oEmbed fallback returns title, channel and thumbnail, with `partial: true`,
`source: youtube_oembed`, null unavailable statistics and `unavailable_fields`.
It never fabricates views, likes, duration or upload dates.

## Authentication

RS256 JWT verification is mandatory: issuer `samoxis-mcp`, audience
`youtube-readonly`, signed by this installation's key. Missing/unreadable key
configuration refuses startup. Missing, invalid, expired and wrong-audience
tokens were rejected with HTTP 401 during live validation.

The application receives only the public key. The signing key is stored under
root administration on the VM, outside the app and inaccessible to its user.
This allows future token administration over SSH without keeping the PC on.
No key or token is included in Git. Validation used short-lived test tokens.
A separate 30-day account token was installed in the Claude connector through
its fixed Authorization request header. Renew it manually before expiry.

This is fixed-header bearer authentication, not an OAuth authorization server.
The Claude account token needs explicit expiry and renewal. A shared
Claude web/mobile connector identifies the account integration, not a unique
phone. The verifier has no individual token denylist: key rotation invalidates
all tokens signed by the old key. Treat any leaked bearer as a credential leak.

## Validation and reproducibility

- Windows: 46 offline tests passed, including the existing Playlist auth tests.
- ARM64: 37 offline tests passed; 9 Playlist tests excluded because this service
  intentionally contains no Playlist code or Google OAuth dependencies.
- Runtime versions are recorded in `/opt/mcp-youtube/requirements-arm64.lock`.
  Pytest/PyJWT validation dependencies reside only in this dedicated environment.
- Real authenticated MCP checks cover search, two captions, languages, basic
  metadata fallback, channel listing, unsafe URL rejection and cache hits.
- After restarting only the new service, cached results persisted and the
  upstream request counter did not increase.
- The service user could write the cache, but could neither modify application
  code nor read the signing key. Bind was verified as loopback only.
- Source/config fingerprints for the two existing projects were unchanged
  during initial installation. Runtime logs/data of those projects were excluded.

Source checkpoints were verified before edits. Keep recovery archives outside
the repository; private backup locations are intentionally omitted.

## Public activation — 2026-10-15

The application and dedicated `cloudflared-youtube.service` are enabled on VM
boot. The configured public hostnames route to this independent
tunnel and then `127.0.0.1:8002`. Missing/invalid bearer requests return HTTP 401.
Authenticated public SDK search and transcript retrieval passed.

The tunnel uses its own non-login user `cloudflared-youtube`, binary
`/usr/local/libexec/cloudflared-youtube` (2026.10.0), and directory
`/etc/cloudflared-youtube/`. Its credentials file `tunnel.json` is root-owned,
group-readable only by that dedicated user. Its config has the two YouTube
hostnames followed by a catch-all HTTP 404. Metrics bind to `127.0.0.1:20242`.
The hardened tunnel unit limits CPU to 25% and memory to 256 MiB.

Claude's YouTube VM connector authenticated and discovered five tools. Those
tools can be set to Always allow in the client. A full Claude conversation
and Android with the PC powered off remain unverified.

The shared Windows tunnel/configuration, Ollama, Playlist deployment and Early
Signals source/configuration were not changed. Old PC YouTube routes/service
remain for a separately reviewed retirement; do not stop the shared tunnel.

Keep a verified encrypted backup of signing material, tunnel credentials and
required state outside Git. Never add deployment secrets or transcript caches
to an ordinary source archive.
