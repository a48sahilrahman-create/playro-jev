r"""
Test Suite: Live Real-World Catalog Search APIs
Target Directory: D:\playro_jev

Verifies:
1. Pingora Typesense Cluster Search:
   - https://search.pingora.fyi/collections/post/documents/search?q=Batman&query_by=post_title,category,stars,director,imdb_id
   - Referer: https://hdhub4u.tv/ and active mirror
2. Pingora DNS Endpoint:
   - https://dns.pingora.fyi/v2/host
   - Verifies base64 decoding of 'c' (full target URL) and 'h' (host domain)
3. VegaMovies Active Mirror Resolution & Search Proxy:
   - Edge resolver (https://vglist.lol/?re=vegamovies + landing trampoline)
   - ts-search.php query on live resolved mirror (https://new1.vegamovies.lease/ts-search.php?q=Batman)
"""

import sys
import time
import json
import base64
import asyncio
from typing import Dict, Any, Optional
import httpx

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def test_pingora_typesense(client: httpx.AsyncClient, referer: str = "https://hdhub4u.tv/") -> Dict[str, Any]:
    """Test 1: Query Pingora Typesense Cluster directly for HDHub4u catalog."""
    url = "https://search.pingora.fyi/collections/post/documents/search"
    params = {
        "q": "Batman",
        "query_by": "post_title,category,stars,director,imdb_id",
        "query_by_weights": "4,2,2,2,4",
        "sort_by": "sort_by_date:desc",
        "limit": 10,
        "highlight_fields": "none",
        "use_cache": "true",
        "page": 1,
    }
    headers = {
        **DEFAULT_HEADERS,
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
    }

    t0 = time.perf_counter()
    resp = await client.get(url, params=params, headers=headers, timeout=8.0)
    latency_ms = (time.perf_counter() - t0) * 1000

    assert resp.status_code == 200, f"Expected 200 from Typesense, got {resp.status_code}: {resp.text[:200]}"
    data = resp.json()

    # Structure validation
    assert "found" in data, "Payload missing 'found' field"
    assert "hits" in data, "Payload missing 'hits' field"
    assert isinstance(data["hits"], list), "'hits' must be a list"
    assert len(data["hits"]) > 0, "Expected at least 1 hit in Typesense results"

    first_hit = data["hits"][0]
    assert "document" in first_hit, "Hit missing 'document' subfield"
    doc = first_hit["document"]
    assert "post_title" in doc, "Document missing 'post_title'"
    assert "permalink" in doc, "Document missing 'permalink'"

    return {
        "status_code": resp.status_code,
        "latency_ms": latency_ms,
        "found": data["found"],
        "hits_count": len(data["hits"]),
        "first_title": doc.get("post_title"),
        "first_permalink": doc.get("permalink"),
        "search_time_ms": data.get("search_time_ms"),
    }


async def test_pingora_dns(client: httpx.AsyncClient) -> Dict[str, Any]:
    """Test 2: Query Pingora DNS Host Dispatcher & decode 'c' and 'h' fields."""
    url = "https://dns.pingora.fyi/v2/host"
    headers = {
        **DEFAULT_HEADERS,
        "Accept": "application/json, text/plain, */*",
    }

    t0 = time.perf_counter()
    resp = await client.get(url, headers=headers, timeout=5.0)
    latency_ms = (time.perf_counter() - t0) * 1000

    assert resp.status_code == 200, f"Expected 200 from Pingora DNS, got {resp.status_code}: {resp.text[:200]}"
    data = resp.json()

    assert "c" in data, "Pingora DNS payload missing field 'c'"
    assert "h" in data, "Pingora DNS payload missing field 'h'"

    # Decode base64 fields
    decoded_c = base64.b64decode(data["c"]).decode("utf-8", errors="replace")
    decoded_h = base64.b64decode(data["h"]).decode("utf-8", errors="replace")

    # Extract clean base host from field c
    clean_host = decoded_c.split("?")[0].rstrip("/")
    assert clean_host.startswith("http"), f"Decoded 'c' field must be valid URL: {decoded_c}"
    assert len(decoded_h) > 0, "Decoded 'h' field must not be empty"

    # Quick probe of decoded host
    t_probe = time.perf_counter()
    probe_resp = await client.get(clean_host, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=5.0)
    probe_latency_ms = (time.perf_counter() - t_probe) * 1000

    return {
        "status_code": resp.status_code,
        "latency_ms": latency_ms,
        "raw_c": data["c"],
        "raw_h": data["h"],
        "decoded_c": decoded_c,
        "decoded_h": decoded_h,
        "clean_host": clean_host,
        "probe_status": probe_resp.status_code,
        "probe_latency_ms": probe_latency_ms,
    }


async def test_vegamovies_resolution_and_ts_search(client: httpx.AsyncClient) -> Dict[str, Any]:
    """Test 3: VegaMovies mirror resolution and live ts-search.php search proxy."""
    # Step 3a: Edge Mirror Resolution
    edge_url = "https://vglist.lol/?re=vegamovies"
    t_res = time.perf_counter()
    edge_resp = await client.get(edge_url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=5.0)
    landing_url = str(edge_resp.url).rstrip("/")

    # Step 3b: Follow landing trampoline to target mirror
    trampoline_url = f"{landing_url}/?re=vg&t=2"
    tramp_resp = await client.get(trampoline_url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=5.0)
    active_mirror = str(tramp_resp.url).rstrip("/")
    resolution_latency_ms = (time.perf_counter() - t_res) * 1000

    assert active_mirror.startswith("http"), f"Invalid resolved mirror: {active_mirror}"

    # Step 3c: Query ts-search.php on active mirror
    search_url = f"{active_mirror}/ts-search.php?q=Batman"
    search_headers = {
        **DEFAULT_HEADERS,
        "Referer": f"{active_mirror}/",
        "Accept": "application/json, text/plain, */*",
    }

    t_search = time.perf_counter()
    search_resp = await client.get(search_url, headers=search_headers, timeout=5.0)
    search_latency_ms = (time.perf_counter() - t_search) * 1000

    assert search_resp.status_code == 200, f"Expected 200 from ts-search.php, got {search_resp.status_code}: {search_resp.text[:200]}"
    search_data = search_resp.json()

    assert "hits" in search_data, "VegaMovies ts-search payload missing 'hits'"
    assert isinstance(search_data["hits"], list), "VegaMovies 'hits' must be list"
    assert len(search_data["hits"]) > 0, "VegaMovies ts-search returned 0 hits"

    first_hit = search_data["hits"][0]
    assert "document" in first_hit, "VegaMovies hit missing 'document'"
    doc = first_hit["document"]
    assert "post_title" in doc, "VegaMovies document missing 'post_title'"

    return {
        "active_mirror": active_mirror,
        "landing_url": landing_url,
        "resolution_latency_ms": resolution_latency_ms,
        "search_status_code": search_resp.status_code,
        "search_latency_ms": search_latency_ms,
        "hits_count": len(search_data["hits"]),
        "found": search_data.get("found", len(search_data["hits"])),
        "first_title": doc.get("post_title"),
        "first_permalink": doc.get("permalink"),
    }


async def run_all_tests():
    print("=" * 70)
    print("LIVE REAL-WORLD CATALOG SEARCH APIS TEST SUITE (D:\\playro_jev)")
    print("=" * 70)

    results = {}

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        # 1. Pingora DNS endpoint
        print("\n[TEST 1] Querying Pingora DNS Host Dispatcher (https://dns.pingora.fyi/v2/host)...")
        try:
            dns_res = await test_pingora_dns(client)
            results["pingora_dns"] = dns_res
            print(f"  [PASS] Status: {dns_res['status_code']} | Latency: {dns_res['latency_ms']:.2f}ms")
            print(f"         Decoded 'c': {dns_res['decoded_c']} (Host: {dns_res['clean_host']})")
            print(f"         Decoded 'h': {dns_res['decoded_h']}")
            print(f"         Host Probe: HTTP {dns_res['probe_status']} in {dns_res['probe_latency_ms']:.2f}ms")
        except Exception as e:
            results["pingora_dns"] = {"error": str(e)}
            print(f"  [FAIL] {e}")

        # 2. Pingora Typesense cluster search
        print("\n[TEST 2] Querying Pingora Typesense Cluster (search.pingora.fyi)...")
        try:
            active_hd_ref = results.get("pingora_dns", {}).get("clean_host", "https://hdhub4u.tv")
            ts_res = await test_pingora_typesense(client, referer=f"{active_hd_ref}/")
            results["pingora_typesense"] = ts_res
            print(f"  [PASS] Status: {ts_res['status_code']} | Latency: {ts_res['latency_ms']:.2f}ms")
            print(f"         Payload: found={ts_res['found']} | hits={ts_res['hits_count']}")
            print(f"         Server Search Time: {ts_res['search_time_ms']}ms")
            print(f"         Sample Title: {ts_res['first_title'][:80]}")
            print(f"         Permalink: {ts_res['first_permalink']}")
        except Exception as e:
            results["pingora_typesense"] = {"error": str(e)}
            print(f"  [FAIL] {e}")

        # 3. VegaMovies Resolution & ts-search.php
        print("\n[TEST 3] Testing VegaMovies Dynamic Resolution & ts-search.php...")
        try:
            vega_res = await test_vegamovies_resolution_and_ts_search(client)
            results["vegamovies_ts_search"] = vega_res
            print(f"  [PASS] Active Mirror: {vega_res['active_mirror']} (Resolution: {vega_res['resolution_latency_ms']:.2f}ms)")
            print(f"         Search Status: {vega_res['search_status_code']} | Latency: {vega_res['search_latency_ms']:.2f}ms")
            print(f"         Payload: hits={vega_res['hits_count']} | found={vega_res['found']}")
            print(f"         Sample Title: {vega_res['first_title'][:80]}")
            print(f"         Permalink: {vega_res['first_permalink']}")
        except Exception as e:
            results["vegamovies_ts_search"] = {"error": str(e)}
            print(f"  [FAIL] {e}")

    print("\n" + "=" * 70)
    print("SUMMARY VERDICT")
    print("=" * 70)
    all_passed = all("error" not in v for v in results.values())
    if all_passed:
        print("[SUCCESS] All 3 live direct catalog search endpoints verified operational!")
    else:
        print("[FAILURE] Some endpoints failed.")

    # Write execution output to local JSON for verification
    with open("D:/playro_jev/live_catalog_test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    return all_passed, results


if __name__ == "__main__":
    success, _ = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
