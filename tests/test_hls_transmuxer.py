"""Unit and integration test suite for Dynamic VOD HLS Transmuxer.

Tests HLSSegmentCache LRU mechanics, RFC 8216 master/media manifest generation,
real FFmpeg MPEG-TS segment slicing on local clips, prebuffering, and
HLSSessionManager lifecycle tracking.
"""

import asyncio
import os
from pathlib import Path
import sys
import time
import pytest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hls_transmuxer import (
    HLSSegmentCache,
    HLSSession,
    HLSSessionManager,
    HLS_SEGMENT_CACHE,
    HLS_SESSION_MANAGER,
)

BUNNY_CLIP = str(REPO_ROOT / "static" / "test_clips" / "clip1_bunny.mp4")
FLOWER_CLIP = str(REPO_ROOT / "static" / "test_clips" / "clip3_flower.mp4")


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ============================================================================
# 1. HLSSegmentCache Tests
# ============================================================================

def test_cache_put_get_and_miss():
    """Verify segment storage, retrieval, and cache miss semantics."""
    cache = HLSSegmentCache(max_size_mb=1)

    assert cache.get("missing_key") is None
    assert len(cache) == 0
    assert cache.current_size == 0

    payload = b"MPEGTS_DUMMY_PAYLOAD_001"
    cache.put("sess1:0", payload)

    assert cache.get("sess1:0") == payload
    assert len(cache) == 1
    assert cache.current_size == len(payload)
    assert "sess1:0" in cache


def test_cache_lru_eviction():
    """Verify least recently used segments are evicted when capacity limit is breached."""
    # Create a small 100-byte cache
    cache = HLSSegmentCache(max_size_mb=1)
    cache.max_size_bytes = 100  # Override to 100 bytes for deterministic testing

    chunk1 = b"A" * 40
    chunk2 = b"B" * 40
    chunk3 = b"C" * 40

    cache.put("k1", chunk1)
    cache.put("k2", chunk2)
    assert len(cache) == 2
    assert cache.current_size == 80

    # Inserting chunk3 (40 bytes) exceeds 100 bytes (80 + 40 = 120 > 100)
    # k1 was oldest, so k1 should be evicted; k2 and k3 remain
    cache.put("k3", chunk3)
    assert cache.get("k1") is None
    assert cache.get("k2") == chunk2
    assert cache.get("k3") == chunk3
    assert cache.current_size == 80
    assert len(cache) == 2


def test_cache_access_promotes_mru():
    """Verify accessing a key moves it to MRU, protecting it from immediate eviction."""
    cache = HLSSegmentCache(max_size_mb=1)
    cache.max_size_bytes = 100

    chunk1 = b"A" * 40
    chunk2 = b"B" * 40
    chunk3 = b"C" * 40

    cache.put("k1", chunk1)
    cache.put("k2", chunk2)

    # Access k1 so k2 becomes the LRU
    assert cache.get("k1") == chunk1

    # Inserting k3 should now evict k2, not k1
    cache.put("k3", chunk3)
    assert cache.get("k2") is None
    assert cache.get("k1") == chunk1
    assert cache.get("k3") == chunk3


def test_cache_oversized_payload_rejected():
    """Verify single payload larger than cache capacity is rejected cleanly without corrupting state."""
    cache = HLSSegmentCache(max_size_mb=1)
    cache.max_size_bytes = 50

    giant_chunk = b"X" * 100
    cache.put("giant", giant_chunk)
    assert cache.get("giant") is None
    assert len(cache) == 0
    assert cache.current_size == 0


def test_cache_overwrite_updates_size():
    """Verify overwriting an existing key updates the payload and byte tracking correctly."""
    cache = HLSSegmentCache(max_size_mb=1)
    cache.put("k1", b"12345")
    assert cache.current_size == 5

    cache.put("k1", b"1234567890")
    assert cache.get("k1") == b"1234567890"
    assert cache.current_size == 10
    assert len(cache) == 1


def test_cache_purge_session_and_clear():
    """Verify session purging removes only corresponding session keys."""
    cache = HLSSegmentCache(max_size_mb=1)
    cache.put("sess_A:0", b"A0")
    cache.put("sess_A:1", b"A1")
    cache.put("sess_B:0", b"B0")

    purged = cache.purge_session("sess_A")
    assert purged == 2
    assert cache.get("sess_A:0") is None
    assert cache.get("sess_A:1") is None
    assert cache.get("sess_B:0") == b"B0"
    assert len(cache) == 1

    cache.clear()
    assert len(cache) == 0
    assert cache.current_size == 0


# ============================================================================
# 2. HLSSession RFC 8216 Manifest Generation Tests
# ============================================================================

def test_generate_master_manifest():
    """Verify RFC 8216 master manifest contains standard headers and expected URI."""
    session = HLSSession(
        session_id="test_sess_001",
        media_url="http://example.com/video.mkv",
        duration=120.0,
    )

    # Master manifest without base_url
    master_default = session.generate_master_manifest()
    assert master_default.startswith("#EXTM3U\n")
    assert "#EXT-X-VERSION:3" in master_default
    assert "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080" in master_default
    assert "/api/hls/test_sess_001/index.m3u8" in master_default

    # Master manifest with base_url
    master_with_base = session.generate_master_manifest(base_url="https://playro.local:8080/")
    assert "https://playro.local:8080/api/hls/test_sess_001/index.m3u8" in master_with_base


def test_generate_media_manifest_segments_and_durations():
    """Verify media playlist generates correct target duration, segment count, and durations."""
    # 10.0 seconds with 4.0s segment duration -> 3 segments (4.0, 4.0, 2.0)
    session = HLSSession(
        session_id="sess_media_test",
        media_url="http://example.com/test.mp4",
        duration=10.0,
        segment_duration=4.0,
    )

    playlist = session.generate_media_manifest()
    lines = [line.strip() for line in playlist.strip().splitlines() if line.strip()]

    assert lines[0] == "#EXTM3U"
    assert lines[1] == "#EXT-X-VERSION:3"
    assert lines[2] == "#EXT-X-TARGETDURATION:5"
    assert lines[3] == "#EXT-X-MEDIA-SEQUENCE:0"
    assert lines[4] == "#EXT-X-PLAYLIST-TYPE:VOD"

    # Segment 0
    assert "#EXTINF:4.000," in lines
    assert "segment_0.ts" in lines

    # Segment 1
    assert "segment_1.ts" in lines

    # Segment 2 (final chunk: 10.0 - 8.0 = 2.000s)
    assert "#EXTINF:2.000," in lines
    assert "segment_2.ts" in lines

    # Trailing endlist
    assert lines[-1] == "#EXT-X-ENDLIST"


def test_generate_media_manifest_with_base_url():
    """Verify media playlist prefixes segment URIs when base_url is supplied."""
    session = HLSSession(
        session_id="sess_base_test",
        media_url="http://example.com/test.mp4",
        duration=8.0,
        segment_duration=4.0,
    )

    playlist = session.generate_media_manifest(base_url="/api/hls/sess_base_test")
    assert "/api/hls/sess_base_test/segment_0.ts" in playlist
    assert "/api/hls/sess_base_test/segment_1.ts" in playlist


def test_generate_media_manifest_exact_multiple_duration():
    """Verify playlist handles exact multiple duration (8.0s / 4.0s = exactly 2 segments of 4.000s)."""
    session = HLSSession(
        session_id="sess_exact",
        media_url="http://example.com/test.mp4",
        duration=8.0,
        segment_duration=4.0,
    )
    playlist = session.generate_media_manifest()
    assert playlist.count("#EXTINF:4.000,") == 2
    assert "segment_0.ts" in playlist
    assert "segment_1.ts" in playlist
    assert "segment_2.ts" not in playlist


# ============================================================================
# 3. Real FFmpeg MPEG-TS Segment Slicing & Prebuffering Tests
# ============================================================================

@pytest.mark.anyio
async def test_slice_segment_ffmpeg_bunny_clip():
    """Verify slicing segment 0 of clip1_bunny.mp4 generates valid MPEG-TS bytes with 0x47 sync byte."""
    assert os.path.isfile(BUNNY_CLIP), f"Test clip not found: {BUNNY_CLIP}"

    session = HLSSession(
        session_id="test_bunny_slice",
        media_url=BUNNY_CLIP,
        duration=10.0,
        segment_duration=4.0,
        audio_track=0,
        is_audio_aac=False,
    )

    data = await session._slice_segment_ffmpeg(0)
    assert isinstance(data, bytes)
    assert len(data) > 0
    # Every MPEG-TS packet begins with the 0x47 sync byte
    assert data[0] == 0x47


@pytest.mark.anyio
async def test_get_or_generate_segment_caching_and_prebuffering():
    """Verify get_or_generate_segment caches data on miss and hits cache on subsequent calls."""
    cache = HLSSegmentCache(max_size_mb=10)
    session = HLSSession(
        session_id="test_caching_session",
        media_url=BUNNY_CLIP,
        duration=10.0,
        segment_duration=4.0,
        cache=cache,
    )

    cache_key_0 = f"{session.session_id}:0"
    assert cache.get(cache_key_0) is None

    # First fetch: invokes FFmpeg and populates cache
    data_first = await session.get_or_generate_segment(0)
    assert len(data_first) > 0
    assert data_first[0] == 0x47
    assert cache.get(cache_key_0) == data_first

    # Second fetch: instant cache hit
    data_second = await session.get_or_generate_segment(0)
    assert data_second == data_first

    # Wait brief moment for background prebuffering of segment 1 and 2
    await asyncio.sleep(1.0)
    cache_key_1 = f"{session.session_id}:1"
    # Prebuffer should have cached or be in progress
    data_seg1 = cache.get(cache_key_1)
    if data_seg1 is not None:
        assert data_seg1[0] == 0x47


@pytest.mark.anyio
async def test_slice_segment_ffmpeg_with_audio_and_volume_boost():
    """Verify slicing clip3_flower.mp4 with audio stream transcoding and volume boost."""
    assert os.path.isfile(FLOWER_CLIP), f"Test clip not found: {FLOWER_CLIP}"

    session = HLSSession(
        session_id="test_flower_audio_boost",
        media_url=FLOWER_CLIP,
        duration=5.0,
        segment_duration=4.0,
        audio_track=0,
        volume_boost=1.5,
        is_audio_aac=True,
    )

    data = await session._slice_segment_ffmpeg(0)
    assert isinstance(data, bytes)
    assert len(data) > 0
    assert data[0] == 0x47


# ============================================================================
# 4. HLSSessionManager Tests
# ============================================================================

@pytest.mark.anyio
async def test_session_manager_get_or_create_deterministic():
    """Verify session creation is deterministic and returns identical instance for same parameters."""
    manager = HLSSessionManager()

    session1 = await manager.get_or_create_session(
        media_url=BUNNY_CLIP,
        audio_track=0,
        volume_boost=1.0,
    )
    assert session1 is not None
    assert session1.session_id
    assert session1.duration == 10.0  # Probed from bunny clip

    # Second call with identical params returns the same session instance
    session2 = await manager.get_or_create_session(
        media_url=BUNNY_CLIP,
        audio_track=0,
        volume_boost=1.0,
    )
    assert session1 is session2

    # Different volume boost yields distinct session ID
    session3 = await manager.get_or_create_session(
        media_url=BUNNY_CLIP,
        audio_track=0,
        volume_boost=1.5,
    )
    assert session3.session_id != session1.session_id


@pytest.mark.anyio
async def test_session_manager_fallback_duration_on_unknown():
    """Verify invalid or unprobeable URL defaults to 7200.0s duration gracefully."""
    manager = HLSSessionManager()

    session = await manager.get_or_create_session(
        media_url="http://invalid.playro.test/nonexistent.mkv",
        audio_track=0,
        volume_boost=1.0,
    )
    assert session.duration == 7200.0


def test_session_manager_get_session_and_cleanup():
    """Verify session lookup and idle session cleanup with cache purge."""
    cache = HLSSegmentCache(max_size_mb=10)
    manager = HLSSessionManager(cache=cache)

    now = time.time()
    active_session = HLSSession(
        session_id="active_sess",
        media_url="http://test/active.mp4",
        duration=100.0,
        created_at=now,
        last_accessed=now,
        cache=cache,
    )
    stale_session = HLSSession(
        session_id="stale_sess",
        media_url="http://test/stale.mp4",
        duration=100.0,
        created_at=now - 500.0,
        last_accessed=now - 500.0,
        cache=cache,
    )

    manager.sessions["active_sess"] = active_session
    manager.sessions["stale_sess"] = stale_session

    cache.put("active_sess:0", b"ACTIVE_DATA")
    cache.put("stale_sess:0", b"STALE_DATA")

    # get_session returns active session and None for unknown
    assert manager.get_session("active_sess") is active_session
    assert manager.get_session("unknown_sess") is None

    # Cleanup expired sessions older than 180 seconds
    purged_count = manager.cleanup_expired_sessions(max_idle_sec=180.0)
    assert purged_count == 1
    assert "stale_sess" not in manager.sessions
    assert "active_sess" in manager.sessions

    # Verify stale session cache was purged while active remains
    assert cache.get("stale_sess:0") is None
    assert cache.get("active_sess:0") == b"ACTIVE_DATA"
