"""
Live Verification Suite for Phase 1 Integration: JEV Adaptive Dynamic Mirror Engine
Tests:
  1. Dynamic mirror resolution (< 5ms cached) across UHDMovies, HDHub4u, VegaMovies, OlaMovies.
  2. Live search execution & catalog link extraction across UHDMovies, HDHub4u, VegaMovies.
  3. Mirror health tracking & success/failure reporting verification.
"""

import sys
import time
import asyncio
import json
from pathlib import Path
import httpx

from domain_resolver import (
    get_active_mirror,
    get_active_mirror_sync,
    default_resolver
)
from fast_scrapers import FastScrapers

async def run_live_tests():
    print("=" * 70)
    print("  PHASE 1 INTEGRATION LIVE TEST: JEV ADAPTIVE DYNAMIC MIRROR ENGINE")
    print("=" * 70)

    results = {
        "mirrors": {},
        "cache_speed": {},
        "scrapers": {},
        "health": {}
    }

    # 1. Dynamic Mirror Resolution
    print("\n[STEP 1] Testing Dynamic Mirror Resolution...")
    brands = ["uhdmovies", "hdhub4u", "vegamovies", "olamovies"]
    for brand in brands:
        t0 = time.perf_counter()
        mirror = await get_active_mirror(brand)
        t_first = (time.perf_counter() - t0) * 1000

        # Second call to verify cache (< 5ms)
        t1 = time.perf_counter()
        mirror_cached = await get_active_mirror(brand)
        t_cached = (time.perf_counter() - t1) * 1000

        # Sync call
        mirror_sync = get_active_mirror_sync(brand)

        print(f"  [{brand}]")
        print(f"    - Active Mirror : {mirror}")
        print(f"    - First lookup  : {t_first:.2f}ms")
        print(f"    - Cached lookup : {t_cached:.4f}ms (Target: < 5ms)")
        print(f"    - Sync lookup   : {mirror_sync}")

        results["mirrors"][brand] = mirror
        results["cache_speed"][brand] = {
            "first_ms": round(t_first, 2),
            "cached_ms": round(t_cached, 4),
            "valid": t_cached < 5.0 and bool(mirror)
        }

    # 2. Live Scraper Testing with "Deadpool" and "Avengers Endgame"
    test_query = "Deadpool"
    print(f"\n[STEP 2] Testing Live Scrapers with query: '{test_query}'...")
    fs = FastScrapers()

    async with httpx.AsyncClient(http2=True, limits=fs.limits, follow_redirects=True) as client:
        # Test UHDMovies
        print("\n  --> Querying UHDMovies...")
        t0 = time.perf_counter()
        try:
            uhd_results = await fs.search_uhdmovies(client, test_query)
            t_uhd = (time.perf_counter() - t0) * 1000
            print(f"      [OK] UHDMovies returned {len(uhd_results)} results in {t_uhd:.1f}ms")
            if uhd_results:
                print(f"           Sample: {uhd_results[0].get('title')} | {uhd_results[0].get('resolution')} | {uhd_results[0].get('target_url')[:60]}...")
            results["scrapers"]["uhdmovies"] = {
                "count": len(uhd_results),
                "latency_ms": round(t_uhd, 1),
                "success": len(uhd_results) > 0,
                "sample": uhd_results[0] if uhd_results else None
            }
        except Exception as e:
            print(f"      [FAIL] UHDMovies error: {e}")
            results["scrapers"]["uhdmovies"] = {"count": 0, "error": str(e), "success": False}

        # Test HDHub4u
        print("\n  --> Querying HDHub4u...")
        t0 = time.perf_counter()
        try:
            hdh_results = await fs.search_hdhub4u(client, test_query)
            t_hdh = (time.perf_counter() - t0) * 1000
            print(f"      [OK] HDHub4u returned {len(hdh_results)} results in {t_hdh:.1f}ms")
            if hdh_results:
                print(f"           Sample: {hdh_results[0].get('title')} | {hdh_results[0].get('resolution')} | {hdh_results[0].get('target_url')[:60]}...")
            results["scrapers"]["hdhub4u"] = {
                "count": len(hdh_results),
                "latency_ms": round(t_hdh, 1),
                "success": len(hdh_results) > 0,
                "sample": hdh_results[0] if hdh_results else None
            }
        except Exception as e:
            print(f"      [FAIL] HDHub4u error: {e}")
            results["scrapers"]["hdhub4u"] = {"count": 0, "error": str(e), "success": False}

        # Test VegaMovies
        print("\n  --> Querying VegaMovies...")
        t0 = time.perf_counter()
        try:
            vega_results = await fs.search_vegamovies(client, test_query)
            t_vega = (time.perf_counter() - t0) * 1000
            print(f"      [OK] VegaMovies returned {len(vega_results)} results in {t_vega:.1f}ms")
            if vega_results:
                print(f"           Sample: {vega_results[0].get('title')} | {vega_results[0].get('resolution')} | {vega_results[0].get('target_url')[:60]}...")
            results["scrapers"]["vegamovies"] = {
                "count": len(vega_results),
                "latency_ms": round(t_vega, 1),
                "success": len(vega_results) > 0,
                "sample": vega_results[0] if vega_results else None
            }
        except Exception as e:
            print(f"      [FAIL] VegaMovies error: {e}")
            results["scrapers"]["vegamovies"] = {"count": 0, "error": str(e), "success": False}

    # 3. Test Full Concurrent Fleet
    print("\n[STEP 3] Testing search_all_concurrent across all scrapers...")
    t0 = time.perf_counter()
    all_res = await fs.search_all_concurrent("Avengers Endgame")
    t_fleet = (time.perf_counter() - t0) * 1000
    print(f"  [OK] Concurrent fleet returned {len(all_res)} total results in {t_fleet:.1f}ms")
    platforms = set(r.get("platform") for r in all_res)
    print(f"       Platforms represented: {list(platforms)}")
    results["fleet"] = {
        "total_results": len(all_res),
        "total_time_ms": round(t_fleet, 1),
        "platforms": list(platforms)
    }

    # 4. Check Health File
    print("\n[STEP 4] Checking Mirror Health Tracking...")
    health_state = default_resolver._health
    for b in brands:
        h = health_state.get(b, {})
        print(f"  [{b}] Status: {h.get('status')} | Active: {h.get('active_mirror')} | Failures: {h.get('consecutive_failures')}")
        results["health"][b] = h

    return results

if __name__ == "__main__":
    test_results = asyncio.run(run_live_tests())
    print("\n" + "=" * 70)
    print("  VERIFICATION RUN FINISHED")
    print("=" * 70)
