"""Audio Stream Probe Module for Playro JEV.

Inspects incoming media streams using FFmpeg/ffprobe to extract audio streams,
codecs (TrueHD 7.1, AC-3 5.1, DTS, AAC 2.0), channels, sample rates, languages,
and determines HTML5 browser playback compatibility.
"""

import asyncio
import logging
import os
import re
import shutil
from typing import Any, Dict, List, Optional

logger = logging.getLogger("audio_probe")

# Well-known FFmpeg search paths on Windows and Unix systems
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

# Well-known ffprobe search paths on Windows and Unix systems
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

# Audio codecs natively supported by standard HTML5 browsers (Chrome, Firefox, Safari, Edge)
BROWSER_COMPATIBLE_AUDIO_CODECS = {
    "aac",
    "mp3",
    "opus",
    "vorbis",
    "flac",
    "wav",
    "pcm_s16le",
}


def find_ffmpeg() -> str:
    """Locates the FFmpeg executable on the host system.

    Checks system PATH first, then well-known installation paths,
    falling back to 'ffmpeg'.
    """
    which_path = shutil.which("ffmpeg")
    if which_path and os.path.isfile(which_path):
        return os.path.abspath(which_path)

    for candidate in KNOWN_FFMPEG_PATHS:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)

    return "ffmpeg"


def find_ffprobe() -> str:
    """Locates the ffprobe executable on the host system.

    Checks system PATH first, then well-known installation paths,
    falling back to sibling directory of find_ffmpeg() or 'ffprobe'.
    """
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
    """Parses raw FFmpeg stream probe output into structured audio stream metadata.

    Handles TrueHD 7.1, AC-3 5.1, DTS, and AAC 2.0 / stereo along with metadata tags.
    """
    duration_seconds: float = 0.0
    duration_formatted: str = ""

    dur_match = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', probe_text, re.IGNORECASE)
    if dur_match:
        h, m, s = dur_match.group(1), dur_match.group(2), dur_match.group(3)
        duration_seconds = round(int(h) * 3600 + int(m) * 60 + float(s), 2)
        h_val, m_val, s_val = int(h), int(m), int(float(s))
        if h_val > 0:
            duration_formatted = f"{h_val:02d}:{m_val:02d}:{s_val:02d}"
        else:
            duration_formatted = f"{m_val:02d}:{s_val:02d}"

    lines = probe_text.splitlines()
    audio_streams: List[Dict[str, Any]] = []
    current_stream: Optional[Dict[str, Any]] = None

    stream_regex = re.compile(
        r'Stream\s+#(?P<stream_id>\d+:\d+)(?:\((?P<lang>[a-zA-Z0-9_\-]+)\))?(?:\[(?P<id>[^\]]+)\])?:\s*Audio:\s*(?P<codec>[a-zA-Z0-9_\-]+)(?:\s*\((?P<profile>[^)]+)\))?,?\s*(?P<rest>.*)$',
        re.IGNORECASE,
    )

    for line in lines:
        m = stream_regex.search(line)
        if m:
            stream_id = m.group("stream_id")
            lang = m.group("lang") or ""
            raw_codec = m.group("codec").lower()
            profile = m.group("profile") or ""
            rest = m.group("rest") or ""

            # Normalize codec names
            codec = raw_codec
            if codec in {"dts", "dca", "dtshd"}:
                codec = "dts"
            elif codec in {"eac3", "ac-3", "ac3"}:
                codec = "ac3" if codec == "ac-3" else codec
            elif codec in {"truehd"}:
                codec = "truehd"
            elif codec in {"aac"}:
                codec = "aac"

            # Parse sample rate
            sr_match = re.search(r'(\d+)\s*Hz', rest)
            sample_rate = int(sr_match.group(1)) if sr_match else 48000

            # Parse channels and layout
            channels = "2.0"
            channel_count = 2
            if re.search(r'\b7\.1(?:\(side\)|\(raw\))?\b', rest, re.I):
                channels = "7.1"
                channel_count = 8
            elif re.search(r'\b5\.1(?:\(side\))?\b', rest, re.I):
                channels = "5.1"
                channel_count = 6
            elif re.search(r'\b5\.0\b', rest):
                channels = "5.0"
                channel_count = 5
            elif re.search(r'\b(?:stereo|2\.0)\b', rest, re.I):
                channels = "2.0"
                channel_count = 2
            elif re.search(r'\b(?:mono|1\.0)\b', rest, re.I):
                channels = "1.0"
                channel_count = 1

            # Parse bitrate
            br_match = re.search(r'(\d+)\s*kb/s', rest)
            bitrate = f"{br_match.group(1)} kb/s" if br_match else None

            # Detect default flag
            is_default = "(default)" in rest.lower() or "[default]" in rest.lower()

            # Browser compatibility check
            browser_compat = codec in BROWSER_COMPATIBLE_AUDIO_CODECS

            current_stream = {
                "index": len(audio_streams),
                "stream_id": stream_id,
                "codec": codec,
                "profile": profile,
                "channels": channels,
                "channel_count": channel_count,
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "language": lang,
                "title": "",
                "is_default": is_default,
                "browser_compatible": browser_compat,
            }
            audio_streams.append(current_stream)
            continue

        # Look for metadata title / language lines immediately under stream
        if current_stream:
            stripped = line.strip()
            if stripped.lower().startswith("title"):
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    current_stream["title"] = parts[1].strip()
            elif stripped.lower().startswith("language") and not current_stream.get("language"):
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    current_stream["language"] = parts[1].strip()
            elif stripped.startswith("Stream #") and "Audio:" not in stripped:
                # Video or subtitle stream encountered, reset stream context
                current_stream = None

    # Deduplicate unique codecs and languages in order of appearance
    unique_codecs: List[str] = []
    for s in audio_streams:
        if s["codec"] not in unique_codecs:
            unique_codecs.append(s["codec"])

    unique_languages: List[str] = []
    for s in audio_streams:
        lang = s.get("language")
        if lang and lang != "und" and lang not in unique_languages:
            unique_languages.append(lang)

    # Primary audio stream determines baseline browser compatibility
    primary_compat = bool(audio_streams and audio_streams[0]["browser_compatible"])
    has_incompatible = any(not s["browser_compatible"] for s in audio_streams)
    needs_transcode = (not primary_compat) if audio_streams else False

    return {
        "url": url,
        "stream_count": len(audio_streams),
        "codecs": unique_codecs,
        "languages": unique_languages,
        "duration_seconds": duration_seconds,
        "duration_formatted": duration_formatted,
        "browser_compatible": primary_compat and not has_incompatible,
        "primary_browser_compatible": primary_compat,
        "has_incompatible_audio": has_incompatible,
        "needs_transcode": needs_transcode,
        "streams": audio_streams,
    }


async def probe_audio_streams(url_or_content: str, timeout: float = 10.0) -> Dict[str, Any]:
    """Probes audio streams and codecs of the given stream URL or raw FFmpeg text.

    If url_or_content is raw FFmpeg output, parses it directly without network calls.
    Otherwise, invokes the FFmpeg binary via an async subprocess and parses stderr.
    """
    if not url_or_content or not isinstance(url_or_content, str):
        return {
            "url": "",
            "stream_count": 0,
            "codecs": [],
            "languages": [],
            "duration_seconds": 0.0,
            "duration_formatted": "",
            "browser_compatible": True,
            "has_incompatible_audio": False,
            "needs_transcode": False,
            "streams": [],
            "error": "Empty or invalid input",
        }

    # Direct parsing if raw FFmpeg stream output is passed
    if "Stream #" in url_or_content or "\n" in url_or_content:
        return parse_audio_streams(url_or_content, url="raw_input")

    ffmpeg_bin = find_ffmpeg()
    cmd = [ffmpeg_bin, "-hide_banner", "-i", url_or_content]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        raw_output = stderr.decode("utf-8", errors="replace")
        return parse_audio_streams(raw_output, url=url_or_content)
    except Exception as e:
        logger.warning("probe_audio_streams failed for %s: %s", url_or_content, e)
        return {
            "url": url_or_content,
            "stream_count": 0,
            "codecs": [],
            "languages": [],
            "duration_seconds": 0.0,
            "duration_formatted": "",
            "browser_compatible": True,
            "has_incompatible_audio": False,
            "needs_transcode": False,
            "streams": [],
            "error": str(e),
        }
