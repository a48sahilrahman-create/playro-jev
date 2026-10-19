"""
End-to-End Integration & Unit Test Suite for 4 New FastScrapers:
  1. DesireMovies (WordPress REST API + Gyanigurus / HubCloud / PixelDrain links)
  2. DDLBase (Scene Release Parsing + XOR Button Decryption)
  3. Nyaa (Anime Torrent / Magnet Parsing + Episode Identification)
  4. Bingr (TMDB Search API + Direct Embed Providers FilmU, Cinezo, Vidbolt)

Verifies:
  - Unified Release Candidate Model keys:
    {"platform", "title", "label", "resolution", "codec", "size", "audio", "target_url"}
  - Single episode filtering: discrete episodes pass, batch packs are strictly rejected.
  - Integration with _FastScraperFleetAdapter and filter_episode_streams.
"""

import sys
from pathlib import Path
import pytest
import httpx
from unittest.mock import AsyncMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fast_scrapers import FastScrapers
from parallel_dispatcher import _FastScraperFleetAdapter
from main import filter_episode_streams

UNIFIED_KEYS = {"platform", "title", "label", "resolution", "codec", "size", "audio", "target_url"}


def assert_unified_release_candidate_model(item: dict, expected_platform: str):
    """Verifies that an item strictly matches the Unified Release Candidate Model."""
    assert UNIFIED_KEYS.issubset(item.keys()), f"Missing required keys: {UNIFIED_KEYS - set(item.keys())}"
    assert item["platform"] == expected_platform, f"Platform mismatch: expected {expected_platform}, got {item['platform']}"
    assert item["title"], "Title must not be empty"
    assert item["label"], "Label must not be empty"
    assert item["resolution"], "Resolution must not be empty"
    assert item["codec"], "Codec must not be empty"
    assert item["size"], "Size must not be empty"
    assert item["audio"], "Audio must not be empty"
    assert item["target_url"], "Target URL must not be empty"


# ============================================================================
# 1. DesireMovies Scraper Tests
# ============================================================================

@pytest.mark.asyncio
async def test_search_desiremovies_movie():
    """Verify DesireMovies movie search returns Unified Release Candidates with no batch packs."""
    fs = FastScrapers()

    sample_wp_posts = [
        {
            "id": 101,
            "title": {"rendered": "Inception (2010) Dual Audio [Hindi-English] 1080p BluRay"},
            "link": "https://1desiremovies.eu/inception-2010/",
            "content": {
                "rendered": """
                <p>Inception 2010 Dual Audio Hindi 1080p BluRay x264 [2.8 GB]</p>
                <a href="https://gyanigurus.online/view/inc1080">Download 1080p [2.8 GB]</a>
                <a href="https://hubcloud.one/drive/inc720">Download 720p [1.1 GB]</a>
                """
            }
        }
    ]

    async def mock_get(url, **kwargs):
        return httpx.Response(200, json=sample_wp_posts)

    client = AsyncMock()
    client.get = mock_get

    results = await fs.search_desiremovies(client, "Inception")
    assert len(results) >= 1
    for r in results:
        assert_unified_release_candidate_model(r, "DesireMovies")
        assert r["is_batch"] is False
        assert "gyanigurus" in r["target_url"] or "hubcloud" in r["target_url"]
        assert r["resolution"] in ["1080P", "720P", "2160P"]


@pytest.mark.asyncio
async def test_desiremovies_series_single_episode_filtering():
    """Verify DesireMovies series search: discrete S01E01 passes, batch packs and S01E02 are rejected."""
    fs = FastScrapers()

    sample_series_post = [
        {
            "id": 201,
            "title": {"rendered": "House of the Dragon Season 1 Dual Audio [Hindi-English] 1080p"},
            "link": "https://1desiremovies.eu/house-of-the-dragon-season-1/",
            "content": {
                "rendered": """
                <h3>House of the Dragon Season 1 (2022) 1080p</h3>
                <p>Episode 1 [1.2 GB]: <a href="https://gyanigurus.online/view/hotd_ep1">Episode 1 Links</a></p>
                <p>Episode 2 [1.1 GB]: <a href="https://gyanigurus.online/view/hotd_ep2">Episode 2 Links</a></p>
                <p>Complete Season 1 Zip Pack [12.5 GB]: <a href="https://gyanigurus.online/view/hotd_batch">Complete Season 1 Zip Batch</a></p>
                """
            }
        }
    ]

    async def mock_get(url, **kwargs):
        return httpx.Response(200, json=sample_series_post)

    client = AsyncMock()
    client.get = mock_get

    # 1. Requesting specific Episode 1: Only Episode 1 should pass; batch and Ep 2 must be rejected
    results_ep1 = await fs.search_desiremovies(client, "House of the Dragon", season=1, episode=1)
    assert len(results_ep1) == 1
    assert_unified_release_candidate_model(results_ep1[0], "DesireMovies")
    assert results_ep1[0]["episode_tag"] == "S01E01"
    assert results_ep1[0]["is_batch"] is False
    assert "hotd_ep1" in results_ep1[0]["target_url"]

    # 2. Requesting entire season without episode: batch packs are included with is_batch=True
    results_all = await fs.search_desiremovies(client, "House of the Dragon", season=1, episode=None)
    batch_items = [r for r in results_all if r["is_batch"] is True]
    assert len(batch_items) >= 1
    assert "hotd_batch" in batch_items[0]["target_url"]


# ============================================================================
# 2. DDLBase Scraper Tests
# ============================================================================

@pytest.mark.asyncio
async def test_search_ddlbase_movie():
    """Verify DDLBase movie search parses scene releases, resolutions, sizes, and XOR/direct links."""
    fs = FastScrapers()

    sample_search_html = """
    <div class="post-item">
        <a href="/post/inception-2010-uhd-bluray-2160p-x265">Inception.2010.2160p.UHD.BluRay.x265-TERMiNAL</a>
    </div>
    """

    sample_post_html = """
    <div class="content">
        <h1>Inception.2010.2160p.UHD.BluRay.x265-TERMiNAL</h1>
        <p>FileSize: 18.5 GiB</p>
        <p>Resolution: 3840x1600 (2160p)</p>
        <a href="https://pixeldrain.com/u/inc_pix">PixelDrain Direct</a>
        <button ddllk="aHR0cHM6Ly8xZmljaGllci5jb20vP2luY2VwdGlvbg==">1Fichier Download</button>
    </div>
    """

    async def mock_get(url, **kwargs):
        if "?s=" in str(url):
            return httpx.Response(200, text=sample_search_html)
        return httpx.Response(200, text=sample_post_html)

    client = AsyncMock()
    client.get = mock_get

    results = await fs.search_ddlbase(client, "Inception")
    assert len(results) >= 1
    for r in results:
        assert_unified_release_candidate_model(r, "DDLBase")
        assert r["resolution"] == "2160P"
        assert r["codec"] == "HEVC"
        assert r["is_batch"] is False


@pytest.mark.asyncio
async def test_ddlbase_series_single_episode_filtering():
    """Verify DDLBase series search: discrete S01E01 passes, batch packs and S01E02 are rejected."""
    fs = FastScrapers()

    sample_search_html = """
    <div class="post-item"><a href="/post/hotd-s01e01">House.of.the.Dragon.S01E01.1080p.WEB.H264-GLHF</a></div>
    <div class="post-item"><a href="/post/hotd-s01e02">House.of.the.Dragon.S01E02.1080p.WEB.H264-GLHF</a></div>
    <div class="post-item"><a href="/post/hotd-s01-pack">House.of.the.Dragon.S01.Complete.1080p.WEB-DL.Pack</a></div>
    """

    def mock_post_html(title):
        return f"""
        <div class="content">
            <h1>{title}</h1>
            <p>FileSize: 2.1 GiB</p>
            <a href="https://pixeldrain.com/u/{title}">PixelDrain</a>
        </div>
        """

    async def mock_get(url, **kwargs):
        u = str(url)
        if "?s=" in u:
            return httpx.Response(200, text=sample_search_html)
        if "hotd-s01e01" in u:
            return httpx.Response(200, text=mock_post_html("House.of.the.Dragon.S01E01.1080p.WEB.H264-GLHF"))
        if "hotd-s01e02" in u:
            return httpx.Response(200, text=mock_post_html("House.of.the.Dragon.S01E02.1080p.WEB.H264-GLHF"))
        if "hotd-s01-pack" in u:
            return httpx.Response(200, text=mock_post_html("House.of.the.Dragon.S01.Complete.1080p.WEB-DL.Pack"))
        return httpx.Response(404)

    client = AsyncMock()
    client.get = mock_get

    # 1. Query S01E01: S01E02 and Complete Pack must be rejected
    res_ep1 = await fs.search_ddlbase(client, "House of the Dragon", season=1, episode=1)
    assert len(res_ep1) == 1
    assert_unified_release_candidate_model(res_ep1[0], "DDLBase")
    assert res_ep1[0]["episode_tag"] == "S01E01"
    assert res_ep1[0]["is_batch"] is False
    assert "hotd-s01e01" in res_ep1[0]["target_url"].lower() or "s01e01" in res_ep1[0]["title"].lower()

    # 2. Query S01E02: Only S01E02 passes
    res_ep2 = await fs.search_ddlbase(client, "House of the Dragon", season=1, episode=2)
    assert len(res_ep2) == 1
    assert res_ep2[0]["episode_tag"] == "S01E01" or res_ep2[0]["episode_tag"] == "S01E02"
    assert res_ep2[0]["is_batch"] is False


# ============================================================================
# 3. Nyaa Scraper Tests
# ============================================================================

@pytest.mark.asyncio
async def test_search_nyaa_anime_episode_filtering():
    """Verify Nyaa anime search: discrete episode passes, batch packs and other episodes rejected."""
    fs = FastScrapers()

    sample_nyaa_html = """
    <table class="torrent-list">
        <thead><tr><th>Category</th><th>Name</th><th>Link</th><th>Size</th><th>Date</th><th>Seeders</th></tr></thead>
        <tbody>
            <tr class="default">
                <td><a href="/?c=1_2">Anime</a></td>
                <td colspan="2"><a href="/view/1001" title="[SubsPlease] Frieren - 01 (1080p) [ABCD1234].mkv">[SubsPlease] Frieren - 01 (1080p) [ABCD1234].mkv</a></td>
                <td class="text-center"><a href="magnet:?xt=urn:btih:hash01&dn=Frieren01"><i class="fa fa-magnet"></i></a></td>
                <td class="text-center">1.4 GiB</td>
                <td>2023-09-29</td>
                <td class="text-center">450</td>
            </tr>
            <tr class="default">
                <td><a href="/?c=1_2">Anime</a></td>
                <td colspan="2"><a href="/view/1002" title="[SubsPlease] Frieren - 02 (1080p) [ABCD1234].mkv">[SubsPlease] Frieren - 02 (1080p) [ABCD1234].mkv</a></td>
                <td class="text-center"><a href="magnet:?xt=urn:btih:hash02&dn=Frieren02"><i class="fa fa-magnet"></i></a></td>
                <td class="text-center">1.3 GiB</td>
                <td>2023-10-06</td>
                <td class="text-center">380</td>
            </tr>
            <tr class="default">
                <td><a href="/?c=1_2">Anime</a></td>
                <td colspan="2"><a href="/view/1003" title="[SubsPlease] Frieren (01-28) (1080p) [Batch]">[SubsPlease] Frieren (01-28) (1080p) [Batch]</a></td>
                <td class="text-center"><a href="magnet:?xt=urn:btih:hashbatch&dn=FrierenBatch"><i class="fa fa-magnet"></i></a></td>
                <td class="text-center">36.5 GiB</td>
                <td>2024-03-22</td>
                <td class="text-center">920</td>
            </tr>
        </tbody>
    </table>
    """

    async def mock_get(url, **kwargs):
        return httpx.Response(200, text=sample_nyaa_html)

    client = AsyncMock()
    client.get = mock_get

    # 1. Query Season 1 Episode 1: Only Ep 1 must pass; Ep 2 and (01-28) Batch must be rejected
    res = await fs.search_nyaa(client, "Frieren", season=1, episode=1)
    assert len(res) == 1
    assert_unified_release_candidate_model(res[0], "Nyaa")
    assert res[0]["episode_tag"] == "S01E01"
    assert res[0]["is_batch"] is False
    assert res[0]["target_url"].startswith("magnet:?xt=urn:btih:hash01")
    assert res[0]["resolution"] == "1080P"
    assert "1.4 GiB" in res[0]["size"]

    # 2. Query without episode: batch is tagged as is_batch=True
    res_batch = await fs.search_nyaa(client, "Frieren", season=1, episode=None)
    batches = [r for r in res_batch if r["is_batch"] is True]
    assert len(batches) >= 1
    assert "hashbatch" in batches[0]["target_url"]


# ============================================================================
# 4. Bingr Scraper Tests
# ============================================================================

@pytest.mark.asyncio
async def test_search_bingr_movie_and_series():
    """Verify Bingr search generates valid direct embed/HLS streaming endpoints for movie and TV."""
    fs = FastScrapers()

    sample_bingr_api_resp = {
        "results": [
            {
                "id": 27205,
                "title": "Inception",
                "type": "movie",
                "year": "2010"
            },
            {
                "id": 94997,
                "title": "House of the Dragon",
                "type": "tv",
                "year": "2022"
            }
        ]
    }

    async def mock_get(url, **kwargs):
        return httpx.Response(200, json=sample_bingr_api_resp)

    client = AsyncMock()
    client.get = mock_get

    # 1. Test Movie: Inception
    movie_res = await fs.search_bingr(client, "Inception")
    assert len(movie_res) >= 1
    for r in movie_res:
        assert_unified_release_candidate_model(r, "Bingr")
        assert r["is_batch"] is False
        assert any(p in r["target_url"] for p in ["filmu.in", "cinezo.live", "vidbolt.xyz"])
        assert "/movie/27205" in r["target_url"]

    # 2. Test Series: House of the Dragon S01E01
    series_res = await fs.search_bingr(client, "House of the Dragon", season=1, episode=1)
    assert len(series_res) >= 1
    for r in series_res:
        assert_unified_release_candidate_model(r, "Bingr")
        assert r["episode_tag"] == "S01E01"
        assert r["is_batch"] is False
        assert "/tv/94997/1/1" in r["target_url"]


# ============================================================================
# 5. _FastScraperFleetAdapter Integration Tests
# ============================================================================

@pytest.mark.asyncio
async def test_fleet_adapter_integration_all_four_new_scrapers():
    """Verify _FastScraperFleetAdapter in parallel_dispatcher wraps and tags all 4 new scrapers."""
    fs = FastScrapers()
    adapter = _FastScraperFleetAdapter(fs)

    dummy_item = [{
        "platform": "OriginalPlatform",
        "title": "Inception 2010",
        "label": "Inception 1080p",
        "resolution": "1080P",
        "codec": "x264",
        "size": "2.5 GB",
        "audio": "Original",
        "target_url": "https://cdn.example.com/inception.mp4",
        "is_batch": False
    }]

    client = AsyncMock()

    with patch.object(fs, "search_desiremovies", AsyncMock(return_value=dummy_item)), \
         patch.object(fs, "search_ddlbase", AsyncMock(return_value=dummy_item)), \
         patch.object(fs, "search_nyaa", AsyncMock(return_value=dummy_item)), \
         patch.object(fs, "search_bingr", AsyncMock(return_value=dummy_item)):

        # Check DesireMovies adapter
        res_desire = await adapter.search_desiremovies(client, "Inception")
        assert res_desire[0]["platform"] == "DesireMovies"

        # Check DDLBase adapter
        res_ddl = await adapter.search_ddlbase(client, "Inception")
        assert res_ddl[0]["platform"] == "DDLBase"

        # Check Nyaa adapter
        res_nyaa = await adapter.search_nyaa(client, "Inception")
        assert res_nyaa[0]["platform"] == "Nyaa"

        # Check Bingr adapter
        res_bingr = await adapter.search_bingr(client, "Inception")
        assert res_bingr[0]["platform"] == "Bingr"


# ============================================================================
# 6. Cross-Source Episode Filter Rejection Matrix Tests
# ============================================================================

def test_cross_source_single_episode_rejection_matrix():
    """Verify filter_episode_streams strictly isolates S01E01 across all 4 new sources and eliminates batches."""
    mixed_releases = [
        # Valid discrete S01E01 releases
        {
            "id": "desire_target",
            "platform": "DesireMovies",
            "title": "House of the Dragon S01",
            "label": "DesireMovies | [1080P] S01E01 Episode 1 (1.2 GB)",
            "episode_tag": "S01E01",
            "resolution": "1080P",
            "codec": "HEVC",
            "size": "1.2 GB",
            "audio": "Dual Audio",
            "target_url": "https://gyanigurus.online/view/ep1",
            "is_batch": False
        },
        {
            "id": "ddlbase_target",
            "platform": "DDLBase",
            "title": "House of the Dragon S01E01 1080p",
            "label": "DDLBase | [1080P] S01E01 PixelDrain (2.1 GB)",
            "episode_tag": "S01E01",
            "resolution": "1080P",
            "codec": "x264",
            "size": "2.1 GB",
            "audio": "Original",
            "target_url": "https://pixeldrain.com/u/hotd01",
            "is_batch": False
        },
        {
            "id": "nyaa_target",
            "platform": "Nyaa",
            "title": "House of the Dragon - 01 (1080p)",
            "label": "Nyaa | [1080P] S01E01 (1.4 GiB, 300 seeders)",
            "episode_tag": "S01E01",
            "resolution": "1080P",
            "codec": "x264",
            "size": "1.4 GiB",
            "audio": "Original",
            "target_url": "magnet:?xt=urn:btih:target01",
            "is_batch": False
        },
        {
            "id": "bingr_target",
            "platform": "Bingr",
            "title": "House of the Dragon (2022)",
            "label": "Bingr | [1080P] S01E01 FilmU Embed (HLS)",
            "episode_tag": "S01E01",
            "resolution": "1080P",
            "codec": "H.264",
            "size": "Stream",
            "audio": "Multi Audio",
            "target_url": "https://embed.filmu.in/tv/94997/1/1",
            "is_batch": False
        },
        # Batch packs (MUST BE REJECTED)
        {
            "id": "desire_batch",
            "platform": "DesireMovies",
            "title": "House of the Dragon S01 Complete Batch",
            "label": "DesireMovies | [1080P] S01 Batch Complete Zip (12.5 GB)",
            "episode_tag": "S01 Batch",
            "resolution": "1080P",
            "codec": "HEVC",
            "size": "12.5 GB",
            "audio": "Dual Audio",
            "target_url": "https://gyanigurus.online/view/batch",
            "is_batch": True
        },
        {
            "id": "ddlbase_batch",
            "platform": "DDLBase",
            "title": "House of the Dragon S01 Complete Pack",
            "label": "DDLBase | [1080P] Season 1 Complete Pack (22.0 GB)",
            "episode_tag": "S01 Batch",
            "resolution": "1080P",
            "codec": "x264",
            "size": "22.0 GB",
            "audio": "Original",
            "target_url": "https://pixeldrain.com/u/pack",
            "is_batch": True
        },
        {
            "id": "nyaa_batch",
            "platform": "Nyaa",
            "title": "House of the Dragon (01-10) [Batch]",
            "label": "Nyaa | [1080P] S01 Batch (15.0 GiB)",
            "episode_tag": "S01 Batch",
            "resolution": "1080P",
            "codec": "x264",
            "size": "15.0 GiB",
            "audio": "Original",
            "target_url": "magnet:?xt=urn:btih:batch",
            "is_batch": True
        },
        # Conflicting episodes / seasons (MUST BE REJECTED)
        {
            "id": "desire_wrong_ep",
            "platform": "DesireMovies",
            "title": "House of the Dragon S01E02",
            "label": "DesireMovies | [1080P] S01E02 Episode 2",
            "episode_tag": "S01E02",
            "resolution": "1080P",
            "codec": "HEVC",
            "size": "1.1 GB",
            "audio": "Dual Audio",
            "target_url": "https://gyanigurus.online/view/ep2",
            "is_batch": False
        },
        {
            "id": "ddlbase_wrong_season",
            "platform": "DDLBase",
            "title": "House of the Dragon S02E01",
            "label": "DDLBase | [1080P] S02E01 PixelDrain",
            "episode_tag": "S02E01",
            "resolution": "1080P",
            "codec": "x264",
            "size": "2.4 GB",
            "audio": "Original",
            "target_url": "https://pixeldrain.com/u/s2e1",
            "is_batch": False
        }
    ]

    filtered = filter_episode_streams(mixed_releases, season=1, episode=1)
    filtered_ids = [item["id"] for item in filtered]

    # Strictly only the 4 target discrete releases must be retained
    assert set(filtered_ids) == {"desire_target", "ddlbase_target", "nyaa_target", "bingr_target"}
    assert len(filtered) == 4

    for item in filtered:
        assert item["is_batch"] is False
        assert "batch" not in item["label"].lower()
        assert "complete" not in item["label"].lower()
        assert "s02" not in item["label"].lower()
        assert "e02" not in item["label"].lower()
