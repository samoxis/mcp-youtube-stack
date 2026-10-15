import importlib.util
from pathlib import Path
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def server(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = tmp_path / "public.pem"
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv("MCP_AUTH_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("MCP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.syspath_prepend(str(ROOT / "youtube"))
    spec = importlib.util.spec_from_file_location("youtube_input_tests", ROOT / "youtube/youtube_mcp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("url", ["https://youtu.be/H14bBuluwB8", "https://www.youtube.com/watch?v=H14bBuluwB8", "H14bBuluwB8"])
def test_video_inputs(server, url):
    assert server.extract_video_id(url) == "H14bBuluwB8"


@pytest.mark.parametrize("url", ["https://youtube.com.evil.invalid/watch?v=H14bBuluwB8", "http://127.0.0.1/H14bBuluwB8", "https://youtube.com@127.0.0.1/watch?v=H14bBuluwB8"])
def test_foreign_video_hosts_rejected(server, url):
    with pytest.raises(ValueError):
        server.extract_video_id(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1", "https://169.254.169.254/latest/meta-data", "https://www.youtube.com/redirect?q=http://127.0.0.1", "https://youtube.com.evil.invalid/@TED"])
def test_channel_ssrf_rejected_without_network(server, monkeypatch, url):
    monkeypatch.setattr(server.yt_dlp, "YoutubeDL", lambda *args: pytest.fail("unsafe network access"))
    assert server.youtube_get_channel_videos(url)["error"] == "invalid_channel_url"


def test_legacy_transcript_fields_preserved_with_cache_marker(server):
    payload = {"language": "en", "transcript": [{"text": "Hello", "start": 1.0, "duration": 2.0}]}
    server.transcripts.transport = lambda *args: payload
    first = server.youtube_get_transcript("H14bBuluwB8")
    second = server.youtube_get_transcript("H14bBuluwB8")
    assert first["transcript"] == "Hello"
    assert first["duration_seconds"] == 3
    assert first["segment_count"] == 1
    assert not first["cached"] and second["cached"]


def test_blocked_rich_metadata_falls_back_without_inventing_statistics(server, monkeypatch):
    import io
    import json
    class Blocked:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, *args, **kwargs): raise RuntimeError("Bot check")
    monkeypatch.setattr(server.yt_dlp, "YoutubeDL", lambda *args: Blocked())
    class Opener:
        def open(self, url, timeout):
            assert url.startswith("https://www.youtube.com/oembed?")
            return io.BytesIO(json.dumps({"title":"Example","author_name":"Channel","author_url":"https://www.youtube.com/@Example","thumbnail_url":"https://i.ytimg.com/example.jpg"}).encode())
    monkeypatch.setattr(server.urllib.request, "build_opener", lambda *args: Opener())
    result = server.youtube_get_metadata("H14bBuluwB8")
    assert result["title"] == "Example"
    assert result["source"] == "youtube_oembed" and result["partial"]
    assert result["view_count"] is None and "view_count" in result["unavailable_fields"]


def test_metadata_failure_returns_safe_error(server, monkeypatch):
    monkeypatch.setattr(server.yt_dlp, "YoutubeDL", lambda *args: (_ for _ in ()).throw(RuntimeError("blocked")))
    monkeypatch.setattr(server, "basic_metadata", lambda *args: (_ for _ in ()).throw(RuntimeError("provider failure")))
    assert server.youtube_get_metadata("H14bBuluwB8")["error"] == "metadata_unavailable"
