import sys
sys.path.insert(0, r'C:\Users\sahil\.claude\skills\play\tools')
import asyncio
import httpx
from link_resolver import LinkResolver
from parallel_dispatcher import ParallelMediaDispatcher
from stream_verifier import StreamVerifier

async def test_vega_obsession():
    dp = ParallelMediaDispatcher()
    verifier = StreamVerifier()
    resolver = LinkResolver()

    async with httpx.AsyncClient(follow_redirects=True) as c:
        hits = await dp.fetch_vegamovies(c, "Obsession")
    print(f"Total Vega hits for Obsession: {len(hits)}")

    for i, h in enumerate(hits[:10], 1):
        print(f"\nVega #{i}: {h.get('title')} [{h.get('resolution')}] ({h.get('size')})")
        print(f"  Target URL: {h.get('target_url')}")
        resolved = await resolver.resolve_auto(h.get('target_url'))
        direct_url = resolved.get("direct_url")
        print(f"  Host: {resolved.get('host')} | Direct URL: {direct_url[:80]}...")
        v = await verifier.verify(direct_url)
        print(f"  Verification: valid={v.get('valid')}, format={v.get('format')}, size={v.get('size')}")

if __name__ == '__main__':
    asyncio.run(test_vega_obsession())
