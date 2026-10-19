import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import cascade_resolver
from cascade_resolver import (
    verify_single_candidate,
    resolve_stream_cascade,
    cascade_search_and_resolve
)


@pytest.mark.asyncio
async def test_single_candidate_verification_success():
    """Verify single candidate verification enriches dict with direct_url, ttfb_ms, verified=True, format, size."""
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
        assert result["verified"] is True
        assert result["direct_url"] == "https://fast.cdn/interstellar.mkv"
        assert result["format"] == "MKV (EBML)"
        assert result["size"] == "15.4 GB"
        assert "ttfb_ms" in result
        assert isinstance(result["ttfb_ms"], (int, float))
        assert result["ttfb_ms"] >= 0
        assert result["title"] == "Interstellar (2014) 2160p UHD"


@pytest.mark.asyncio
async def test_single_candidate_verification_via_class_patching():
    """Verify single candidate verification works when patching LinkResolver and StreamVerifier classes."""
    candidate = {
        "url": "https://gateway.com/link_via_url_field",
        "title": "URL Field Candidate"
    }
    mock_resolve = {"status": "SUCCESS", "direct_url": "https://cdn.example/video.mp4"}
    mock_verify = {"valid": True, "format": "MP4", "size": "1.2 GB"}

    with patch("link_resolver.LinkResolver.resolve_auto", new_callable=AsyncMock, return_value=mock_resolve), \
         patch("stream_verifier.StreamVerifier.verify", new_callable=AsyncMock, return_value=mock_verify):

        result = await verify_single_candidate(candidate)
        assert result is not None
        assert result["verified"] is True
        assert result["direct_url"] == "https://cdn.example/video.mp4"
        assert result["format"] == "MP4"
        assert result["size"] == "1.2 GB"


@pytest.mark.asyncio
async def test_single_candidate_verification_resolution_failure():
    """Verify single candidate returns None when gateway shortener fails to resolve."""
    candidate = {
        "target_url": "https://broken-gateway.com/dead",
        "title": "Broken Link"
    }

    mock_resolve = {"status": "FAILED", "error": "Gateway timeout 504"}

    with patch.object(cascade_resolver.link_resolver, "resolve_auto", new_callable=AsyncMock, return_value=mock_resolve):
        result = await verify_single_candidate(candidate)
        assert result is None


@pytest.mark.asyncio
async def test_single_candidate_verification_invalid_container():
    """Verify single candidate returns None when stream verification fails (e.g. HTML error page)."""
    candidate = {
        "target_url": "https://gateway.com/watch",
        "title": "HTML Fake Stream"
    }

    mock_resolve = {"status": "SUCCESS", "direct_url": "https://fake.site/error.html"}
    mock_verify = {"valid": False, "error": "HTML page detected instead of video"}

    with patch.object(cascade_resolver.link_resolver, "resolve_auto", new_callable=AsyncMock, return_value=mock_resolve), \
         patch.object(cascade_resolver.stream_verifier, "verify", new_callable=AsyncMock, return_value=mock_verify):

        result = await verify_single_candidate(candidate)
        assert result is None


@pytest.mark.asyncio
async def test_single_candidate_empty_and_invalid_inputs():
    """Verify verify_single_candidate safely handles empty candidate or missing URL."""
    assert await verify_single_candidate({}) is None
    assert await verify_single_candidate({"target_url": ""}) is None
    assert await verify_single_candidate(None) is None


@pytest.mark.asyncio
async def test_parallel_cascade_selection_fast_beats_slow():
    """
    Test parallel cascade selection: fast responder (100ms) beats slow responder (500ms).
    Fast responder must be chosen as primary, while slow responder lands in waterfall.
    """
    cand_slow = {
        "title": "Release Slow 4K",
        "target_url": "https://slow.host/stream.mkv",
        "resolution": "2160p",
        "size": "20.0 GB"
    }
    cand_fast = {
        "title": "Release Fast 1080p",
        "target_url": "https://fast.host/stream.mp4",
        "resolution": "1080p",
        "size": "4.0 GB"
    }

    async def mock_verify_single(candidate, client=None, timeout=2.5):
        url = candidate.get("target_url")
        if "slow.host" in url:
            await asyncio.sleep(0.500)
            return {
                **candidate,
                "direct_url": "https://slow.cdn/stream.mkv",
                "ttfb_ms": 500.0,
                "verified": True,
                "format": "MKV (EBML)",
                "size": "20.0 GB"
            }
        elif "fast.host" in url:
            await asyncio.sleep(0.100)
            return {
                **candidate,
                "direct_url": "https://fast.cdn/stream.mp4",
                "ttfb_ms": 100.0,
                "verified": True,
                "format": "MP4",
                "size": "4.0 GB"
            }
        return None

    with patch("cascade_resolver.verify_single_candidate", side_effect=mock_verify_single):
        # Stagger delay of 0.05s ensures cand_slow starts at t=0, cand_fast starts at t=0.05s.
        # cand_fast completes at ~t=0.15s, beating cand_slow which completes at ~t=0.50s.
        res = await resolve_stream_cascade([cand_slow, cand_fast], max_workers=5, stagger_delay=0.05, timeout=4.0)

        assert res["status"] == "SUCCESS"
        assert res["primary"] is not None
        assert res["primary"]["direct_url"] == "https://fast.cdn/stream.mp4"
        assert res["primary"]["title"] == "Release Fast 1080p"

        # Slow responder lands in waterfall
        assert len(res["waterfall"]) == 1
        assert res["waterfall"][0]["direct_url"] == "https://slow.cdn/stream.mkv"
        assert res["candidates_tested"] == 2


@pytest.mark.asyncio
async def test_fallback_candidate1_fails_candidate2_succeeds():
    """
    Test fallback when candidate 1 fails and candidate 2 succeeds.
    Cascade must select candidate 2 as primary.
    """
    cand_fail = {
        "title": "Dead Link 4K",
        "target_url": "https://dead.com/bad",
        "resolution": "2160p",
        "size": "25.0 GB"
    }
    cand_ok = {
        "title": "Healthy Link 1080p",
        "target_url": "https://healthy.com/good",
        "resolution": "1080p",
        "size": "2.5 GB"
    }

    async def mock_verify_single(candidate, client=None, timeout=2.5):
        if "dead.com" in candidate.get("target_url", ""):
            return None
        return {
            **candidate,
            "direct_url": "https://healthy.cdn/stream.mkv",
            "ttfb_ms": 120.0,
            "verified": True,
            "format": "MKV (EBML)",
            "size": "2.5 GB"
        }

    with patch("cascade_resolver.verify_single_candidate", side_effect=mock_verify_single):
        res = await resolve_stream_cascade([cand_fail, cand_ok], max_workers=5, stagger_delay=0.05, timeout=2.0)

        assert res["status"] == "SUCCESS"
        assert res["primary"] is not None
        assert res["primary"]["direct_url"] == "https://healthy.cdn/stream.mkv"
        assert res["primary"]["title"] == "Healthy Link 1080p"
        assert res["waterfall"] == []
        assert res["candidates_tested"] == 2


@pytest.mark.asyncio
async def test_cascade_all_fail_returns_no_streams():
    """Verify resolve_stream_cascade returns NO_STREAMS when all candidates fail."""
    candidates = [
        {"target_url": "https://dead1.com", "title": "Dead 1"},
        {"target_url": "https://dead2.com", "title": "Dead 2"}
    ]

    with patch("cascade_resolver.verify_single_candidate", new_callable=AsyncMock, return_value=None):
        res = await resolve_stream_cascade(candidates, max_workers=2, stagger_delay=0.01)

        assert res["status"] == "NO_STREAMS"
        assert res["primary"] is None
        assert res["waterfall"] == []
        assert res["candidates_tested"] == 2


@pytest.mark.asyncio
async def test_cascade_empty_candidates_returns_no_streams():
    """Verify resolve_stream_cascade handles empty or non-list candidates gracefully."""
    res_empty = await resolve_stream_cascade([])
    assert res_empty["status"] == "NO_STREAMS"
    assert res_empty["primary"] is None
    assert res_empty["candidates_tested"] == 0

    res_none = await resolve_stream_cascade(None)
    assert res_none["status"] == "NO_STREAMS"
    assert res_none["primary"] is None
    assert res_none["candidates_tested"] == 0


@pytest.mark.asyncio
async def test_cascade_waterfall_sorted_by_ttfb():
    """Verify that multiple waterfall responders are strictly sorted ascending by TTFB."""
    cands = [
        {"target_url": f"https://cdn.example.com/{i}", "title": f"Candidate {i}"}
        for i in range(4)
    ]

    # Map responses: Candidate 0 is primary, 1/2/3 are waterfall with different TTFBs
    ttfbs = {
        "https://cdn.example.com/0": 80.0,
        "https://cdn.example.com/1": 350.0,
        "https://cdn.example.com/2": 150.0,
        "https://cdn.example.com/3": 220.0
    }

    async def mock_verify(candidate, client=None, timeout=2.5):
        url = candidate["target_url"]
        ttfb = ttfbs[url]
        await asyncio.sleep(ttfb / 1000.0)
        return {
            **candidate,
            "direct_url": url,
            "ttfb_ms": ttfb,
            "verified": True,
            "format": "MKV",
            "size": "2GB"
        }

    with patch("cascade_resolver.verify_single_candidate", side_effect=mock_verify):
        res = await resolve_stream_cascade(cands, max_workers=4, stagger_delay=0.0, timeout=2.0)

        assert res["status"] == "SUCCESS"
        assert res["primary"]["direct_url"] == "https://cdn.example.com/0"
        assert len(res["waterfall"]) == 3

        # Waterfall must be sorted by TTFB: 150ms -> 220ms -> 350ms
        waterfall_ttfbs = [item["ttfb_ms"] for item in res["waterfall"]]
        assert waterfall_ttfbs == [150.0, 220.0, 350.0]


@pytest.mark.asyncio
async def test_cascade_search_and_resolve_integration():
    """Verify cascade_search_and_resolve calls engine.search() and cascades top candidates."""
    mock_releases = [
        {"title": "Dune 2021 2160p", "target_url": "https://gateway.com/dune1", "resolution": "2160p"},
        {"title": "Dune 2021 1080p", "target_url": "https://gateway.com/dune2", "resolution": "1080p"}
    ]

    mock_engine = MagicMock()
    mock_engine.search = AsyncMock(return_value={"status": "CATALOG_FOUND", "releases": mock_releases})

    mock_resolved = {
        "title": "Dune 2021 2160p",
        "direct_url": "https://cdn.stream/dune.mkv",
        "ttfb_ms": 110.0,
        "verified": True,
        "format": "MKV (EBML)",
        "size": "18 GB"
    }

    with patch("cascade_resolver.verify_single_candidate", new_callable=AsyncMock, return_value=mock_resolved):
        res = await cascade_search_and_resolve("Dune", media_type="movie", engine=mock_engine)

        assert res["status"] == "SUCCESS"
        assert res["primary"] is not None
        assert res["primary"]["direct_url"] == "https://cdn.stream/dune.mkv"
        mock_engine.search.assert_awaited_once_with("Dune", season=None, episode=None)
