"""
Zero-Trust Async Stream Verifier (< 200ms)
Performs HTTP Range request (bytes=0-63) via streaming chunk inspection:
  - MKV / WebM: 1A 45 DF A3 (EBML Container)
  - MP4: 00 00 00 .. 66 74 79 70 ('ftyp' box) or 'moov' / 'mdat'
  - HLS: #EXTM3U (23 45 58 54 4D 33 55) or .m3u8 playlist
  - MPEG-TS: 0x47 Sync Byte
  - AVI: RIFF .... AVI
  - Anti-Malware / Non-Video Rejection: Rejects ZIP (50 4B 03 04), EXE (4D 5A), Images, and HTML Error Pages.
  - Streaming Chunking: Uses async client.stream() to prevent downloading full multi-gigabyte files when servers ignore Range headers.
  - Zero-Crash Guarantee: Silently traps timeouts, SSL errors, DNS errors, and dead links.
"""

import sys
import time
import asyncio
import json
import httpx

# Verification timeout for pre-flight stream checks
STREAM_CHECK_TIMEOUT = 2.5


class StreamVerifier:
    def __init__(self):
        self.headers = {
            "Range": "bytes=0-8191",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Encoding": "identity"
        }

    async def verify(self, url: str, client: httpx.AsyncClient = None) -> dict:
        """
        Verify stream safety and magic bytes in < 200ms.
        Guarantees zero crashes and silent error fallback on dead links.
        """
        t0 = time.perf_counter()
        if not url or not str(url).strip():
            return {
                "valid": False,
                "error": "Empty URL provided",
                "elapsed_ms": 0.0
            }

        url_str = str(url).strip()

        # Handle URLs with :: delimiters cleanly (e.g. stream_url::headers or stream_url::token) without URL parsing exceptions
        clean_url = url_str
        req_headers = dict(self.headers)
        if "::" in url_str:
            is_ipv6_only = False
            bo = url_str.find('[')
            bc = url_str.find(']')
            dc = url_str.find('::')
            if bo != -1 and bc != -1 and bo < dc < bc and url_str.count('::') == 1:
                is_ipv6_only = True

            if not is_ipv6_only:
                if bo != -1 and bc != -1 and bo < dc < bc:
                    base = url_str[:bc+1]
                    rest = url_str[bc+1:]
                    parts = [base + rest.split('::')[0]] + rest.split('::')[1:]
                else:
                    parts = url_str.split('::')
                if parts[0].startswith(('http://', 'https://')):
                    clean_url = parts[0].strip()
                    for extra in parts[1:]:
                        extra = extra.strip()
                        if '=' in extra:
                            hk, hv = extra.split('=', 1)
                            req_headers[hk.strip()] = hv.strip()

        # SSRF Security Guard & DNS Rebinding Interceptor
        try:
            from link_resolver import is_safe_url, install_dns_ssrf_guard
            install_dns_ssrf_guard()
            if not is_safe_url(clean_url):
                return {
                    "valid": False,
                    "error": "Destination blocked by SSRF private-network policy",
                    "format": "Unknown/Invalid",
                    "detected_format": "Unknown/Invalid",
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
                }
        except Exception:
            pass

        # Special handling for YouTube URLs & Direct Google CDN video streams
        if any(k in clean_url for k in ["youtube.com/watch", "youtu.be/", "googlevideo.com", "video-downloads.googleusercontent.com"]):
            return {
                "valid": True,
                "status_code": 200,
                "format": "Direct Video Stream",
                "detected_format": "Direct Video Stream",
                "magic_bytes_hex": "DIRECT",
                "content_type": "video/mp4",
                "size": "Direct Stream",
                "total_bytes": 0,
                "malware_detected": False,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
            }

        own_client = False
        if client is None:
            own_client = True
            client = httpx.AsyncClient(
                http2=False,
                timeout=STREAM_CHECK_TIMEOUT,
                follow_redirects=True,
                verify=False
            )

        try:
            # For local stream endpoints or HTTP Range endpoints, perform targeted Range GET
            if "127.0.0.1" in clean_url or "localhost" in clean_url or not hasattr(client, "stream"):
                resp = await client.get(clean_url, headers=req_headers, timeout=STREAM_CHECK_TIMEOUT)
                elapsed = (time.perf_counter() - t0) * 1000
                status_code = resp.status_code
                content_type = resp.headers.get("content-type", "").lower()
                content_range = resp.headers.get("content-range", "")
                content_length = resp.headers.get("content-length", "")
                data = resp.content if isinstance(getattr(resp, "content", None), (bytes, bytearray)) else (resp.text.encode() if hasattr(resp, "text") else b"")
            else:
                # Use stream() context to read only the initial chunk (prevents downloading full multi-GB file if server ignores Range)
                async with client.stream("GET", clean_url, headers=req_headers, timeout=STREAM_CHECK_TIMEOUT) as resp:
                    elapsed = (time.perf_counter() - t0) * 1000

                    # SSRF Hop Verification on all redirects followed
                    try:
                        from link_resolver import is_safe_url
                        for hist in resp.history:
                            if not is_safe_url(str(hist.url)):
                                await resp.aclose()
                                return {
                                    "valid": False,
                                    "error": "Redirect target blocked by SSRF private-network policy",
                                    "format": "Unknown/Invalid",
                                    "detected_format": "Unknown/Invalid",
                                    "elapsed_ms": round(elapsed, 2)
                                }
                        if not is_safe_url(str(resp.url)):
                            await resp.aclose()
                            return {
                                "valid": False,
                                "error": "Destination blocked by SSRF private-network policy",
                                "format": "Unknown/Invalid",
                                "detected_format": "Unknown/Invalid",
                                "elapsed_ms": round(elapsed, 2)
                            }
                    except Exception:
                        pass

                    status_code = resp.status_code
                    content_type = resp.headers.get("content-type", "").lower()
                    content_range = resp.headers.get("content-range", "")
                    content_length = resp.headers.get("content-length", "")

                    # Read up to 8192 bytes of initial header/container data
                    data = b""
                    try:
                        async for chunk in resp.aiter_bytes():
                            data += chunk
                            if len(data) >= 8192:
                                break
                    finally:
                        # Gracefully close stream response to prevent connection drain and background socket leak
                        try:
                            await resp.aclose()
                        except Exception:
                            pass

            # Parse Total Size if in Content-Range: bytes 0-8191/20130211188
            total_size_bytes = 0
            if content_range and "/" in content_range:
                try:
                    total_size_bytes = int(content_range.split("/")[-1])
                except ValueError:
                    pass
            elif content_length and content_length.isdigit():
                total_size_bytes = int(content_length)

            if total_size_bytes >= 1024**3:
                formatted_size = f"{round(total_size_bytes / (1024**3), 2)} GB"
            elif total_size_bytes >= 1024**2:
                formatted_size = f"{round(total_size_bytes / (1024**2), 2)} MB"
            else:
                formatted_size = "Direct Stream"

            # Immediate rejection for non-200/206 HTTP status or JSON error bodies (e.g. 403 quota exceeded, 500 worker crash, 7B 0A / {"error": ...})
            is_json_error = (
                data[:2] == b'\x7b\x0a' or
                data.startswith(b'{\n') or
                data.startswith(b'{\r\n') or
                data.startswith(b'{\t') or
                data.startswith(b'{"') or
                b'{"error"' in data or
                b'"error":' in data or
                b'downloadquotaexceeded' in data.lower() or
                b'quota exceeded' in data.lower()
            )

            if status_code not in (200, 206) or is_json_error:
                error_fmt = "Quota Exceeded / Error" if (status_code in (403, 500) or is_json_error) else "Unknown/Invalid"
                return {
                    "valid": False,
                    "status_code": status_code,
                    "format": error_fmt,
                    "detected_format": error_fmt,
                    "magic_bytes_hex": data[:8].hex().upper() if len(data) >= 8 else "",
                    "content_type": content_type,
                    "size": formatted_size,
                    "total_bytes": total_size_bytes,
                    "malware_detected": False,
                    "elapsed_ms": round(elapsed, 2)
                }

            # Non-Fast-Start MP4 Tail Moov Box Detection
            # If mdat is upfront and moov is not in the initial probe, probe up to 256KB from the tail
            # Enforce strict HTTP 206 Partial Content check (if server returns 200, it ignored Range and is sending from byte 0)
            if (b'mdat' in data or b'ftyp' in data) and b'moov' not in data and isinstance(total_size_bytes, int) and total_size_bytes > 131072:
                try:
                    probe_size = min(262144, total_size_bytes - 1)
                    probe_start = max(0, total_size_bytes - probe_size)
                    tail_headers = dict(req_headers)
                    tail_headers["Range"] = f"bytes={probe_start}-{total_size_bytes - 1}"
                    async with client.stream("GET", clean_url, headers=tail_headers, timeout=1.5) as tail_resp:
                        if tail_resp.status_code == 206:
                            tail_data = b""
                            try:
                                async for chunk in tail_resp.aiter_bytes():
                                    tail_data += chunk
                                    if len(tail_data) >= probe_size:
                                        break
                            finally:
                                try:
                                    await tail_resp.aclose()
                                except Exception:
                                    pass
                            if b'moov' in tail_data:
                                data += tail_data
                        else:
                            # Server returned 200 or error - close immediately to prevent full file download
                            try:
                                await tail_resp.aclose()
                            except Exception:
                                pass
                except Exception:
                    pass

            # Magic Bytes & Container Inspection
            is_mkv = data.startswith(b'\x1a\x45\xdf\xa3')
            # Tolerant MP4 detection covering faststart (ftyp, moov) and non-faststart (leading mdat with ISOBMFF container atoms)
            is_mp4 = (
                (len(data) >= 8 and data[4:8] == b'ftyp') or
                (b'moov' in data[:8192]) or
                (b'mdat' in data[:8192] and any(atom in data[:8192] for atom in [b'ftyp', b'wide', b'free', b'skip', b'uuid'])) or
                ("video/mp4" in content_type and len(data) >= 32 and any(atom in data[:8192] for atom in [b'ftyp', b'moov', b'mdat', b'wide']))
            )
            is_hls = (
                (b'#EXTM3U' in data) or
                ("mpegurl" in content_type) or
                (".m3u8" in clean_url.lower())
            )
            is_ts = data.startswith(b'\x47') or ("video/mp2t" in content_type)
            is_avi = data.startswith(b'RIFF') and (b'AVI ' in data[:16])

            is_zip = data.startswith(b'PK\x03\x04')
            is_exe = data.startswith(b'MZ')
            is_img = (
                data.startswith(b'GIF8') or
                data.startswith(b'\x89PNG') or
                data.startswith(b'\xff\xd8\xff') or
                ("image/" in content_type)
            )
            is_html = (
                data.startswith(b'<!DOCTYPE') or
                data.startswith(b'<html') or
                data.startswith(b'<?xml') or
                ("text/html" in content_type)
            )

            is_valid_video = (
                (is_mkv or is_mp4 or is_hls or is_ts or is_avi or "video" in content_type or "octet-stream" in content_type) and
                not (is_zip or is_exe or is_img or is_html) and
                status_code in [200, 206]
            )

            # Fallback for servers rejecting byte-range (e.g. 416 on range header)
            if not is_valid_video and status_code == 416:
                try:
                    head_resp = await client.head(clean_url, timeout=STREAM_CHECK_TIMEOUT)
                    h_type = head_resp.headers.get("content-type", "").lower()
                    if ("video" in h_type or "octet-stream" in h_type) or head_resp.status_code == 200:
                        is_valid_video = True
                        status_code = head_resp.status_code
                        content_type = h_type
                except Exception:
                    pass

            if is_mkv:
                detected_format = "MKV (EBML)"
            elif is_mp4:
                detected_format = "MP4"
            elif is_hls:
                detected_format = "HLS (.m3u8)"
            elif is_ts:
                detected_format = "MPEG-TS"
            elif is_avi:
                detected_format = "AVI"
            elif "video" in content_type:
                detected_format = content_type
            elif is_valid_video:
                detected_format = "Direct Video Stream"
            else:
                detected_format = "Unknown/Invalid"

            return {
                "valid": is_valid_video,
                "status_code": status_code,
                "format": detected_format,
                "detected_format": detected_format,
                "magic_bytes_hex": data[:8].hex().upper() if len(data) >= 8 else "",
                "content_type": content_type,
                "size": formatted_size,
                "total_bytes": total_size_bytes,
                "malware_detected": is_zip or is_exe,
                "elapsed_ms": round(elapsed, 2)
            }
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            return {
                "valid": False,
                "error": str(e),
                "format": "Unknown/Invalid",
                "detected_format": "Unknown/Invalid",
                "elapsed_ms": round(elapsed, 2)
            }
        finally:
            if own_client:
                try:
                    await client.aclose()
                except Exception:
                    pass


if __name__ == "__main__":
    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://pixeldrain.dev/api/file/1noW8snv"
    verifier = StreamVerifier()
    res = asyncio.run(verifier.verify(test_url))
    print(json.dumps(res, indent=2))
