"""Playro JEV — Audio Stack Verification Test Suite.

Verifies:
1. find_ffmpeg() locates executable and returns valid path.
2. probe_audio_streams() parsing logic against TrueHD 7.1, AC-3 5.1, DTS, AAC 2.0.
3. stream_transcoded_media() generator initialization and parameter validation.
4. FastAPI endpoints /api/audio/probe and /stream/transcode via httpx.AsyncClient / TestClient.
"""

import asyncio
import inspect
import os
import sys
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Ensure D:\playro_jev is at root of sys.path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from audio_probe import (
    BROWSER_COMPATIBLE_AUDIO_CODECS,
    KNOWN_FFMPEG_PATHS,
    find_ffmpeg,
    parse_audio_streams,
    probe_audio_streams,
)
from audio_transcoder import (
    build_ffmpeg_args,
    stream_transcoded_media,
    validate_transcode_params,
)
from main import app

# Sample FFmpeg stderr probe output with TrueHD 7.1, AC-3 5.1, DTS, and AAC 2.0
SAMPLE_FFMPEG_MULTITRACK_OUTPUT = """
Input #0, matroska,webm, from 'http://example.com/remux_movie.mkv':
  Metadata:
    title           : Sample Master Feature 4K Remux
    encoder         : libebml v1.4.2
  Duration: 02:28:40.15, start: 0.000000, bitrate: 62450 kb/s
    Stream #0:0(eng): Video: hevc (Main 10), yuv420p10le(tv, bt2020nc/bt2020/smpte2084), 3840x2160 [SAR 1:1 DAR 16:9], 23.98 fps, 23.98 tbr, 1k tbn (default)
    Metadata:
      title           : 4K HDR Main Video
    Stream #0:1(eng): Audio: truehd, 48000 Hz, 7.1, s32 (default)
    Metadata:
      title           : English Dolby TrueHD Atmos 7.1
    Stream #0:2(eng): Audio: ac3, 48000 Hz, 5.1(side), fltp, 640 kb/s
    Metadata:
      title           : English Dolby Digital 5.1 Compatibility
    Stream #0:3(hin): Audio: dts (DTS), 48000 Hz, 5.1(side), fltp, 1536 kb/s
    Metadata:
      title           : Hindi DTS 5.1 Surround
    Stream #0:4(jpn): Audio: aac (LC), 48000 Hz, stereo, fltp, 192 kb/s
    Metadata:
      title           : Japanese AAC 2.0 Stereo
"""


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ==============================================================================
# SECTION 1: find_ffmpeg() Executable Discovery Tests
# ==============================================================================
def test_find_ffmpeg_host_resolution():
    """Verify find_ffmpeg finds an executable on host and returns valid path."""
    ffmpeg_path = find_ffmpeg()
    assert isinstance(ffmpeg_path, str)
    assert len(ffmpeg_path.strip()) > 0
    # On this Windows host, FFmpeg is installed at one of the known paths
    assert os.path.exists(ffmpeg_path), f"Resolved FFmpeg path does not exist: {ffmpeg_path}"
    assert os.path.isfile(ffmpeg_path), f"Resolved FFmpeg is not a file: {ffmpeg_path}"
    assert "ffmpeg" in os.path.basename(ffmpeg_path).lower()


def test_find_ffmpeg_path_precedence():
    """Verify find_ffmpeg checks which() first, then known paths, then fallback."""
    # 1. System PATH via shutil.which takes priority
    with patch("shutil.which", return_value=r"C:\Custom\ffmpeg.exe"), \
         patch("os.path.isfile", return_value=True):
        assert find_ffmpeg() == os.path.abspath(r"C:\Custom\ffmpeg.exe")

    # 2. Known fallback candidate when which() is None
    with patch("shutil.which", return_value=None), \
         patch("os.path.isfile", side_effect=lambda p: p == KNOWN_FFMPEG_PATHS[0]):
        assert find_ffmpeg() == os.path.abspath(KNOWN_FFMPEG_PATHS[0])

    # 3. Default fallback "ffmpeg" when nothing matches
    with patch("shutil.which", return_value=None), \
         patch("os.path.isfile", return_value=False):
        assert find_ffmpeg() == "ffmpeg"


# ==============================================================================
# SECTION 2: probe_audio_streams() Parsing Logic Tests (TrueHD, AC-3, DTS, AAC)
# ==============================================================================
@pytest.mark.anyio
async def test_probe_audio_streams_multichannel_parsing():
    """Test probe_audio_streams parses TrueHD 7.1, AC-3 5.1, DTS, and AAC 2.0 accurately."""
    result = await probe_audio_streams(SAMPLE_FFMPEG_MULTITRACK_OUTPUT)

    assert result["stream_count"] == 4
    assert result["codecs"] == ["truehd", "ac3", "dts", "aac"]
    assert "eng" in result["languages"]
    assert "hin" in result["languages"]
    assert "jpn" in result["languages"]

    streams = result["streams"]
    assert len(streams) == 4

    # 1. TrueHD 7.1 Stream
    truehd_stream = streams[0]
    assert truehd_stream["stream_id"] == "0:1"
    assert truehd_stream["codec"] == "truehd"
    assert truehd_stream["channels"] == "7.1"
    assert truehd_stream["channel_count"] == 8
    assert truehd_stream["sample_rate"] == 48000
    assert truehd_stream["language"] == "eng"
    assert truehd_stream["title"] == "English Dolby TrueHD Atmos 7.1"
    assert truehd_stream["is_default"] is True
    assert truehd_stream["browser_compatible"] is False

    # 2. AC-3 5.1 Stream
    ac3_stream = streams[1]
    assert ac3_stream["stream_id"] == "0:2"
    assert ac3_stream["codec"] == "ac3"
    assert ac3_stream["channels"] == "5.1"
    assert ac3_stream["channel_count"] == 6
    assert ac3_stream["bitrate"] == "640 kb/s"
    assert ac3_stream["language"] == "eng"
    assert ac3_stream["title"] == "English Dolby Digital 5.1 Compatibility"
    assert ac3_stream["browser_compatible"] is False

    # 3. DTS 5.1 Stream
    dts_stream = streams[2]
    assert dts_stream["stream_id"] == "0:3"
    assert dts_stream["codec"] == "dts"
    assert dts_stream["channels"] == "5.1"
    assert dts_stream["channel_count"] == 6
    assert dts_stream["bitrate"] == "1536 kb/s"
    assert dts_stream["language"] == "hin"
    assert dts_stream["title"] == "Hindi DTS 5.1 Surround"
    assert dts_stream["browser_compatible"] is False

    # 4. AAC 2.0 / Stereo Stream
    aac_stream = streams[3]
    assert aac_stream["stream_id"] == "0:4"
    assert aac_stream["codec"] == "aac"
    assert aac_stream["channels"] in ["2.0", "stereo"]
    assert aac_stream["channel_count"] == 2
    assert aac_stream["bitrate"] == "192 kb/s"
    assert aac_stream["language"] == "jpn"
    assert aac_stream["title"] == "Japanese AAC 2.0 Stereo"
    assert aac_stream["browser_compatible"] is True

    # Check overall compatibility and transcoding requirements
    assert result["has_incompatible_audio"] is True
    assert result["needs_transcode"] is True
    assert result["browser_compatible"] is False


@pytest.mark.anyio
async def test_probe_audio_streams_aac_stereo_native_compatible():
    """Verify AAC 2.0 alone is recognized as natively browser compatible."""
    aac_sample = """
    Stream #0:1(eng): Audio: aac (LC), 48000 Hz, stereo, fltp, 256 kb/s (default)
    Metadata:
      title           : Stereo English Audio
    """
    res = await probe_audio_streams(aac_sample)
    assert res["stream_count"] == 1
    assert res["codecs"] == ["aac"]
    assert res["browser_compatible"] is True
    assert res["needs_transcode"] is False
    assert res["has_incompatible_audio"] is False


@pytest.mark.anyio
async def test_probe_audio_streams_subprocess_call():
    """Verify probe_audio_streams invokes FFmpeg subprocess when given a remote URL."""
    fake_stderr = b"""
    Stream #0:1(eng): Audio: ac3, 48000 Hz, 5.1, fltp, 448 kb/s
    """
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", fake_stderr)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        res = await probe_audio_streams("http://example.com/test_video.mkv")
        assert mock_exec.called
        assert res["stream_count"] == 1
        assert res["codecs"] == ["ac3"]
        assert res["streams"][0]["codec"] == "ac3"


# ==============================================================================
# SECTION 3: stream_transcoded_media() Generator & Parameter Validation Tests
# ==============================================================================
def test_stream_transcoded_media_generator_initialization():
    """Verify stream_transcoded_media initializes as an asynchronous generator."""
    gen = stream_transcoded_media(
        url="http://example.com/media.mkv",
        audio_track_index=1,
        start_time=12.5,
        volume_boost=1.5,
    )
    assert inspect.isasyncgen(gen)
    assert hasattr(gen, "__aiter__")
    assert hasattr(gen, "__anext__")


def test_build_ffmpeg_args_structure():
    """Verify build_ffmpeg_args compiles the exact expected CLI arguments."""
    args = build_ffmpeg_args(
        ffmpeg_bin=r"C:\ffmpeg.exe",
        url="http://example.com/media.mkv",
        audio_track_index=2,
        start_time=30.0,
        volume_boost=1.75,
    )

    assert args[0] == r"C:\ffmpeg.exe"
    assert "-ss" in args and args[args.index("-ss") + 1] == "30.0"
    assert "-i" in args and args[args.index("-i") + 1] == "http://example.com/media.mkv"
    assert "-map" in args and "0:v:0" in args
    assert f"0:a:2?" in args
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "copy"
    assert "-c:a" in args and args[args.index("-c:a") + 1] == "aac"
    assert "-b:a" in args and args[args.index("-b:a") + 1] == "256k"
    assert "-ac" in args and args[args.index("-ac") + 1] == "2"
    assert "-af" in args and args[args.index("-af") + 1] == "volume=1.75"
    assert "-f" in args and args[args.index("-f") + 1] == "mp4"
    assert "frag_keyframe+empty_moov+default_base_moof" in args
    assert "pipe:1" in args


@pytest.mark.anyio
async def test_stream_transcoded_media_parameter_validation():
    """Verify stream_transcoded_media rejects invalid inputs with ValueError."""
    # 1. Empty or whitespace URL
    with pytest.raises(ValueError, match="Invalid or empty media URL"):
        gen = stream_transcoded_media("")
        await anext(gen)

    with pytest.raises(ValueError, match="Invalid or empty media URL"):
        validate_transcode_params("   ")

    # 2. Negative audio track index
    with pytest.raises(ValueError, match="audio_track_index"):
        gen = stream_transcoded_media("http://example.com/media.mkv", audio_track_index=-1)
        await anext(gen)

    with pytest.raises(ValueError, match="audio_track_index"):
        validate_transcode_params("http://example.com/media.mkv", audio_track_index=-2)

    # 3. Negative start time
    with pytest.raises(ValueError, match="start_time"):
        gen = stream_transcoded_media("http://example.com/media.mkv", start_time=-10.0)
        await anext(gen)

    with pytest.raises(ValueError, match="start_time"):
        validate_transcode_params("http://example.com/media.mkv", start_time=-0.1)

    # 4. Zero or negative volume boost
    with pytest.raises(ValueError, match="volume_boost"):
        gen = stream_transcoded_media("http://example.com/media.mkv", volume_boost=0.0)
        await anext(gen)

    with pytest.raises(ValueError, match="volume_boost"):
        gen = stream_transcoded_media("http://example.com/media.mkv", volume_boost=-1.5)
        await anext(gen)


@pytest.mark.anyio
async def test_stream_transcoded_media_chunk_yielding():
    """Verify stream_transcoded_media streams byte chunks from FFmpeg stdout pipe."""
    mock_stdout = AsyncMock()
    # Return 2 chunks, then EOF
    mock_stdout.read.side_effect = [b"MP4_HEADER_CHUNK", b"MP4_DATA_CHUNK", b""]

    mock_proc = AsyncMock()
    mock_proc.stdout = mock_stdout
    mock_proc.returncode = None
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.terminate = MagicMock()
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        chunks = []
        async for chunk in stream_transcoded_media("http://example.com/movie.mkv"):
            chunks.append(chunk)

        assert chunks == [b"MP4_HEADER_CHUNK", b"MP4_DATA_CHUNK"]
        assert mock_proc.terminate.called or mock_proc.wait.called


# ==============================================================================
# SECTION 4: FastAPI Server Endpoint Tests (/api/audio/probe, /stream/transcode)
# ==============================================================================
@pytest.mark.anyio
async def test_fastapi_audio_probe_endpoint():
    """Verify /api/audio/probe endpoint returns correct probe schema and handles errors."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Success probe with mocked backend
        mock_probe_data = {
            "url": "http://example.com/test.mkv",
            "stream_count": 2,
            "codecs": ["truehd", "aac"],
            "languages": ["eng"],
            "browser_compatible": False,
            "has_incompatible_audio": True,
            "needs_transcode": True,
            "streams": [
                {"index": 0, "codec": "truehd", "channels": "7.1", "browser_compatible": False},
                {"index": 1, "codec": "aac", "channels": "2.0", "browser_compatible": True},
            ],
        }

        with patch("main.probe_audio_streams", new_callable=AsyncMock, return_value=mock_probe_data):
            res = await client.get("/api/audio/probe?url=http://example.com/test.mkv")
            assert res.status_code == 200
            data = res.json()
            assert data["stream_count"] == 2
            assert data["codecs"] == ["truehd", "aac"]
            assert data["browser_compatible"] is False

        # 2. Missing url query param -> 422 Unprocessable Entity
        res_missing = await client.get("/api/audio/probe")
        assert res_missing.status_code == 422

        # 3. Empty url query param -> 400 Bad Request
        res_empty = await client.get("/api/audio/probe?url=")
        assert res_empty.status_code == 400


@pytest.mark.anyio
async def test_fastapi_stream_transcode_endpoint():
    """Verify /stream/transcode endpoint initializes streaming and validates query parameters."""
    async def mock_byte_generator(url: str, audio_track_index: int = 0, start_time: float = 0.0, volume_boost: float = 1.0) -> AsyncGenerator[bytes, None]:
        yield b"\x00\x00\x00 ftypmp42"
        yield b"\x00\x00\x00\x08mdattest"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Valid streaming request
        with patch("main.stream_transcoded_media", side_effect=mock_byte_generator):
            res = await client.get("/stream/transcode?url=http://example.com/video.mkv&track=1&ss=15.0&boost=1.5")
            assert res.status_code == 200
            assert "video/mp4" in res.headers["content-type"]
            assert res.headers.get("accept-ranges") == "bytes"
            assert res.content == b"\x00\x00\x00 ftypmp42\x00\x00\x00\x08mdattest"

        # 2. Missing required URL parameter -> 422 Unprocessable Entity
        res_no_url = await client.get("/stream/transcode")
        assert res_no_url.status_code == 422

        # 3. Empty URL -> 400 Bad Request
        res_empty = await client.get("/stream/transcode?url=")
        assert res_empty.status_code == 400

        # 4. Negative track index -> 400 Bad Request
        res_bad_track = await client.get("/stream/transcode?url=http://example.com/video.mkv&track=-1")
        assert res_bad_track.status_code == 400

        # 5. Negative start time -> 400 Bad Request
        res_bad_ss = await client.get("/stream/transcode?url=http://example.com/video.mkv&ss=-5.0")
        assert res_bad_ss.status_code == 400

        # 6. Invalid volume boost -> 400 Bad Request
        res_bad_boost = await client.get("/stream/transcode?url=http://example.com/video.mkv&boost=0")
        assert res_bad_boost.status_code == 400


# ==============================================================================
# CLI Entrypoint for: python D:\playro_jev\test_audio_engine.py
# ==============================================================================
if __name__ == "__main__":
    print("\n" + "=" * 70)
    print(" PLAYRO JEV — AUDIO STACK VERIFICATION SUITE")
    print("=" * 70)
    exit_code = pytest.main([
        "-v",
        "-s",
        "--tb=short",
        os.path.abspath(__file__),
    ])
    sys.exit(exit_code)
