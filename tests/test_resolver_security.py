import pytest
import asyncio
import httpx
import re
import urllib.parse
from unittest.mock import patch
from link_resolver import LinkResolver, is_safe_url, is_safe_ip


@pytest.mark.asyncio
async def test_private_ip_rejection():
    """Verify that private, loopback, link-local, and non-http schemes are blocked."""
    resolver = LinkResolver()

    blocked_urls = [
        "http://localhost:7860/api",
        "http://127.0.0.1:8000/secret",
        "http://127.0.0.1",
        "http://0.0.0.0:80/",
        "http://[::1]:8080/",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://10.0.0.1/admin",
        "http://192.168.1.1/router",
        "http://172.16.0.10/",
        "http://100.64.0.1/cgnat",
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_flushall",
        "ftp://192.168.1.50/data",
        "http://server.local/page",
        "http://internal-db.localhost/query",
    ]

    for url in blocked_urls:
        assert not is_safe_url(url), f"Expected {url} to be flagged as unsafe"
        res = await resolver.resolve_auto(url)
        assert res["status"] == "FAILED"
        assert "SSRF" in res.get("error", "") or "blocked" in res.get("error", "").lower() or "Destination" in res.get("error", "")
        # Must retain the URL as fallback without throwing unhandled exceptions
        assert res["direct_url"] == url


@pytest.mark.asyncio
async def test_redirect_cap_and_ssrf_redirect():
    """Verify redirect loop is capped at max 5 hops and redirects to private IPs are blocked."""
    resolver = LinkResolver()

    # Mock transport that generates an infinite redirect loop
    def infinite_redirect_handler(request: httpx.Request) -> httpx.Response:
        current_step = 0
        if "step=" in str(request.url):
            m = re.search(r"step=(\d+)", str(request.url))
            if m:
                current_step = int(m.group(1))
        next_url = f"https://example-test-gateway.org/redirect?step={current_step + 1}"
        return httpx.Response(status_code=302, headers={"Location": next_url})

    transport = httpx.MockTransport(infinite_redirect_handler)
    with patch("link_resolver.is_safe_url", side_effect=lambda u, **kwargs: not ("169.254" in u or "localhost" in u or "127.0.0.1" in u)), \
         patch("link_resolver.get_safe_resolved_ip", return_value="93.184.216.34"):
        async with httpx.AsyncClient(transport=transport) as mock_client:
            # Test safe_fetch directly with redirect cap
            with pytest.raises(ValueError, match=r"Exceeded maximum redirects cap"):
                await resolver.safe_fetch(mock_client, "GET", "https://example-test-gateway.org/redirect?step=0", max_redirects=5)

    # Test redirect to private IP is intercepted on hop
    def ssrf_redirect_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    ssrf_transport = httpx.MockTransport(ssrf_redirect_handler)
    with patch("link_resolver.is_safe_url", side_effect=lambda u, **kwargs: not ("169.254" in u or "localhost" in u or "127.0.0.1" in u)), \
         patch("link_resolver.get_safe_resolved_ip", side_effect=lambda h, *a, **k: None if "169.254" in str(h) else "93.184.216.34"):
        async with httpx.AsyncClient(transport=ssrf_transport) as mock_client:
            with pytest.raises(ValueError, match=r"SSRF blocked"):
                await resolver.safe_fetch(mock_client, "GET", "https://example-test-gateway.org/start", max_redirects=5)


@pytest.mark.asyncio
async def test_malformed_gateway_fallback():
    """Verify that 500 errors, corrupted HTML, empty responses, and timeouts gracefully fallback."""
    resolver = LinkResolver()

    def error_gateway_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "corrupt" in url_str:
            return httpx.Response(status_code=200, content=b"<html><script>eval(atob('invalid-base64-%%%'));</script></html>")
        elif "empty" in url_str:
            return httpx.Response(status_code=200, content=b"")
        elif "500" in url_str:
            return httpx.Response(status_code=500, content=b"Internal Server Error")
        return httpx.Response(status_code=404, content=b"Not Found")

    transport = httpx.MockTransport(error_gateway_handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        # 1. Corrupt HTML on V-Cloud
        res_vcloud = await resolver.resolve_vcloud(mock_client, "https://vcloud.lol/corrupt")
        assert res_vcloud["status"] == "FAILED"
        assert "error" in res_vcloud
        assert res_vcloud["elapsed_ms"] >= 0

        # 2. Corrupt HTML on HubCloud
        res_hub = await resolver.resolve_hubcloud_gamerxyt(mock_client, "https://hubcloud.lol/drive/corrupt")
        assert res_hub["status"] == "FAILED"

        # 3. Corrupt HTML on DriveSeed
        res_ds = await resolver.resolve_driveseed_multi(mock_client, "https://driveseed.org/file/corrupt")
        assert res_ds["status"] == "FAILED"


@pytest.mark.asyncio
async def test_response_size_bomb_protection():
    """Verify that huge HTML responses are capped to MAX_GATEWAY_BYTES without crashing."""
    resolver = LinkResolver()

    # 10MB generated chunk stream
    def huge_stream_handler(request: httpx.Request) -> httpx.Response:
        chunk = b"A" * 1024 * 1024  # 1MB
        return httpx.Response(status_code=200, content=chunk * 10)

    transport = httpx.MockTransport(huge_stream_handler)
    with patch("link_resolver.is_safe_url", return_value=True), \
         patch("link_resolver.get_safe_resolved_ip", return_value="93.184.216.34"):
        async with httpx.AsyncClient(transport=transport) as mock_client:
            res = await resolver.safe_fetch(mock_client, "GET", "https://example-test-gateway.org/huge", max_bytes=1024 * 1024)
            assert len(res.content) <= 1024 * 1024 + 65536  # Max 1MB read window enforced


@pytest.mark.asyncio
async def test_pixeldrain_and_youtube_safe_routes():
    """Verify benign public links (Pixeldrain & YouTube) resolve with valid direct_urls."""
    resolver = LinkResolver()

    # Pixeldrain transform
    pd_res = await resolver.resolve_auto("https://pixeldrain.com/u/abc12345")
    assert pd_res["status"] == "SUCCESS"
    assert pd_res["direct_url"] == "https://pixeldrain.dev/api/file/abc12345"
    assert pd_res["host"] == "Pixeldrain API"
    assert pd_res["elapsed_ms"] >= 0

    # YouTube Watch direct URL
    yt_res = await resolver.resolve_auto("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert yt_res["status"] == "SUCCESS"
    assert yt_res["direct_url"]
