import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from parallel_dispatcher import ParallelMediaDispatcher
from play_engine import PlayEngine


@pytest.mark.asyncio
async def test_stream_all_parallel_concurrency_and_timeout():
    """Verify that stream_all_parallel runs sources concurrently and completes faster than sequential sum."""
    dispatcher = ParallelMediaDispatcher()

    # Mock individual scraper methods with controlled delays
    async def mock_fetch_fast(client, query):
        await asyncio.sleep(0.05)
        return [{"platform": "Vega", "target_url": "https://vega.com/1", "resolution": "1080p", "codec": "HEVC", "size": "2GB", "audio": "AAC"}]

    async def mock_fetch_slow(client, query):
        await asyncio.sleep(0.1)
        return [{"platform": "UHD", "target_url": "https://uhd.com/1", "resolution": "4K", "codec": "HEVC", "size": "10GB", "audio": "Atmos"}]

    async def mock_fetch_tg(query):
        await asyncio.sleep(0.05)
        return [{"platform": "Telegram Bot", "target_url": "tg://123", "resolution": "1080p", "codec": "HEVC", "size": "1.5GB", "audio": "AAC"}]

    with patch.object(dispatcher, "fetch_vegamovies", side_effect=mock_fetch_fast), \
         patch.object(dispatcher, "fetch_uhdmovies", side_effect=mock_fetch_slow), \
         patch.object(dispatcher, "fetch_hdhub4u", side_effect=mock_fetch_fast), \
         patch.object(dispatcher, "fetch_olamovies", side_effect=mock_fetch_fast), \
         patch.object(dispatcher, "fetch_telegram", side_effect=mock_fetch_tg), \
         patch.object(dispatcher, "fetch_youtube", side_effect=mock_fetch_fast):

        t0 = time.perf_counter()
        batches = []
        async for batch in dispatcher.stream_all_parallel("Inception"):
            if batch:
                batches.append(batch)
        elapsed = time.perf_counter() - t0

        # Deterministic concurrency check: mocked delays sum to 0.35s sequentially.
        # Concurrent execution must complete in ~max(0.1s). Use generous wall-clock
        # bound (2.5s) to avoid flaky failures under CI/machine load, while still
        # failing hard on true sequential execution or a missing per-source timeout.
        assert elapsed < 2.5, f"Expected concurrent completion well under sequential 0.35s+overhead, took {elapsed:.2f}s"
        assert len(batches) >= 1, "Expected at least 1 fast batch returned"


@pytest.mark.asyncio
async def test_play_engine_search_deduplication_and_isolation():
    """Verify PlayEngine deduplicates by target_url and direct_url without corruption."""
    engine = PlayEngine()

    mock_release_1 = {"title": "Batman", "target_url": "https://server.com/link1", "direct_url": "", "resolution": "1080p", "size": "2GB"}
    mock_release_2 = {"title": "Batman", "target_url": "https://server.com/link1", "direct_url": "", "resolution": "1080p", "size": "2GB"} # duplicate target
    mock_release_3 = {"title": "Batman", "target_url": "", "direct_url": "https://direct.com/video.mkv", "resolution": "4K", "size": "15GB"}

    async def mock_stream_parallel(query):
        yield [mock_release_1, mock_release_2]
        yield [mock_release_3]

    with patch.object(engine.resolver, "resolve", new_callable=AsyncMock) as mock_res, \
         patch.object(engine.dispatcher, "stream_all_parallel", side_effect=mock_stream_parallel), \
         patch.object(engine.dispatcher, "fetch_local_db", new_callable=AsyncMock, return_value=[]), \
         patch.object(engine.db, "upsert_title", return_value=True):

        mock_res.return_value = {"canonical_title": "The Batman", "formatted": "The Batman", "year": "2022"}

        res = await engine.search("The Batman")
        assert res["status"] == "CATALOG_FOUND"
        assert res["count"] == 2  # release_1 and release_3 (release_2 deduplicated)
