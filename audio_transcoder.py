"""Audio Transcoder module for Playro JEV.

Transcodes incoming media streams on-the-fly using FFmpeg with video copy
(0% re-encoding overhead), AAC audio normalization, track mapping,
volume boosting, and fragmented MP4 container packaging for browser playback.
"""

import asyncio
import inspect
import logging
from typing import AsyncGenerator, List, Optional

try:
    from audio_probe import find_ffmpeg
except ImportError:
    import shutil

    def find_ffmpeg() -> str:
        """Locate ffmpeg binary on system or fallback to default executable name."""
        return shutil.which("ffmpeg") or "ffmpeg"

logger = logging.getLogger("audio_transcoder")


def validate_transcode_params(
    url: str,
    audio_track_index: int = 0,
    start_time: float = 0.0,
    volume_boost: float = 1.0,
) -> None:
    """Validates transcoding parameters, raising ValueError on invalid inputs."""
    if not url or not isinstance(url, str) or not url.strip():
        raise ValueError("Invalid or empty media URL")
    if not isinstance(audio_track_index, int) or audio_track_index < 0:
        raise ValueError("audio_track_index must be a non-negative integer")
    if not isinstance(start_time, (int, float)) or start_time < 0:
        raise ValueError("start_time must be non-negative")
    if not isinstance(volume_boost, (int, float)) or volume_boost <= 0:
        raise ValueError("volume_boost must be greater than 0")


def build_ffmpeg_args(
    ffmpeg_bin: str,
    url: str,
    audio_track_index: int = 0,
    start_time: float = 0.0,
    volume_boost: float = 1.0,
) -> List[str]:
    """Construct FFmpeg CLI arguments according to transcoding requirements."""
    cmd: List[str] = [ffmpeg_bin]

    if start_time > 0:
        cmd.extend(["-ss", str(start_time)])

    cmd.extend(["-i", url])

    cmd.extend([
        "-map", "0:v:0",
        "-map", f"0:a:{audio_track_index}?",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "256k",
        "-ac", "2",
    ])

    if volume_boost != 1.0:
        cmd.extend(["-af", f"volume={volume_boost}"])

    cmd.extend([
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "pipe:1",
    ])

    return cmd


async def stream_transcoded_media(
    url: str,
    audio_track_index: int = 0,
    start_time: float = 0.0,
    volume_boost: float = 1.0,
) -> AsyncGenerator[bytes, None]:
    """Stream transcoded media chunks as an asynchronous byte generator.

    Args:
        url: Direct stream or media URL.
        audio_track_index: 0-based index of the target audio track.
        start_time: Seek offset in seconds.
        volume_boost: Multiplier for audio volume (e.g. 1.5, 2.0).

    Yields:
        Fragmented MP4 byte chunks (64KB buffer).
    """
    validate_transcode_params(
        url=url,
        audio_track_index=audio_track_index,
        start_time=start_time,
        volume_boost=volume_boost,
    )

    ffmpeg_val = find_ffmpeg()
    if inspect.iscoroutine(ffmpeg_val):
        ffmpeg_bin = await ffmpeg_val
    else:
        ffmpeg_bin = ffmpeg_val
    ffmpeg_bin = str(ffmpeg_bin or "ffmpeg")

    cmd = build_ffmpeg_args(
        ffmpeg_bin=ffmpeg_bin,
        url=url,
        audio_track_index=audio_track_index,
        start_time=start_time,
        volume_boost=volume_boost,
    )

    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        if proc.stdout is not None:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk

    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.debug("Error during proc.terminate(): %s", e)

            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    except Exception as e:
                        logger.debug("Error during proc.kill(): %s", e)
                    try:
                        await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=1.0)
                    except Exception:
                        pass
