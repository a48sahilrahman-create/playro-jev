import sys
from pathlib import Path
import pytest
import httpx
from unittest.mock import AsyncMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parallel_dispatcher import is_strict_match, ParallelMediaDispatcher
from main import filter_episode_streams
from link_resolver import resolve_gateway_url


def test_strict_match_avoids_stopword_false_positives():
    query = "The Boys"
    # Target valid release
    assert is_strict_match(query, "The Boys Season 1 [Dual Audio] 1080p") is True

    # False positives that share the stopword or keyword "Boys"
    assert is_strict_match(query, "My Life with the Walter Boys") is False
    assert is_strict_match(query, "Bad Boys") is False
    assert is_strict_match(query, "Lost Boys") is False


def test_filter_episode_streams_zero_leak_policy():
    releases = [
        # Batch and full season packs (must be eliminated)
        {
            "title": "The Boys S01 Complete",
            "label": "Season 1 Complete Batch [Zip]",
            "episode_tag": "Complete Batch",
            "target_url": "https://gateway/batch.zip"
        },
        {
            "title": "The Boys Season 1 Full Season",
            "label": "The Boys S01 All Episodes Pack",
            "is_batch": True,
            "target_url": "https://gateway/season_pack"
        },
        # Wrong season episodes (must be eliminated)
        {
            "title": "The Boys S02E01",
            "label": "The Boys S02E01 1080p",
            "episode_tag": "S02E01",
            "target_url": "https://gateway/s2e1"
        },
        # Wrong episode in same season (must be eliminated)
        {
            "title": "The Boys S01E02",
            "label": "The Boys S01E02 1080p",
            "episode_tag": "S01E02",
            "target_url": "https://gateway/s1e2"
        },
        # Exact target episode matches (must be preserved)
        {
            "title": "The Boys",
            "label": "The Boys S01E01 1080p",
            "episode_tag": "S01E01",
            "target_url": "https://gateway/s1e1_1080p",
            "id": "target_1"
        },
        {
            "title": "The Boys Season 1",
            "label": "The Boys Season 1 Episode 1 720p",
            "episode_tag": "",
            "target_url": "https://gateway/s1e1_720p",
            "id": "target_2"
        }
    ]

    # Verify matching only returns the target season 1 episode 1 streams
    matched = filter_episode_streams(releases, season=1, episode=1)
    assert len(matched) == 2
    matched_ids = [r["id"] for r in matched]
    assert matched_ids == ["target_1", "target_2"]

    # Verify mixed batch packs and wrong season episodes are strictly eliminated
    for r in matched:
        assert r.get("is_batch") is not True
        assert "complete" not in (r.get("label", "") + r.get("title", "")).lower()
        assert "pack" not in (r.get("label", "") + r.get("title", "")).lower()
        assert "s02" not in (r.get("label", "") + r.get("episode_tag", "")).lower()

    # Zero-leak policy: When 0 episodes match, returns []
    zero_leak_releases = [
        releases[0],  # batch
        releases[1],  # is_batch
        releases[2],  # S02E01
        releases[3],  # S01E02
    ]
    no_matches = filter_episode_streams(zero_leak_releases, season=1, episode=1)
    assert no_matches == []

    # Non-existent episode returns []
    empty_result = filter_episode_streams(releases, season=1, episode=99)
    assert empty_result == []


@pytest.mark.asyncio
async def test_resolve_gateway_greenmotors():
    destination_hubcloud_url = "https://hubcloud.club/drive/testabc123"

    def greenmotors_mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "greenmotors.club" in url_str:
            return httpx.Response(
                302,
                headers={"Location": destination_hubcloud_url}
            )
        elif "hubcloud.club" in url_str:
            return httpx.Response(
                200,
                text="<html><body><a href='https://stream.direct/video.mkv'>Download</a></body></html>"
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(greenmotors_mock_handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        resolved = await resolve_gateway_url(
            "https://greenmotors.club/?id=bXlfaWQxMjM=",
            client=mock_client
        )
        assert resolved == destination_hubcloud_url


@pytest.mark.asyncio
async def test_dispatcher_multi_source_fleet():
    dispatcher = ParallelMediaDispatcher()

    # Mock the 5 underlying FastScrapers fleet adapter provider methods
    with patch("parallel_dispatcher._FastScraperFleetAdapter.search_uhdmovies", new_callable=AsyncMock) as m_uhd, \
         patch("parallel_dispatcher._FastScraperFleetAdapter.search_4khdhub", new_callable=AsyncMock) as m_4k, \
         patch("parallel_dispatcher._FastScraperFleetAdapter.search_hdhub4u", new_callable=AsyncMock) as m_hd, \
         patch("parallel_dispatcher._FastScraperFleetAdapter.search_vegamovies", new_callable=AsyncMock) as m_vega, \
         patch("parallel_dispatcher._FastScraperFleetAdapter.search_olamovies", new_callable=AsyncMock) as m_ola, \
         patch.object(dispatcher, "fetch_local_db", new_callable=AsyncMock, return_value=[]), \
         patch.object(dispatcher, "fetch_series_simultaneous", new_callable=AsyncMock, return_value={}), \
         patch.object(dispatcher, "fetch_telegram", new_callable=AsyncMock, return_value=[]), \
         patch.object(dispatcher, "fetch_youtube", new_callable=AsyncMock, return_value=[]):

        m_uhd.return_value = [{"title": "The Boys S01E01 UHD", "resolution": "2160P", "platform": "UHDMovies"}]
        m_4k.return_value = [{"title": "The Boys S01E01 4K", "resolution": "1080P", "platform": "4KHDHub"}]
        m_hd.return_value = [{"title": "The Boys S01E01 HD", "resolution": "1080P", "platform": "HDHub4u"}]
        m_vega.return_value = [{"title": "The Boys S01E01 Vega", "resolution": "720P", "platform": "VegaMovies"}]
        m_ola.return_value = [{"title": "The Boys S01E01 Ola", "resolution": "480P", "platform": "OlaMovies"}]

        batches = []
        async for batch in dispatcher.stream_all_parallel("The Boys", season=1, episode=1):
            if batch:
                batches.append(batch)

        # Verify all 5 providers were called with query and season=1, episode=1
        for name, mock_method in [
            ("UHDMovies", m_uhd),
            ("4KHDHub", m_4k),
            ("HDHub4u", m_hd),
            ("VegaMovies", m_vega),
            ("OlaMovies", m_ola),
        ]:
            assert mock_method.called, f"Provider {name} was not called!"
            args, kwargs = mock_method.call_args
            assert args[1] == "The Boys"
            assert kwargs.get("season") == 1
            assert kwargs.get("episode") == 1

        # Verify all 5 provider batches were received and tagged
        flattened = [item for batch in batches for item in batch]
        platforms = {item.get("platform") for item in flattened}
        expected_platforms = {"UHDMovies", "4KHDHub", "HDHub4u", "VegaMovies", "OlaMovies"}
        assert expected_platforms.issubset(platforms), f"Missing platforms: {expected_platforms - platforms}"
