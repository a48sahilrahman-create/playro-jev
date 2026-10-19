"""
Automated Test Suite: Nyaa Anime Search Precision & Platform Normalization
Covers:
  1. normalizePlatform: Platform tagging normalization ('4KHDHub' vs 'HDHub4u' collision avoidance).
  2. Query Construction: Targeted episode query generation ('01', '001') for season/episode isolation.
  3. Live Integration (Black Clover S01E01): Valid discrete episode releases, S01E01 tags, magnet links.
  4. Live Integration (Demon Slayer / Solo Leveling S01E01): Discrete release verification.
  5. E2E Fleet Integration: PlayEngine.search returns releases from BOTH Nyaa and 4KHDHub.
"""

import sys
import re
import asyncio
import urllib.parse
from pathlib import Path
import pytest
import httpx
from unittest.mock import AsyncMock

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import time
from fast_scrapers import FastScrapers
from play_engine import PlayEngine
from domain_resolver import default_resolver


@pytest.fixture(autouse=True)
def ensure_mirrors_healthy():
    """Ensure dynamic domain resolver health states are clean and healthy prior to test execution."""
    default_resolver._health["nyaa"] = {
        "active_mirror": "https://nyaa.media",
        "latency_ms": 0.0,
        "last_success": time.time(),
        "consecutive_failures": 0,
        "status": "healthy"
    }
    default_resolver._health["4khdhub"] = {
        "active_mirror": "https://4khdhub.one",
        "latency_ms": 0.0,
        "last_success": time.time(),
        "consecutive_failures": 0,
        "status": "healthy"
    }
    default_resolver._save_health_sync()
    yield


# ============================================================================
# 1. normalizePlatform Tests (Simulation & Static Regex Analysis)
# ============================================================================

def normalize_platform_simulated(p: str) -> str:
    """Python simulation of normalizePlatform from static/index.html."""
    if not p:
        return 'Cloud'
    s = str(p).strip()
    if re.search(r'telegram', s, re.I):
        return 'Telegram'
    if re.search(r'4khdhub', s, re.I):
        return '4KHDHub'
    if re.search(r'hdhub', s, re.I):
        return 'HDHub4u'
    if re.search(r'uhdmovies', s, re.I):
        return 'UHDMovies'
    if re.search(r'vegamovies', s, re.I):
        return 'VegaMovies'
    if re.search(r'olamovies', s, re.I):
        return 'OlaMovies'
    if re.search(r'youtube', s, re.I):
        return 'YouTube'
    if re.search(r'desiremovies', s, re.I):
        return 'DesireMovies'
    if re.search(r'ddlbase', s, re.I):
        return 'DDLBase'
    if re.search(r'nyaa', s, re.I):
        return 'Nyaa'
    if re.search(r'bingr', s, re.I):
        return 'Bingr'
    return s


def test_normalize_platform_4khdhub_vs_hdhub4u_simulation():
    """Verify normalizePlatform differentiates 4KHDHub and HDHub4u without regex collision."""
    # Strict platform names
    assert normalize_platform_simulated("4KHDHub") == "4KHDHub"
    assert normalize_platform_simulated("HDHub4u") == "HDHub4u"

    # Case insensitivity & variations
    assert normalize_platform_simulated("4khdhub") == "4KHDHub"
    assert normalize_platform_simulated("4KHDHUB") == "4KHDHub"
    assert normalize_platform_simulated("hdhub") == "HDHub4u"
    assert normalize_platform_simulated("HDHub") == "HDHub4u"

    # Other fleet providers
    assert normalize_platform_simulated("Nyaa") == "Nyaa"
    assert normalize_platform_simulated("UHDMovies") == "UHDMovies"
    assert normalize_platform_simulated("VegaMovies") == "VegaMovies"
    assert normalize_platform_simulated("") == "Cloud"


def test_normalize_platform_html_regex_order():
    """Verify static/index.html defines /4khdhub/i before /hdhub/i to prevent substring masking."""
    index_html_path = PROJECT_ROOT / "static" / "index.html"
    assert index_html_path.exists(), f"static/index.html not found at {index_html_path}"

    content = index_html_path.read_text(encoding="utf-8")
    assert "function normalizePlatform" in content

    # Find indices of 4khdhub and hdhub regex patterns inside normalizePlatform
    pos_4k = content.find("/4khdhub/i")
    pos_hd = content.find("/hdhub/i")

    assert pos_4k != -1, "/4khdhub/i regex not found in static/index.html"
    assert pos_hd != -1, "/hdhub/i regex not found in static/index.html"
    assert pos_4k < pos_hd, f"/4khdhub/i (pos {pos_4k}) must appear before /hdhub/i (pos {pos_hd}) to prevent collision"


# ============================================================================
# 2. Query Construction Tests
# ============================================================================

def test_search_nyaa_query_construction_season_episode():
    """Verify search_nyaa builds targeted queries containing '01' and '001' for season=1, episode=1."""
    fs = FastScrapers()

    # 1. Method-level query builder check
    queries = fs.build_nyaa_queries("Black Clover", season=1, episode=1)
    assert isinstance(queries, list)
    assert len(queries) >= 2

    # Verify targeted discrete episode queries '01' and '001' are generated
    has_01 = any("01" in q for q in queries)
    has_001 = any("001" in q for q in queries)
    assert has_01, f"Expected query containing '01', got: {queries}"
    assert has_001, f"Expected query containing '001', got: {queries}"

    # Also test higher episode numbers (e.g., episode 7 -> '07' and '007')
    queries_ep7 = fs.build_nyaa_queries("Demon Slayer", season=1, episode=7)
    assert any("07" in q for q in queries_ep7)
    assert any("007" in q for q in queries_ep7)


@pytest.mark.asyncio
async def test_search_nyaa_requests_issued_with_targeted_queries():
    """Verify search_nyaa dispatches client requests with the constructed targeted queries."""
    fs = FastScrapers()
    requested_urls = []

    async def mock_get(url, **kwargs):
        requested_urls.append(str(url))
        return httpx.Response(200, text="<html><body><table class='torrent-list'></table></body></html>")

    mock_client = AsyncMock()
    mock_client.get = mock_get

    await fs.search_nyaa(mock_client, "Black Clover", season=1, episode=1)

    decoded_urls = [urllib.parse.unquote(u) for u in requested_urls]
    assert any("01" in u for u in decoded_urls), f"No URL containing '01' found in {decoded_urls}"
    assert any("001" in u for u in decoded_urls), f"No URL containing '001' found in {decoded_urls}"


# ============================================================================
# 3. Live Integration Test: Black Clover S01E01
# ============================================================================

@pytest.mark.asyncio
async def test_search_nyaa_black_clover_s01e01_live():
    """Live test of search_nyaa for Black Clover S01E01: verifies >= 2 discrete episodes, S01E01, magnets."""
    fs = FastScrapers()
    async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
        results = await fs.search_nyaa(client, "Black Clover", season=1, episode=1)

    assert len(results) >= 2, f"Expected at least 2 discrete releases for Black Clover S01E01, got {len(results)}"

    for r in results:
        assert r["platform"] == "Nyaa"
        assert r["episode_tag"] == "S01E01", f"Expected episode_tag 'S01E01', got '{r.get('episode_tag')}'"
        assert r["is_batch"] is False, f"Expected is_batch=False for discrete episode, got True for '{r.get('title')}'"
        assert r["target_url"].startswith("magnet:?"), f"Expected magnet link, got '{r.get('target_url')}'"
        assert "Black Clover" in r["title"]
        assert r["resolution"] in ["2160P", "1080P", "720P", "480P"]


# ============================================================================
# 4. Live Integration Test: Demon Slayer or Solo Leveling S01E01
# ============================================================================

@pytest.mark.asyncio
async def test_search_nyaa_demon_slayer_or_solo_leveling_live():
    """Live test of search_nyaa for Demon Slayer or Solo Leveling S01E01: verifies discrete releases."""
    fs = FastScrapers()
    async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
        # Probe Demon Slayer first, then Solo Leveling
        results = await fs.search_nyaa(client, "Demon Slayer", season=1, episode=1)
        if not results:
            results = await fs.search_nyaa(client, "Solo Leveling", season=1, episode=1)

    assert len(results) >= 1, f"Expected at least 1 discrete release for Demon Slayer or Solo Leveling S01E01, got {len(results)}"

    for r in results:
        assert r["platform"] == "Nyaa"
        assert r["episode_tag"] == "S01E01", f"Expected S01E01, got {r.get('episode_tag')}"
        assert r["is_batch"] is False, f"Expected is_batch=False, got {r.get('is_batch')}"
        assert r["target_url"].startswith("magnet:?"), f"Expected magnet link, got {r.get('target_url')}"


# ============================================================================
# 5. End-to-End Integration: PlayEngine Black Clover S01E01 Dual-Platform
# ============================================================================

@pytest.mark.asyncio
async def test_play_engine_e2e_black_clover_dual_platform():
    """End-to-end integration: PlayEngine.search returns releases from BOTH Nyaa and 4KHDHub."""
    engine = PlayEngine()
    res = await engine.search("Black Clover", season=1, episode=1)

    assert res.get("status") == "CATALOG_FOUND"
    releases = res.get("releases", [])
    assert len(releases) > 0, "PlayEngine returned no releases for Black Clover S01E01"

    nyaa_releases = [r for r in releases if r.get("platform") == "Nyaa"]
    h4k_releases = [r for r in releases if r.get("platform") == "4KHDHub"]

    # If transient network delay occurred during concurrent fleet run, retry once
    if not nyaa_releases:
        await asyncio.sleep(1.0)
        res = await engine.search("Black Clover", season=1, episode=1)
        releases = res.get("releases", [])
        nyaa_releases = [r for r in releases if r.get("platform") == "Nyaa"]
        h4k_releases = [r for r in releases if r.get("platform") == "4KHDHub"]

    assert len(nyaa_releases) >= 1, f"Expected at least 1 release from Nyaa, got {len(nyaa_releases)}"
    assert len(h4k_releases) >= 1, f"Expected at least 1 release from 4KHDHub, got {len(h4k_releases)}"

    # Check discrete episode tagging and attributes
    for nr in nyaa_releases:
        assert nr.get("episode_tag") == "S01E01"
        assert nr.get("is_batch") is False
        assert nr.get("target_url", "").startswith("magnet:?")

    for hr in h4k_releases:
        assert hr.get("episode_tag") == "S01E01"
        assert hr.get("is_batch") is False
