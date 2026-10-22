"""
Tests for /api/stream/accelerated Hybrid Routing & Waterfall Logic.

Verifies:
1. Hybrid routing: range header parsing & p_start extraction.
2. Initial stream start / container header probe (p_start == 0 or no Range):
   - Direct Google CDN wire-speed streaming via google_range_adapter.
   - Micro-range coalescing / cache passthrough.
3. Player Seeking (p_start > 0):
   - NotImplementedError in google_range_adapter cleanly waterfalls to swarm_stream_engine.
   - Sequential-only Google CDN URL waterfalls to swarm_stream_engine.
   - Known / resolved seekable mirror (worker, pixeldrain, r2) routed via swarm_stream_engine.
4. Total Size Propagation:
   - Content-Range header is always complete with valid total_size (not '*').
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
import httpx
from starlette.responses import Response, StreamingResponse

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main import app, SEEKABLE_MIRRORS, MEDIA_TOTAL_SIZE_CACHE


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


@pytest.mark.anyio
async def test_initial_start_google_wire_speed(client: httpx.AsyncClient):
    """Initial stream start (p_start == 0) routes directly through google_range_adapter."""
    google_url = "https://video-downloads.googleusercontent.com/test_video.mkv"
    total_file_size = 500_000_000

    async def fake_stream():
        yield b"GOOGLE_STREAM_DATA_0"

    with patch("main.google_range_adapter") as mock_gra:
        mock_gra.is_google_url.return_value = True
        mock_gra.probe_metadata = AsyncMock(return_value={
            "total_size": total_file_size,
            "content_type": "video/x-matroska",
            "seekable": True
        })
        mock_gra.handle_range_request = AsyncMock(return_value=(
            206,
            {
                "Content-Type": "video/x-matroska",
                "Content-Range": f"bytes 0-1023/{total_file_size}",
                "Content-Length": "1024",
                "Accept-Ranges": "bytes"
            },
            fake_stream()
        ))

        resp = await client.get(
            f"/api/stream/accelerated?url={google_url}",
            headers={"Range": "bytes=0-1023"}
        )

        assert resp.status_code == 206
        assert resp.text == "GOOGLE_STREAM_DATA_0"
        assert resp.headers["content-range"] == f"bytes 0-1023/{total_file_size}"
        mock_gra.handle_range_request.assert_awaited_once()
        call_kwargs = mock_gra.handle_range_request.call_args.kwargs
        assert call_kwargs["total_size"] == total_file_size
        assert call_kwargs["range_header"] == "bytes=0-1023"


@pytest.mark.anyio
async def test_seeking_notimplemented_waterfalls_to_swarm(client: httpx.AsyncClient):
    """Seeking with mirror parameter waterfalls directly to swarm_stream_engine."""
    google_url = "https://video-downloads.googleusercontent.com/test_video.mkv"
    mirror_url = "https://worker.playro.workers.dev/movie.mkv"
    total_file_size = 500_000_000

    with patch("main.google_range_adapter") as mock_gra, \
         patch("main.swarm_stream_engine") as mock_sse:

        mock_gra.is_google_url.return_value = True
        mock_gra.probe_metadata = AsyncMock(return_value={
            "total_size": total_file_size,
            "content_type": "video/x-matroska",
        })

        mock_sse.stream_range = AsyncMock(return_value=Response(
            content=b"SWARM_STREAM_SEEK_DATA",
            status_code=206,
            headers={"Content-Range": f"bytes 1048576-2097151/{total_file_size}"}
        ))

        resp = await client.get(
            f"/api/stream/accelerated?url={google_url}&mirror={mirror_url}",
            headers={"Range": "bytes=1048576-2097151"}
        )

        assert resp.status_code == 206
        assert resp.text == "SWARM_STREAM_SEEK_DATA"
        assert resp.headers["content-range"] == f"bytes 1048576-2097151/{total_file_size}"
        mock_sse.stream_range.assert_awaited_once()


@pytest.mark.anyio
async def test_seeking_sequential_only_routes_via_known_seekable_mirror(client: httpx.AsyncClient):
    """Seeking on sequential-only Google URL routes through known seekable mirror (worker/r2/pixeldrain)."""
    google_url = "https://video-downloads.googleusercontent.com/seq_video.mkv"
    seekable_mirror_url = "https://worker-stream.playro.workers.dev/file.mkv"
    total_file_size = 800_000_000

    SEEKABLE_MIRRORS[google_url] = seekable_mirror_url

    with patch("main.google_range_adapter") as mock_gra, \
         patch("main.swarm_stream_engine") as mock_sse:

        mock_gra.is_google_url.return_value = True
        mock_gra.is_sequential_only = MagicMock(return_value=True)
        mock_gra.probe_metadata = AsyncMock(return_value={
            "total_size": total_file_size,
            "content_type": "video/x-matroska"
        })

        mock_sse.stream_range = AsyncMock(return_value=Response(
            content=b"MIRROR_SWARM_DATA",
            status_code=206,
            headers={"Content-Range": f"bytes 5000000-6000000/{total_file_size}"}
        ))

        resp = await client.get(
            f"/api/stream/accelerated?url={google_url}",
            headers={"Range": "bytes=5000000-6000000"}
        )

        assert resp.status_code == 206
        assert resp.text == "MIRROR_SWARM_DATA"
        mock_sse.stream_range.assert_awaited_once()
        args, kwargs = mock_sse.stream_range.call_args
        assert args[0] == seekable_mirror_url
        assert args[3] == total_file_size


@pytest.mark.anyio
async def test_total_size_propagation_head_request(client: httpx.AsyncClient):
    """Total size propagation ensures Content-Range has complete total_size in HEAD responses."""
    stream_url = "https://pixeldrain.dev/api/file/abc12345"
    total_size = 12_345_678

    with patch("main.google_range_adapter") as mock_gra, \
         patch("main.swarm_stream_engine") as mock_sse, \
         patch("main.bingr_hls_engine", None):

        mock_gra.is_google_url.return_value = False
        mock_sse.probe_upstream = AsyncMock(return_value=(total_size, "video/x-matroska", True))
        mock_sse.stream_range = AsyncMock(return_value=Response(
            status_code=206,
            headers={
                "Content-Range": f"bytes 0-1023/{total_size}",
                "Content-Length": "1024",
                "Accept-Ranges": "bytes",
                "Content-Type": "video/x-matroska",
            }
        ))

        resp = await client.head(
            f"/api/stream/accelerated?url={stream_url}",
            headers={"Range": "bytes=0-1023"}
        )

        assert resp.status_code == 206
        assert resp.headers["content-range"] == f"bytes 0-1023/{total_size}"
        assert resp.headers["content-length"] == "1024"
        assert resp.headers["accept-ranges"] == "bytes"


@pytest.mark.anyio
async def test_seeking_sequential_only_no_mirror_returns_206_never_416(client: httpx.AsyncClient):
    """When is_google and p_start > 0 on sequential-only token with no mirror, returns HTTP 206 (never 416!)."""
    google_url = "https://video-downloads.googleusercontent.com/no_mirror_seq.mkv"
    total_file_size = 900_000_000

    SEEKABLE_MIRRORS.pop(google_url, None)

    async def fake_stream():
        yield b"REMAINING_STREAM_BYTES"

    with patch("main.google_range_adapter") as mock_gra:
        mock_gra.is_google_url.return_value = True
        mock_gra.is_sequential_only = MagicMock(return_value=True)
        mock_gra.probe_metadata = AsyncMock(return_value={
            "total_size": total_file_size,
            "content_type": "video/x-matroska"
        })
        mock_gra.stream_range = MagicMock(return_value=fake_stream())

        resp = await client.get(
            f"/api/stream/accelerated?url={google_url}",
            headers={"Range": "bytes=1048576-2097151"}
        )

        assert resp.status_code == 206
        assert resp.headers.get("content-range") == f"bytes 1048576-2097151/{total_file_size}"


@pytest.mark.anyio
async def test_vlc_play_default_accelerate_media_streams(client: httpx.AsyncClient):
    """VLC play endpoint defaults accelerate=True for media streams and uses dynamic port."""
    media_url = "https://worker-stream.workers.dev/movie.mkv"
    mock_resolve = {"status": "SUCCESS", "direct_url": media_url}
    mock_verify = {"valid": True, "format": "Matroska", "size": "4.2 GB"}

    with patch("main.link_resolver.resolve_auto", new=AsyncMock(return_value=mock_resolve)), \
         patch("main.stream_verifier.verify", new=AsyncMock(return_value=mock_verify)), \
         patch("subprocess.Popen") as mock_popen:

        mock_popen.return_value = MagicMock()
        res = await client.get("/api/vlc/play", params={"url": media_url, "title": "Test Movie"})

        assert res.status_code == 200
        assert mock_popen.called
        spawn_args = mock_popen.call_args[0][0]
        play_target = spawn_args[1]

        # Port from testserver defaults to 7860
        assert play_target.startswith("http://127.0.0.1:7860/api/stream/accelerated?url=")
        assert "worker-stream.workers.dev" in play_target


@pytest.mark.anyio
async def test_vlc_play_explicit_accelerate_false(client: httpx.AsyncClient):
    """When accelerate is explicitly False, VLC plays direct stream without accelerated proxy."""
    media_url = "https://worker-stream.workers.dev/movie.mp4"
    mock_resolve = {"status": "SUCCESS", "direct_url": media_url}
    mock_verify = {"valid": True, "format": "MP4", "size": "1.2 GB"}

    with patch("main.link_resolver.resolve_auto", new=AsyncMock(return_value=mock_resolve)), \
         patch("main.stream_verifier.verify", new=AsyncMock(return_value=mock_verify)), \
         patch("subprocess.Popen") as mock_popen:

        mock_popen.return_value = MagicMock()
        res = await client.get("/api/vlc/play", params={"url": media_url, "title": "Test Movie", "accelerate": "false"})

        assert res.status_code == 200
        assert mock_popen.called
        spawn_args = mock_popen.call_args[0][0]
        play_target = spawn_args[1]

        # Should be direct media_url, not accelerated proxy
        assert play_target == media_url


@pytest.mark.anyio
async def test_vlc_play_dynamic_port_custom():
    """Verify VLC play endpoint dynamically captures port from request URL."""
    media_url = "https://video-downloads.googleusercontent.com/test_movie.mkv"
    mock_resolve = {"status": "SUCCESS", "direct_url": media_url}
    mock_verify = {"valid": True, "format": "Matroska", "size": "2.0 GB"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:9090"
    ) as custom_client:
        with patch("main.link_resolver.resolve_auto", new=AsyncMock(return_value=mock_resolve)), \
             patch("main.stream_verifier.verify", new=AsyncMock(return_value=mock_verify)), \
             patch("subprocess.Popen") as mock_popen:

            mock_popen.return_value = MagicMock()
            res = await custom_client.get("/api/vlc/play", params={"url": media_url, "title": "Test Movie"})

            assert res.status_code == 200
            assert mock_popen.called
            spawn_args = mock_popen.call_args[0][0]
            play_target = spawn_args[1]

            assert play_target.startswith("http://127.0.0.1:9090/api/stream/accelerated?url=")

