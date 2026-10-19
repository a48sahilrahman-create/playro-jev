"""
Playro JEV — Comprehensive Stremio Addon Test Suite
---------------------------------------------------
Tests manifest normalization, manifest parsing and validation, AddonManager CRUD
and persistence, zero-guessing search integrity, metadata deduplication and merging,
FastAPI addon REST API endpoints, API catalog search zero-guessing, and PlayEngine
search parameter handling.
"""

import os
import sys
import json
import pytest
import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure D:\playro_jev is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import httpx
from main import app
from addon_manager import (
    normalize_manifest_url,
    parse_and_validate_manifest,
    AddonManager,
    DEFAULT_ADDONS
)
from play_engine import PlayEngine


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ==============================================================================
# Requirement 1: test_normalize_manifest_url
# ==============================================================================
def test_normalize_manifest_url():
    """
    Test URL normalization requirements:
    - "stremio://v3-cinemeta.strem.io/manifest.json" -> "https://v3-cinemeta.strem.io/manifest.json"
    - "https://v3-cinemeta.strem.io" -> "https://v3-cinemeta.strem.io/manifest.json"
    - "  https://v3-cinemeta.strem.io/manifest.json/  " -> "https://v3-cinemeta.strem.io/manifest.json"
    """
    assert (
        normalize_manifest_url("stremio://v3-cinemeta.strem.io/manifest.json")
        == "https://v3-cinemeta.strem.io/manifest.json"
    )
    assert (
        normalize_manifest_url("https://v3-cinemeta.strem.io")
        == "https://v3-cinemeta.strem.io/manifest.json"
    )
    assert (
        normalize_manifest_url("  https://v3-cinemeta.strem.io/manifest.json/  ")
        == "https://v3-cinemeta.strem.io/manifest.json"
    )
    # Additional edge cases
    assert (
        normalize_manifest_url("stremio://v3-cinemeta.strem.io")
        == "https://v3-cinemeta.strem.io/manifest.json"
    )
    assert (
        normalize_manifest_url("stremio://v3-cinemeta.strem.io/")
        == "https://v3-cinemeta.strem.io/manifest.json"
    )
    assert normalize_manifest_url("") == ""
    assert normalize_manifest_url(None) == ""


# ==============================================================================
# Requirement 2: test_parse_and_validate_manifest
# ==============================================================================
def test_parse_and_validate_manifest():
    """
    Test manifest parsing and validation:
    - Valid manifest with catalogs and search capability.
    - Invalid manifest missing name or id.
    """
    valid_raw_manifest = {
        "id": "community.test.addon",
        "name": "Community Test Addon",
        "version": "2.1.0",
        "description": "Federated test catalog for movies and TV series",
        "logo": "https://test.addon/icon.png",
        "background": "https://test.addon/bg.jpg",
        "resources": ["catalog", "meta"],
        "types": ["movie", "series"],
        "catalogs": [
            {
                "id": "test_search_movies",
                "type": "movie",
                "name": "Search Movies",
                "extra": [{"name": "search", "isRequired": True}]
            },
            {
                "id": "test_browse_series",
                "type": "series",
                "name": "Browse Series",
                "extra": [{"name": "genre"}, {"name": "skip"}]
            }
        ]
    }

    parsed = parse_and_validate_manifest(
        valid_raw_manifest,
        "stremio://test.addon/c/manifest.json"
    )

    assert parsed["id"] == "community.test.addon"
    assert parsed["name"] == "Community Test Addon"
    assert parsed["version"] == "2.1.0"
    assert parsed["description"] == "Federated test catalog for movies and TV series"
    assert parsed["icon"] == "https://test.addon/icon.png"
    assert parsed["manifest_url"] == "https://test.addon/c/manifest.json"
    assert parsed["transport_url"] == "https://test.addon/c"
    assert parsed["has_catalog"] is True
    assert parsed["enabled"] is True
    assert parsed["is_default"] is False

    # Catalogs with search should be extracted into searchable catalogs
    search_cat_ids = [c["id"] for c in parsed["catalogs"]]
    assert "test_search_movies" in search_cat_ids
    assert "test_browse_series" not in search_cat_ids

    # Invalid manifest missing id
    with pytest.raises(ValueError, match="id"):
        parse_and_validate_manifest(
            {"name": "No ID Addon", "version": "1.0.0"},
            "https://test.addon/manifest.json"
        )

    # Invalid manifest missing name
    with pytest.raises(ValueError, match="name"):
        parse_and_validate_manifest(
            {"id": "no.name.addon", "version": "1.0.0"},
            "https://test.addon/manifest.json"
        )

    # Invalid manifest with empty string id
    with pytest.raises(ValueError):
        parse_and_validate_manifest(
            {"id": "   ", "name": "Valid Name"},
            "https://test.addon/manifest.json"
        )

    # Non-dictionary manifest
    with pytest.raises(ValueError):
        parse_and_validate_manifest("not a dict", "https://test.addon/manifest.json")


# ==============================================================================
# Requirement 3: test_addon_manager_defaults_and_crud
# ==============================================================================
@pytest.mark.anyio
async def test_addon_manager_defaults_and_crud(tmp_path):
    """
    Test AddonManager:
    - Use temporary config file (via tmp_path).
    - Verify defaults are auto-created (Cinemeta, TMDB, CyberFlix).
    - Test get_addons(), toggle_addon(), delete_addon().
    """
    config_file = str(tmp_path / "installed_addons.json")
    mgr = AddonManager(config_path=config_file)

    # 1. Defaults auto-created
    addons = mgr.get_addons()
    assert len(addons) == 3, f"Expected 3 default addons, got {len(addons)}"

    addon_ids = [a["id"] for a in addons]
    addon_names = [a["name"] for a in addons]

    assert "com.linvo.cinemeta" in addon_ids
    assert "tmdb-addon" in addon_ids
    assert "org.cyberflix.service" in addon_ids

    assert any("cinemeta" in name.lower() for name in addon_names)
    assert any("tmdb" in name.lower() or "movie database" in name.lower() for name in addon_names)
    assert any("cyberflix" in name.lower() for name in addon_names)

    # Verify get_addon
    cinemeta = mgr.get_addon("com.linvo.cinemeta")
    assert cinemeta is not None
    assert cinemeta["id"] == "com.linvo.cinemeta"
    assert cinemeta["enabled"] is True

    # 2. Test toggle_addon
    # Toggle off explicitly
    toggled_off = await mgr.toggle_addon("com.linvo.cinemeta", enabled=False)
    assert toggled_off["enabled"] is False
    assert mgr.get_addon("com.linvo.cinemeta")["enabled"] is False

    # Toggle with enabled=None (flip)
    toggled_back = await mgr.toggle_addon("com.linvo.cinemeta")
    assert toggled_back["enabled"] is True
    assert mgr.get_addon("com.linvo.cinemeta")["enabled"] is True

    # 3. Test delete_addon
    del_result = await mgr.delete_addon("org.cyberflix.service")
    assert del_result is True
    assert mgr.get_addon("org.cyberflix.service") is None
    assert len(mgr.get_addons()) == 2

    # Delete non-existent addon
    del_nonexistent = await mgr.delete_addon("non.existent.addon")
    assert del_nonexistent is False

    # 4. Verify persistence across instances
    mgr_persisted = AddonManager(config_path=config_file)
    persisted_addons = mgr_persisted.get_addons()
    assert len(persisted_addons) == 2
    assert "org.cyberflix.service" not in [a["id"] for a in persisted_addons]


# ==============================================================================
# Requirement 4: test_zero_guessing_search_integrity
# ==============================================================================
@pytest.mark.anyio
async def test_zero_guessing_search_integrity(tmp_path):
    """
    Test zero-guessing policy in search_catalogs:
    - Passes authentic query without alteration.
    - Verify query 'The End of oak tree' is never changed to 'The End of Us'.
    """
    config_file = str(tmp_path / "installed_addons.json")
    mgr = AddonManager(config_path=config_file)

    query = "The End of oak tree"
    requested_urls = []

    async def mock_get(url, *args, **kwargs):
        requested_urls.append(url)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"metas": []}
        return mock_resp

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=mock_get)

    results = await mgr.search_catalogs(query, media_type="movie", client=mock_client)

    assert len(requested_urls) > 0, "No catalog search requests were generated"

    quoted_query = urllib.parse.quote(query)
    for url in requested_urls:
        # Authentic query must be present in every request URL
        assert quoted_query in url, f"Expected {quoted_query} in {url}"
        # Altered query must never appear
        assert "The End of Us" not in url
        assert "The%20End%20of%20Us" not in url
        assert "the_end_of_us" not in url.lower()


# ==============================================================================
# Requirement 5: test_deduplicate_and_merge
# ==============================================================================
def test_deduplicate_and_merge(tmp_path):
    """
    Test metadata deduplication and merging:
    - Items with same ID are deduplicated.
    - Items with same title and year merge metadata (high-res poster, synopsis, combined sources).
    """
    config_file = str(tmp_path / "installed_addons.json")
    mgr = AddonManager(config_path=config_file)

    # 1. Deduplication by same ID
    item_cinemeta = {
        "id": "tt1375666",
        "title": "Inception",
        "year": "2010",
        "type": "movie",
        "category": "Sci-Fi",
        "rating": "8.0",
        "poster": "https://example.com/cinemeta_poster.jpg",
        "backdrop": "https://example.com/cinemeta_poster.jpg",
        "overview": "Short synopsis.",
        "badge": "4K",
        "source": "Cinemeta"
    }

    item_tmdb = {
        "id": "tt1375666",
        "title": "Inception",
        "year": "2010",
        "type": "movie",
        "category": "Action",
        "rating": "8.8",
        "poster": "https://images-na.ssl-images-amazon.com/inception_highres.jpg",
        "backdrop": "https://example.com/inception_backdrop.jpg",
        "overview": "A thief who steals corporate secrets through dream-sharing technology is given the inverse task of planting an idea into the mind of a C.E.O.",
        "badge": "4K",
        "source": "TMDB"
    }

    merged = mgr._deduplicate_and_merge([item_cinemeta, item_tmdb])

    assert len(merged) == 1, f"Expected 1 deduplicated item, got {len(merged)}"
    m = merged[0]
    assert m["id"] == "tt1375666"
    assert m["title"] == "Inception"
    assert m["year"] == "2010"
    # Combined sources
    assert "Cinemeta" in m["sources"]
    assert "TMDB" in m["sources"]
    assert "Cinemeta" in m["source"] and "TMDB" in m["source"]
    # High-res poster prioritized over lower quality
    assert m["poster"] == "https://images-na.ssl-images-amazon.com/inception_highres.jpg"
    # Distinct backdrop prioritized over poster fallback
    assert m["backdrop"] == "https://example.com/inception_backdrop.jpg"
    # Longer synopsis preserved
    assert len(m["overview"]) > len("Short synopsis.")
    # More accurate rating preserved
    assert m["rating"] == "8.8"

    # 2. Merge items with same title and year where one has non-IMDb ID and another has IMDb ID
    item_custom_id = {
        "id": "cyberflix:12345",
        "title": "Interstellar",
        "year": "2014",
        "type": "movie",
        "category": "Sci-Fi",
        "rating": "8.0",
        "poster": "https://example.com/cyberflix_poster.jpg",
        "backdrop": "https://example.com/cyberflix_poster.jpg",
        "overview": "Explorers travel through a wormhole in space.",
        "badge": "4K",
        "source": "CyberFlix"
    }

    item_imdb_id = {
        "id": "tt0816692",
        "title": "Interstellar",
        "year": "2014",
        "type": "movie",
        "category": "Sci-Fi",
        "rating": "8.7",
        "poster": "https://images-na.ssl-images-amazon.com/interstellar_highres.jpg",
        "backdrop": "https://example.com/interstellar_backdrop.jpg",
        "overview": "When Earth becomes uninhabitable in the future, a farmer and ex-NASA pilot, Joseph Cooper, is tasked to pilot a spacecraft, along with a team of researchers, to find a new planet for humans.",
        "badge": "4K",
        "source": "Cinemeta"
    }

    merged2 = mgr._deduplicate_and_merge([item_custom_id, item_imdb_id])
    assert len(merged2) == 1
    m2 = merged2[0]
    # Prioritizes IMDb tt... ID
    assert m2["id"] == "tt0816692"
    assert "CyberFlix" in m2["sources"]
    assert "Cinemeta" in m2["sources"]
    assert m2["backdrop"] == "https://example.com/interstellar_backdrop.jpg"
    assert len(m2["overview"]) > len("Explorers travel through a wormhole in space.")


# ==============================================================================
# Requirement 6: test_api_addons_endpoints
# ==============================================================================
@pytest.mark.anyio
async def test_api_addons_endpoints():
    """
    Test FastAPI endpoints using httpx ASGI client:
    - GET /api/addons
    - POST /api/addons/install
    - POST /api/addons/{id}/toggle
    - DELETE /api/addons/{id}
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        # 1. GET /api/addons
        res_get = await client.get("/api/addons")
        assert res_get.status_code == 200
        data_get = res_get.json()
        assert data_get["status"] == "success"
        assert isinstance(data_get["addons"], list)

        # 2. POST /api/addons/install
        with patch("main.addon_manager.install_addon", new_callable=AsyncMock) as mock_install:
            mock_install.return_value = {
                "id": "new.test.addon",
                "name": "New Test Addon",
                "manifest_url": "https://new.test/manifest.json",
                "enabled": True
            }
            # Valid install
            res_install = await client.post(
                "/api/addons/install",
                json={"url": "https://new.test/manifest.json"}
            )
            assert res_install.status_code == 200
            data_install = res_install.json()
            assert data_install["status"] == "success"
            assert data_install["addon"]["id"] == "new.test.addon"

            # Invalid install: missing url
            res_invalid = await client.post("/api/addons/install", json={})
            assert res_invalid.status_code == 400

        # 3. POST /api/addons/{id}/toggle
        with patch("main.addon_manager.toggle_addon", new_callable=AsyncMock) as mock_toggle:
            mock_toggle.return_value = {
                "id": "new.test.addon",
                "name": "New Test Addon",
                "enabled": False
            }
            # Successful toggle
            res_toggle = await client.post(
                "/api/addons/new.test.addon/toggle",
                json={"enabled": False}
            )
            assert res_toggle.status_code == 200
            data_toggle = res_toggle.json()
            assert data_toggle["status"] == "success"
            assert data_toggle["addon"]["enabled"] is False

            # Toggle non-existent addon returns 404
            mock_toggle.return_value = None
            res_toggle_404 = await client.post(
                "/api/addons/non_existent/toggle",
                json={"enabled": True}
            )
            assert res_toggle_404.status_code == 404

        # 4. DELETE /api/addons/{id}
        with patch("main.addon_manager.delete_addon", new_callable=AsyncMock) as mock_del:
            mock_del.return_value = True
            # Successful deletion
            res_del = await client.delete("/api/addons/new.test.addon")
            assert res_del.status_code == 200
            data_del = res_del.json()
            assert data_del["status"] == "success"
            assert data_del["addon_id"] == "new.test.addon"

            # Delete non-existent addon returns 404
            mock_del.return_value = False
            res_del_404 = await client.delete("/api/addons/non_existent")
            assert res_del_404.status_code == 404


# ==============================================================================
# Requirement 7: test_api_catalog_search_zero_guessing
# ==============================================================================
@pytest.mark.anyio
async def test_api_catalog_search_zero_guessing():
    """
    Test GET /api/catalog/search?q=The+End+of+oak+tree:
    - Verify query returned is 'The End of oak tree'.
    - Verify corrected_query is empty or matches query.
    - Verify authentic query was forwarded to addon_manager.search_catalogs.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        with patch("main.addon_manager.search_catalogs", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [
                {
                    "id": "tt12345678",
                    "title": "The End of oak tree",
                    "year": "2024",
                    "type": "movie",
                    "category": "Drama",
                    "rating": "8.1",
                    "poster": "https://example.com/poster.jpg",
                    "backdrop": "https://example.com/backdrop.jpg",
                    "overview": "A drama about an oak tree.",
                    "badge": "4K",
                    "source": "Cinemeta"
                }
            ]

            res = await client.get("/api/catalog/search?q=The+End+of+oak+tree")
            assert res.status_code == 200
            data = res.json()

            assert data["query"] == "The End of oak tree"
            assert data["corrected_query"] in ("", "The End of oak tree")
            assert len(data["results"]) == 1
            assert data["results"][0]["title"] == "The End of oak tree"

            # Verify authentic query was passed to addon_manager
            mock_search.assert_awaited_once()
            passed_query = mock_search.call_args[0][0]
            assert passed_query == "The End of oak tree"


# ==============================================================================
# Requirement 8: test_play_engine_search_parameters
# ==============================================================================
@pytest.mark.anyio
async def test_play_engine_search_parameters():
    """
    Test play_engine.search(query='Inception', year='2010', imdb_id='tt1375666')
    accepts authentic parameters without alteration and returns structured payload.
    """
    engine = PlayEngine()
    dispatched_queries = []

    async def mock_stream(sq):
        dispatched_queries.append(sq)
        if False:
            yield

    engine.dispatcher.stream_all_parallel = mock_stream

    result = await engine.search(query="Inception", year="2010", imdb_id="tt1375666")

    # Assert payload contracts
    assert result["query"] == "Inception"
    assert result["year"] == "2010"
    assert result["imdb_id"] == "tt1375666"
    assert result["canonical_title"] == "Inception"
    assert result["formatted_title"] == "Inception"

    # Assert dispatched queries include query, query + year, and imdb_id
    assert "Inception" in dispatched_queries
    assert "Inception 2010" in dispatched_queries
    assert "tt1375666" in dispatched_queries


# ==============================================================================
# Requirement 9: test_play_engine_search_with_season_and_episode
# ==============================================================================
@pytest.mark.anyio
async def test_play_engine_search_with_season_and_episode():
    """
    Verify play_engine.search passes season=season and episode=episode
    to dispatcher.stream_all_parallel.
    """
    engine = PlayEngine()
    dispatched_calls = []

    async def mock_stream(sq, season=None, episode=None):
        dispatched_calls.append({"sq": sq, "season": season, "episode": episode})
        if False:
            yield

    engine.dispatcher.stream_all_parallel = mock_stream

    result = await engine.search(query="The Boys", season=1, episode=2)

    assert result["season"] == 1
    assert result["episode"] == 2
    assert len(dispatched_calls) >= 1
    for call in dispatched_calls:
        assert call["season"] == 1
        assert call["episode"] == 2


# ==============================================================================
# Requirement 10: test_filter_episode_streams_zero_leak_policy
# ==============================================================================
def test_filter_episode_streams_zero_leak_policy():
    """
    Strictly verify Zero-Leak policy in filter_episode_streams:
    1. Returns exact episode matches.
    2. Explicitly rejects batch zips and whole season packs.
    3. Rejects conflicting season releases.
    4. If no matching episode streams found, strictly returns [] (never leaks batch packs).
    """
    from main import filter_episode_streams

    test_releases = [
        {"title": "The Boys S01E02 1080p", "label": "Episode 2 [1080P]", "episode_tag": "S01E02", "target_url": "https://ok.com/e2"},
        {"title": "The Boys S01 Complete Season 1 [Batch]", "label": "Batch Zip (Season 1)", "episode_tag": "", "target_url": "https://leak.com/batch"},
        {"title": "The Boys Season 1 Complete Pack", "label": "Full Season Pack", "episode_tag": "", "target_url": "https://leak.com/pack"},
        {"title": "The Boys S02E02 1080p", "label": "Episode 2 [1080P]", "episode_tag": "S02E02", "target_url": "https://wrong.com/s2e2"},
        {"title": "The Boys S01E03 1080p", "label": "Episode 3 [1080P]", "episode_tag": "S01E03", "target_url": "https://wrong.com/e3"},
    ]

    # Test exact match
    res = filter_episode_streams(test_releases, season=1, episode=2)
    assert len(res) == 1
    assert res[0]["target_url"] == "https://ok.com/e2"

    # Test Zero-Leak policy when no matching episode exists (e.g. S01E05)
    # Must return [] - never fall back to batch packs or all streams!
    no_match_res = filter_episode_streams(test_releases, season=1, episode=5)
    assert no_match_res == []

    # Test only batch packs present for the requested season
    batch_only_releases = [
        {"title": "The Boys S01 1080p [Batch]", "label": "Complete Series", "episode_tag": "", "target_url": "https://leak.com/b1"},
        {"title": "The Boys Season 1 Complete", "label": "Season 1 Zip", "episode_tag": "", "target_url": "https://leak.com/b2"}
    ]
    assert filter_episode_streams(batch_only_releases, season=1, episode=1) == []


# ==============================================================================
# Requirement 11: test_api_search_season_episode_forwarding
# ==============================================================================
@pytest.mark.anyio
async def test_api_search_season_episode_forwarding():
    """
    Verify GET /api/search forwards season and episode to play_engine.search.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        with patch("main.play_engine.search", new_callable=AsyncMock) as mock_search, \
             patch("main.fetch_tmdb_meta", new_callable=AsyncMock) as mock_tmdb:

            mock_search.return_value = {
                "status": "CATALOG_FOUND",
                "releases": [
                    {"title": "Severance S01E01 1080p", "label": "Episode 01 [1080P]", "episode_tag": "S01E01", "target_url": "https://ok.com/e1"}
                ]
            }
            mock_tmdb.return_value = {"poster": "https://img.com/p.jpg"}

            res = await client.get("/api/search?q=Severance&season=1&episode=1&type=series&refresh=true")
            assert res.status_code == 200
            mock_search.assert_awaited_once()
            _, kwargs = mock_search.call_args
            assert kwargs.get("season") == 1
            assert kwargs.get("episode") == 1


# ==============================================================================
# Requirement 12: test_stream_endpoint_season_episode_forwarding
# ==============================================================================
@pytest.mark.anyio
async def test_stream_endpoint_season_episode_forwarding():
    """
    Verify GET /stream/series/{stream_id} forwards season and episode to play_engine.search.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        with patch("main.resolve_imdb_to_title", new_callable=AsyncMock) as mock_resolve, \
             patch("main.play_engine.search", new_callable=AsyncMock) as mock_search:

            mock_resolve.return_value = {"name": "Loki", "year": "2021"}
            mock_search.return_value = {
                "status": "CATALOG_FOUND",
                "releases": [
                    {"title": "Loki S02E01 1080p", "label": "Episode 01 [1080P]", "episode_tag": "S02E01", "target_url": "https://ok.com/s2e1"}
                ]
            }

            res = await client.get("/stream/series/tt10168312:2:1.json")
            assert res.status_code == 200
            mock_search.assert_awaited()
            _, kwargs = mock_search.call_args
            assert kwargs.get("season") == 2
            assert kwargs.get("episode") == 1
