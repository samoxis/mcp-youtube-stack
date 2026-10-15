"""
YouTube MCP Server - HTTP transport
Captions via anonymous FreeTranscriptAPI, persistent cache; search via yt-dlp.

Tools:
  - youtube_get_metadata(url)
  - youtube_get_transcript(url, lang='en')
  - youtube_get_available_languages(url)
  - youtube_search(query, max_results=10)
"""
import os
import re
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier
from transcript_backend import TranscriptBackend, ProviderError
import yt_dlp

auth = JWTVerifier(
    public_key=Path(os.environ["MCP_AUTH_PUBLIC_KEY_FILE"]).read_text(encoding="utf-8"),
    issuer="samoxis-mcp",
    audience="youtube-readonly",
    algorithm="RS256",
)
mcp = FastMCP("youtube_mcp_server", auth=auth)
transcripts = TranscriptBackend(
    os.environ.get("MCP_CACHE_DIR", str(Path.home() / ".cache" / "mcp-youtube")),
    ttl=int(os.environ.get("MCP_CACHE_TTL_SECONDS", "604800")),
    quota=int(os.environ.get("MCP_PROVIDER_REQUESTS_PER_HOUR", "18")),
)


def provider_error(error, video_id):
    result = {"error": error.code, "message": str(error), "video_id": video_id}
    if error.retry_after is not None:
        result["retry_after_seconds"] = error.retry_after
    return result


def ydl_options():
    return {"quiet": True, "skip_download": True, "no_warnings": True,
            "socket_timeout": 15, "retries": 0, "extractor_retries": 0,
            "cachedir": False, "proxy": ""}


def basic_metadata(video_id):
    """Public YouTube oEmbed fallback; unavailable statistics remain null."""
    query = urlencode({"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("https://www.youtube.com/oembed?" + query, timeout=15) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("Metadata response too large")
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("title"), str) or not data["title"]:
        raise ValueError("Invalid YouTube oEmbed metadata")
    return {"video_id": video_id, "title": data["title"], "channel": data.get("author_name"),
            "channel_url": data.get("author_url"), "thumbnail": data.get("thumbnail_url"),
            "duration_seconds": None, "view_count": None, "like_count": None,
            "upload_date": None, "description": None, "tags": [], "categories": [],
            "source": "youtube_oembed", "partial": True,
            "unavailable_fields": ["duration_seconds", "view_count", "like_count", "upload_date", "description", "tags", "categories"]}


def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from any URL form."""
    if not url:
        raise ValueError("URL is empty")
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    parsed = urlparse(url)
    candidate = None
    if parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in (None, 443):
        if parsed.hostname == "youtu.be":
            candidate = parsed.path.lstrip("/").split("/")[0]
        elif parsed.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            if parsed.path.startswith(("/shorts/", "/embed/", "/v/")):
                candidate = parsed.path.split("/")[2]
            elif parsed.path == "/watch":
                candidate = parse_qs(parsed.query).get("v", [None])[0]
    if candidate and re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
        return candidate
    raise ValueError(f"Could not extract video ID from URL: {url}")


@mcp.tool
def youtube_get_transcript(url: str, lang: str = "en") -> dict:
    """Fetch transcript text of a YouTube video.

    Args:
        url: Full YouTube URL or 11-char video ID.
        lang: Preferred language code (default 'en'). Falls back to any available.
    """
    video_id = extract_video_id(url)
    try:
        data, cached = transcripts.get("transcript", video_id, lang)
        snippets = data["transcript"]
        return {
            "video_id": video_id,
            "language": data["language"],
            "transcript": " ".join(s["text"] for s in snippets),
            "segment_count": len(snippets),
            "duration_seconds": max(s["start"] + s["duration"] for s in snippets),
            "source": "freetranscriptapi",
            "cached": cached,
        }
    except ProviderError as error:
        return provider_error(error, video_id)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "video_id": video_id}


@mcp.tool
def youtube_get_metadata(url: str) -> dict:
    """Fetch metadata of a YouTube video (no transcript).

    Returns: title, channel, duration, views, likes, upload_date, description (truncated), tags.
    When rich metadata is blocked, returns basic oEmbed metadata with partial=True
    and an explicit unavailable_fields list. Missing statistics are never invented.
    """
    video_id = extract_video_id(url)
    ydl_opts = ydl_options()
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        desc = (info.get("description") or "")[:2000]
        return {
            "video_id": video_id,
            "title": info.get("title"),
            "channel": info.get("uploader") or info.get("channel"),
            "channel_url": info.get("uploader_url") or info.get("channel_url"),
            "duration_seconds": info.get("duration"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "upload_date": info.get("upload_date"),
            "description": desc,
            "tags": (info.get("tags") or [])[:20],
            "categories": info.get("categories") or [],
            "thumbnail": info.get("thumbnail"),
        }
    except Exception:
        try:
            return basic_metadata(video_id)
        except Exception:
            return {"error": "metadata_unavailable", "message": "YouTube metadata could not be retrieved", "video_id": video_id}


@mcp.tool
def youtube_get_available_languages(url: str) -> dict:
    """List available transcript languages for a video."""
    video_id = extract_video_id(url)
    try:
        data, cached = transcripts.get("languages", video_id)
        langs = [{"language_code": t["code"], "language": t.get("name", t["code"]),
                  "is_generated": t.get("auto_generated"), "is_translatable": None}
                 for t in data["languages"]]
        return {"video_id": video_id, "languages": langs, "source": "freetranscriptapi", "cached": cached}
    except ProviderError as error:
        return provider_error(error, video_id)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "video_id": video_id}


@mcp.tool
def youtube_search(query: str, max_results: int = 10) -> dict:
    """Search YouTube for videos matching a query.

    Args:
        query: Search query string.
        max_results: Max number of results (default 10, cap 25).
    Returns: list of {video_id, title, channel, duration, views, url}.
    """
    max_results = min(max(1, int(max_results)), 25)
    ydl_opts = {
        **ydl_options(),
        "extract_flat": True,
        "default_search": "ytsearch",
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        entries = info.get("entries") or []
        results = []
        for e in entries[:max_results]:
            results.append({
                "video_id": e.get("id"),
                "title": e.get("title"),
                "channel": e.get("uploader") or e.get("channel"),
                "duration_seconds": e.get("duration"),
                "view_count": e.get("view_count"),
                "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}",
            })
        return {"query": query, "count": len(results), "results": results}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "query": query}


@mcp.tool
def youtube_get_channel_videos(channel_url: str, max_videos: int = 20) -> dict:
    """Get recent videos from a YouTube channel.

    Args:
        channel_url: Channel URL (https://www.youtube.com/@handle or /channel/UCxxx).
        max_videos: Max videos to return (default 20, cap 50).
    """
    max_videos = min(max(1, int(max_videos)), 50)
    parsed_channel = urlparse(channel_url)
    if (parsed_channel.scheme != "https" or parsed_channel.hostname not in ("youtube.com", "www.youtube.com", "m.youtube.com")
            or parsed_channel.username or parsed_channel.password or parsed_channel.port not in (None, 443)
            or not re.fullmatch(r"/(?:@[A-Za-z0-9_.\-\u0080-\uffff]+|channel/UC[A-Za-z0-9_-]{22})(?:/videos)?/?", parsed_channel.path)
            or parsed_channel.query or parsed_channel.fragment):
        return {"error": "invalid_channel_url", "message": "Use an HTTPS YouTube @handle or /channel/UC... URL"}
    channel_url = "https://www.youtube.com" + parsed_channel.path.rstrip("/")
    ydl_opts = {
        **ydl_options(),
        "extract_flat": True,
        "playlistend": max_videos,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False, ie_key="YoutubeTab")
        entries = info.get("entries") or []
        # Sometimes channels return tabs (Videos/Shorts/Live)
        if entries and entries[0].get("_type") == "playlist":
            entries = entries[0].get("entries") or []
        videos = []
        for e in entries[:max_videos]:
            videos.append({
                "video_id": e.get("id"),
                "title": e.get("title"),
                "duration_seconds": e.get("duration"),
                "view_count": e.get("view_count"),
                "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}",
            })
        return {
            "channel": info.get("title") or info.get("uploader"),
            "channel_url": channel_url,
            "count": len(videos),
            "videos": videos,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "channel_url": channel_url}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    mcp.run(transport="http", host=args.host, port=args.port)
