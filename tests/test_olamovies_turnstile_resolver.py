import pytest
import asyncio
import base64
import time
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from auth_bridge import extract_b64_redirect_url, try_click_turnstile, InteractiveAuthBridge, BROWSER_PROFILE_DIR
from link_resolver import LinkResolver
from fast_scrapers import FastScrapers


class TestBase64RedirectExtraction:
    """Test suite for extracting downstream destinations from protected gateway pages."""

    def test_standard_base64_extraction(self):
        target = "https://drive.olamovies.mov/file/12345"
        b64 = base64.b64encode(target.encode()).decode()
        html = f'<html><body><input type="hidden" name="token" value="{b64}"></body></html>'
        extracted = extract_b64_redirect_url(html)
        assert extracted == target

    def test_urlsafe_and_unpadded_base64(self):
        target = "https://dl.olamdrive.com/dl?t=xyz_987-abc"
        b64 = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        html = f'<div class="wrapper"><input value="{b64}" id="res"/></div>'
        extracted = extract_b64_redirect_url(html)
        assert extracted == target

    def test_double_base64_extraction(self):
        target = "https://drive.olamovies.mov/d/abcdef123"
        b64_inner = base64.b64encode(target.encode()).decode()
        b64_outer = base64.b64encode(b64_inner.encode()).decode()
        html = f'<input name="data" value="{b64_outer}">'
        extracted = extract_b64_redirect_url(html)
        assert extracted == target

    def test_non_matching_and_empty_inputs(self):
        assert extract_b64_redirect_url("") is None
        assert extract_b64_redirect_url("<html>No input elements</html>") is None
        # Valid base64 but does not start with http or ola domains
        random_b64 = base64.b64encode(b"Some random text payload without links").decode()
        assert extract_b64_redirect_url(f'<input value="{random_b64}">') is None


class TestTurnstileAutoClicker:
    """Test suite for Cloudflare Turnstile automated click dispatching and debounce latch."""

    @pytest.mark.asyncio
    async def test_turnstile_click_success(self):
        page = MagicMock()
        frame = MagicMock()
        frame.url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile"

        locator = MagicMock()
        locator.count = AsyncMock(return_value=1)
        element = MagicMock()
        element.is_visible = AsyncMock(return_value=True)
        element.bounding_box = AsyncMock(return_value={"x": 100, "y": 200, "width": 50, "height": 30})
        locator.first = element
        frame.locator = MagicMock(return_value=locator)

        page.main_frame = frame
        page.frames = [frame]
        page.mouse.move = AsyncMock()
        page.mouse.down = AsyncMock()
        page.mouse.up = AsyncMock()

        last_click_ref = [0.0]
        clicked = await try_click_turnstile(page, last_click_ref)
        assert clicked is True
        assert last_click_ref[0] > 0
        page.mouse.move.assert_awaited_once()
        page.mouse.down.assert_awaited_once()
        page.mouse.up.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_turnstile_debounce_latch(self):
        page = MagicMock()
        now = time.time()
        last_click_ref = [now]  # Clicked just now (<3.0s ago)

        clicked = await try_click_turnstile(page, last_click_ref)
        assert clicked is False


class TestOlaMoviesMultiStrategySearch:
    """Test suite for FastScrapers OlaMovies multi-strategy search and episode isolation."""

    @pytest.mark.asyncio
    async def test_strategy1_json_api_search(self):
        scrapers = FastScrapers()
        client = AsyncMock()

        api_response_data = {
            "items": [
                {
                    "id": "1",
                    "title": "The Boys - Season 1",
                    "slug": "the-boys-season-1",
                    "url": "/tv-series/the-boys-season-1",
                    "category": "English TV Series"
                }
            ]
        }

        # Mock API 200 response
        mock_api_resp = MagicMock()
        mock_api_resp.status_code = 200
        mock_api_resp.json = MagicMock(return_value=api_response_data)

        # Mock post HTML response with single episode S01E01 and a batch pack
        mock_post_html = (
            '{"id":"itm_1","btnId":"b1","url":"https://links.ol-am.top/101","label":"Episode 1","filename":"The.Boys.S01E01.1080p.mkv"}'
            '{"id":"itm_2","btnId":"b2","url":"https://links.ol-am.top/102","label":"Episode 2","filename":"The.Boys.S01E02.1080p.mkv"}'
            '{"id":"itm_batch","btnId":"b3","url":"https://links.ol-am.top/103","label":"Full Season Batch Pack","filename":"The.Boys.S01.Batch.zip"}'
        )
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.text = mock_post_html

        client.get = AsyncMock(side_effect=[mock_api_resp, mock_post_resp])

        # Request strictly Season 1 Episode 1
        results = await scrapers.search_olamovies(client, "The Boys", season=1, episode=1)

        assert len(results) == 1
        ep = results[0]
        assert ep["platform"] == "OlaMovies"
        assert ep["episode_tag"] == "S01E01"
        assert ep["target_url"] == "https://links.ol-am.top/101"
        assert ep["is_batch"] is False

    @pytest.mark.asyncio
    async def test_strategy2_ssr_fallback_when_api_fails(self):
        scrapers = FastScrapers()
        client = AsyncMock()

        # Mock API failure (status 500)
        mock_api_resp = MagicMock()
        mock_api_resp.status_code = 500

        # Mock SSR HTML search response
        ssr_search_html = (
            '<html><body>'
            '<a href="/tv-series/the-boys-2019">The Boys Season 1 (2019)</a>'
            '</body></html>'
        )
        mock_ssr_resp = MagicMock()
        mock_ssr_resp.status_code = 200
        mock_ssr_resp.text = ssr_search_html

        # Mock post HTML
        post_html = (
            '{"id":"itm_1","btnId":"b1","url":"https://links.ol-am.top/501","label":"S01E01","filename":"The.Boys.S01E01.mkv"}'
        )
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.text = post_html

        client.get = AsyncMock(side_effect=[mock_api_resp, mock_ssr_resp, mock_post_resp])

        results = await scrapers.search_olamovies(client, "The Boys", season=1, episode=1)
        assert len(results) == 1
        assert results[0]["episode_tag"] == "S01E01"
        assert results[0]["target_url"] == "https://links.ol-am.top/501"

    @pytest.mark.asyncio
    async def test_structured_sections_multi_season_isolation(self):
        scrapers = FastScrapers()
        client = AsyncMock()

        api_response_data = {
            "items": [
                {
                    "id": "100",
                    "title": "The Boys (2019)",
                    "slug": "the-boys-2019",
                    "url": "/tv-series/the-boys-2019",
                    "category": "English TV Series"
                }
            ]
        }
        mock_api_resp = MagicMock()
        mock_api_resp.status_code = 200
        mock_api_resp.json = MagicMock(return_value=api_response_data)

        # Mock structured Next.js sections with Season 1 and Season 5
        sections_data = [
            {
                "id": "season-1",
                "label": "Season 1",
                "season": "1",
                "sources": [
                    {
                        "label": "BluRay",
                        "blocks": [
                            {
                                "resolution": "720p",
                                "group": "HETeam",
                                "heading": "The.Boys.S01.720p.BluRay.zip",
                                "packs": [{"url": "https://links.ol-am.top/s1_pack", "label": "720p zip [5GB]"}],
                                "episodes": [
                                    {"episode": 1, "url": "https://links.ol-am.top/s1_e1", "filesize": "650MB"},
                                    {"episode": 2, "url": "https://links.ol-am.top/s1_e2", "filesize": "640MB"}
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "id": "season-5",
                "label": "Season 5",
                "season": "5",
                "sources": [
                    {
                        "label": "AMZN WEB-DL",
                        "blocks": [
                            {
                                "resolution": "2160p",
                                "group": "FLUX",
                                "heading": "The.Boys.S05.2160p.FLUX.zip",
                                "packs": [{"url": "https://links.ol-am.top/s5_pack", "label": "4K zip [82GB]"}],
                                "episodes": [
                                    {"episode": 1, "url": "https://links.ol-am.top/s5_e1", "filesize": "9.96GB"},
                                    {"episode": 2, "url": "https://links.ol-am.top/s5_e2", "filesize": "9.77GB"}
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

        import json
        escaped_json = json.dumps(sections_data).replace('"', '\\"')
        post_html = f'<html><body><script>self.__next_f.push([1, "1:[\"$\",\"div\",null,{{\"sections\":{escaped_json}}}]"])</script></body></html>'

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.text = post_html

        # Test A: Request Season 5 Episode 1
        client.get = AsyncMock(side_effect=[mock_api_resp, mock_post_resp])
        res_s5e1 = await scrapers.search_olamovies(client, "The Boys", season=5, episode=1)
        assert len(res_s5e1) == 1
        assert res_s5e1[0]["episode_tag"] == "S05E01"
        assert res_s5e1[0]["target_url"] == "https://links.ol-am.top/s5_e1"
        assert "9.96GB" in res_s5e1[0]["label"]

        # Test B: Request Season 1 Episode 1
        client.get = AsyncMock(side_effect=[mock_api_resp, mock_post_resp])
        res_s1e1 = await scrapers.search_olamovies(client, "The Boys", season=1, episode=1)
        assert len(res_s1e1) == 1
        assert res_s1e1[0]["episode_tag"] == "S01E01"
        assert res_s1e1[0]["target_url"] == "https://links.ol-am.top/s1_e1"

        # Test C: Request Season 1 Batch
        client.get = AsyncMock(side_effect=[mock_api_resp, mock_post_resp])
        res_s1_batch = await scrapers.search_olamovies(client, "The Boys", season=1, episode=None)
        assert any(item["is_batch"] and item["target_url"] == "https://links.ol-am.top/s1_pack" for item in res_s1_batch)

    @pytest.mark.asyncio
    async def test_structured_sections_movie_files(self):
        scrapers = FastScrapers()
        client = AsyncMock()

        api_response_data = {
            "items": [
                {
                    "id": "200",
                    "title": "Deadpool (2016)",
                    "slug": "deadpool-2016",
                    "url": "/movies/deadpool-2016",
                    "category": "Hollywood Movies"
                }
            ]
        }
        mock_api_resp = MagicMock()
        mock_api_resp.status_code = 200
        mock_api_resp.json = MagicMock(return_value=api_response_data)

        movie_sections = [
            {
                "id": "sec_movie",
                "label": "Movie",
                "blocks": [
                    {
                        "resolution": "720p",
                        "group": "PSA",
                        "files": [
                            {"url": "https://links.ol-am.top/dp_720", "label": "720p [1.24gb]", "filename": "Deadpool.720p.mkv"}
                        ]
                    },
                    {
                        "resolution": "2160p",
                        "group": "FGT",
                        "files": [
                            {"url": "https://links.ol-am.top/dp_4k", "label": "2160p [44.9gb]", "filename": "Deadpool.2160p.mkv"}
                        ]
                    }
                ]
            }
        ]

        import json
        escaped_json = json.dumps(movie_sections).replace('"', '\\"')
        post_html = f'<html><body><script>self.__next_f.push([1, "1:[\"$\",\"div\",null,{{\"sections\":{escaped_json}}}]"])</script></body></html>'

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.text = post_html

        client.get = AsyncMock(side_effect=[mock_api_resp, mock_post_resp])
        results = await scrapers.search_olamovies(client, "Deadpool")

        assert len(results) == 2
        urls = [r["target_url"] for r in results]
        assert "https://links.ol-am.top/dp_720" in urls
        assert "https://links.ol-am.top/dp_4k" in urls


class TestOlaMoviesResolverAndBridge:
    """Test suite for LinkResolver OlaMovies handling and InteractiveAuthBridge."""

    @pytest.mark.asyncio
    async def test_cached_stream_fast_path(self):
        resolver = LinkResolver()
        client = AsyncMock()
        url = "https://links.olamovies.mov/5674"
        direct_stream = "https://dl.olamdrive.com/dl?t=token_12345"

        resolver.auth_bridge._resolved_streams[url] = direct_stream
        res = await resolver.resolve_olamovies(client, url, allow_interactive=False)

        assert res["status"] == "SUCCESS"
        assert res["direct_url"] == direct_stream
        assert "OMDrive" in res["host"] or "OlaMovies" in res["host"]

    @pytest.mark.asyncio
    async def test_direct_cdn_url_fast_path(self):
        resolver = LinkResolver()
        client = AsyncMock()
        direct_url = "https://dl.olamdrive.com/dl?t=abc12345"

        res = await resolver.resolve_olamovies(client, direct_url)
        assert res["status"] == "SUCCESS"
        assert res["direct_url"] == direct_url

    def test_browser_profile_dir_configured(self):
        assert BROWSER_PROFILE_DIR.name == ".browser_profile"
        assert str(BROWSER_PROFILE_DIR).endswith(".browser_profile")
