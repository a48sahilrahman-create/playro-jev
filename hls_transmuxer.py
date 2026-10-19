"""Dynamic VOD HLS Transmuxer for Playro JEV.

Converts high-bitrate remote or local media streams on-the-fly into RFC 8216
compliant HLS (HTTP Live Streaming) VOD playlists with dynamic MPEG-TS chunk slicing,
audio track selection, volume boosting, memory-bounded LRU segment caching,
and speculative background prebuffering.
"""

import asyncio
from collections import OrderedDict
import hashlib
import logging
import math
import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from audio_probe import find_ffmpeg, parse_audio_streams, probe_audio_streams
except ImportError:
    try:
        from .audio_probe import find_ffmpeg, parse_audio_streams, probe_audio_streams
    except ImportError:
        def find_ffmpeg() -> str:
            """Fallback FFmpeg path locator."""
            which_path = shutil.which("ffmpeg")
            return which_path if which_path else "ffmpeg"

        def parse_audio_streams(probe_text: str, url: str = "") -> Dict[str, Any]:
            """Fallback empty audio parser."""
            return {"url": url, "duration_seconds": 0.0, "streams": [], "codecs": []}

        async def probe_audio_streams(url_or_content: str, timeout: float = 10.0) -> Dict[str, Any]:
            """Fallback empty audio stream probe."""
            return {"url": url_or_content, "duration_seconds": 0.0, "streams": [], "codecs": []}

logger = logging.getLogger("hls_transmuxer")


class HLSSegmentCache:
    """In-memory bounded LRU cache storing segment_key -> bytes.

    Evicts least recently used segments when total stored payload exceeds max_size_mb.
    """

    def __init__(self, max_size_mb: int = 150) -> None:
        self.max_size_mb: int = max_size_mb
        self.max_size_bytes: int = max_size_mb * 1024 * 1024
        self.current_size: int = 0
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    def get(self, key: str) -> Optional[bytes]:
        """Retrieves a cached segment, promoting it to most recently used."""
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key: str, data: bytes) -> None:
        """Stores a segment payload, executing LRU eviction if capacity is exceeded."""
        if not isinstance(data, (bytes, bytearray)):
            return
        data_bytes = bytes(data)
        data_len = len(data_bytes)

        if data_len > self.max_size_bytes:
            logger.warning(
                "Segment %s (%d bytes) exceeds total cache capacity (%d bytes); skipping.",
                key,
                data_len,
                self.max_size_bytes,
            )
            return

        if key in self._cache:
            self.current_size -= len(self._cache[key])
            del self._cache[key]

        while self._cache and (self.current_size + data_len > self.max_size_bytes):
            evicted_key, evicted_data = self._cache.popitem(last=False)
            self.current_size -= len(evicted_data)
            logger.debug("LRU evicted segment %s (%d bytes)", evicted_key, len(evicted_data))

        self._cache[key] = data_bytes
        self.current_size += data_len

    def purge_session(self, session_id: str) -> int:
        """Purges all cached segments associated with the given session ID."""
        prefix = f"{session_id}:"
        to_remove = [k for k in self._cache if k.startswith(prefix)]
        for k in to_remove:
            self.current_size -= len(self._cache[k])
            del self._cache[k]
        return len(to_remove)

    def clear(self) -> None:
        """Clears all stored segments and resets byte counter."""
        self._cache.clear()
        self.current_size = 0

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        return key in self._cache


# Global segment cache singleton (150 MB ceiling)
HLS_SEGMENT_CACHE = HLSSegmentCache(max_size_mb=150)


class HLSSession:
    """Represents a dynamic VOD HLS transmuxing session for a specific media source and configuration."""

    def __init__(
        self,
        session_id: str,
        media_url: str,
        duration: float,
        audio_track: int = 0,
        volume_boost: float = 1.0,
        is_audio_aac: bool = False,
        segment_duration: float = 4.0,
        created_at: Optional[float] = None,
        last_accessed: Optional[float] = None,
        cache: Optional[HLSSegmentCache] = None,
    ) -> None:
        self.session_id: str = session_id
        self.media_url: str = media_url
        self.duration: float = float(duration)
        self.audio_track: int = int(audio_track)
        self.volume_boost: float = float(volume_boost)
        self.is_audio_aac: bool = bool(is_audio_aac)
        self.segment_duration: float = float(segment_duration)

        now = time.time()
        self.created_at: float = created_at if created_at is not None else now
        self.last_accessed: float = last_accessed if last_accessed is not None else now
        self.cache: HLSSegmentCache = cache if cache is not None else HLS_SEGMENT_CACHE
        self._inflight: Dict[str, asyncio.Task] = {}

    def generate_master_manifest(self, base_url: str = "") -> str:
        """Returns standard RFC 8216 master playlist pointing to this session's media manifest."""
        base = base_url.rstrip("/") if base_url else ""
        manifest_url = f"{base}/api/hls/{self.session_id}/index.m3u8"
        return (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n"
            f"{manifest_url}\n"
        )

    def generate_media_manifest(self, base_url: str = "") -> str:
        """Calculates total segments and generates an RFC 8216 compliant VOD media playlist."""
        total_segments = max(1, math.ceil(self.duration / self.segment_duration))
        target_duration = int(self.segment_duration) + 1

        lines: List[str] = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{target_duration}",
            "#EXT-X-MEDIA-SEQUENCE:0",
            "#EXT-X-PLAYLIST-TYPE:VOD",
        ]

        base = base_url.rstrip("/") if base_url else ""
        for i in range(total_segments):
            if i < total_segments - 1:
                seg_dur = self.segment_duration
            else:
                remaining = self.duration - (i * self.segment_duration)
                seg_dur = remaining if remaining > 0 else self.segment_duration

            lines.append(f"#EXTINF:{seg_dur:.3f},")
            if base:
                lines.append(f"{base}/segment_{i}.ts")
            else:
                lines.append(f"segment_{i}.ts")

        lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    async def get_or_generate_segment(self, segment_idx: int) -> bytes:
        """Retrieves segment from cache if present; otherwise slices it with FFmpeg and caches it."""
        self.last_accessed = time.time()
        cache_key = f"{self.session_id}:{segment_idx}"

        cached_data = self.cache.get(cache_key)
        if cached_data is not None:
            # Trigger speculative prebuffering for upcoming chunks
            self._schedule_prebuffer(segment_idx)
            return cached_data

        if cache_key in self._inflight:
            return await self._inflight[cache_key]

        task = asyncio.create_task(self._slice_segment_ffmpeg(segment_idx))
        self._inflight[cache_key] = task
        try:
            data = await task
            if data:
                self.cache.put(cache_key, data)
        finally:
            self._inflight.pop(cache_key, None)

        self._schedule_prebuffer(segment_idx)
        return data

    def _schedule_prebuffer(self, segment_idx: int) -> None:
        """Safely fires speculative prebuffer task on the running event loop."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._prebuffer(segment_idx))
        except RuntimeError:
            pass

    async def _prebuffer(self, segment_idx: int) -> None:
        """Asynchronously pre-slices segments idx+1 and idx+2 into cache."""
        total_segments = max(1, math.ceil(self.duration / self.segment_duration))
        for offset in (1, 2):
            next_idx = segment_idx + offset
            if 0 <= next_idx < total_segments:
                next_key = f"{self.session_id}:{next_idx}"
                if self.cache.get(next_key) is not None or next_key in self._inflight:
                    continue

                try:
                    task = asyncio.create_task(self._slice_segment_ffmpeg(next_idx))
                    self._inflight[next_key] = task
                    next_data = await task
                    if next_data:
                        self.cache.put(next_key, next_data)
                except Exception as e:
                    logger.debug(
                        "Prebuffer failed for segment %d of session %s: %s",
                        next_idx,
                        self.session_id,
                        e,
                    )
                finally:
                    self._inflight.pop(next_key, None)

    async def _slice_segment_ffmpeg(self, segment_idx: int) -> bytes:
        """Invokes FFmpeg to slice an MPEG-TS segment directly from the source stream."""
        ss = max(0.0, segment_idx * self.segment_duration)
        ffmpeg_bin = find_ffmpeg()

        cmd: List[str] = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel", "error",
        ]

        # Protocol reconnect options are valid for network streams (http/https)
        if self.media_url.startswith(("http://", "https://")):
            cmd.extend([
                "-reconnect", "1",
                "-reconnect_at_eof", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "2",
            ])

        cmd.extend([
            "-ss", f"{ss:.3f}",
            "-i", self.media_url,
            "-t", f"{self.segment_duration + 0.05:.3f}",
            "-map", "0:v:0",
            "-map", f"0:a:{self.audio_track}?",
            "-c:v", "copy",
        ])

        # Audio stream handling:
        # If volume boost is requested, audio must be decoded through the filtergraph.
        # Otherwise, copy directly if already AAC or transcode to AAC 192k stereo.
        if self.volume_boost != 1.0:
            cmd.extend([
                "-c:a", "aac",
                "-b:a", "192k",
                "-ac", "2",
                "-af", f"volume={self.volume_boost}",
            ])
        elif self.is_audio_aac:
            cmd.extend(["-c:a", "copy"])
        else:
            cmd.extend([
                "-c:a", "aac",
                "-b:a", "192k",
                "-ac", "2",
            ])

        cmd.extend([
            "-output_ts_offset", f"{ss:.3f}",
            "-f", "mpegts",
            "pipe:1",
        ])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45.0)
            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.error(
                    "FFmpeg slice failed for segment %d (exit code %d): %s",
                    segment_idx,
                    proc.returncode,
                    err_msg,
                )
                return stdout or b""
            return stdout
        except asyncio.TimeoutError:
            logger.error(
                "FFmpeg slice segment %d timed out for session %s",
                segment_idx,
                self.session_id,
            )
            try:
                proc.kill()
            except Exception:
                pass
            return b""
        except Exception as e:
            logger.error("FFmpeg slice segment %d failed: %s", segment_idx, e)
            return b""


class HLSSessionManager:
    """Manages active HLS sessions, lifecycle tracking, and idle session garbage collection."""

    def __init__(self, cache: Optional[HLSSegmentCache] = None) -> None:
        self.sessions: Dict[str, HLSSession] = {}
        self.cache: HLSSegmentCache = cache if cache is not None else HLS_SEGMENT_CACHE

    async def get_or_create_session(
        self,
        media_url: str,
        audio_track: int = 0,
        volume_boost: float = 1.0,
    ) -> HLSSession:
        """Retrieves an existing session or probes the media URL and creates a new HLSSession."""
        session_key = f"{media_url}|{audio_track}|{volume_boost:.4f}"
        session_id = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:16]

        if session_id in self.sessions:
            session = self.sessions[session_id]
            session.last_accessed = time.time()
            return session

        # Probe duration and audio codecs
        probe_res: Dict[str, Any] = {}
        try:
            probe_res = await probe_audio_streams(media_url)
        except Exception as e:
            logger.warning("probe_audio_streams failed for %s: %s", media_url, e)

        duration = probe_res.get("duration_seconds", 0.0) if probe_res else 0.0
        if not duration or duration <= 0:
            duration = 7200.0

        streams = probe_res.get("streams", []) if probe_res else []
        is_audio_aac = False
        if 0 <= audio_track < len(streams):
            track_codec = str(streams[audio_track].get("codec", "")).lower()
            is_audio_aac = (track_codec == "aac")
        elif not streams and probe_res:
            codecs = [str(c).lower() for c in probe_res.get("codecs", [])]
            if "aac" in codecs:
                is_audio_aac = True

        now = time.time()
        session = HLSSession(
            session_id=session_id,
            media_url=media_url,
            duration=duration,
            audio_track=audio_track,
            volume_boost=volume_boost,
            is_audio_aac=is_audio_aac,
            segment_duration=4.0,
            created_at=now,
            last_accessed=now,
            cache=self.cache,
        )
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[HLSSession]:
        """Retrieves an existing session by ID, updating its last_accessed timestamp."""
        session = self.sessions.get(session_id)
        if session:
            session.last_accessed = time.time()
        return session

    def cleanup_expired_sessions(self, max_idle_sec: float = 180.0) -> int:
        """Purges sessions inactive for more than max_idle_sec along with cached segments."""
        now = time.time()
        expired_ids = [
            sid for sid, s in self.sessions.items()
            if (now - s.last_accessed) > max_idle_sec
        ]
        for sid in expired_ids:
            del self.sessions[sid]
            self.cache.purge_session(sid)

        if expired_ids:
            logger.info("Purged %d expired HLS session(s): %s", len(expired_ids), expired_ids)
        return len(expired_ids)


# Global session manager singleton
HLS_SESSION_MANAGER = HLSSessionManager(cache=HLS_SEGMENT_CACHE)
