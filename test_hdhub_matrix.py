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

async def test_full_matrix():
    d = ParallelMediaDispatcher()
    lr = LinkResolver()
    sv = StreamVerifier()

    q = "Obsession"
    print(f"=== Multi-Platform Release Discovery for '{q}' ===")
    total_releases = 0
    hdhub_releases = []

    async for batch in d.stream_all_parallel(q):
        if isinstance(batch, list) and batch:
            plat = batch[0].get("platform", "Unknown")
            print(f"\n[+] Discovered {len(batch)} releases from [{plat}]:")
            for item in batch:
                total_releases += 1
                if item.get("platform") == "HDHub4u":
                    hdhub_releases.append(item)
                print(f"  [{item.get('platform')}] {item.get('size')} | {item.get('resolution')} | {item.get('label')[:50]}")

    print(f"\n[+] Total Discovered Releases across all sources: {total_releases}")
    print(f"[+] Total HDHub4u Releases: {len(hdhub_releases)}")

    # Resolve and verify HDHub4u release
    if hdhub_releases:
        test_hdh = hdhub_releases[3] # 1080p release
        print(f"\n[*] Resolving HDHub4u target: {test_hdh.get('label')} -> {test_hdh.get('target_url')}")
        res = await lr.resolve_auto(test_hdh.get("target_url"))
        print(f"    Resolved Status: {res.get('status')} | Host: {res.get('host')}")
        print(f"    Direct URL: {res.get('direct_url', '')[:80]}...")
        if res.get("direct_url"):
            ver = await sv.verify(res["direct_url"])
            print(f"    EBML Verification: valid={ver.get('valid')}, format={ver.get('format')}, size={ver.get('size')}")

if __name__ == "__main__":
    asyncio.run(test_full_matrix())
