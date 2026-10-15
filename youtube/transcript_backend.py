"""Anonymous FreeTranscriptAPI adapter with bounded persistent cache and quota.

No API keys, Google cookies, proxies, or paid fallback. One application process.
Only successful, validated responses are cached; quota reservations survive restarts.
"""
import json
import math
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class ProviderError(Exception):
    def __init__(self, code, message, retry_after=None):
        super().__init__(message)
        self.code, self.retry_after = code, retry_after


def validate_request(video_id, language):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Expected an 11-character YouTube video ID")
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language) or len(language) > 35:
        raise ValueError("Invalid caption language code")


def validate_payload(kind, data):
    if not isinstance(data, dict):
        raise ProviderError("invalid_response", "Provider returned an invalid response")
    if kind == "transcript":
        segments = data.get("transcript")
        if not isinstance(data.get("language"), str) or not isinstance(segments, list) or not segments:
            raise ProviderError("invalid_response", "Provider returned no valid caption segments")
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
                raise ProviderError("invalid_response", "Invalid caption segment")
            for key in ("start", "duration"):
                value = segment.get(key)
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                    raise ProviderError("invalid_response", "Invalid caption timestamp")
    elif kind == "languages":
        if not isinstance(data.get("languages"), list):
            raise ProviderError("invalid_response", "Invalid caption language list")
        for item in data["languages"]:
            if not isinstance(item, dict) or not isinstance(item.get("code"), str):
                raise ProviderError("invalid_response", "Invalid caption language")
    else:
        raise ValueError("Unsupported provider operation")
    return data


class TranscriptBackend:
    ENDPOINT = "https://api.freetranscriptapi.com/v1/"
    MAX_RESPONSE = 4 * 1024 * 1024
    MAX_ROWS = 1000
    MAX_CACHE_BYTES = 64 * 1024 * 1024

    def __init__(self, cache_dir, ttl=604800, quota=18, transport=None, clock=time.time):
        self.directory = Path(cache_dir)
        self.ttl, self.quota = int(ttl), int(quota)
        if not 60 <= self.ttl <= 2592000 or not 1 <= self.quota <= 20:
            raise ValueError("Invalid cache lifetime or provider hourly quota")
        self.transport, self.clock = transport or self._http, clock
        self.lock = threading.Lock()

    def _connect(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        db = sqlite3.connect(self.directory / "transcripts.sqlite3", timeout=10)
        db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS requests (created REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value REAL NOT NULL)")
        return db

    def get(self, kind, video_id, language="en"):
        validate_request(video_id, language)
        if kind not in ("transcript", "languages"):
            raise ValueError("Unsupported provider operation")
        key = json.dumps([kind, video_id, language if kind == "transcript" else ""], separators=(",", ":"))
        # Serializes cache misses to avoid duplicate upstream calls and quota races.
        with self.lock:
            now = self.clock()
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM cache WHERE created <= ?", (now - self.ttl,))
                row = db.execute("SELECT payload FROM cache WHERE key=?", (key,)).fetchone()
                if row:
                    data = validate_payload(kind, json.loads(row[0]))
                    db.commit()
                    return data, True
                cooldown = db.execute("SELECT value FROM state WHERE key='cooldown'").fetchone()
                if cooldown and cooldown[0] > now:
                    raise ProviderError("provider_rate_limited", "Provider is cooling down; cached results remain available", math.ceil(cooldown[0] - now))
                db.execute("DELETE FROM requests WHERE created <= ?", (now - 3600,))
                count, oldest = db.execute("SELECT COUNT(*), MIN(created) FROM requests").fetchone()
                if count >= self.quota:
                    raise ProviderError("local_quota_exhausted", "Free provider request budget reached; cached results remain available", max(1, math.ceil(oldest + 3600 - now)))
                db.execute("INSERT INTO requests VALUES (?)", (now,))
                db.commit()  # Reserve before HTTP, including failed requests.
                try:
                    data = validate_payload(kind, self.transport(kind, video_id, language))
                except ProviderError as error:
                    if error.code == "provider_rate_limited":
                        db.execute("INSERT OR REPLACE INTO state VALUES ('cooldown', ?)", (self.clock() + min(max(error.retry_after or 3600, 1), 86400),))
                        db.commit()
                    raise
                payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                if len(payload.encode()) > self.MAX_RESPONSE:
                    raise ProviderError("invalid_response", "Caption response exceeds maximum size")
                db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, self.clock(), payload))
                while True:
                    rows, size = db.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(payload AS BLOB))),0) FROM cache").fetchone()
                    if rows <= self.MAX_ROWS and size <= self.MAX_CACHE_BYTES:
                        break
                    db.execute("DELETE FROM cache WHERE key=(SELECT key FROM cache ORDER BY created,key LIMIT 1)")
                db.commit()
                return data, False
            finally:
                db.close()

    def _http(self, kind, video_id, language):
        params = {"video_url": video_id}
        if kind == "transcript":
            params.update(lang=language, format="json")
        request = urllib.request.Request(self.ENDPOINT + kind + "?" + urllib.parse.urlencode(params),
                                         headers={"Accept": "application/json", "User-Agent": "Samoxis-YouTube-MCP/1.0"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=25) as response:
                raw = response.read(self.MAX_RESPONSE + 1)
            if len(raw) > self.MAX_RESPONSE:
                raise ProviderError("invalid_response", "Caption response exceeds maximum size")
            try:
                return json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise ProviderError("invalid_response", "Provider did not return valid JSON") from exc
        except urllib.error.HTTPError as exc:
            status = exc.code
            retry_after = exc.headers.get("Retry-After", "")
            reset = exc.headers.get("X-RateLimit-Reset", "")
            exc.close()
            if status == 429:
                wait = int(retry_after) if retry_after.isdigit() else max(1, int(reset) - int(self.clock())) if reset.isdigit() else 3600
                raise ProviderError("provider_rate_limited", "Anonymous provider quota is temporarily exhausted", wait) from exc
            if status == 404:
                raise ProviderError("transcript_unavailable", "No captions available for this video") from exc
            raise ProviderError("provider_unavailable", f"Caption provider returned HTTP {status}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError("provider_unavailable", "Caption provider connection failed or timed out") from exc
