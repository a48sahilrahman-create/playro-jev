import sys
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from parallel_dispatcher import ParallelMediaDispatcher
from link_resolver import LinkResolver
from stream_verifier import StreamVerifier
import asyncio

async def test_movies():
    d = ParallelMediaDispatcher()
    lr = LinkResolver()
    sv = StreamVerifier()

    q = "Michael"
    print(f"=== Searching and Resolving Releases for Movie: '{q}' ===")
    candidates = []
    async for batch in d.stream_all_parallel(q):
        if isinstance(batch, list):
            for item in batch:
                if item.get("target_url"):
                    candidates.append(item)
                    print(f"  Discovered: [{item.get('platform')}] {item.get('size')} | {item.get('resolution')} | {item.get('label')[:45]}")

    print(f"\n[+] Total Discovered Candidates: {len(candidates)}")

    # Test resolving candidates to direct playable EBML streams
    verified_url = None
    for c in candidates:
        plat = c.get("platform")
        t_url = c.get("target_url")
        print(f"\n[*] Resolving stream from [{plat}]: {t_url[:70]}...")
        out = await lr.resolve_auto(t_url)
        print(f"    Status: {out.get('status')} | Host: {out.get('host')} | Direct: {out.get('direct_url', '')[:65]}...")
        if out.get("direct_url"):
            v = await sv.verify(out["direct_url"])
            print(f"    EBML Check: valid={v.get('valid')}, format={v.get('format')}, size={v.get('size')}")
            if v.get("valid"):
                verified_url = out["direct_url"]
                print(f"\n[+] SUCCESS: Verified playable stream found: {verified_url}")
                return verified_url

    return verified_url

if __name__ == "__main__":
    asyncio.run(test_movies())
