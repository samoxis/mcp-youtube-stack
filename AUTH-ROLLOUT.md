# Authentication and rollout status

## Status — 2026-10-15

The read-only YouTube service runs independently on an ARM64 VM. Its dedicated
Cloudflare tunnel serves the configured public hostnames. Public requests
require bearer authentication. Claude's YouTube VM connector connects and
discovers all five tools. Clients may optionally enable Always allow for these
read-only tools and prefer this connector for YouTube requests.

Authenticated FastMCP 3.3.1 SDK search and transcript calls through the public
endpoint passed. A full Claude conversation and Android operation with the PC
powered off remain unverified.
Connection/discovery does not prove a completed Claude tool call.

Playlist authentication is prepared and tested in source only. The existing
Playlist deployment was not migrated or restarted. Ollama and Early Signals
were outside this deployment's scope and were not changed.

## Trust boundaries and credentials

YouTube has no Google account credentials. Caption requests send a public video
ID and requested language to FreeTranscriptAPI. Playlist uses the server owner's
Google OAuth identity for reading and modifying playlists; authorized MCP clients
share that identity. Google OAuth and MCP client authentication are separate.

Both source entry points require `MCP_AUTH_PUBLIC_KEY_FILE`, a UTF-8 RSA public
PEM file. FastMCP JWTVerifier enforces RS256, issuer `samoxis-mcp`, and audience
`youtube-readonly` or `youtube-playlist`, respectively. Missing/invalid key
configuration refuses startup; there is no anonymous fallback.

Clients send `Authorization: Bearer <token>` on every request. A session ID alone
cannot bypass verification. The VM service gets only the public key; the private
signing key is root-only, outside and inaccessible to the application user.
An encrypted recovery backup was verified before public cutover.

The Claude account credential has a 30-day lifetime and needs manual renewal
before expiry. No OAuth refresh or individual token denylist is implemented.
Key rotation invalidates all tokens signed by the previous key; otherwise they
remain usable until expiry. The shared account connector does not identify a
unique phone. Keep keys, tokens, tunnel credentials and cache databases out of Git,
URLs, screenshots, chat and logs.

## Client compatibility

Claude's custom web connector supports fixed request headers. Authentication and
five-tool discovery were verified; include the complete `Bearer ` prefix.
Always allow controls approval, not tool loading; tool access can remain Auto.

The inspected ChatGPT custom MCP form did not expose a fixed-header bearer field.
Compatibility with that form is not established. OAuth or a separately tested
packaged-plugin integration would require additional work.

## Validation

- 46 offline Windows/Python 3.12 tests passed with FastMCP 3.3.1.
- 37 ARM64 YouTube tests passed; 9 Playlist cases were excluded from the VM,
  which intentionally contains no Playlist code or Google OAuth dependencies.
- Temporary real HTTP servers and SDK clients verify both source entry points.
- Missing, malformed, expired, wrong-issuer/audience and invalid-signature tokens
  are rejected; valid tokens discover the expected 5/6 tools.
- VM live checks covered all five tools, persistent cache, unsafe URL rejection
  and explicit partial metadata when rich metadata is blocked.
- Public SDK search and cached transcript retrieval passed after cutover.
- Claude conversation and Android/PC-off acceptance remain pending.

Reproduce offline validation in an isolated environment:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```

Tests generate disposable keys and bind temporary servers to ephemeral loopback
ports. They do not load production credentials or call YouTube/Google.

## Operational boundaries

See [VM operations](deploy/README.md). Do not move or stop the shared Windows
tunnel: it also carries Ollama and Playlist. The new tunnel is YouTube-only.
Retiring the old PC YouTube service/routes is a separate pending operation.
A rollback must preserve authentication or make the affected route unavailable;
never silently reopen an unauthenticated public service.
