"""
YouTube MCP Server - HTTP transport
Compatible with youtube-transcript-api v1.x and yt-dlp.

Tools:
  - youtube_get_metadata(url)
  - youtube_get_transcript(url, lang='en')
  - youtube_get_available_languages(url)
  - youtube_search(query, max_results=10)
"""
import re
from urllib.parse import urlparse, parse_qs
from fastmcp import FastMCP
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp

mcp = FastMCP("youtube_mcp_server")
yt_api = YouTubeTranscriptApi()


def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from any URL form."""
    if not url:
        raise ValueError("URL is empty")
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    parsed = urlparse(url)
    if "youtu.be" in parsed.netloc:
        return parsed.path.lstrip("/").split("/")[0]
    if "youtube.com" in parsed.netloc:
        if parsed.path.startswith(("/shorts/", "/embed/", "/v/")):
            return parsed.path.split("/")[2]
        q = parse_qs(parsed.query)
        if "v" in q:
            return q["v"][0]
    m = re.search(r"([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
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
        # Try preferred language first
        try:
            fetched = yt_api.fetch(video_id, languages=[lang])
        except Exception:
            # Fallback: list available, take first
            transcript_list = yt_api.list(video_id)
            first = next(iter(transcript_list))
            fetched = first.fetch()

        # fetched is FetchedTranscript object — iterable of snippets
        snippets = list(fetched)
        full_text = " ".join(s.text for s in snippets)
        used_lang = fetched.language_code if hasattr(fetched, "language_code") else lang

        return {
            "video_id": video_id,
            "language": used_lang,
            "transcript": full_text,
            "segment_count": len(snippets),
            "duration_seconds": snippets[-1].start + snippets[-1].duration if snippets else 0,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "video_id": video_id}


@mcp.tool
def youtube_get_metadata(url: str) -> dict:
    """Fetch metadata of a YouTube video (no transcript).

    Returns: title, channel, duration, views, likes, upload_date, description (truncated), tags.
    """
    video_id = extract_video_id(url)
    ydl_opts = {"quiet": True, "skip_download": True, "no_warnings": True}
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
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "video_id": video_id}


@mcp.tool
def youtube_get_available_languages(url: str) -> dict:
    """List available transcript languages for a video."""
    video_id = extract_video_id(url)
    try:
        transcript_list = yt_api.list(video_id)
        langs = []
        for t in transcript_list:
            langs.append({
                "language_code": t.language_code,
                "language": t.language,
                "is_generated": t.is_generated,
                "is_translatable": t.is_translatable,
            })
        return {"video_id": video_id, "languages": langs}
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
        "quiet": True,
        "skip_download": True,
        "no_warnings": True,
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
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": max_videos,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
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
