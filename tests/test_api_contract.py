import pytest
import base64
import json
import urllib.parse
import httpx
from unittest.mock import AsyncMock, patch

from main import app, decode_user_config, encode_user_config


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_route_determinism_and_ordering():
    """Verify static vs dynamic route resolution (/health, /, /manifest.json)."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. /health route
        res_health = await client.get("/health")
        assert res_health.status_code == 200
        assert res_health.json()["status"] == "healthy"
        assert res_health.json()["service"] == "play_engine_stremio_addon"

        # 2. Root UI route /
        res_root = await client.get("/")
        assert res_root.status_code == 200
        assert "PLAYRO" in res_root.text or "Play Engine" in res_root.text

        # 3. /configure UI route
        res_cfg = await client.get("/configure")
        assert res_cfg.status_code == 200
        assert "Play Engine" in res_cfg.text

        # 4. Default manifest
        res_manifest = await client.get("/manifest.json")
        assert res_manifest.status_code == 200
        assert res_manifest.json()["id"] == "org.playengine.cloudstreamer"
        assert "stream" in res_manifest.json()["resources"]

        # 5. Configured manifest
        custom_cfg = {"tg_token": "123:ABC", "public": True}
        b64_cfg = encode_user_config(custom_cfg)
        res_custom_manifest = await client.get(f"/{b64_cfg}/manifest.json")
        assert res_custom_manifest.status_code == 200
        assert "Custom Private Bot" in res_custom_manifest.json()["name"]


@pytest.mark.anyio
async def test_user_config_decoding_edge_cases():
    """Verify decode_user_config handles corruption, malformed padding, and non-dict JSON gracefully."""
    # Empty / default
    assert decode_user_config("") == {}
    assert decode_user_config("default") == {}
    assert decode_user_config(None) == {}

    # Valid config with unpadded base64
    cfg = {"ola": "cookie123", "tg_token": "token123", "public": False}
    b64_str = encode_user_config(cfg)
    assert decode_user_config(b64_str) == cfg

    # Malformed base64
    assert decode_user_config("invalid_base64!@#$%^&*()") == {}

    # Valid base64 but invalid JSON
    invalid_json_b64 = base64.urlsafe_b64encode(b"not a json").decode("utf-8").rstrip("=")
    assert decode_user_config(invalid_json_b64) == {}

    # Valid JSON but non-dict payload (e.g. list, bool, number, string)
    for non_dict in ["[1, 2, 3]", "true", "12345", '"just a string"']:
        b64_val = base64.urlsafe_b64encode(non_dict.encode("utf-8")).decode("utf-8").rstrip("=")
        assert decode_user_config(b64_val) == {}


@pytest.mark.anyio
async def test_stream_id_parsing_and_contract():
    """Verify stream_id parsing edge cases: series 'tt...:1:1', 'play:' prefixes, non-tt prefixes, .json suffix."""
    mock_releases = [
        {
            "title": "The Dark Knight (2008)",
            "resolution": "4k",
            "size": "15GB",
            "codec": "HEVC",
            "audio": "DDP 5.1",
            "platform": "Vega",
            "direct_url": "https://direct.stream/video.mkv"
        }
    ]

    with patch("main.play_engine.search", new_callable=AsyncMock) as mock_search, \
         patch("main.resolve_imdb_to_title", new_callable=AsyncMock) as mock_imdb:

        mock_search.return_value = {"releases": mock_releases}
        mock_imdb.return_value = {"name": "The Dark Knight", "year": "2008", "type": "movie"}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # 1. Standard movie stream
            res = await client.get("/stream/movie/tt0468569.json")
            assert res.status_code == 200
            data = res.json()
            assert "streams" in data
            assert len(data["streams"]) == 1
            assert data["streams"][0]["name"] == "⚡ Play [4K]"
            assert mock_imdb.call_args[0][0] == "tt0468569"
            mock_search.assert_awaited()

            # 2. Series stream with season and episode (tt0468569:1:1.json)
            mock_imdb.reset_mock()
            mock_search.reset_mock()
            mock_imdb.return_value = {"name": "Breaking Bad", "year": "2008", "type": "series"}

            res_series = await client.get("/stream/series/tt0903747:1:1.json")
            assert res_series.status_code == 200
            assert mock_imdb.call_args[0][0] == "tt0903747"
            # Should search with base title or formatted S01E01 query
            assert mock_search.call_args[0][0] in ("Breaking Bad", "Breaking Bad S01E01")

            # 3. Stream route with user_config prefix
            custom_cfg = {"public": True}
            b64_cfg = encode_user_config(custom_cfg)
            res_user = await client.get(f"/{b64_cfg}/stream/movie/tt0468569.json")
            assert res_user.status_code == 200
            assert b64_cfg in res_user.json()["streams"][0]["url"]

            # 4. Stream without .json suffix (if route supports or strips it)
            res_no_json = await client.get("/stream/movie/tt0468569")
            assert res_no_json.status_code in (200, 404)

            # 5. Play prefix 'play:tt0468569:1:1.json' or 'play:Inception.json'
            mock_imdb.reset_mock()
            mock_search.reset_mock()
            res_play = await client.get("/stream/movie/play:tt0468569.json")
            assert res_play.status_code == 200
            assert mock_imdb.call_args[0][0] == "tt0468569"

            # 6. Non-tt prefix (e.g. tmdb:12345 or direct custom query)
            mock_imdb.reset_mock()
            mock_search.reset_mock()
            res_custom = await client.get("/stream/movie/play:Interstellar.json")
            assert res_custom.status_code == 200
            assert mock_search.call_args[0][0] == "Interstellar"


@pytest.mark.anyio
async def test_playback_resolve_endpoint_contract():
    """Verify /playback/resolve and /{user_config}/playback/resolve 302 redirects and query param unquoting."""
    with patch("main.link_resolver.resolve_auto", new_callable=AsyncMock) as mock_resolve:
        mock_resolve.return_value = {"status": "SUCCESS", "direct_url": "https://fast.cdn/final.mkv"}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            # 1. Direct container stream bypasses resolver
            direct_url = "https://cdn.example.com/video.mp4"
            res_direct = await client.get(f"/playback/resolve?url={urllib.parse.quote(direct_url)}")
            assert res_direct.status_code == 302
            assert res_direct.headers["location"] == direct_url
            mock_resolve.assert_not_called()

            # 2. Non-direct gateway URL resolves via link_resolver
            gateway_url = "https://hubcloud.club/drive/xyz123"
            res_gateway = await client.get(f"/playback/resolve?url={urllib.parse.quote(gateway_url)}")
            assert res_gateway.status_code == 302
            assert res_gateway.headers["location"] == "https://fast.cdn/final.mkv"
            mock_resolve.assert_awaited_with(gateway_url)

            # 3. /user_config/playback/resolve
            res_user_res = await client.get(f"/my_custom_cfg/playback/resolve?url={urllib.parse.quote(direct_url)}")
            assert res_user_res.status_code == 302
            assert res_user_res.headers["location"] == direct_url

            # 4. Missing url param returns 400 or 422
            res_missing = await client.get("/playback/resolve?url=")
            assert res_missing.status_code == 400


@pytest.mark.anyio
async def test_cors_headers_spec_compliance():
    """Verify CORS response headers comply with W3C / Fetch specification (no wildcard + credentials=True)."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/manifest.json", headers={"Origin": "https://web.stremio.com"})
        assert res.status_code == 200
        # If allow_origins is "*", Access-Control-Allow-Credentials must NOT be true per CORS spec
        allow_cred = res.headers.get("access-control-allow-credentials")
        allow_origin = res.headers.get("access-control-allow-origin")
        if allow_origin == "*":
            assert allow_cred != "true", "CORS violation: Access-Control-Allow-Origin: * with Allow-Credentials: true is invalid"
