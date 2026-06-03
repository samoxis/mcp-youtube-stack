"""
YouTube Playlist MCP Server - HTTP transport
Requires OAuth2 credentials for sorinbrocker@gmail.com.

Tools:
  - youtube_playlist_create(title, description, privacy='private')
  - youtube_playlist_list()
  - youtube_playlist_add_video(playlist_id, video_url_or_id)
  - youtube_playlist_search_and_add(playlist_id, query, max_videos=10)
  - youtube_playlist_remove_video(playlist_item_id)
  - youtube_playlist_get_items(playlist_id, max_results=50)
"""
import os
import re
import json
import pickle
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from fastmcp import FastMCP
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import yt_dlp

SCOPES = ["https://www.googleapis.com/auth/youtube"]
# Defaults to a `config/` directory next to this script. Override with MCP_CONFIG_DIR env var.
CREDS_DIR = Path(os.environ.get("MCP_CONFIG_DIR", Path(__file__).parent / "config"))
CLIENT_SECRETS_FILE = CREDS_DIR / "google_oauth_client.json"
TOKEN_FILE = CREDS_DIR / "youtube_token.pickle"

mcp = FastMCP("youtube_playlist_mcp_server")


def _extract_video_id(url: str) -> str:
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


def _get_youtube_service():
    """Get authenticated YouTube API client. Refreshes token automatically."""
    creds = None
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRETS_FILE.exists():
                raise RuntimeError(
                    f"OAuth client secrets not found at {CLIENT_SECRETS_FILE}. "
                    "Run oauth_setup.py first."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRETS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=8765, open_browser=True)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return build("youtube", "v3", credentials=creds)


@mcp.tool
def youtube_playlist_create(title: str, description: str = "", privacy: str = "private") -> dict:
    """Create a new playlist on the authenticated YouTube account.

    Args:
        title: Playlist name (max 150 chars).
        description: Optional description (max 5000 chars).
        privacy: 'private' (default), 'unlisted', or 'public'.
    Returns: {playlist_id, title, url}
    """
    if privacy not in ("private", "unlisted", "public"):
        return {"error": f"Invalid privacy '{privacy}'. Use private/unlisted/public."}
    try:
        yt = _get_youtube_service()
        response = yt.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {"title": title[:150], "description": description[:5000]},
                "status": {"privacyStatus": privacy},
            },
        ).execute()
        pid = response["id"]
        return {
            "playlist_id": pid,
            "title": response["snippet"]["title"],
            "privacy": response["status"]["privacyStatus"],
            "url": f"https://www.youtube.com/playlist?list={pid}",
        }
    except HttpError as e:
        return {"error": f"HttpError: {e.resp.status} {e._get_reason()}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool
def youtube_playlist_list() -> dict:
    """List all playlists owned by the authenticated user."""
    try:
        yt = _get_youtube_service()
        playlists = []
        page_token = None
        while True:
            req = yt.playlists().list(
                part="snippet,contentDetails,status",
                mine=True,
                maxResults=50,
                pageToken=page_token,
            )
            resp = req.execute()
            for p in resp.get("items", []):
                playlists.append({
                    "playlist_id": p["id"],
                    "title": p["snippet"]["title"],
                    "description": p["snippet"]["description"][:300],
                    "item_count": p["contentDetails"]["itemCount"],
                    "privacy": p["status"]["privacyStatus"],
                    "url": f"https://www.youtube.com/playlist?list={p['id']}",
                })
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return {"count": len(playlists), "playlists": playlists}
    except HttpError as e:
        return {"error": f"HttpError: {e.resp.status} {e._get_reason()}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool
def youtube_playlist_add_video(playlist_id: str, video_url_or_id: str) -> dict:
    """Add a single video to a playlist."""
    try:
        video_id = _extract_video_id(video_url_or_id)
        yt = _get_youtube_service()
        response = yt.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        return {
            "playlist_item_id": response["id"],
            "playlist_id": playlist_id,
            "video_id": video_id,
            "position": response["snippet"].get("position"),
        }
    except HttpError as e:
        return {"error": f"HttpError: {e.resp.status} {e._get_reason()}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool
def youtube_playlist_search_and_add(playlist_id: str, query: str, max_videos: int = 10) -> dict:
    """Search YouTube and add top results to a playlist.

    Uses yt-dlp for search (no API quota cost on search), then API for adding.
    """
    max_videos = min(max(1, int(max_videos)), 25)
    ydl_opts = {
        "quiet": True, "skip_download": True, "no_warnings": True,
        "extract_flat": True, "default_search": "ytsearch",
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_videos}:{query}", download=False)
        entries = info.get("entries") or []
        yt = _get_youtube_service()
        added = []
        failed = []
        for e in entries[:max_videos]:
            video_id = e.get("id")
            if not video_id:
                continue
            try:
                resp = yt.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": playlist_id,
                            "resourceId": {"kind": "youtube#video", "videoId": video_id},
                        }
                    },
                ).execute()
                added.append({
                    "video_id": video_id,
                    "title": e.get("title"),
                    "channel": e.get("uploader") or e.get("channel"),
                    "playlist_item_id": resp["id"],
                })
            except HttpError as he:
                failed.append({"video_id": video_id, "title": e.get("title"), "error": he._get_reason()})
        return {
            "query": query,
            "playlist_id": playlist_id,
            "added_count": len(added),
            "failed_count": len(failed),
            "added": added,
            "failed": failed,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool
def youtube_playlist_remove_video(playlist_item_id: str) -> dict:
    """Remove a specific item from a playlist (use playlist_item_id from get_items, NOT video_id)."""
    try:
        yt = _get_youtube_service()
        yt.playlistItems().delete(id=playlist_item_id).execute()
        return {"deleted": playlist_item_id, "status": "ok"}
    except HttpError as e:
        return {"error": f"HttpError: {e.resp.status} {e._get_reason()}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool
def youtube_playlist_get_items(playlist_id: str, max_results: int = 50) -> dict:
    """List videos in a playlist."""
    max_results = min(max(1, int(max_results)), 200)
    try:
        yt = _get_youtube_service()
        items = []
        page_token = None
        while len(items) < max_results:
            req = yt.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=min(50, max_results - len(items)),
                pageToken=page_token,
            )
            resp = req.execute()
            for it in resp.get("items", []):
                items.append({
                    "playlist_item_id": it["id"],
                    "video_id": it["contentDetails"]["videoId"],
                    "title": it["snippet"]["title"],
                    "channel": it["snippet"].get("videoOwnerChannelTitle"),
                    "position": it["snippet"]["position"],
                    "added_at": it["snippet"]["publishedAt"],
                })
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return {"playlist_id": playlist_id, "count": len(items), "items": items}
    except HttpError as e:
        return {"error": f"HttpError: {e.resp.status} {e._get_reason()}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    mcp.run(transport="http", host=args.host, port=args.port)
