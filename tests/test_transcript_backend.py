import concurrent.futures
import json
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "youtube"))
from transcript_backend import TranscriptBackend, ProviderError

VIDEO = "H14bBuluwB8"
PAYLOAD = {"language": "en", "transcript": [{"text": "Example caption", "start": 0.0, "duration": 2.5}]}


def test_persistent_cache_avoids_network_after_restart(tmp_path):
    calls = []
    def transport(*args):
        calls.append(args)
        return PAYLOAD
    original = TranscriptBackend(tmp_path, transport=transport)
    assert original.get("transcript", VIDEO) == (PAYLOAD, False)
    restarted = TranscriptBackend(tmp_path, transport=lambda *args: pytest.fail("unexpected upstream request"))
    assert restarted.get("transcript", VIDEO) == (PAYLOAD, True)
    assert len(calls) == 1


def test_ttl_and_language_are_independent(tmp_path):
    now = [10000]
    calls = []
    def transport(*args):
        calls.append(args)
        return PAYLOAD
    backend = TranscriptBackend(tmp_path, ttl=60, transport=transport, clock=lambda: now[0])
    backend.get("transcript", VIDEO)
    backend.get("transcript", VIDEO, "ro")
    assert backend.get("transcript", VIDEO)[1]
    now[0] += 61
    assert not backend.get("transcript", VIDEO)[1]
    assert len(calls) == 3


def test_failed_requests_count_against_durable_quota_but_cache_still_works(tmp_path):
    now = [10000]
    def transport(kind, video, lang):
        if lang == "ro":
            raise ProviderError("provider_unavailable", "Unavailable")
        return PAYLOAD
    backend = TranscriptBackend(tmp_path, quota=2, transport=transport, clock=lambda: now[0])
    backend.get("transcript", VIDEO)
    with pytest.raises(ProviderError, match="Unavailable"):
        backend.get("transcript", VIDEO, "ro")
    restarted = TranscriptBackend(tmp_path, quota=2, transport=transport, clock=lambda: now[0])
    assert restarted.get("transcript", VIDEO)[1]
    with pytest.raises(ProviderError) as error:
        restarted.get("transcript", VIDEO, "de")
    assert error.value.code == "local_quota_exhausted"
    now[0] += 3601
    assert not restarted.get("transcript", VIDEO, "de")[1]


def test_provider_429_persists_cooldown_and_serves_cached_responses(tmp_path):
    now = [10000]
    backend = TranscriptBackend(tmp_path, transport=lambda *args: PAYLOAD, clock=lambda: now[0])
    backend.get("transcript", VIDEO)
    def limited(*args):
        raise ProviderError("provider_rate_limited", "Limited", 300)
    backend.transport = limited
    with pytest.raises(ProviderError):
        backend.get("transcript", VIDEO, "ro")
    restarted = TranscriptBackend(tmp_path, transport=lambda *args: pytest.fail("must not retry during cooldown"), clock=lambda: now[0])
    assert restarted.get("transcript", VIDEO)[1]
    with pytest.raises(ProviderError) as error:
        restarted.get("transcript", VIDEO, "de")
    assert error.value.retry_after == 300


@pytest.mark.parametrize("bad", [{}, {"language": "en", "transcript": []},
    {"language": "en", "transcript": [{"text": "bad", "start": -1, "duration": 1}]},
    {"language": "en", "transcript": [{"text": "bad", "start": float("nan"), "duration": 1}]}])
def test_invalid_provider_payload_is_not_cached(tmp_path, bad):
    backend = TranscriptBackend(tmp_path, transport=lambda *args: bad)
    with pytest.raises(ProviderError):
        backend.get("transcript", VIDEO)
    backend.transport = lambda *args: PAYLOAD
    assert not backend.get("transcript", VIDEO)[1]


def test_concurrent_identical_requests_make_only_one_network_call(tmp_path):
    calls = []
    def transport(*args):
        calls.append(args)
        time.sleep(.02)
        return PAYLOAD
    backend = TranscriptBackend(tmp_path, transport=transport)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: backend.get("transcript", VIDEO), range(6)))
    assert len(calls) == 1
    assert sum(not cached for _, cached in results) == 1


def test_languages_are_cached_separately(tmp_path):
    data = {"languages": [{"code": "en", "name": "English", "auto_generated": False}]}
    backend = TranscriptBackend(tmp_path, transport=lambda *args: data)
    assert backend.get("languages", VIDEO) == (data, False)
    assert backend.get("languages", VIDEO) == (data, True)


def test_cache_evicts_oldest_records(tmp_path):
    now = [10000]
    backend = TranscriptBackend(tmp_path, transport=lambda *args: PAYLOAD, clock=lambda: now[0])
    backend.MAX_ROWS = 2
    for lang in ["en", "ro", "de"]:
        backend.get("transcript", VIDEO, lang)
        now[0] += 1
    assert backend.get("transcript", VIDEO, "de")[1]
    assert not backend.get("transcript", VIDEO, "en")[1]


@pytest.mark.parametrize("video,lang", [("../../etc/passwd", "en"), (VIDEO, "en&url=http://127.0.0.1"), (VIDEO, "")])
def test_invalid_inputs_never_reach_provider(tmp_path, video, lang):
    backend = TranscriptBackend(tmp_path, transport=lambda *args: pytest.fail("unexpected provider access"))
    with pytest.raises(ValueError):
        backend.get("transcript", video, lang)


def test_http_429_has_no_automatic_retry(tmp_path, monkeypatch):
    import urllib.error
    import urllib.request
    from email.message import Message
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            headers = Message()
            headers["Retry-After"] = "120"
            raise urllib.error.HTTPError(request.full_url, 429, "Limited", headers, None)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    backend = TranscriptBackend(tmp_path)
    with pytest.raises(ProviderError) as error:
        backend.get("transcript", VIDEO)
    assert error.value.code == "provider_rate_limited"
    assert len(calls) == 1
    assert "Authorization" not in calls[0].headers
