"""Audio Engine module for Playro JEV.

Combines stream audio probing, multi-track detection, and on-the-fly
FFmpeg audio transcoding.
"""

from audio_probe import (
    find_ffmpeg,
    probe_audio_streams,
    parse_audio_streams,
    KNOWN_FFMPEG_PATHS,
    BROWSER_COMPATIBLE_AUDIO_CODECS,
)
from audio_transcoder import (
    stream_transcoded_media,
    build_ffmpeg_args,
    validate_transcode_params,
)

__all__ = [
    "find_ffmpeg",
    "probe_audio_streams",
    "parse_audio_streams",
    "KNOWN_FFMPEG_PATHS",
    "BROWSER_COMPATIBLE_AUDIO_CODECS",
    "stream_transcoded_media",
    "build_ffmpeg_args",
    "validate_transcode_params",
]
