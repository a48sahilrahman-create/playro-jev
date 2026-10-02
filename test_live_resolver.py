import asyncio
import time
from link_resolver import LinkResolver

async def test_live_resolutions():
    resolver = LinkResolver()

    test_urls = [
        ("HDHub4u (HubDrive)", "https://hubdrive.pics/file/2545044776"),
        ("VegaMovies (NexDrive)", "https://nexdrive.fit/genxfm784776393874/"),
        ("UHDMovies (Thenaukriadda Gateway)", "https://en.thenaukriadda.in/?sid=c2V2M3NYZlh2d0R5SEx4ZXZoQ0tDdz09&id=67b2ff929d28e469502fdbe4")
    ]

    for name, url in test_urls:
        print("=" * 60)
        print(f"Resolving: {name}")
        print(f"Input URL: {url}")
        t0 = time.perf_counter()
        res = await resolver.resolve_auto(url)
        elapsed = time.perf_counter() - t0
        print(f"Status: {res.get('status')}")
        print(f"Host: {res.get('host')}")
        direct = res.get('direct_url', '')
        print(f"Direct Stream URL: {direct[:90]}..." if direct else "No direct URL")
        print(f"Elapsed Time: {elapsed:.2f}s")

if __name__ == "__main__":
    asyncio.run(test_live_resolutions())
