"""
Live Test Suite for Dynamic Multi-Provider Link Retriever and Closest-Size Quality Matcher
"""

import sys
import os
import asyncio
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from link_resolver import LinkResolver
from play_app import parse_size_in_mb
from stream_verifier import StreamVerifier

async def test_dynamic_driveseed_harvesting():
    print("\n[TEST 1] Dynamic DriveSeed Multi-Provider Harvesting & Cloudflare Worker Extraction")
    print("-" * 80)
    resolver = LinkResolver()
    verifier = StreamVerifier()

    test_url = "https://driveseed.org/file/k2guEv6YdQfP8LTZfZmj"
    res = await resolver.resolve_auto(test_url)
    print("  Resolver result:", res)

    is_success = res.get("status") == "SUCCESS"
    direct_url = res.get("direct_url", "")
    print(f"  [{'PASSED' if is_success else 'FAILED'}] Resolved Host: {res.get('host')}")

    if direct_url:
        v = await verifier.verify(direct_url)
        print(f"  [{'PASSED' if v.get('valid') else 'FAILED'}] Stream EBML: valid={v.get('valid')}, format={v.get('format')}, size={v.get('size')}")
        return is_success and v.get("valid") is True
    return False

def test_closest_size_sorting():
    print("\n[TEST 2] Closest File Size Calculation & Ranking")
    print("-" * 80)

    target_size_str = "20.05GB"
    target_mb = parse_size_in_mb(target_size_str)
    print(f"  Target size parsed: '{target_size_str}' -> {target_mb:.2f} MB")

    candidates = [
        {"title": "Release A", "resolution": "2160P", "size": "5.8GB"},
        {"title": "Release B", "resolution": "2160P", "size": "19.94GB"},
        {"title": "Release C", "resolution": "2160P", "size": "47.7GB"},
        {"title": "Release D", "resolution": "1080P", "size": "2.2GB"},
    ]

    # Sort candidates by distance to target size (20.05GB)
    candidates.sort(key=lambda x: abs(parse_size_in_mb(x.get("size", "")) - target_mb))
    print("  Ranked closest candidates to 20.05GB:")
    for idx, c in enumerate(candidates, 1):
        diff = abs(parse_size_in_mb(c["size"]) - target_mb)
        print(f"    #{idx}: {c['title']} [{c['resolution']}] size={c['size']} (diff={diff:.2f} MB)")

    # Closest should be Release B (19.94GB, diff ~ 112MB)
    is_correct = candidates[0]["title"] == "Release B"
    print(f"  [{'PASSED' if is_correct else 'FAILED'}] Top ranked release: {candidates[0]['title']} ({candidates[0]['size']})")
    return is_correct

async def main():
    print("=" * 80)
    print("DYNAMIC LINK RETRIEVER & SIZE MATCHER TEST SUITE")
    print("=" * 80)

    t1 = await test_dynamic_driveseed_harvesting()
    t2 = test_closest_size_sorting()

    print("\n" + "=" * 80)
    all_ok = t1 and t2
    print(f"STATUS: {'100% PASSED' if all_ok else 'FAILED'}")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
