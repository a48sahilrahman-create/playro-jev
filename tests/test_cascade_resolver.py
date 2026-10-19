import sys
from pathlib import Path
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Ensure repo root is in Python path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cascade_resolver
from cascade_resolver import (
    verify_single_candidate,
    resolve_stream_cascade,
    cascade_search_and_resolve
)


@pytest.mark.asyncio
async def test_single_candidate_verification_success():
    """Verify single candidate verification returns SUCCESS, valid=True, direct_url, ttfb_ms, format, and candidate."""
    candidate = {
        "title": "Interstellar (2014) 2160p UHD",
        "target_url": "https://hubcloud.club/drive/interstellar123",
        "resolution": "2160p",
        "size": "15.4 GB"
    }

    mock_resolve = {"status": "SUCCESS", "direct_url": "https://fast.cdn/interstellar.mkv"}
    mock_verify = {
        "valid": True,
        "format": "MKV (EBML)",
        "size": "15.4 GB",
        "total_bytes": 16535000000,
        "content_type": "video/x-matroska"
    }

    with patch.object(cascade_resolver.link_resolver, "resolve_auto", new_callable=AsyncMock, return_value=mock_resolve), \
         patch.object(cascade_resolver.stream_verifier, "verify", new_callable=AsyncMock, return_value=mock_verify):

        result = await verify_single_candidate(candidate)

        assert result is not None
        assert result["status"] == "SUCCESS"
        assert result["valid"] is True
        assert result["verified"] is True
        assert result["direct_url"] == "https://fast.cdn/interstellar.mkv"
        assert result["format"] == "MKV (EBML)"
        assert result["size"] == "15.4 GB"
        assert result["candidate"] == candidate
        assert "ttfb_ms" in result
        assert isinstance(result["ttfb_ms"], (int, float))
        assert result["ttfb_ms"] >= 0
        assert result["title"] == "Interstellar (2014) 2160p UHD"


@pytest.mark.asyncio
async def test_single_candidate_403_quota_exceeded():
    """Verify 403 HTTP status code marks candidate status FAILED and reason QUOTA_EXCEEDED."""
    candidate = {
        "title": "Quota Limited Candidate",
        "target_url": "https://pixeldrain.com/u/quota_limited",
        "size": "5.0 GB"
    }

    mock_resolve = {"status": "SUCCESS", "direct_url": "https://pixeldrain.com/api/file/quota_limited"}
    mock_verify = {
        "valid": False,
        "status_code": 403,
        "error": "HTTP 403 Forbidden: Daily download quota exceeded"
    }

    with patch.object(cascade_resolver.link_resolver, "resolve_auto", new_callable=AsyncMock, return_value=mock_resolve), \
         patch.object(cascade_resolver.stream_verifier, "verify", new_callable=AsyncMock, return_value=mock_verify):

        result = await verify_single_candidate(candidate)

        assert result is not None
        assert result["status"] == "FAILED"
        assert result["valid"] is False
        assert result["reason"] == "QUOTA_EXCEEDED"
        assert result["candidate"] == candidate


@pytest.mark.asyncio
async def test_single_candidate_json_quota_exceeded():
    """Verify JSON rate limit/quota error from resolver marks candidate status FAILED and reason QUOTA_EXCEEDED."""
    candidate = {
        "title": "Cloudflare Rate Limited Link",
        "target_url": "https://gateway.com/ratelimit"
    }

    mock_resolve = {
        "status": "FAILED",
        "status_code": 403,
        "error": '{"error": {"code": 403, "message": "User Rate Limit Exceeded"}}'
    }

    with patch.object(cascade_resolver.link_resolver, "resolve_auto", new_callable=AsyncMock, return_value=mock_resolve):
        result = await verify_single_candidate(candidate)

        assert result is not None
        assert result["status"] == "FAILED"
        assert result["valid"] is False
        assert result["reason"] == "QUOTA_EXCEEDED"
        assert result["candidate"] == candidate


@pytest.mark.asyncio
async def test_single_candidate_resolution_failure():
    """Verify single candidate returns status FAILED when gateway shortener fails to resolve."""
    candidate = {
        "target_url": "https://broken-gateway.com/dead",
        "title": "Broken Link"
    }

    mock_resolve = {"status": "FAILED", "error": "Gateway timeout 504"}

    with patch.object(cascade_resolver.link_resolver, "resolve_auto", new_callable=AsyncMock, return_value=mock_resolve):
        result = await verify_single_candidate(candidate)
        assert result is not None
        assert result["status"] == "FAILED"
        assert result["valid"] is False
        assert result["candidate"] == candidate


@pytest.mark.asyncio
async def test_single_candidate_invalid_container():
    """Verify single candidate returns status FAILED when container verification fails (e.g. HTML error page)."""
    candidate = {
        "target_url": "https://gateway.com/watch",
        "title": "HTML Fake Stream"
    }

    mock_resolve = {"status": "SUCCESS", "direct_url": "https://fake.site/error.html"}
    mock_verify = {"valid": False, "error": "HTML page detected instead of video"}

    with patch.object(cascade_resolver.link_resolver, "resolve_auto", new_callable=AsyncMock, return_value=mock_resolve), \
         patch.object(cascade_resolver.stream_verifier, "verify", new_callable=AsyncMock, return_value=mock_verify):

        result = await verify_single_candidate(candidate)
        assert result is not None
        assert result["status"] == "FAILED"
        assert result["valid"] is False
        assert result["candidate"] == candidate


@pytest.mark.asyncio
async def test_single_candidate_empty_and_invalid_inputs():
    """Verify verify_single_candidate safely handles empty candidate or missing URL."""
    res_empty = await verify_single_candidate({})
    assert res_empty["status"] == "FAILED"
    assert res_empty["valid"] is False

    res_no_url = await verify_single_candidate({"target_url": ""})
    assert res_no_url["status"] == "FAILED"
    assert res_no_url["valid"] is False

    res_none = await verify_single_candidate(None)
    assert res_none["status"] == "FAILED"
    assert res_none["valid"] is False


@pytest.mark.asyncio
async def test_parallel_cascade_election_fastest_responder_and_waterfall_ordering_with_403_quota():
    """
    Test parallel cascade resolution with candidates of various latencies and one 403 quota candidate:
    - Candidate Fast (80ms TTFB) -> must be elected as primary.
    - Candidate Medium (200ms TTFB) -> ranked in waterfall (1st position).
    - Candidate Slow (450ms TTFB) -> ranked in waterfall (2nd position).
    - Candidate Quota (403 quota exceeded) -> marked FAILED, excluded from primary and waterfall.
    - Embed fallbacks attached with 5 providers.
    """
    cand_slow = {
        "title": "Release Slow 4K",
        "target_url": "https://slow.host/stream.mkv",
        "resolution": "2160p",
        "size": "20.0 GB",
        "imdb_id": "tt1375666"
    }
    cand_fast = {
        "title": "Release Fast 1080p",
        "target_url": "https://fast.host/stream.mp4",
        "resolution": "1080p",
        "size": "4.0 GB",
        "imdb_id": "tt1375666"
    }
    cand_medium = {
        "title": "Release Medium 1080p",
        "target_url": "https://medium.host/stream.mkv",
        "resolution": "1080p",
        "size": "6.0 GB",
        "imdb_id": "tt1375666"
    }
    cand_quota_403 = {
        "title": "Release Quota Blocked",
        "target_url": "https://quota.host/blocked.mkv",
        "resolution": "1080p",
        "size": "5.0 GB",
        "imdb_id": "tt1375666"
    }

    async def mock_verify(candidate, client=None, timeout=2.5):
        url = candidate.get("target_url", "")
        if "fast.host" in url:
            await asyncio.sleep(0.080)
            return {
                "status": "SUCCESS",
                "candidate": candidate,
                "direct_url": "https://fast.cdn/stream.mp4",
                "ttfb_ms": 80.0,
                "format": "MP4",
                "valid": True,
                "verified": True,
                "size": "4.0 GB"
            }
        elif "medium.host" in url:
            await asyncio.sleep(0.200)
            return {
                "status": "SUCCESS",
                "candidate": candidate,
                "direct_url": "https://medium.cdn/stream.mkv",
                "ttfb_ms": 200.0,
                "format": "MKV (EBML)",
                "valid": True,
                "verified": True,
                "size": "6.0 GB"
            }
        elif "slow.host" in url:
            await asyncio.sleep(0.450)
            return {
                "status": "SUCCESS",
                "candidate": candidate,
                "direct_url": "https://slow.cdn/stream.mkv",
                "ttfb_ms": 450.0,
                "format": "MKV (EBML)",
                "valid": True,
                "verified": True,
                "size": "20.0 GB"
            }
        elif "quota.host" in url:
            await asyncio.sleep(0.050)
            return {
                "status": "FAILED",
                "reason": "QUOTA_EXCEEDED",
                "candidate": candidate,
                "direct_url": "https://quota.cdn/blocked.mkv",
                "ttfb_ms": 50.0,
                "format": "Unknown",
                "valid": False,
                "verified": False,
                "error": "403 Quota Exceeded"
            }
        return {"status": "FAILED", "valid": False}

    candidates = [cand_slow, cand_fast, cand_medium, cand_quota_403]

    with patch("cascade_resolver.verify_single_candidate", side_effect=mock_verify):
        res = await resolve_stream_cascade(
            candidates,
            max_workers=5,
            stagger_delay=0.02,
            timeout=3.0,
            imdb_id="tt1375666"
        )

        assert res["status"] == "SUCCESS"
        assert res["primary"] is not None
        # Fastest healthy responder elected as primary
        assert res["primary"]["direct_url"] == "https://fast.cdn/stream.mp4"
        assert res["primary"]["valid"] is True
        assert res["primary"]["status"] == "SUCCESS"

        # Waterfall contains medium (200ms) and slow (450ms) in exact ascending TTFB order
        assert len(res["waterfall"]) == 2
        assert res["waterfall"][0]["direct_url"] == "https://medium.cdn/stream.mkv"
        assert res["waterfall"][0]["ttfb_ms"] == 200.0
        assert res["waterfall"][1]["direct_url"] == "https://slow.cdn/stream.mkv"
        assert res["waterfall"][1]["ttfb_ms"] == 450.0

        # Quota candidate is excluded from primary and waterfall
        all_stream_urls = [res["primary"]["direct_url"]] + [w["direct_url"] for w in res["waterfall"]]
        assert "https://quota.cdn/blocked.mkv" not in all_stream_urls

        # Embed fallback is attached with 5 providers
        assert "embed_fallback" in res
        assert "all_providers" in res["embed_fallback"]
        assert len(res["embed_fallback"]["all_providers"]) == 5
        assert "tt1375666" in res["embed_fallback"]["embed_url"]


@pytest.mark.asyncio
async def test_cascade_all_fail_or_quota_returns_fallback():
    """Verify resolve_stream_cascade returns FALLBACK status and attaches embed fallbacks when all candidates fail or quota-out."""
    candidates = [
        {"target_url": "https://quota.host/403_link", "title": "403 Quota", "imdb_id": "tt0137523"},
        {"target_url": "https://dead.host/dead_link", "title": "Dead Link", "imdb_id": "tt0137523"}
    ]

    async def mock_verify(candidate, client=None, timeout=2.5):
        if "quota.host" in candidate["target_url"]:
            return {
                "status": "FAILED",
                "reason": "QUOTA_EXCEEDED",
                "candidate": candidate,
                "direct_url": "",
                "ttfb_ms": 50.0,
                "valid": False
            }
        return {
            "status": "FAILED",
            "reason": "RESOLUTION_FAILED",
            "candidate": candidate,
            "direct_url": "",
            "ttfb_ms": 100.0,
            "valid": False
        }

    with patch("cascade_resolver.verify_single_candidate", side_effect=mock_verify):
        res = await resolve_stream_cascade(candidates, max_workers=2, stagger_delay=0.01, imdb_id="tt0137523")

        assert res["status"] == "FALLBACK"
        assert res["primary"] is None
        assert res["waterfall"] == []
        assert "embed_fallback" in res
        assert res["embed_fallback"]["provider"] == "filmu"
        assert "tt0137523" in res["embed_fallback"]["embed_url"]
        assert len(res["embed_fallback"]["all_providers"]) == 5


@pytest.mark.asyncio
async def test_cascade_empty_candidates_returns_fallback():
    """Verify resolve_stream_cascade handles empty candidate list gracefully with status FALLBACK."""
    res_empty = await resolve_stream_cascade([], imdb_id="tt0137523")
    assert res_empty["status"] == "FALLBACK"
    assert res_empty["primary"] is None
    assert res_empty["waterfall"] == []
    assert res_empty["candidates_tested"] == 0
    assert "embed_fallback" in res_empty

    res_none = await resolve_stream_cascade(None)
    assert res_none["status"] == "FALLBACK"
    assert res_none["primary"] is None
    assert res_none["waterfall"] == []
    assert res_none["candidates_tested"] == 0


@pytest.mark.asyncio
async def test_cascade_search_and_resolve_integration():
    """Verify cascade_search_and_resolve calls engine.search() and cascades top candidates."""
    mock_releases = [
        {"title": "Dune 2021 2160p", "target_url": "https://gateway.com/dune1", "resolution": "2160p", "imdb_id": "tt1160419"},
        {"title": "Dune 2021 1080p", "target_url": "https://gateway.com/dune2", "resolution": "1080p", "imdb_id": "tt1160419"}
    ]

    mock_engine = MagicMock()
    mock_engine.search = AsyncMock(return_value={"status": "CATALOG_FOUND", "releases": mock_releases})

    mock_resolved = {
        "status": "SUCCESS",
        "title": "Dune 2021 2160p",
        "candidate": mock_releases[0],
        "direct_url": "https://cdn.stream/dune.mkv",
        "ttfb_ms": 110.0,
        "valid": True,
        "verified": True,
        "format": "MKV (EBML)",
        "size": "18 GB"
    }

    with patch("cascade_resolver.verify_single_candidate", new_callable=AsyncMock, return_value=mock_resolved):
        res = await cascade_search_and_resolve("Dune", media_type="movie", engine=mock_engine, imdb_id="tt1160419")

        assert res["status"] == "SUCCESS"
        assert res["primary"] is not None
        assert res["primary"]["direct_url"] == "https://cdn.stream/dune.mkv"
        assert "embed_fallback" in res
        assert "tt1160419" in res["embed_fallback"]["embed_url"]
        mock_engine.search.assert_awaited_once_with("Dune", season=None, episode=None)
