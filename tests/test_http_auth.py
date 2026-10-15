"""Offline tests: real HTTP middleware; never call YouTube or load Google tokens."""
import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

ROOT = Path(__file__).resolve().parents[1]
SERVERS = [
    ("youtube/youtube_mcp.py", "youtube-readonly", 5),
    ("youtube-playlist/youtube_playlist_mcp.py", "youtube-playlist", 6),
]

@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    path = tmp_path_factory.mktemp("auth") / "public.pem"
    path.write_bytes(public)
    return private, path

def token(private, aud, **changes):
    now = int(time.time())
    claims = {"iss": "samoxis-mcp", "aud": aud, "sub": "test-client", "iat": now, "exp": now + 300}
    claims.update(changes)
    return jwt.encode(claims, private, algorithm="RS256")

def load_server(relative, public, monkeypatch):
    monkeypatch.setenv("MCP_AUTH_PUBLIC_KEY_FILE", str(public))
    monkeypatch.syspath_prepend(str(ROOT / "youtube"))
    spec = importlib.util.spec_from_file_location("server_under_test", ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Any accidental Google access fails without reading a real credential file.
    if hasattr(module, "_get_youtube_service"):
        def forbidden():
            raise AssertionError("Google access is forbidden in auth tests")
        module._get_youtube_service = forbidden
    return module.mcp.http_app()

@pytest.mark.parametrize("relative,audience,count", SERVERS)
@pytest.mark.parametrize("case", ["missing", "garbage", "expired", "wrong_audience", "wrong_issuer", "bad_signature", "valid"])
def test_http_auth(relative, audience, count, case, keys, monkeypatch):
    private, public = keys
    app = load_server(relative, public, monkeypatch)
    bearer = {
        "missing": None,
        "garbage": "invalid-token",
        "expired": token(private, audience, exp=int(time.time()) - 3600),
        "wrong_audience": token(private, "youtube-playlist" if audience == "youtube-readonly" else "youtube-readonly"),
        "wrong_issuer": token(private, audience, iss="untrusted"),
        "bad_signature": token(rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()),audience),
        "valid": token(private, audience),
    }[case]

    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                headers = {"Accept": "application/json, text/event-stream"}
                if bearer: headers["Authorization"] = "Bearer " + bearer
                response = await client.post("/mcp", headers=headers, json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"offline-test","version":"1"}}})
                if case != "valid":
                    assert response.status_code in (401,403), (case,response.status_code,response.text)
                    return
                assert response.status_code == 200, response.text
                headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
                headers["MCP-Protocol-Version"] = "2025-03-26"
                response = await client.post("/mcp", headers=headers, json={"jsonrpc":"2.0","method":"notifications/initialized"})
                assert response.status_code == 202
                response = await client.post("/mcp", headers=headers, json={"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
                assert response.status_code == 200, response.text
                import json
                data = next(json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: "))
                assert len(data["result"]["tools"]) == count
                # A session ID alone must never bypass bearer validation.
                headers.pop("Authorization")
                response = await client.post("/mcp", headers=headers, json={"jsonrpc":"2.0","id":3,"method":"tools/list","params":{}})
                assert response.status_code in (401,403)
    asyncio.run(run())

@pytest.mark.parametrize("relative,audience,count", SERVERS)
def test_missing_config_refuses_start(relative, audience, count):
    env = os.environ.copy()
    env.pop("MCP_AUTH_PUBLIC_KEY_FILE", None)
    result = subprocess.run([sys.executable, str(ROOT/relative), "--help"],env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode != 0
    assert "MCP_AUTH_PUBLIC_KEY_FILE" in result.stderr

@pytest.mark.parametrize("relative,audience,count", SERVERS)
def test_real_http_sdk_client(relative, audience, count, keys):
    """Temporary loopback process, isolated key; no production ports or credentials."""
    import socket
    from fastmcp import Client
    private, public = keys
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["MCP_AUTH_PUBLIC_KEY_FILE"] = str(public)
    proc = subprocess.Popen([sys.executable,str(ROOT/relative),"--host","127.0.0.1","--port",str(port)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            if proc.poll() is not None: pytest.fail("temporary test server exited")
            try:
                with socket.create_connection(("127.0.0.1",port),timeout=.2): break
            except OSError: time.sleep(.1)
        else: pytest.fail("temporary test server did not start")
        async def run():
            async with Client(f"http://127.0.0.1:{port}/mcp",auth=token(private,audience)) as client:
                assert len(await client.list_tools()) == count
        asyncio.run(run())
    finally:
        proc.terminate()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
