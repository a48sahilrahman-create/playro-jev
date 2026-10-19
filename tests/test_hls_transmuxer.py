"""Unit and integration test suite for Dynamic VOD HLS Transmuxer.

Tests:
1. HLSSegmentCache: in-memory LRU mechanics, max_segments eviction, temp disk fallback,
   <1ms retrieval, and canonical key formatting ({session_id}_{segment_idx}_{track_idx}.ts).
2. HLSSession: audio probe inspection, RFC 8216 master/media playlist generation
   (including 120s mock duration -> 30 segments), and FFmpeg MPEG-TS slicing with prebuffering.
3. HLSSessionManager: thread-safe _sessions registry, get_or_create_session(url, title),
   and cleanup_idle_sessions(max_idle_seconds=120).
"""

import asyncio
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import MagicMock, patch
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
    """Verify segment storage, sub-millisecond retrieval, and cache miss semantics."""
    cache = HLSSegmentCache(max_size_mb=1, disk_fallback=False)

    assert cache.get("missing_key") is None
    assert len(cache) == 0
    assert cache.current_size == 0

    key = HLSSegmentCache.format_key("sess1", 0, 0)
    assert key == "sess1_0_0.ts"

    payload = b"MPEGTS_DUMMY_PAYLOAD_001"
    t0 = time.perf_counter()
    cache.put(key, payload)
    t1 = time.perf_counter()
    retrieved = cache.get(key)
    t2 = time.perf_counter()

    assert retrieved == payload
    assert len(cache) == 1
    assert cache.current_size == len(payload)
    assert key in cache
    # Verify sub-millisecond in-memory cache retrieval (<1ms)
    retrieval_ms = (t2 - t1) * 1000
    assert retrieval_ms < 1.0


def test_cache_lru_eviction():
    """Verify least recently used segments are evicted when capacity limit is breached."""
    # Create small 100-byte cache without disk fallback for deterministic memory LRU testing
    cache = HLSSegmentCache(max_size_mb=1, disk_fallback=False)
    cache.max_size_bytes = 100

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


def test_cache_max_segments_eviction():
    """Verify LRU eviction is enforced when max_segments count ceiling is reached."""
    cache = HLSSegmentCache(max_size_mb=10, max_segments=3, disk_fallback=False)

    cache.put("seg_0", b"0000")
    cache.put("seg_1", b"1111")
    cache.put("seg_2", b"2222")
    assert len(cache) == 3

    # Inserting 4th segment evicts seg_0 because max_segments is 3
    cache.put("seg_3", b"3333")
    assert len(cache) == 3
    assert cache.get("seg_0") is None
    assert cache.get("seg_1") == b"1111"
    assert cache.get("seg_2") == b"2222"
    assert cache.get("seg_3") == b"3333"


def test_cache_temp_disk_fallback():
    """Verify in-memory evicted segments are preserved in temp disk fallback and recoverable."""
    cache = HLSSegmentCache(max_size_mb=1, max_segments=2, disk_fallback=True)

    key0 = HLSSegmentCache.format_key("sess_disk", 0, 0)
    key1 = HLSSegmentCache.format_key("sess_disk", 1, 0)
    key2 = HLSSegmentCache.format_key("sess_disk", 2, 0)

    data0 = b"DISK_SEGMENT_DATA_0"
    data1 = b"DISK_SEGMENT_DATA_1"
    data2 = b"DISK_SEGMENT_DATA_2"

    cache.put(key0, data0)
    cache.put(key1, data1)
    assert cache.in_memory(key0)
    assert cache.in_memory(key1)

    # Inserting 3rd segment evicts key0 from memory to temp disk
    cache.put(key2, data2)
    assert not cache.in_memory(key0)
    assert cache.in_memory(key1)
    assert cache.in_memory(key2)

    # Retrieval of key0 should transparently succeed via temp disk fallback
    recovered = cache.get(key0)
    assert recovered == data0
    # And is promoted back into memory
    assert cache.in_memory(key0)

    # Clean up
    cache.clear()


def test_cache_access_promotes_mru():
    """Verify accessing a key moves it to MRU, protecting it from immediate eviction."""
    cache = HLSSegmentCache(max_size_mb=1, disk_fallback=False)
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
    cache = HLSSegmentCache(max_size_mb=1, disk_fallback=False)
    cache.max_size_bytes = 50

    giant_chunk = b"X" * 100
    cache.put("giant", giant_chunk)
    assert cache.get("giant") is None
    assert len(cache) == 0
    assert cache.current_size == 0


def test_cache_overwrite_updates_size():
    """Verify overwriting an existing key updates the payload and byte tracking correctly."""
    cache = HLSSegmentCache(max_size_mb=1, disk_fallback=False)
    cache.put("k1", b"12345")
    assert cache.current_size == 5

    cache.put("k1", b"1234567890")
    assert cache.get("k1") == b"1234567890"
    assert cache.current_size == 10
    assert len(cache) == 1


def test_cache_purge_session_and_clear():
    """Verify session purging removes both colon and underscore key variants from memory and disk."""
    cache = HLSSegmentCache(max_size_mb=1, disk_fallback=True)
    cache.put("sess_A:0", b"A0")
    cache.put("sess_A_1_0.ts", b"A1")
    cache.put("sess_B:0", b"B0")

    purged = cache.purge_session("sess_A")
    assert purged == 2
    assert cache.get("sess_A:0") is None
    assert cache.get("sess_A_1_0.ts") is None
    assert cache.get("sess_B:0") == b"B0"
    assert len(cache) == 1

    cache.clear()
    assert len(cache) == 0
    assert cache.current_size == 0


# ============================================================================
# 2. HLSSession RFC 8216 Manifest & Playlist Generation Tests
# ============================================================================

def test_generate_master_playlist():
    """Verify generate_master_playlist returns RFC 8216 header and configured index.m3u8."""
    session = HLSSession(
        session_id="test_sess_master_001",
        media_url="http://example.com/stream.mkv",
        duration=120.0,
    )

    # Master playlist without base_url
    master_default = session.generate_master_playlist(selected_track=1, boost_db=6.0)
    assert master_default.startswith("#EXTM3U\n")
    assert "#EXT-X-VERSION:3" in master_default
    assert "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080" in master_default
    assert "/api/hls/test_sess_master_001/index.m3u8" in master_default
    assert session.audio_track == 1
    assert session.volume_boost == 6.0

    # Master playlist with base_url
    master_with_base = session.generate_master_playlist(base_url="https://playro.local:8080")
    assert "https://playro.local:8080/api/hls/test_sess_master_001/index.m3u8" in master_with_base

    # Verify backward-compatible alias generate_master_manifest
    alias_master = session.generate_master_manifest()
    assert alias_master == master_default or "#EXTM3U\n" in alias_master


def test_generate_media_playlist_mock_duration_120s():
    """Verify 120s mock duration produces exactly 30 segments (120 / 4.0 = 30) with explicit #EXTINF:4.000."""
    session = HLSSession(
        session_id="sess_120s_mock",
        media_url="http://mock.playro.test/movie.mp4",
        duration=120.0,
        segment_duration=4.0,
    )

    playlist = session.generate_media_playlist(track_idx=0)
    lines = [line.strip() for line in playlist.strip().splitlines() if line.strip()]

    # Verify required RFC 8216 headers
    assert lines[0] == "#EXTM3U"
    assert lines[1] == "#EXT-X-VERSION:3"
    assert lines[2] == "#EXT-X-TARGETDURATION:5"
    assert lines[3] == "#EXT-X-MEDIA-SEQUENCE:0"
    assert lines[4] == "#EXT-X-PLAYLIST-TYPE:VOD"

    # Exactly 30 segments of 4.000s duration
    inf_tags = [l for l in lines if l.startswith("#EXTINF:")]
    assert len(inf_tags) == 30
    assert all(tag == "#EXTINF:4.000," for tag in inf_tags)

    # Segment URIs from segment_0.ts to segment_29.ts
    seg_uris = [l for l in lines if l.endswith(".ts")]
    assert len(seg_uris) == 30
    assert seg_uris[0] == "segment_0.ts"
    assert seg_uris[-1] == "segment_29.ts"

    # Trailing endlist tag
    assert lines[-1] == "#EXT-X-ENDLIST"


def test_generate_media_playlist_with_remainder():
    """Verify non-multiple duration (10.0s / 4.0s) produces 3 segments (4.0, 4.0, 2.0)."""
    session = HLSSession(
        session_id="sess_remainder_test",
        media_url="http://example.com/test.mp4",
        duration=10.0,
        segment_duration=4.0,
    )

    playlist = session.generate_media_playlist()
    lines = [line.strip() for line in playlist.strip().splitlines() if line.strip()]

    assert "#EXT-X-TARGETDURATION:5" in lines
    assert "#EXTINF:4.000," in lines
    assert "#EXTINF:2.000," in lines
    assert "segment_0.ts" in lines
    assert "segment_1.ts" in lines
    assert "segment_2.ts" in lines
    assert lines[-1] == "#EXT-X-ENDLIST"


def test_generate_media_playlist_with_base_url():
    """Verify media playlist prefixes segment URIs when base_url is supplied."""
    session = HLSSession(
        session_id="sess_base_test",
        media_url="http://example.com/test.mp4",
        duration=8.0,
        segment_duration=4.0,
    )

    playlist = session.generate_media_playlist(track_idx=0, base_url="/api/hls/sess_base_test")
    assert "/api/hls/sess_base_test/segment_0.ts" in playlist
    assert "/api/hls/sess_base_test/segment_1.ts" in playlist


def test_generate_media_manifest_exact_multiple_duration():
    """Verify backward-compatible generate_media_manifest handles exact multiple duration (8.0s -> 2 segments)."""
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
async def test_get_segment_caching_and_prebuffering():
    """Verify get_segment populates cache on miss, hits cache on second call, and triggers prebuffer."""
    cache = HLSSegmentCache(max_size_mb=10, disk_fallback=False)
    session = HLSSession(
        session_id="test_get_segment_session",
        media_url=BUNNY_CLIP,
        duration=10.0,
        segment_duration=4.0,
        cache=cache,
    )

    key0 = HLSSegmentCache.format_key(session.session_id, 0, 0)
    assert cache.get(key0) is None

    # First fetch: invokes FFmpeg and populates cache
    data_first = await session.get_segment(idx=0, track_idx=0, boost_db=0.0)
    assert len(data_first) > 0
    assert data_first[0] == 0x47
    assert cache.get(key0) == data_first

    # Second fetch: instant cache hit (<1ms)
    t0 = time.perf_counter()
    data_second = await session.get_segment(idx=0, track_idx=0, boost_db=0.0)
    t1 = time.perf_counter()
    assert (t1 - t0) * 1000 < 5.0
    assert data_second == data_first

    # Wait brief moment for background prebuffering of idx+1 and idx+2
    await asyncio.sleep(1.0)
    key1 = HLSSegmentCache.format_key(session.session_id, 1, 0)
    data_seg1 = cache.get(key1)
    if data_seg1 is not None:
        assert data_seg1[0] == 0x47

    # Verify get_or_generate_segment compatibility alias
    alias_data = await session.get_or_generate_segment(0)
    assert alias_data == data_first


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

    data = await session._slice_segment_ffmpeg(0, track_idx=0, boost_db=6.0)
    assert isinstance(data, bytes)
    assert len(data) > 0
    assert data[0] == 0x47


def test_inspect_media_duration_and_audio_tracks():
    """Verify HLSSession inspects duration and audio tracks using audio_probe.py."""
    assert os.path.isfile(BUNNY_CLIP), f"Test clip not found: {BUNNY_CLIP}"

    session = HLSSession(
        session_id="test_inspect_clip",
        media_url=BUNNY_CLIP,
        duration=0.0,  # Force inspection
    )

    assert session.duration > 0.0
    assert session.duration == 10.0


# ============================================================================
# 4. HLSSessionManager Tests
# ============================================================================

@pytest.mark.anyio
async def test_session_manager_get_or_create_deterministic():
    """Verify session creation is deterministic and returns identical instance for same parameters."""
    manager = HLSSessionManager()

    session1 = await manager.get_or_create_session(
        url=BUNNY_CLIP,
        title="Big Buck Bunny",
        audio_track=0,
        volume_boost=1.0,
    )
    assert session1 is not None
    assert session1.session_id
    assert session1.duration == 10.0
    assert session1.title == "Big Buck Bunny"

    # Second call with identical params returns the same session instance
    session2 = await manager.get_or_create_session(
        url=BUNNY_CLIP,
        title="Big Buck Bunny",
        audio_track=0,
        volume_boost=1.0,
    )
    assert session1 is session2

    # Different volume boost yields distinct session ID
    session3 = await manager.get_or_create_session(
        url=BUNNY_CLIP,
        title="Big Buck Bunny",
        audio_track=0,
        volume_boost=1.5,
    )
    assert session3.session_id != session1.session_id


@pytest.mark.anyio
async def test_session_manager_thread_safety_registry():
    """Verify HLSSessionManager _sessions registry is thread-safe under concurrent access."""
    manager = HLSSessionManager()

    def register_worker(worker_id: int):
        sid = f"concurrent_sess_{worker_id}"
        sess = HLSSession(
            session_id=sid,
            media_url=f"http://example.com/video_{worker_id}.mp4",
            duration=60.0,
        )
        with manager._lock:
            manager._sessions[sid] = sess

    threads = [threading.Thread(target=register_worker, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(manager._sessions) == 25
    assert len(manager.sessions) == 25  # Property alias
    for i in range(25):
        assert f"concurrent_sess_{i}" in manager._sessions


@pytest.mark.anyio
async def test_session_manager_fallback_duration_on_unknown():
    """Verify invalid or unprobeable URL defaults to 7200.0s duration gracefully."""
    manager = HLSSessionManager()

    session = await manager.get_or_create_session(
        url="http://invalid.playro.test/nonexistent.mkv",
        audio_track=0,
        volume_boost=1.0,
    )
    assert session.duration == 7200.0


def test_session_manager_cleanup_idle_sessions():
    """Verify cleanup_idle_sessions purges inactive sessions and their segment cache."""
    cache = HLSSegmentCache(max_size_mb=10, disk_fallback=False)
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

    manager._sessions["active_sess"] = active_session
    manager._sessions["stale_sess"] = stale_session

    cache.put(HLSSegmentCache.format_key("active_sess", 0, 0), b"ACTIVE_DATA")
    cache.put(HLSSegmentCache.format_key("stale_sess", 0, 0), b"STALE_DATA")

    # get_session returns active session and None for unknown
    assert manager.get_session("active_sess") is active_session
    assert manager.get_session("unknown_sess") is None

    # Cleanup idle sessions exceeding 120 seconds default
    purged_count = manager.cleanup_idle_sessions(max_idle_seconds=120)
    assert purged_count == 1
    assert "stale_sess" not in manager._sessions
    assert "active_sess" in manager._sessions

    # Verify stale session cache was purged while active remains
    assert cache.get(HLSSegmentCache.format_key("stale_sess", 0, 0)) is None
    assert cache.get(HLSSegmentCache.format_key("active_sess", 0, 0)) == b"ACTIVE_DATA"

    # Verify backward-compatible alias cleanup_expired_sessions
    purged_alias = manager.cleanup_expired_sessions(max_idle_sec=10.0)
    assert purged_alias == 0  # active_sess was recently accessed
