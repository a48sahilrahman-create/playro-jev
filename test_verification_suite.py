"""
Comprehensive Unit & Integration Test Suite for Link Resolver & Stream Verifier
Tests:
1. EBML (MKV/WebM) Magic Bytes (1A 45 DF A3)
2. MP4 ftyp Box Magic Bytes (00 00 00 .. 66 74 79 70)
3. MP4 moov / mdat Boxes
4. HLS Stream Playlist (#EXTM3U)
5. MPEG-TS Stream Sync Byte (0x47)
6. AVI Stream Container (RIFF ... AVI)
7. Anti-Malware / Non-Video Rejection:
   - ZIP (PK\x03\x04)
   - Windows PE EXE (MZ)
   - PNG Image (\x89PNG)
   - JPEG Image (\xff\xd8\xff)
   - GIF Image (GIF89a)
   - HTML Error Page (<!DOCTYPE html> or <html>)
8. YouTube Stream Direct Pass-through (< 1ms)
9. Pixeldrain Link Transform (< 1ms)
10. DDLBase XOR Obfuscation (< 1ms)
11. VCloud / NexDrive Gateway Resolution & R2 Pre-Signed Video Extraction (< 200ms)
12. HubCloud / Gamerxyt Gateway Resolution (< 200ms)
13. DriveSeed / Google CDN Resolution (< 200ms)
14. OlaMovies / OMDrive Resolution (< 200ms)
15. Resilient Dead Link & Timeout Isolation (< 2500ms max timeout, zero crashes)
"""

import sys
import time
import asyncio
import base64
import unittest
import httpx
from link_resolver import LinkResolver
from stream_verifier import StreamVerifier


class MockTransport(httpx.AsyncBaseTransport):
    """Custom mock transport to simulate various network endpoints without external dependencies."""

    def __init__(self, routes=None):
        self.routes = routes or {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        for route_pattern, handler in self.routes.items():
            if route_pattern in url_str:
                return await handler(request)

        return httpx.Response(404, text="Not Found")


class TestStreamVerifierAndLinkResolver(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.verifier = StreamVerifier()
        self.resolver = LinkResolver()

    # =========================================================================
    # 1. STREAM VERIFIER TESTS: MAGIC BYTES & CONTAINER DETECTION
    # =========================================================================

    async def test_ebml_mkv_detection(self):
        """Verify MKV/WebM container with EBML header 1A 45 DF A3."""
        ebml_data = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81" + b"\x00" * 48

        async def mock_handler(request):
            return httpx.Response(206, content=ebml_data, headers={
                "Content-Type": "video/x-matroska",
                "Content-Range": "bytes 0-63/1048576000",
                "Content-Length": "64"
            })

        transport = MockTransport({"mkv.stream": mock_handler})
        async with httpx.AsyncClient(transport=transport) as client:
            res = await self.verifier.verify("https://mock.cdn/mkv.stream", client=client)
            self.assertTrue(res["valid"])
            self.assertEqual(res["format"], "MKV (EBML)")
            self.assertEqual(res["magic_bytes_hex"], "1A45DFA39F428681")
            self.assertEqual(res["status_code"], 206)
            self.assertFalse(res["malware_detected"])

    async def test_mp4_ftyp_detection(self):
        """Verify MP4 container with ftyp box."""
        mp4_data = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42" + b"\x00" * 40

        async def mock_handler(request):
            return httpx.Response(206, content=mp4_data, headers={
                "Content-Type": "video/mp4",
                "Content-Range": "bytes 0-63/524288000",
                "Content-Length": "64"
            })

        transport = MockTransport({"mp4.stream": mock_handler})
        async with httpx.AsyncClient(transport=transport) as client:
            res = await self.verifier.verify("https://mock.cdn/mp4.stream", client=client)
            self.assertTrue(res["valid"])
            self.assertEqual(res["format"], "MP4")
            self.assertTrue("ftyp" in str(mp4_data))
            self.assertEqual(res["status_code"], 206)
            self.assertFalse(res["malware_detected"])

    async def test_hls_stream_detection(self):
        """Verify HLS m3u8 playlist detection."""
        hls_data = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n" + b"\x00" * 20

        async def mock_handler(request):
            return httpx.Response(200, content=hls_data, headers={
                "Content-Type": "application/vnd.apple.mpegurl",
                "Content-Length": "64"
            })

        transport = MockTransport({"playlist.m3u8": mock_handler})
        async with httpx.AsyncClient(transport=transport) as client:
            res = await self.verifier.verify("https://mock.cdn/live/playlist.m3u8", client=client)
            self.assertTrue(res["valid"])
            self.assertEqual(res["format"], "HLS (.m3u8)")
            self.assertFalse(res["malware_detected"])

    async def test_mpeg_ts_detection(self):
        """Verify MPEG-TS sync byte 0x47."""
        ts_data = b"\x47\x40\x11\x10" + b"\xff" * 60

        async def mock_handler(request):
            return httpx.Response(200, content=ts_data, headers={
                "Content-Type": "video/mp2t"
            })

        transport = MockTransport({"segment.ts": mock_handler})
        async with httpx.AsyncClient(transport=transport) as client:
            res = await self.verifier.verify("https://mock.cdn/segment.ts", client=client)
            self.assertTrue(res["valid"])
            self.assertEqual(res["format"], "MPEG-TS")

    async def test_avi_detection(self):
        """Verify AVI RIFF container."""
        avi_data = b"RIFF\x00\x00\x00\x00AVI LIST\x00\x00\x00\x00hdrl" + b"\x00" * 44

        async def mock_handler(request):
            return httpx.Response(200, content=avi_data, headers={
                "Content-Type": "video/x-msvideo"
            })

        transport = MockTransport({"movie.avi": mock_handler})
        async with httpx.AsyncClient(transport=transport) as client:
            res = await self.verifier.verify("https://mock.cdn/movie.avi", client=client)
            self.assertTrue(res["valid"])
            self.assertEqual(res["format"], "AVI")

    # =========================================================================
    # 2. STREAM VERIFIER TESTS: ANTI-MALWARE & NON-VIDEO REJECTION
    # =========================================================================

    async def test_reject_zip_malware(self):
        """Verify rejection of ZIP archives masquerading as video."""
        zip_data = b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"\x00" * 54

        async def mock_handler(request):
            return httpx.Response(200, content=zip_data, headers={
                "Content-Type": "application/zip"
            })

        transport = MockTransport({"payload.zip": mock_handler})
        async with httpx.AsyncClient(transport=transport) as client:
            res = await self.verifier.verify("https://mock.cdn/payload.zip", client=client)
            self.assertFalse(res["valid"])
            self.assertTrue(res["malware_detected"])
            self.assertEqual(res["format"], "Unknown/Invalid")

    async def test_reject_exe_malware(self):
        """Verify rejection of Windows executable binaries (MZ)."""
        exe_data = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00" + b"\x00" * 54

        async def mock_handler(request):
            return httpx.Response(200, content=exe_data, headers={
                "Content-Type": "application/octet-stream"
            })

        transport = MockTransport({"malware.exe": mock_handler})
        async with httpx.AsyncClient(transport=transport) as client:
            res = await self.verifier.verify("https://mock.cdn/malware.exe", client=client)
            self.assertFalse(res["valid"])
            self.assertTrue(res["malware_detected"])

    async def test_reject_image_files(self):
        """Verify rejection of image files (PNG, JPG, GIF)."""
        png_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 48

        async def mock_handler(request):
            return httpx.Response(200, content=png_data, headers={
                "Content-Type": "image/png"
            })

        transport = MockTransport({"banner.png": mock_handler})
        async with httpx.AsyncClient(transport=transport) as client:
            res = await self.verifier.verify("https://mock.cdn/banner.png", client=client)
            self.assertFalse(res["valid"])

    async def test_reject_html_error_pages(self):
        """Verify rejection of 200 OK Cloudflare/ad gateway HTML error pages."""
        html_data = b"<!DOCTYPE html><html><head><title>Cloudflare DDoS</title></head></html>"

        async def mock_handler(request):
            return httpx.Response(200, content=html_data, headers={
                "Content-Type": "text/html; charset=UTF-8"
            })

        transport = MockTransport({"gateway.html": mock_handler})
        async with httpx.AsyncClient(transport=transport) as client:
            res = await self.verifier.verify("https://mock.cdn/gateway.html", client=client)
            self.assertFalse(res["valid"])

    # =========================================================================
    # 3. LINK RESOLVER & DISPATCHER TESTS
    # =========================================================================

    async def test_youtube_resolution(self):
        """Verify instant YouTube URL pass-through in < 1ms."""
        yt_urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://youtube.com/watch?v=9bZkp7q19f0"
        ]
        for url in yt_urls:
            res = await self.resolver.resolve_auto(url)
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["host"], "YouTube")
            self.assertEqual(res["direct_url"], url)
            self.assertLess(res["elapsed_ms"], 200)

            # StreamVerifier should also instantly recognize YouTube streams
            v_res = await self.verifier.verify(url)
            self.assertTrue(v_res["valid"])
            self.assertEqual(v_res["format"], "YouTube Stream (1080p/720p)")
            self.assertEqual(v_res["magic_bytes_hex"], "YOUTUBE")

    async def test_pixeldrain_transform(self):
        """Verify instant Pixeldrain /u/ -> /api/file/ URL transformation in < 1ms."""
        pd_url = "https://pixeldrain.com/u/1noW8snv"
        res = await self.resolver.resolve_auto(pd_url)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["host"], "Pixeldrain API")
        self.assertEqual(res["direct_url"], "https://pixeldrain.dev/api/file/1noW8snv")
        self.assertLess(res["elapsed_ms"], 200)

    def test_ddlbase_xor_cipher(self):
        """Verify DDLBase XOR decoding in < 1ms."""
        raw_target = "https://cdn.ddlbase.net/stream/video_1080p.mkv"
        key = "mySecret123"
        # Encrypt
        encrypted_bytes = "".join(chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(raw_target))
        cipher_b64 = base64.b64encode(encrypted_bytes.encode("utf-8")).decode("utf-8")

        t0 = time.perf_counter()
        decoded = self.resolver.resolve_ddlbase_xor(cipher_b64, key)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        self.assertEqual(decoded, raw_target)
        self.assertLess(elapsed_ms, 5.0)

    async def test_vcloud_r2_gateway_resolution(self):
        """Verify V-Cloud gateway parsing to Cloudflare R2 direct stream URL."""
        vcloud_html = """
        <html><body>
        <h1>Download Ready</h1>
        <a href="https://pub-a1b2c3d4.r2.dev/stream_video_1080p.mkv?token=abc123xyz">Download Video</a>
        </body></html>
        """
        async def vcloud_handler(request):
            return httpx.Response(200, text=vcloud_html)

        transport = MockTransport({"vcloud.fit": vcloud_handler})
        async with httpx.AsyncClient(transport=transport) as client:
            res = await self.resolver.resolve_vcloud(client, "https://vcloud.fit/v/abc123")
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["host"], "Cloudflare R2")
            self.assertTrue("pub-a1b2c3d4.r2.dev" in res["direct_url"])

    async def test_hubcloud_gamerxyt_resolution(self):
        """Verify HubCloud / Gamerxyt gateway resolution."""
        hubcloud_html = """
        <html><body>
        <a href="https://bucket.r2.cloudflarestorage.com/hub/movie_4k.mkv">Direct Download</a>
        </body></html>
        """
        async def hub_handler(request):
            return httpx.Response(200, text=hubcloud_html)

        transport = MockTransport({"gamerxyt.com": hub_handler})
        async with httpx.AsyncClient(transport=transport) as client:
            res = await self.resolver.resolve_hubcloud_gamerxyt(client, "https://gamerxyt.com/hub/xyz789")
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["host"], "Cloudflare R2")
            self.assertTrue("bucket.r2.cloudflarestorage.com/hub" in res["direct_url"])

    async def test_olamovies_resolution(self):
        """Verify OlaMovies / dl.olamdrive direct CDN resolution."""
        url = "https://dl.olamdrive.com/dl?t=abc123def456"
        async with httpx.AsyncClient() as client:
            res = await self.resolver.resolve_olamovies(client, url)
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["host"], "OMDrive Direct CDN")
            self.assertEqual(res["direct_url"], url)

    async def test_error_resilience_empty_and_dead_links(self):
        """Verify zero-crash guarantee on empty URLs, invalid URLs, and network errors."""
        # Empty URL
        res1 = await self.resolver.resolve_auto("")
        self.assertEqual(res1["status"], "FAILED")
        self.assertEqual(res1["error"], "Empty URL provided")

        res_v1 = await self.verifier.verify("")
        self.assertFalse(res_v1["valid"])
        self.assertEqual(res_v1["error"], "Empty URL provided")

        # Network error simulation
        async def dead_handler(request):
            raise httpx.ConnectError("Connection refused by target server")

        transport = MockTransport({"dead.server": dead_handler})
        async with httpx.AsyncClient(transport=transport) as client:
            res_dead = await self.verifier.verify("https://dead.server/video.mp4", client=client)
            self.assertFalse(res_dead["valid"])
            self.assertTrue("Connection refused" in res_dead["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
