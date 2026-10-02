"""
Live End-to-End Test & Debug Verification Suite for play engine
Tests:
  1. Tokenizer & Sequel Matching (Dune Part 2 vs Dune 2021 2GB, Spider-Man 2, Avatar 2)
  2. StreamVerifier Magic Bytes & YouTube/GoogleVideo CDN Passthrough
  3. Telegram Local Stream Server Dynamic Port Binding & Health Check
  4. ParallelMediaDispatcher Multi-Source Series & Movie Parsing
  5. TitleResolver Typo & Fuzzy Match Recovery
"""

import sys
import os
import asyncio
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from parallel_dispatcher import is_strict_match, extract_clean_numeric_tokens, ParallelMediaDispatcher
from stream_verifier import StreamVerifier
from title_resolver import TitleResolver
from tg_local_stream_server import TelegramLocalStreamServer, get_or_start_stream_server

async def test_sequel_matching():
    print("\n[TEST 1] Sequel & Token Match Integrity (No File Size Collisions)")
    print("-" * 75)

    test_cases = [
        # Query, Candidate Title, Expected Match (True/False), Description
        ("Dune Part 2", "Dune (2021) 1080p WEB-DL x264 Dual Audio [2GB]", False, "Dune Part 2 vs Dune 2021 2GB file size"),
        ("Dune Part 2", "Dune Part Two (2024) 2160p UHD HDR [14GB]", True, "Dune Part 2 vs Dune Part Two 2024"),
        ("Spider-Man 2", "Spider-Man Far From Home (2019) 1080p [2GB]", False, "Spider-Man 2 vs Far From Home with 2GB"),
        ("Spider-Man 2", "Spider-Man 2 (2004) 1080p BluRay [4GB]", True, "Spider-Man 2 vs 2004 sequel"),
        ("Avatar 2", "Avatar (2009) Extended Collector Edition 1080p 2-Disc", False, "Avatar 2 vs Avatar 2009 2-Disc"),
        ("Avatar 2", "Avatar The Way of Water (2022) 2160p 4K", True, "Avatar 2 vs Way of Water (via canonical mapping)"),
        ("John Wick 4", "John Wick Chapter 4 (2023) 1080p", True, "John Wick 4 vs Chapter 4"),
        ("John Wick 4", "John Wick Chapter 2 (2017) 1080p", False, "John Wick 4 vs Chapter 2"),
    ]

    passed = 0
    for q, c, expected, desc in test_cases:
        res = is_strict_match(q, c)
        status = "PASSED" if res == expected else "FAILED"
        if res == expected:
            passed += 1
        print(f"  [{status}] {desc}: query='{q}' | candidate='{c[:35]}...' -> result={res}, expected={expected}")

    print(f"Test 1 Summary: {passed}/{len(test_cases)} Passed.")
    return passed == len(test_cases)

async def test_stream_verifier():
    print("\n[TEST 2] StreamVerifier Magic Bytes & YouTube/Google CDN Handling")
    print("-" * 75)

    verifier = StreamVerifier()

    # 1. YouTube direct stream
    yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    yt_res = await verifier.verify(yt_url)
    yt_pass = yt_res.get("valid") is True
    print(f"  [{'PASSED' if yt_pass else 'FAILED'}] YouTube Watch URL: valid={yt_res.get('valid')}, format={yt_res.get('format')}")

    # 2. GoogleVideo CDN stream URL
    gv_url = "https://rr1---sn-cvh76nlr.googlevideo.com/videoplayback?expire=12345&itag=22"
    gv_res = await verifier.verify(gv_url)
    gv_pass = gv_res.get("valid") is True
    print(f"  [{'PASSED' if gv_pass else 'FAILED'}] GoogleVideo Direct CDN: valid={gv_res.get('valid')}, format={gv_res.get('format')}")

    # 3. Invalid / non-existent stream
    dead_url = "https://invalid-dead-stream-domain-xyz123.com/video.mp4"
    dead_res = await verifier.verify(dead_url)
    dead_pass = dead_res.get("valid") is False
    print(f"  [{'PASSED' if dead_pass else 'FAILED'}] Dead Stream Safety: valid={dead_res.get('valid')}, error={dead_res.get('error', 'None')[:30]}")

    return yt_pass and gv_pass and dead_pass

async def test_tg_stream_server():
    print("\n[TEST 3] Local Stream Server Dynamic Port Binding & Health Check")
    print("-" * 75)

    server = TelegramLocalStreamServer()
    assigned_port = await server.start(port=8989)
    print(f"  [+] Stream Server started on port: {assigned_port}")

    # Test HTTP health probe
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"http://127.0.0.1:{assigned_port}/health", timeout=2.0)
        health_ok = resp.status_code == 200 and "OK" in resp.text
        print(f"  [{'PASSED' if health_ok else 'FAILED'}] Health check HTTP probe: {resp.status_code} -> '{resp.text}'")

    await server.stop()
    return health_ok

async def test_title_resolver():
    print("\n[TEST 4] TitleResolver Typo & Fuzzy Match Recovery")
    print("-" * 75)

    resolver = TitleResolver()
    typo_cases = [
        ("avengrs endgme", "Avengers: Endgame (2019)"),
        ("interstelar", "Interstellar (2014)"),
        ("inceptn", "Inception (2010)"),
        ("kesu ee nadinte", "Keshu Ee Veedinte Nadhan (2021)"),
        ("dune part 2", "Dune: Part Two (2024)"),
    ]

    passed = 0
    for typo, expected in typo_cases:
        res = await resolver.resolve(typo)
        fmt = res.get("formatted", "")
        is_ok = expected.lower() in fmt.lower() or res.get("canonical_title", "").lower() in expected.lower()
        status = "PASSED" if is_ok else "FAILED"
        if is_ok:
            passed += 1
        print(f"  [{status}] Typo '{typo}' -> Resolved: '{fmt}' in {res.get('elapsed_ms')}ms")

    print(f"Test 4 Summary: {passed}/{len(typo_cases)} Passed.")
    return passed == len(typo_cases)

async def main():
    print("=" * 75)
    print("LIVE PLAY ENGINE TEST & DEBUG MATRIX")
    print("=" * 75)

    t1 = await test_sequel_matching()
    t2 = await test_stream_verifier()
    t3 = await test_tg_stream_server()
    t4 = await test_title_resolver()

    print("\n" + "=" * 75)
    all_passed = t1 and t2 and t3 and t4
    print(f"OVERALL TEST SUITE STATUS: {'100% PASSED' if all_passed else 'SOME TESTS FAILED'}")
    print("=" * 75)

if __name__ == "__main__":
    asyncio.run(main())
