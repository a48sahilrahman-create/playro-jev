import asyncio
import time
import httpx
from fast_scrapers import FastScrapers
from link_resolver import LinkResolver

async def test_fresh_uhd():
    fs = FastScrapers()
    resolver = LinkResolver()

    async with httpx.AsyncClient(http2=True, follow_redirects=True, timeout=10.0) as client:
        print("Scraping fresh UHDMovies release for Inception...")
        releases = await fs.search_uhdmovies(client, "Inception")
        print(f"Found {len(releases)} UHDMovies releases.")

        # Pick the first thenaukriadda / gateway link
        target = None
        for r in releases:
            url = r.get("target_url", "")
            if "thenaukriadda" in url or "techmody" in url or "driveseed" in url:
                target = r
                break

        if not target:
            print("No gateway target found in releases:", [r.get("target_url") for r in releases[:3]])
            return

        print(f"Testing fresh release: {target['label']}")
        print(f"Gateway URL: {target['target_url']}")

        t0 = time.perf_counter()
        res = await resolver.resolve_auto(target['target_url'])
        elapsed = time.perf_counter() - t0

        print("Status:", res.get("status"))
        print("Host:", res.get("host"))
        print("Direct URL:", res.get("direct_url"))
        print(f"Elapsed: {elapsed:.2f}s")

if __name__ == "__main__":
    asyncio.run(test_fresh_uhd())
