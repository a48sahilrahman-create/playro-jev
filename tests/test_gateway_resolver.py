import pytest
import httpx
from link_resolver import resolve_gateway_url, resolve_stream_url, LinkResolver, _decode_greenmotors_token


@pytest.mark.asyncio
async def test_resolve_gateway_url_passthrough():
    """Non-gateway URLs should be returned as-is without network calls."""
    assert await resolve_gateway_url("https://example.com/movie.mp4") == "https://example.com/movie.mp4"
    assert await resolve_gateway_url("https://google.com") == "https://google.com"
    assert await resolve_gateway_url("") == ""


@pytest.mark.asyncio
async def test_greenmotors_redirect_302():
    """Greenmotors URLs redirecting via HTTP 302 to hubcloud.one should be resolved."""
    class RedirectTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if "greenmotors.club" in str(request.url):
                return httpx.Response(302, headers={"Location": "https://hubcloud.one/drive/target123"}, request=request)
            return httpx.Response(200, text="OK", request=request)

    async with httpx.AsyncClient(transport=RedirectTransport(), follow_redirects=True) as client:
        res = await resolve_gateway_url("https://greenmotors.club/?id=xyz", client=client)
        assert res == "https://hubcloud.one/drive/target123"


@pytest.mark.asyncio
async def test_greenmotors_js_replace():
    """Greenmotors HTML with window.location.replace to hubcloud should be extracted."""
    class GMJsTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            html = '<script>window.location.replace("https://hubcloud.club/drive/from_js");</script>'
            return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=GMJsTransport()) as client:
        res = await resolve_gateway_url("https://greenmotors.club/?id=xyz", client=client)
        assert res == "https://hubcloud.club/drive/from_js"


@pytest.mark.asyncio
async def test_greenmotors_cc_anchor():
    """Greenmotors.cc HTML with anchor pointing to gamerxyt.com should be extracted."""
    class GMAnchorTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            html = '<div><a href="https://gamerxyt.com/?id=anchor_target">Download</a></div>'
            return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=GMAnchorTransport()) as client:
        res = await resolve_gateway_url("https://greenmotors.cc/?id=xyz", client=client)
        assert res == "https://gamerxyt.com/?id=anchor_target"


@pytest.mark.asyncio
async def test_greenmotors_hubdrive_anchor():
    """Greenmotors HTML with anchor pointing to hubdrive.pics should be extracted."""
    class GMHubDriveTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            html = '<div><a href="https://hubdrive.pics/file/hd123">Stream Here</a></div>'
            return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=GMHubDriveTransport()) as client:
        res = await resolve_gateway_url("https://greenmotors.club/?id=xyz", client=client)
        assert res == "https://hubdrive.pics/file/hd123"


@pytest.mark.asyncio
async def test_hdstream4u_video_tag():
    """HDStream4u HTML with <video src="..."> should return the video URL."""
    class HDVideoTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            html = '<html><body><video src="https://cdn.stream.com/movie.mp4"></video></body></html>'
            return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=HDVideoTransport()) as client:
        res = await resolve_gateway_url("https://hdstream4u.com/file/12345", client=client)
        assert res == "https://cdn.stream.com/movie.mp4"


@pytest.mark.asyncio
async def test_hdstream4u_js_file():
    """HDStream4u HTML with JS file: "..." player config should return video stream."""
    class HDJsFileTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            html = '<script>var player = jwplayer("v").setup({file: "https://stream.server.org/video.mp4?token=123"});</script>'
            return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=HDJsFileTransport()) as client:
        res = await resolve_gateway_url("https://hdstream4u.com/file/67890", client=client)
        assert res == "https://stream.server.org/video.mp4?token=123"


@pytest.mark.asyncio
async def test_hdstream4u_hubcloud_anchor():
    """HDStream4u HTML with HubCloud button should return HubCloud URL."""
    class HDHubTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            html = '<html><body><a href="https://hubcloud.one/drive/hd_hub">HubCloud Direct</a></body></html>'
            return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=HDHubTransport()) as client:
        res = await resolve_gateway_url("https://hdstream4u.com/file/abcde", client=client)
        assert res == "https://hubcloud.one/drive/hd_hub"


@pytest.mark.asyncio
async def test_unresolved_returns_original():
    """If gateway cannot be resolved, return original url."""
    class FailTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, text="<html><body>No links here</body></html>", request=request)

    async with httpx.AsyncClient(transport=FailTransport()) as client:
        url = "https://greenmotors.club/?id=unresolved"
        res = await resolve_gateway_url(url, client=client)
        assert res == url


@pytest.mark.asyncio
async def test_resolve_stream_url_routing():
    """resolve_stream_url should unpack greenmotors and route to resolve_hubcloud_gamerxyt."""
    class FullRouteTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            url_str = str(request.url)
            if "greenmotors.club" in url_str:
                html = '<script>window.location.replace("https://hubcloud.one/drive/final_hub");</script>'
                return httpx.Response(200, text=html, request=request)
            elif "hubcloud.one" in url_str:
                html = '<a href="https://pixeldrain.com/u/abcdef12">Pixeldrain</a>'
                return httpx.Response(200, text=html, request=request)
            return httpx.Response(200, text="OK", request=request)

    async with httpx.AsyncClient(transport=FullRouteTransport()) as client:
        res = await resolve_stream_url("https://greenmotors.club/?id=final_test", client=client)
        assert res.get("status") == "SUCCESS"
        assert res.get("direct_url") == "https://pixeldrain.dev/api/file/abcdef12"


@pytest.mark.asyncio
async def test_resolve_stream_url_hdstream4u_direct():
    """resolve_stream_url should unpack hdstream4u direct video without HubCloud."""
    class HDDirectTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            html = '<video src="https://cdn.example.com/video.mp4"></video>'
            return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=HDDirectTransport()) as client:
        res = await resolve_stream_url("https://hdstream4u.com/file/direct_test", client=client)
        assert res.get("status") == "SUCCESS"
        assert res.get("direct_url") == "https://cdn.example.com/video.mp4"


def test_decode_greenmotors_token():
    """Verify 5-layer deobfuscation: Base64 -> Base64 -> ROT13 -> Base64 -> JSON -> Base64(o)."""
    token = "Y214WE0xWjNZbXRhVUdwMmIxQldObFo2ZFRCeFZVOXRRbmxxYVV0UU9XRndla2w1YjNveGFYRlVPV3h3YkRWM1RERnFhVzVVT1dkTlNtdDFiM3BGZVhCNWFtbFdkbXAyYjJ4V05sWjZVMVpJZDA5M1JsSXdNa2RWZURWdk1rVkxSbnBqZGtWdGVHdEtlRm94Y0ZSYWJVaExUVzVHVW1OcVRWUXhTWEY1ZVV0YVZYVnVXakp3YTB4NWRVWnVWRk5MV2twbmJrVXlPVFJXWVRBOQ=="
    hub_url = _decode_greenmotors_token(token)
    assert hub_url == "https://hubcloud.ist/drive/am1gx5mtaimddj1"


@pytest.mark.asyncio
async def test_greenmotors_js_token_challenge():
    """GreenMotors HTML with s('o', token, ...) should be extracted and decoded."""
    class GMChallengeTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            url_str = str(request.url)
            if "greenmotors.club" in url_str:
                token = "Y214WE0xWjNZbXRhVUdwMmIxQldObFo2ZFRCeFZVOXRRbmxxYVV0UU9XRndla2w1YjNveGFYRlVPV3h3YkRWM1RERnFhVzVVT1dkTlNtdDFiM3BGZVhCNWFtbFdkbXAyYjJ4V05sWjZVMVpJZDA5M1JsSXdNa2RWZURWdk1rVkxSbnBqZGtWdGVHdEtlRm94Y0ZSYWJVaExUVzVHVW1OcVRWUXhTWEY1ZVV0YVZYVnVXakp3YTB4NWRVWnVWRk5MV2twbmJrVXlPVFJXWVRBOQ=="
                html = f"""
                <script>
                function s(k, v, t) {{ localStorage.setItem(k, JSON.stringify({{value: v, expiry: Date.now() + t}})); }}
                s('o', '{token}', 180000);
                setTimeout(function() {{ window.location.href = "https://greenmotors.cc/homelander/"; }}, 100);
                </script>
                """
                return httpx.Response(200, text=html, request=request)
            return httpx.Response(200, text="OK", request=request)

    async with httpx.AsyncClient(transport=GMChallengeTransport()) as client:
        res = await resolve_gateway_url("https://greenmotors.club/?id=test_challenge", client=client)
        assert res == "https://hubcloud.ist/drive/am1gx5mtaimddj1"

