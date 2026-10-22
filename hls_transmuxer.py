"""Dynamic VOD HLS Transmuxer for Playro JEV.

Converts high-bitrate remote or local media streams on-the-fly into RFC 8216
compliant HLS (HTTP Live Streaming) VOD playlists with dynamic MPEG-TS chunk slicing,
audio track selection, volume boosting, memory-bounded LRU segment caching with temp disk fallback,
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
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from fastapi import HTTPException
except ImportError:
    class HTTPException(Exception):  # type: ignore
        def __init__(self, status_code: int = 502, detail: str = ""):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

try:
    from audio_probe import find_ffmpeg, find_ffprobe, parse_audio_streams, probe_audio_streams
except ImportError:
    try:
        from .audio_probe import find_ffmpeg, find_ffprobe, parse_audio_streams, probe_audio_streams
    except ImportError:
        KNOWN_FFMPEG_PATHS: List[str] = [
            r"C:\Program Files (x86)\XDM\ffmpeg.exe",
            r"C:\Program Files\Softdeluxe\Free Download Manager\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
            r"C:\Users\sahil\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
            os.path.expandvars(r"%USERPROFILE%\AppData\Local\CapCut\Apps\2.3.0.569\ffmpeg.exe"),
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",
        ]

        KNOWN_FFPROBE_PATHS: List[str] = [
            r"C:\Program Files (x86)\XDM\ffprobe.exe",
            r"C:\Program Files\Softdeluxe\Free Download Manager\ffprobe.exe",
            r"C:\ffmpeg\bin\ffprobe.exe",
            r"C:\ProgramData\chocolatey\bin\ffprobe.exe",
            r"C:\Users\sahil\AppData\Local\Microsoft\WinGet\Links\ffprobe.exe",
            r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffprobe.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffprobe.exe"),
            os.path.expandvars(r"%USERPROFILE%\AppData\Local\CapCut\Apps\2.3.0.569\ffprobe.exe"),
            "/usr/bin/ffprobe",
            "/usr/local/bin/ffprobe",
            "/opt/homebrew/bin/ffprobe",
        ]

        def find_ffmpeg() -> str:
            """Fallback FFmpeg path locator."""
            which_path = shutil.which("ffmpeg")
            if which_path and os.path.isfile(which_path):
                return os.path.abspath(which_path)
            for candidate in KNOWN_FFMPEG_PATHS:
                if candidate and os.path.isfile(candidate):
                    return os.path.abspath(candidate)
            return "ffmpeg"

        def find_ffprobe() -> str:
            """Fallback ffprobe path locator."""
            which_path = shutil.which("ffprobe")
            if which_path and os.path.isfile(which_path):
                return os.path.abspath(which_path)
            for candidate in KNOWN_FFPROBE_PATHS:
                if candidate and os.path.isfile(candidate):
                    return os.path.abspath(candidate)
            ffmpeg_bin = find_ffmpeg()
            if ffmpeg_bin and ffmpeg_bin != "ffmpeg":
                sibling = os.path.join(os.path.dirname(ffmpeg_bin), "ffprobe.exe" if os.name == "nt" else "ffprobe")
                if os.path.isfile(sibling):
                    return os.path.abspath(sibling)
            return "ffprobe"

        def parse_audio_streams(probe_text: str, url: str = "") -> Dict[str, Any]:
            """Fallback empty audio parser."""
            return {"url": url, "duration_seconds": 0.0, "streams": [], "codecs": []}

        async def probe_audio_streams(url_or_content: str, timeout: float = 10.0) -> Dict[str, Any]:
            """Fallback empty audio stream probe."""
            return {"url": url_or_content, "duration_seconds": 0.0, "streams": [], "codecs": []}

logger = logging.getLogger("hls_transmuxer")


class HLSSegmentCache:
    """In-memory bounded LRU cache with temp disk fallback storing segment_key -> bytes.

    Defaults to 150MB capacity and up to 200 segments with sub-millisecond retrieval.
    Key format: {session_id}_{segment_idx}_{track_idx}.ts
    """

    def __init__(
        self,
        max_size_mb: int = 150,
        max_segments: int = 200,
        temp_dir: Optional[str] = None,
        disk_fallback: bool = True,
    ) -> None:
        self.max_size_mb: int = max_size_mb
        self.max_size_bytes: int = max_size_mb * 1024 * 1024
        self.max_segments: int = max_segments
        self.disk_fallback: bool = disk_fallback
        self.temp_dir: str = temp_dir or os.path.join(tempfile.gettempdir(), "playro_hls_cache")
        try:
            os.makedirs(self.temp_dir, exist_ok=True)
        except Exception:
            pass

        self.current_size: int = 0
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def format_key(session_id: str, segment_idx: int, track_idx: int = 0) -> str:
        """Returns canonical segment key: {session_id}_{segment_idx}_{track_idx}.ts"""
        return f"{session_id}_{segment_idx}_{track_idx}.ts"

    def _disk_path(self, key: str) -> str:
        safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', key)
        if not safe_name.endswith(".ts"):
            safe_name += ".ts"
        return os.path.join(self.temp_dir, safe_name)

    def _save_to_disk(self, key: str, data: bytes) -> None:
        if not self.disk_fallback or not self.temp_dir:
            return
        try:
            p = self._disk_path(key)
            with open(p, "wb") as f:
                f.write(data)
        except Exception as e:
            logger.debug("Failed saving segment %s to temp disk: %s", key, e)

    def in_memory(self, key: str) -> bool:
        """Checks if a segment is actively held in the in-memory LRU tier."""
        with self._lock:
            return key in self._cache

    def get(self, key: str) -> Optional[bytes]:
        """Retrieves a cached segment, promoting it to MRU (<1ms in-memory LRU).

        If missing in memory and disk_fallback is enabled, attempts temp disk recovery.
        """
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

            if self.disk_fallback:
                p = self._disk_path(key)
                if os.path.isfile(p):
                    try:
                        with open(p, "rb") as f:
                            data = f.read()
                        if data:
                            self.put(key, data, save_disk=False)
                            return data
                    except Exception as e:
                        logger.debug("Failed reading segment %s from temp disk: %s", key, e)

            return None

    def put(self, key: str, data: bytes, save_disk: bool = True) -> None:
        """Stores a segment payload, executing LRU eviction if size or count capacity is exceeded."""
        if not isinstance(data, (bytes, bytearray)):
            return
        data_bytes = bytes(data)
        data_len = len(data_bytes)

        with self._lock:
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

            # Evict LRU items if byte capacity or segment count ceiling is reached
            while self._cache and (
                (self.current_size + data_len > self.max_size_bytes)
                or (len(self._cache) >= self.max_segments)
            ):
                evicted_key, evicted_data = self._cache.popitem(last=False)
                self.current_size -= len(evicted_data)
                if self.disk_fallback:
                    self._save_to_disk(evicted_key, evicted_data)
                logger.debug("LRU evicted segment %s (%d bytes)", evicted_key, len(evicted_data))

            self._cache[key] = data_bytes
            self.current_size += data_len

            if self.disk_fallback and save_disk:
                self._save_to_disk(key, data_bytes)

    def purge_session(self, session_id: str) -> int:
        """Purges all cached segments associated with the given session ID from memory and disk."""
        with self._lock:
            prefix_underscore = f"{session_id}_"
            prefix_colon = f"{session_id}:"
            to_remove = [
                k for k in self._cache
                if k.startswith(prefix_underscore) or k.startswith(prefix_colon)
            ]
            for k in to_remove:
                self.current_size -= len(self._cache[k])
                del self._cache[k]

            # Clean disk fallback files
            if self.temp_dir and os.path.isdir(self.temp_dir):
                try:
                    for fname in os.listdir(self.temp_dir):
                        if fname.startswith(prefix_underscore) or fname.startswith(prefix_colon):
                            try:
                                os.remove(os.path.join(self.temp_dir, fname))
                            except Exception:
                                pass
                except Exception:
                    pass

            return len(to_remove)

    def clear(self) -> None:
        """Clears all stored segments and resets byte counter and temp directory."""
        with self._lock:
            self._cache.clear()
            self.current_size = 0
            if self.temp_dir and os.path.isdir(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)
                os.makedirs(self.temp_dir, exist_ok=True)

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._cache or (
                self.disk_fallback and os.path.isfile(self._disk_path(key))
            )


# Global segment cache singleton (150 MB ceiling, 200 segments max)
HLS_SEGMENT_CACHE = HLSSegmentCache(max_size_mb=150, max_segments=200, disk_fallback=True)


class HLSSession:
    """Represents a dynamic VOD HLS transmuxing session for a specific media source and configuration."""

    def __init__(
        self,
        session_id: str,
        media_url: str,
        duration: float = 0.0,
        audio_track: int = 0,
        volume_boost: float = 1.0,
        is_audio_aac: bool = False,
        segment_duration: float = 4.0,
        title: str = "",
        created_at: Optional[float] = None,
        last_accessed: Optional[float] = None,
        cache: Optional[HLSSegmentCache] = None,
        streams: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.session_id: str = session_id
        self.media_url: str = media_url
        self.duration: float = float(duration)
        self.audio_track: int = int(audio_track)
        self.volume_boost: float = float(volume_boost)
        self.is_audio_aac: bool = bool(is_audio_aac)
        self.segment_duration: float = float(segment_duration)
        self.title: str = title
        self.streams: List[Dict[str, Any]] = streams or []

        now = time.time()
        self.created_at: float = created_at if created_at is not None else now
        self.last_accessed: float = last_accessed if last_accessed is not None else now
        self.cache: HLSSegmentCache = cache if cache is not None else HLS_SEGMENT_CACHE
        self._inflight: Dict[str, asyncio.Task] = {}
        self.health: str = "healthy"
        self.status: str = "healthy"
        self.last_error: Optional[str] = None
        self.failed_segments: List[int] = []

        # Auto-inspect stream duration and audio tracks if duration is missing
        if self.duration <= 0.0 and self.media_url:
            self.inspect_media()

    def get_status(self) -> Dict[str, Any]:
        """Returns active HLS session telemetry, health status, and failure tracking."""
        total_segments = max(1, math.ceil(self.duration / self.segment_duration))
        cached_count = 0
        if hasattr(self.cache, "_cache"):
            with getattr(self.cache, "_lock", threading.RLock()):
                prefix_col = f"{self.session_id}:"
                prefix_und = f"{self.session_id}_"
                cached_count = sum(
                    1 for k in self.cache._cache
                    if k.startswith(prefix_col) or k.startswith(prefix_und)
                )
        codecs = ["aac"] if self.is_audio_aac else ["copy"]
        return {
            "session_id": self.session_id,
            "health": self.health,
            "status": self.status,
            "duration": self.duration,
            "audio_track": self.audio_track,
            "volume_boost": self.volume_boost,
            "segment_count": total_segments,
            "segment_duration": self.segment_duration,
            "is_audio_aac": self.is_audio_aac,
            "cached_segments": cached_count,
            "codecs": codecs,
            "last_error": self.last_error,
            "failed_segments": list(self.failed_segments),
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
        }

    def inspect_media(self) -> Dict[str, Any]:
        """Inspects duration and audio tracks using audio_probe.py (find_ffmpeg and parse_audio_streams)."""
        ffmpeg_bin = find_ffmpeg()
        cmd = [ffmpeg_bin, "-hide_banner", "-i", self.media_url]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10.0,
            )
            output = proc.stderr.decode("utf-8", errors="replace")
            parsed = parse_audio_streams(output, self.media_url)
            if parsed.get("duration_seconds", 0.0) > 0:
                self.duration = parsed["duration_seconds"]
            self.streams = parsed.get("streams", [])
            if 0 <= self.audio_track < len(self.streams):
                codec = str(self.streams[self.audio_track].get("codec", "")).lower()
                self.is_audio_aac = (codec == "aac")
            elif parsed.get("codecs"):
                self.is_audio_aac = "aac" in [c.lower() for c in parsed["codecs"]]
            return parsed
        except Exception as e:
            logger.debug("inspect_media fallback for %s: %s", self.media_url, e)
            if self.duration <= 0.0:
                self.duration = 7200.0
            return {
                "url": self.media_url,
                "duration_seconds": self.duration,
                "streams": self.streams,
                "error": str(e),
            }

    async def inspect_media_async(self) -> Dict[str, Any]:
        """Asynchronously inspects duration and audio tracks using probe_audio_streams."""
        try:
            parsed = await probe_audio_streams(self.media_url)
            if parsed.get("duration_seconds", 0.0) > 0:
                self.duration = parsed["duration_seconds"]
            self.streams = parsed.get("streams", [])
            if 0 <= self.audio_track < len(self.streams):
                codec = str(self.streams[self.audio_track].get("codec", "")).lower()
                self.is_audio_aac = (codec == "aac")
            elif parsed.get("codecs"):
                self.is_audio_aac = "aac" in [c.lower() for c in parsed["codecs"]]
            return parsed
        except Exception as e:
            logger.debug("inspect_media_async failed for %s: %s", self.media_url, e)
            if self.duration <= 0.0:
                self.duration = 7200.0
            return {
                "url": self.media_url,
                "duration_seconds": self.duration,
                "streams": self.streams,
                "error": str(e),
            }

    def generate_master_playlist(
        self,
        selected_track: int = 0,
        boost_db: float = 0.0,
        base_url: str = "",
    ) -> str:
        """Generates an RFC 8216 compliant VOD master playlist with stream info."""
        self.audio_track = selected_track
        if boost_db != 0.0:
            self.volume_boost = boost_db

        base = base_url.rstrip("/") if base_url else ""
        manifest_url = f"{base}/api/hls/{self.session_id}/index.m3u8"
        return (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n"
            f"{manifest_url}\n"
        )

    def generate_master_manifest(self, base_url: str = "") -> str:
        """Backward-compatible alias for generate_master_playlist."""
        return self.generate_master_playlist(
            selected_track=self.audio_track,
            boost_db=self.volume_boost if self.volume_boost != 1.0 else 0.0,
            base_url=base_url,
        )

    def generate_media_playlist(self, track_idx: int = 0, base_url: str = "") -> str:
        """Generates an RFC 8216 compliant VOD media playlist.

        Includes #EXT-X-TARGETDURATION:5, #EXT-X-MEDIA-SEQUENCE:0, explicit #EXTINF:4.000 tags,
        and ends with #EXT-X-ENDLIST.
        """
        self.audio_track = track_idx
        total_segments = max(1, math.ceil(self.duration / self.segment_duration))
        target_duration = int(self.segment_duration) + 1  # 5 for 4.0s segments

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

    def generate_media_manifest(self, base_url: str = "") -> str:
        """Backward-compatible alias for generate_media_playlist."""
        return self.generate_media_playlist(track_idx=self.audio_track, base_url=base_url)

    async def get_segment(
        self,
        idx: int,
        track_idx: int = 0,
        boost_db: float = 0.0,
    ) -> bytes:
        """Retrieves or slices segment idx with track_idx and optional boost_db.

        Checks LRU segment cache first. On miss, slices segment via FFmpeg fast seek,
        caches result, and triggers speculative prebuffering of idx+1 and idx+2.
        """
        self.last_accessed = time.time()
        cache_key = HLSSegmentCache.format_key(self.session_id, idx, track_idx)
        legacy_key = f"{self.session_id}:{idx}"

        cached_data = self.cache.get(cache_key)
        if cached_data is None:
            cached_data = self.cache.get(legacy_key)

        if cached_data is not None:
            self._schedule_prebuffer(idx, track_idx=track_idx, boost_db=boost_db)
            return cached_data

        if cache_key in self._inflight:
            return await self._inflight[cache_key]

        task = asyncio.create_task(
            self._slice_segment_ffmpeg(idx, track_idx=track_idx, boost_db=boost_db)
        )
        self._inflight[cache_key] = task
        try:
            data = await task
            if data:
                self.cache.put(cache_key, data)
                self.cache.put(legacy_key, data)
            else:
                warn_msg = f"FFmpeg returned empty or invalid segment data for segment {idx} (session {self.session_id})"
                logger.warning(warn_msg)
                self.health = "degraded"
                self.status = "degraded"
                if idx not in self.failed_segments:
                    self.failed_segments.append(idx)
                if not self.last_error:
                    self.last_error = warn_msg
                raise HTTPException(
                    status_code=502,
                    detail=f"FFmpeg failed or returned empty bytes for segment {idx}; client buffer protected."
                )
        finally:
            self._inflight.pop(cache_key, None)

        self._schedule_prebuffer(idx, track_idx=track_idx, boost_db=boost_db)
        return data

    async def get_or_generate_segment(self, segment_idx: int) -> bytes:
        """Backward-compatible alias for get_segment using session default track and boost."""
        boost_val = self.volume_boost if self.volume_boost != 1.0 else 0.0
        return await self.get_segment(segment_idx, track_idx=self.audio_track, boost_db=boost_val)

    def _schedule_prebuffer(
        self,
        segment_idx: int,
        track_idx: int = 0,
        boost_db: float = 0.0,
    ) -> None:
        """Safely fires speculative prebuffer task on the running event loop."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._prebuffer(segment_idx, track_idx=track_idx, boost_db=boost_db))
        except RuntimeError:
            pass

    async def _prebuffer(
        self,
        segment_idx: int,
        track_idx: int = 0,
        boost_db: float = 0.0,
    ) -> None:
        """Asynchronously pre-slices segments idx+1 and idx+2 into cache."""
        total_segments = max(1, math.ceil(self.duration / self.segment_duration))
        for offset in (1, 2):
            next_idx = segment_idx + offset
            if 0 <= next_idx < total_segments:
                next_key = HLSSegmentCache.format_key(self.session_id, next_idx, track_idx)
                legacy_key = f"{self.session_id}:{next_idx}"
                if (
                    self.cache.get(next_key) is not None
                    or self.cache.get(legacy_key) is not None
                    or next_key in self._inflight
                    or legacy_key in self._inflight
                ):
                    continue

                try:
                    task = asyncio.create_task(
                        self._slice_segment_ffmpeg(next_idx, track_idx=track_idx, boost_db=boost_db)
                    )
                    self._inflight[next_key] = task
                    next_data = await task
                    if next_data:
                        self.cache.put(next_key, next_data)
                        self.cache.put(legacy_key, next_data)
                except Exception as e:
                    logger.debug(
                        "Prebuffer failed for segment %d of session %s: %s",
                        next_idx,
                        self.session_id,
                        e,
                    )
                finally:
                    self._inflight.pop(next_key, None)

    async def _slice_segment_ffmpeg(
        self,
        segment_idx: int,
        track_idx: Optional[int] = None,
        boost_db: Optional[float] = None,
    ) -> bytes:
        """Invokes FFmpeg to slice an MPEG-TS segment directly from the source stream."""
        actual_track = track_idx if track_idx is not None else self.audio_track
        actual_boost = boost_db if boost_db is not None else (
            self.volume_boost if self.volume_boost != 1.0 else 0.0
        )

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
                "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
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
            "-map", f"0:a:{actual_track}?",
            "-c:v", "copy",
        ])

        # Audio stream handling:
        # If volume boost requested, add -af "volume={boost_db}dB" or linear volume filter
        has_boost = (actual_boost != 0.0 and actual_boost != 1.0)
        if has_boost:
            boost_filter = f"volume={actual_boost}dB" if isinstance(actual_boost, (int, float)) and actual_boost > 1.5 else f"volume={actual_boost}"
            if isinstance(actual_boost, (int, float)) and (0 < actual_boost <= 30.0 and actual_boost != 1.0):
                boost_filter = f"volume={actual_boost}dB"
            cmd.extend([
                "-c:a", "aac",
                "-b:a", "192k",
                "-ac", "2",
                "-af", boost_filter,
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
                logger.warning(
                    "FFmpeg slice failed for segment %d (exit code %d): %s",
                    segment_idx,
                    proc.returncode,
                    err_msg,
                )
                self.health = "degraded"
                self.status = "degraded"
                self.last_error = f"FFmpeg exit code {proc.returncode}: {err_msg}"
                if segment_idx not in self.failed_segments:
                    self.failed_segments.append(segment_idx)
                return stdout or b""
            if not stdout:
                logger.warning(
                    "FFmpeg slice returned empty bytes for segment %d of session %s",
                    segment_idx,
                    self.session_id,
                )
                self.health = "degraded"
                self.status = "degraded"
                self.last_error = f"FFmpeg returned empty bytes for segment {segment_idx}"
                if segment_idx not in self.failed_segments:
                    self.failed_segments.append(segment_idx)
                return b""
            return stdout
        except asyncio.TimeoutError:
            logger.warning(
                "FFmpeg slice segment %d timed out for session %s",
                segment_idx,
                self.session_id,
            )
            self.health = "degraded"
            self.status = "degraded"
            self.last_error = f"FFmpeg slice segment {segment_idx} timed out"
            if segment_idx not in self.failed_segments:
                self.failed_segments.append(segment_idx)
            try:
                proc.kill()
            except Exception:
                pass
            return b""
        except Exception as e:
            logger.warning("FFmpeg slice segment %d failed for session %s: %s", segment_idx, self.session_id, e)
            self.health = "degraded"
            self.status = "degraded"
            self.last_error = str(e)
            if segment_idx not in self.failed_segments:
                self.failed_segments.append(segment_idx)
            return b""


class HLSSessionManager:
    """Manages active HLS sessions, lifecycle tracking, and idle session garbage collection."""

    def __init__(self, cache: Optional[HLSSegmentCache] = None) -> None:
        self._sessions: Dict[str, HLSSession] = {}
        self._lock = threading.RLock()
        self.cache: HLSSegmentCache = cache if cache is not None else HLS_SEGMENT_CACHE

    @property
    def sessions(self) -> Dict[str, HLSSession]:
        """Backward-compatible accessor for sessions dict."""
        return self._sessions

    @sessions.setter
    def sessions(self, value: Dict[str, HLSSession]) -> None:
        self._sessions = value

    async def get_or_create_session(
        self,
        url: Optional[str] = None,
        title: str = "",
        audio_track: int = 0,
        volume_boost: float = 1.0,
        media_url: Optional[str] = None,
        **kwargs: Any,
    ) -> HLSSession:
        """Retrieves an existing session or probes the media URL and creates a new HLSSession."""
        target_url = (url or media_url or "").strip()
        session_key = f"{target_url}|{audio_track}|{volume_boost:.4f}"
        if title:
            session_key += f"|{title}"
        session_id = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:16]

        with self._lock:
            if session_id in self._sessions:
                session = self._sessions[session_id]
                session.last_accessed = time.time()
                return session

        # Probe duration and audio codecs
        probe_res: Dict[str, Any] = {}
        try:
            probe_res = await probe_audio_streams(target_url)
        except Exception as e:
            logger.warning("probe_audio_streams failed for %s: %s", target_url, e)

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
            media_url=target_url,
            duration=duration,
            audio_track=audio_track,
            volume_boost=volume_boost,
            is_audio_aac=is_audio_aac,
            segment_duration=4.0,
            title=title,
            created_at=now,
            last_accessed=now,
            cache=self.cache,
            streams=streams,
        )

        with self._lock:
            self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[HLSSession]:
        """Retrieves an existing session by ID, updating its last_accessed timestamp."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.last_accessed = time.time()
            return session

    def cleanup_idle_sessions(
        self,
        max_idle_seconds: float = 120.0,
        max_idle_sec: Optional[float] = None,
    ) -> int:
        """Purges sessions inactive for more than max_idle_seconds along with cached segments."""
        threshold = max_idle_sec if max_idle_sec is not None else max_idle_seconds
        now = time.time()
        with self._lock:
            expired_ids = [
                sid for sid, s in self._sessions.items()
                if (now - s.last_accessed) > threshold
            ]
            for sid in expired_ids:
                del self._sessions[sid]
                self.cache.purge_session(sid)

        if expired_ids:
            logger.info("Purged %d idle HLS session(s): %s", len(expired_ids), expired_ids)
        return len(expired_ids)

    def cleanup_expired_sessions(self, max_idle_sec: float = 180.0) -> int:
        """Backward-compatible alias for cleanup_idle_sessions."""
        return self.cleanup_idle_sessions(max_idle_seconds=max_idle_sec)


# Global session manager singleton
HLS_SESSION_MANAGER = HLSSessionManager(cache=HLS_SEGMENT_CACHE)
