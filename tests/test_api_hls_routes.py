"""
API Route Tests for HLS Transmuxer, Cascade Resolver, and Embed Engine Endpoints.

Verifies:
1. GET /api/hls/master.m3u8 (Manifest generation, audio track, volume boost)
2. GET /api/hls/{session_id}/index.m3u8 (RFC 8216 VOD media playlist, 404 handling)
3. GET /api/hls/{session_id}/segment_{segment_idx}.ts (MPEG-TS segment slicing, headers)
4. GET /api/hls/{session_id}/status (Session health, duration, segment count telemetry)
5. GET /api/playback/cascade (Cascading parallel resolution, waterfall fallback, query validation)
6. GET /api/embed/sources (Multi-provider embed fallback: Filmu, Cinezo, Vidbolt, Vidy, Vidrift)
"""

import sys
import math
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main import app
from hls_transmuxer import HLS_SESSION_MANAGER, HLSSession
from embed_engine import EmbedEngine


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver"
    ) as c:
        yield c


# ============================================================================
# 1. HLS Master Manifest Tests (/api/hls/master.m3u8)
# ============================================================================

@pytest.mark.anyio
async def test_hls_master_manifest_success(client: httpx.AsyncClient):
    """Verify master manifest returns 200, MPEGURL media type, and index.m3u8 reference."""
    fake_url = "https://cdn.example.com/test_video.mkv"
    mock_probe = {
        "duration_seconds": 120.0,
        "streams": [{"codec": "aac", "index": 0}],
        "codecs": ["aac"]
    }

    with patch("hls_transmuxer.probe_audio_streams", new=AsyncMock(return_value=mock_probe)):
        res = await client.get("/api/hls/master.m3u8", params={"url": fake_url, "track": 0, "boost": 1.2})

    assert res.status_code == 200
    assert "application/vnd.apple.mpegurl" in res.headers.get("content-type", "")
    content = res.text
    assert content.startswith("#EXTM3U")
    assert "#EXT-X-VERSION:3" in content
    assert "#EXT-X-STREAM-INF:BANDWIDTH=8000000" in content
    assert "/index.m3u8" in content


@pytest.mark.anyio
async def test_hls_master_manifest_missing_url(client: httpx.AsyncClient):
    """Verify master manifest returns 400 or 422 if URL is empty or omitted."""
    res_empty = await client.get("/api/hls/master.m3u8", params={"url": ""})
    assert res_empty.status_code in (400, 422)

    res_missing = await client.get("/api/hls/master.m3u8")
    assert res_missing.status_code in (400, 422)


# ============================================================================
# 2. HLS Media Manifest Tests (/api/hls/{session_id}/index.m3u8)
# ============================================================================

@pytest.mark.anyio
async def test_hls_media_manifest_success(client: httpx.AsyncClient):
    """Verify media playlist generates RFC 8216 compliant VOD chunks and headers."""
    session_id = "test_sess_media_01"
    session = HLSSession(
        session_id=session_id,
        media_url="https://cdn.example.com/movie.mp4",
        duration=18.0,
        segment_duration=4.0
    )
    HLS_SESSION_MANAGER.sessions[session_id] = session

    res = await client.get(f"/api/hls/{session_id}/index.m3u8")
    assert res.status_code == 200
    assert "application/vnd.apple.mpegurl" in res.headers.get("content-type", "")

    content = res.text
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    assert lines[0] == "#EXTM3U"
    assert lines[1] == "#EXT-X-VERSION:3"
    assert "#EXT-X-TARGETDURATION:5" in content
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in content
    assert "segment_0.ts" in content
    assert "segment_4.ts" in content  # ceil(18.0 / 4.0) = 5 segments (0, 1, 2, 3, 4)
    assert lines[-1] == "#EXT-X-ENDLIST"


@pytest.mark.anyio
async def test_hls_media_manifest_404_unknown_session(client: httpx.AsyncClient):
    """Verify unknown session ID returns 404 Not Found."""
    res = await client.get("/api/hls/non_existent_session_99999/index.m3u8")
    assert res.status_code == 404
    assert "not found" in res.json().get("detail", "").lower()


# ============================================================================
# 3. HLS Segment Delivery Tests (/api/hls/{session_id}/segment_{idx}.ts)
# ============================================================================

@pytest.mark.anyio
async def test_hls_segment_success(client: httpx.AsyncClient):
    """Verify segment delivery returns HTTP 200, video/mp2t, and expected byte payload."""
    session_id = "test_sess_seg_02"
    session = HLSSession(
        session_id=session_id,
        media_url="https://cdn.example.com/movie.mp4",
        duration=60.0,
        segment_duration=4.0
    )
    HLS_SESSION_MANAGER.sessions[session_id] = session

    expected_bytes = b"G@\x10\x11TEST_MPEGTS_PAYLOAD_CHUNK_DATA"
    with patch.object(session, "get_or_generate_segment", new=AsyncMock(return_value=expected_bytes)):
        res = await client.get(f"/api/hls/{session_id}/segment_0.ts")

    assert res.status_code == 200
    assert res.headers.get("content-type") == "video/mp2t"
    assert res.headers.get("accept-ranges") == "bytes"
    assert "public, max-age=3600" in res.headers.get("cache-control", "")
    assert res.content == expected_bytes


@pytest.mark.anyio
async def test_hls_segment_404_unknown_session(client: httpx.AsyncClient):
    """Verify requesting segment from non-existent session returns 404."""
    res = await client.get("/api/hls/ghost_session/segment_0.ts")
    assert res.status_code == 404


# ============================================================================
# 4. HLS Session Status Telemetry (/api/hls/{session_id}/status)
# ============================================================================

@pytest.mark.anyio
async def test_hls_status_success(client: httpx.AsyncClient):
    """Verify session status returns duration, segment count, health, and audio track."""
    session_id = "test_sess_status_03"
    session = HLSSession(
        session_id=session_id,
        media_url="https://cdn.example.com/feature.mkv",
        duration=120.0,
        audio_track=1,
        volume_boost=1.5,
        segment_duration=4.0
    )
    HLS_SESSION_MANAGER.sessions[session_id] = session

    res = await client.get(f"/api/hls/{session_id}/status")
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == session_id
    assert data["health"] == "healthy"
    assert data["duration"] == 120.0
    assert data["audio_track"] == 1
    assert data["volume_boost"] == 1.5
    assert data["segment_count"] == 30  # 120.0 / 4.0 = 30
    assert data["segment_duration"] == 4.0


@pytest.mark.anyio
async def test_hls_status_404(client: httpx.AsyncClient):
    """Verify status for absent session returns 404."""
    res = await client.get("/api/hls/missing_session_abc/status")
    assert res.status_code == 404


# ============================================================================
# 5. Playback Cascade Resolution Tests (/api/playback/cascade)
# ============================================================================

@pytest.mark.anyio
async def test_playback_cascade_success(client: httpx.AsyncClient):
    """Verify cascade endpoint returns 200 with primary stream and waterfall list."""
    mock_result = {
        "status": "SUCCESS",
        "primary": {
            "title": "Inception (2010) 2160p",
            "direct_url": "https://fast-stream.cdn/inception.mkv",
            "ttfb_ms": 62.4,
            "verified": True,
            "format": "MKV",
            "size": "14.2 GB"
        },
        "waterfall": [
            {
                "title": "Inception (2010) 1080p",
                "direct_url": "https://fallback.cdn/inception_1080p.mp4",
                "ttfb_ms": 112.8,
                "verified": True,
                "format": "MP4",
                "size": "4.5 GB"
            }
        ],
        "candidates_tested": 2
    }

    with patch("main.cascade_search_and_resolve", new=AsyncMock(return_value=mock_result)):
        res = await client.get("/api/playback/cascade", params={"q": "Inception", "type": "movie"})

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "SUCCESS"
    assert data["primary"]["direct_url"] == "https://fast-stream.cdn/inception.mkv"
    assert len(data["waterfall"]) == 1
    assert data["candidates_tested"] == 2


@pytest.mark.anyio
async def test_playback_cascade_imdb_id(client: httpx.AsyncClient):
    """Verify cascade endpoint handles imdb_id parameter properly."""
    mock_search = {
        "releases": [
            {"title": "Fight Club (1999) 1080p", "direct_url": "https://cdn.example/fc.mp4"}
        ]
    }
    mock_cascade = {
        "status": "SUCCESS",
        "primary": {
            "title": "Fight Club (1999) 1080p",
            "direct_url": "https://cdn.example/fc.mp4",
            "ttfb_ms": 40.0,
            "verified": True
        },
        "waterfall": [],
        "candidates_tested": 1
    }

    with patch("main.play_engine.search", new=AsyncMock(return_value=mock_search)), \
         patch("main.resolve_stream_cascade", new=AsyncMock(return_value=mock_cascade)):
        res = await client.get("/api/playback/cascade", params={"imdb_id": "tt0137523"})

    assert res.status_code == 200
    assert res.json()["status"] == "SUCCESS"
    assert res.json()["primary"]["direct_url"] == "https://cdn.example/fc.mp4"


@pytest.mark.anyio
async def test_playback_cascade_empty_query(client: httpx.AsyncClient):
    """Verify empty query immediately returns NO_STREAMS without hanging."""
    res = await client.get("/api/playback/cascade", params={"q": "   "})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "NO_STREAMS"
    assert data["primary"] is None
    assert data["waterfall"] == []


# ============================================================================
# 6. Embed Sources Tests (/api/embed/sources)
# ============================================================================

@pytest.mark.anyio
async def test_embed_sources_movie(client: httpx.AsyncClient):
    """Verify movie embed generation returns valid providers and URL formats."""
    res = await client.get("/api/embed/sources", params={"type": "movie", "id": "tt0137523"})
    assert res.status_code == 200
    data = res.json()

    assert data["provider"] in ("filmu", "cinezo", "vidbolt", "vidy", "vidrift")
    assert "embed_url" in data
    assert "tt0137523" in data["embed_url"]

    all_providers = data["all_providers"]
    for prov in ("filmu", "cinezo", "vidbolt", "vidy", "vidrift"):
        assert prov in all_providers
        assert "tt0137523" in all_providers[prov]
        assert all_providers[prov].startswith("https://")


@pytest.mark.anyio
async def test_embed_sources_tv_series(client: httpx.AsyncClient):
    """Verify TV series embed generation includes season and episode coordinates."""
    res = await client.get(
        "/api/embed/sources",
        params={"type": "tv", "id": "tt0944947", "season": 3, "episode": 9}
    )
    assert res.status_code == 200
    data = res.json()

    assert "/3/9" in data["embed_url"]
    all_providers = data["all_providers"]
    assert "https://embed.filmu.in/tv/tt0944947/3/9" == all_providers["filmu"]
    assert "https://cinezo.net/embed/tv/tt0944947/3/9" == all_providers["cinezo"]
    assert "https://vidbolt.org/embed/tv/tt0944947/3/9" == all_providers["vidbolt"]


@pytest.mark.anyio
async def test_embed_sources_preferred_provider(client: httpx.AsyncClient):
    """Verify selecting a preferred provider switches the primary embed_url."""
    res = await client.get(
        "/api/embed/sources",
        params={"type": "movie", "id": "tt0137523", "preferred": "vidbolt"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["provider"] == "vidbolt"
    assert "https://vidbolt.org/embed/movie/tt0137523" == data["embed_url"]


@pytest.mark.anyio
async def test_embed_sources_missing_id(client: httpx.AsyncClient):
    """Verify missing ID returns 400 Bad Request."""
    res = await client.get("/api/embed/sources", params={"type": "movie", "id": ""})
    assert res.status_code in (400, 422)
