import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from stream_verifier import StreamVerifier
from link_resolver import LinkResolver, is_safe_url


class TestStreamVerifierQuotaAndDelimiters:
    """Test suite verifying StreamVerifier handling of quota errors, non-200/206 codes, and :: delimiters."""

    @pytest.mark.asyncio
    async def test_worker_403_quota_exceeded_rejected(self):
        """Mock worker URL returning 403 quota exceeded -> asserts valid: False, format: Quota Exceeded / Error."""
        verifier = StreamVerifier()

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.history = []
        mock_resp.url = httpx.URL("https://worker.dev/stream.mp4")

        async def fake_aiter_bytes():
            yield b'{"error": "downloadQuotaExceeded", "message": "Google Drive quota exceeded"}'

        mock_resp.aiter_bytes = fake_aiter_bytes
        mock_resp.aclose = AsyncMock()

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_stream)

        res = await verifier.verify("https://worker.dev/stream.mp4", client=mock_client)

        assert res["valid"] is False
        assert res["status_code"] == 403
        assert res["detected_format"] == "Quota Exceeded / Error"
        assert res["format"] == "Quota Exceeded / Error"

    @pytest.mark.asyncio
    async def test_worker_200_json_error_body_rejected(self):
        """Worker returning HTTP 200 with 7B 0A / JSON error body -> asserts valid: False."""
        verifier = StreamVerifier()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.history = []
        mock_resp.url = httpx.URL("https://worker.dev/stream.mp4")

        # 7B 0A is b'{\n'
        async def fake_aiter_bytes():
            yield b'{\n  "error": "The download quota for this file has been exceeded."\n}'

        mock_resp.aiter_bytes = fake_aiter_bytes
        mock_resp.aclose = AsyncMock()

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_stream)

        res = await verifier.verify("https://worker.dev/stream.mp4", client=mock_client)

        assert res["valid"] is False
        assert res["detected_format"] == "Quota Exceeded / Error"

    @pytest.mark.asyncio
    async def test_url_with_double_colon_delimiter_no_crash(self):
        """URLs with :: delimiters (headers, tokens, malformed ports) handle cleanly without crash."""
        verifier = StreamVerifier()

        # Mock a valid MKV stream response
        mkv_magic = b'\x1a\x45\xdf\xa3\x9f\x42\x86\x81'
        mock_resp = MagicMock()
        mock_resp.status_code = 206
        mock_resp.headers = {"content-type": "video/x-matroska", "content-range": "bytes 0-8191/1000000000"}
        mock_resp.history = []
        mock_resp.url = httpx.URL("https://worker.dev/stream.mkv")

        async def fake_aiter_bytes():
            yield mkv_magic + b'\x00' * 100

        mock_resp.aiter_bytes = fake_aiter_bytes
        mock_resp.aclose = AsyncMock()

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_stream)

        test_urls = [
            "https://worker.dev/stream.mkv::custom_token",
            "https://worker.dev/stream.mkv::Referer=https://foo.com",
            "https://worker.dev/stream.mkv::token::extra_param",
            "https://worker.dev/stream.mkv::Referer=https://foo.com::User-Agent=VLC",
            "https://worker.dev::8080/stream.mkv",
        ]

        for u in test_urls:
            res = await verifier.verify(u, client=mock_client)
            assert isinstance(res, dict)
            assert "valid" in res
            assert "detected_format" in res

    @pytest.mark.asyncio
    async def test_500_server_error_rejected(self):
        """Worker returning HTTP 500 -> asserts valid: False, format: Quota Exceeded / Error."""
        verifier = StreamVerifier()

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.headers = {"content-type": "text/plain"}
        mock_resp.history = []
        mock_resp.url = httpx.URL("https://worker.dev/stream.mp4")

        async def fake_aiter_bytes():
            yield b"Internal Server Error"

        mock_resp.aiter_bytes = fake_aiter_bytes
        mock_resp.aclose = AsyncMock()

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_stream)

        res = await verifier.verify("https://worker.dev/stream.mp4", client=mock_client)

        assert res["valid"] is False
        assert res["status_code"] == 500
        assert res["detected_format"] == "Quota Exceeded / Error"


class TestLinkResolverQuotaAndInteractiveProtection:
    """Test suite verifying LinkResolver candidate probe, quota rejection, and Turnstile protections."""

    @pytest.mark.asyncio
    async def test_resolve_candidate_quota_exceeded_rejected(self):
        """resolve_candidate probes worker URL and rejects quota exceeded with QUOTA_EXCEEDED status."""
        resolver = LinkResolver()
        mock_client = AsyncMock()

        mock_probe_resp = MagicMock()
        mock_probe_resp.status_code = 403
        mock_probe_resp.text = '{"error": {"code": 403, "message": "The download quota for this file has been exceeded."}}'
        mock_client.get = AsyncMock(return_value=mock_probe_resp)

        candidate = {
            "type": "worker_direct",
            "url": "https://worker.dev/download/movie.mp4",
            "label": "Cloud Resume Worker"
        }

        res = await resolver.resolve_candidate(mock_client, candidate)

        assert res["status"] == "QUOTA_EXCEEDED"
        assert res["error"] == "Google Drive quota exceeded on Cloudflare Worker"
        assert res["direct_url"] == "https://worker.dev/download/movie.mp4"
        mock_client.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resolve_candidate_quota_keyword_in_body_rejected(self):
        """resolve_candidate rejects 200 OK responses that contain downloadQuotaExceeded text."""
        resolver = LinkResolver()
        mock_client = AsyncMock()

        mock_probe_resp = MagicMock()
        mock_probe_resp.status_code = 200
        mock_probe_resp.text = "Google Drive - downloadQuotaExceeded"
        mock_client.get = AsyncMock(return_value=mock_probe_resp)

        candidate = {
            "type": "r2_direct",
            "url": "https://pub.r2.dev/stream.mp4",
            "label": "R2 Direct CDN"
        }

        res = await resolver.resolve_candidate(mock_client, candidate)

        assert res["status"] == "QUOTA_EXCEEDED"
        assert res["error"] == "Google Drive quota exceeded on Cloudflare Worker"
        assert res["direct_url"] == "https://pub.r2.dev/stream.mp4"

    @pytest.mark.asyncio
    async def test_resolve_candidate_healthy_probe_success(self):
        """resolve_candidate returns SUCCESS when probe responds healthy 200/206."""
        resolver = LinkResolver()
        mock_client = AsyncMock()

        mock_probe_resp = MagicMock()
        mock_probe_resp.status_code = 206
        mock_probe_resp.text = ""
        mock_client.get = AsyncMock(return_value=mock_probe_resp)

        candidate = {
            "type": "worker_direct",
            "url": "https://worker.dev/download/movie.mp4",
            "label": "Cloud Resume Worker"
        }

        res = await resolver.resolve_candidate(mock_client, candidate)

        assert res["status"] == "SUCCESS"
        assert res["direct_url"] == "https://worker.dev/download/movie.mp4"
        assert res["host"] == "Cloud Resume Worker"

    @pytest.mark.asyncio
    async def test_resolve_olamovies_default_allow_interactive_false(self):
        """resolve_olamovies defaults to allow_interactive=False and never launches interactive login window."""
        resolver = LinkResolver()
        mock_client = AsyncMock()

        # Mock stealth solver returning None (automated solve failed) and ensure is_safe_url is mocked for synthetic test URL
        with patch("link_resolver.is_safe_url", return_value=True):
            with patch.object(resolver, "_solve_olamovies_stealth", return_value=None):
                with patch.object(resolver.auth_bridge, "trigger_interactive_login", new_callable=AsyncMock) as mock_interactive:
                    res = await resolver.resolve_olamovies(mock_client, "https://links.olamovies.top/item/456")

                    assert res["status"] == "CLOUDFLARE_PROTECTED"
                    assert res["error"] == "Cloudflare Turnstile Active (Automated solve failed)"
                    # Must NOT have launched interactive login
                    mock_interactive.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_auto_default_allow_interactive_false(self):
        """resolve_auto has allow_interactive defaulting to False."""
        import inspect
        sig = inspect.signature(LinkResolver.resolve_auto)
        assert "allow_interactive" in sig.parameters
        assert sig.parameters["allow_interactive"].default is False
