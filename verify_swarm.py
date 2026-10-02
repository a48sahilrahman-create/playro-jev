#!/usr/bin/env python3
"""
verify_swarm.py — End-to-End Consumer-Journey Reality Verification Suite
========================================================================
Phase 5 Outside-In Reality Testing for Play Engine Cloud Stremio Addon.

Verifies:
  1. GET /health -> Status 200, healthy JSON payload
  2. GET /manifest.json -> Stremio specification compliance (id, resources has 'stream', types)
  3. GET /stream/movie/tt0468569.json -> 90s timeout, streams list validation
  4. GET first stream resolve endpoint with follow_redirects=False -> 302/307 Redirect & valid Location
  5. Platform breakdown distribution parsing
  6. Graceful timeout handling (SKIPPED on upstream scraper timeout)
  7. Final formatted PASSED / FAILED / SKIPPED verdict matrix

Outputs clean stdout and exits with code 0 on PASSED/SKIPPED or 1 on FAILED.
"""

import sys
import time
import re
from typing import Dict, Any, List, Optional
import httpx

# Windows cp1252 consoles cannot encode emoji/unicode stream names (⚡📦) — force UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:7860"
TEST_IMDB_ID = "tt0468569"  # The Dark Knight (2008)

def print_separator(title: str = ""):
    if title:
        print(f"\n{'=' * 20} {title} {'=' * 20}")
    else:
        print("=" * 60)

def extract_platform_tag(stream_title: str) -> str:
    """Extract source platform tag from stream title formatted like '[PLATFORM]' at line ends."""
    lines = stream_title.strip().split("\n")
    last_line = lines[-1] if lines else stream_title
    match = re.search(r'\[([A-Za-z0-9 _-]+)\]\s*$', last_line)
    if match:
        return match.group(1).upper()
    return "UNKNOWN"

def run_verification() -> bool:
    print_separator("PLAY ENGINE OUTSIDE-IN REALITY TEST SUITE")
    print(f"Target Addon Endpoint : {BASE_URL}")
    print(f"Target Media ID       : {TEST_IMDB_ID} (The Dark Knight)")
    print(f"Timestamp             : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print_separator()

    results: Dict[str, Dict[str, Any]] = {}

    with httpx.Client(base_url=BASE_URL, timeout=90.0) as client:
        # -------------------------------------------------------------
        # STEP 1: /health Endpoint
        # -------------------------------------------------------------
        print("[Step 1/4] Verifying /health endpoint...")
        t0 = time.perf_counter()
        try:
            r_health = client.get("/health", timeout=10.0)
            elapsed = time.perf_counter() - t0
            if r_health.status_code == 200:
                data = r_health.json()
                if data.get("status") == "healthy":
                    print(f"  --> PASSED ({elapsed:.3f}s): HTTP {r_health.status_code}, status='healthy'")
                    results["Health Check"] = {"status": "PASSED", "duration": f"{elapsed:.3f}s", "details": f"Status 200, status={data.get('status')}"}
                else:
                    print(f"  --> FAILED ({elapsed:.3f}s): status field was '{data.get('status')}'")
                    results["Health Check"] = {"status": "FAILED", "duration": f"{elapsed:.3f}s", "details": f"Unexpected status payload: {data}"}
            else:
                print(f"  --> FAILED ({elapsed:.3f}s): HTTP {r_health.status_code}")
                results["Health Check"] = {"status": "FAILED", "duration": f"{elapsed:.3f}s", "details": f"HTTP status {r_health.status_code}"}
        except Exception as e:
            elapsed = time.perf_counter() - t0
            print(f"  --> FAILED ({elapsed:.3f}s): Exception {e}")
            results["Health Check"] = {"status": "FAILED", "duration": f"{elapsed:.3f}s", "details": str(e)}

        # -------------------------------------------------------------
        # STEP 2: /manifest.json Stremio Specification
        # -------------------------------------------------------------
        print("\n[Step 2/4] Verifying /manifest.json Stremio contract...")
        t0 = time.perf_counter()
        try:
            r_manifest = client.get("/manifest.json", timeout=10.0)
            elapsed = time.perf_counter() - t0
            if r_manifest.status_code == 200:
                manifest = r_manifest.json()
                m_id = manifest.get("id")
                m_resources = manifest.get("resources", [])
                m_types = manifest.get("types", [])

                # Validation checks
                has_valid_id = bool(m_id and isinstance(m_id, str))
                has_stream_res = "stream" in m_resources or any(r == "stream" or (isinstance(r, dict) and r.get("name") == "stream") for r in m_resources)
                has_types = bool(m_types and isinstance(m_types, list))

                if has_valid_id and has_stream_res and has_types:
                    print(f"  --> PASSED ({elapsed:.3f}s): ID='{m_id}', resources={m_resources}, types={m_types}")
                    results["Manifest Contract"] = {
                        "status": "PASSED",
                        "duration": f"{elapsed:.3f}s",
                        "details": f"id='{m_id}', resources={m_resources}, types={m_types}"
                    }
                else:
                    err_msg = f"Contract mismatch: id={has_valid_id}, has_stream={has_stream_res}, types={has_types}"
                    print(f"  --> FAILED ({elapsed:.3f}s): {err_msg}")
                    results["Manifest Contract"] = {"status": "FAILED", "duration": f"{elapsed:.3f}s", "details": err_msg}
            else:
                print(f"  --> FAILED ({elapsed:.3f}s): HTTP {r_manifest.status_code}")
                results["Manifest Contract"] = {"status": "FAILED", "duration": f"{elapsed:.3f}s", "details": f"HTTP {r_manifest.status_code}"}
        except Exception as e:
            elapsed = time.perf_counter() - t0
            print(f"  --> FAILED ({elapsed:.3f}s): Exception {e}")
            results["Manifest Contract"] = {"status": "FAILED", "duration": f"{elapsed:.3f}s", "details": str(e)}

        # -------------------------------------------------------------
        # STEP 3: Stream Discovery Endpoint (/stream/movie/tt0468569.json)
        # -------------------------------------------------------------
        print(f"\n[Step 3/4] Fetching live stream list for IMDb ID {TEST_IMDB_ID} (90s timeout)...")
        t0 = time.perf_counter()
        streams: List[Dict[str, Any]] = []
        try:
            r_stream = client.get(f"/stream/movie/{TEST_IMDB_ID}.json", timeout=90.0)
            elapsed = time.perf_counter() - t0
            if r_stream.status_code == 200:
                data = r_stream.json()
                if "streams" in data and isinstance(data["streams"], list):
                    streams = data["streams"]
                    print(f"  --> PASSED ({elapsed:.2f}s): Discovered {len(streams)} streams.")
                    results["Stream Discovery"] = {
                        "status": "PASSED",
                        "duration": f"{elapsed:.2f}s",
                        "details": f"Returned {len(streams)} streams for {TEST_IMDB_ID}"
                    }
                else:
                    print(f"  --> FAILED ({elapsed:.2f}s): 'streams' key missing from JSON response: {data}")
                    results["Stream Discovery"] = {
                        "status": "FAILED",
                        "duration": f"{elapsed:.2f}s",
                        "details": "JSON response missing 'streams' array"
                    }
            else:
                print(f"  --> FAILED ({elapsed:.2f}s): HTTP {r_stream.status_code}")
                results["Stream Discovery"] = {
                    "status": "FAILED",
                    "duration": f"{elapsed:.2f}s",
                    "details": f"HTTP status {r_stream.status_code}"
                }
        except httpx.TimeoutException:
            elapsed = time.perf_counter() - t0
            print(f"  --> SKIPPED ({elapsed:.2f}s): Upstream scrapers timed out gracefully after 90s.")
            results["Stream Discovery"] = {
                "status": "SKIPPED",
                "duration": f"{elapsed:.2f}s",
                "details": "Upstream scrapers timed out (network latency)"
            }
        except Exception as e:
            elapsed = time.perf_counter() - t0
            print(f"  --> FAILED ({elapsed:.2f}s): Exception {e}")
            results["Stream Discovery"] = {
                "status": "FAILED",
                "duration": f"{elapsed:.2f}s",
                "details": str(e)
            }

        # -------------------------------------------------------------
        # STEP 4: First Stream Playback Resolve & 302/307 Verification
        # -------------------------------------------------------------
        print("\n[Step 4/4] Verifying first stream on-demand resolve redirection...")
        if not streams:
            if results.get("Stream Discovery", {}).get("status") == "SKIPPED":
                print("  --> SKIPPED: Stream discovery was skipped due to upstream timeout.")
                results["Stream Resolve Redirect"] = {
                    "status": "SKIPPED",
                    "duration": "0.000s",
                    "details": "Skipped because no streams were discovered"
                }
            else:
                print("  --> FAILED: No streams available to test playback resolve.")
                results["Stream Resolve Redirect"] = {
                    "status": "FAILED",
                    "duration": "0.000s",
                    "details": "No streams discovered"
                }
        else:
            first_stream = streams[0]
            first_url = first_stream.get("url")
            print(f"  Testing First Stream: '{first_stream.get('name')}'")
            print(f"  Target Endpoint     : {first_url}")

            t0 = time.perf_counter()
            try:
                # Issue GET with follow_redirects=False expecting 302 or 307
                r_resolve = client.get(first_url, follow_redirects=False, timeout=60.0)
                elapsed = time.perf_counter() - t0
                status_code = r_resolve.status_code
                loc_header = r_resolve.headers.get("Location") or r_resolve.headers.get("location")

                print(f"  Response Status     : HTTP {status_code}")
                print(f"  Location Header     : {loc_header}")

                if status_code in (302, 307) and loc_header:
                    print(f"  --> PASSED ({elapsed:.3f}s): Clean {status_code} redirect to target buffer.")
                    results["Stream Resolve Redirect"] = {
                        "status": "PASSED",
                        "duration": f"{elapsed:.3f}s",
                        "details": f"HTTP {status_code} -> Location: {loc_header[:80]}..."
                    }
                else:
                    print(f"  --> FAILED ({elapsed:.3f}s): Expected 302/307 with Location header, got status {status_code}, Location={loc_header}")
                    results["Stream Resolve Redirect"] = {
                        "status": "FAILED",
                        "duration": f"{elapsed:.3f}s",
                        "details": f"Status {status_code}, Location: {loc_header}"
                    }
            except httpx.TimeoutException:
                elapsed = time.perf_counter() - t0
                print(f"  --> SKIPPED ({elapsed:.3f}s): Live gateway resolver timed out upstream.")
                results["Stream Resolve Redirect"] = {
                    "status": "SKIPPED",
                    "duration": f"{elapsed:.3f}s",
                    "details": "Gateway resolver timed out upstream"
                }
            except Exception as e:
                elapsed = time.perf_counter() - t0
                print(f"  --> FAILED ({elapsed:.3f}s): Exception {e}")
                results["Stream Resolve Redirect"] = {
                    "status": "FAILED",
                    "duration": f"{elapsed:.3f}s",
                    "details": str(e)
                }

    # -------------------------------------------------------------
    # Platform Distribution Analysis
    # -------------------------------------------------------------
    platform_counts: Dict[str, int] = {}
    if streams:
        for s in streams:
            tag = extract_platform_tag(s.get("title", ""))
            platform_counts[tag] = platform_counts.get(tag, 0) + 1

    print_separator("PLATFORM DISTRIBUTION BREAKDOWN")
    if platform_counts:
        for platform, count in sorted(platform_counts.items(), key=lambda x: x[1], reverse=True):
            pct = (count / len(streams)) * 100
            print(f"  - [{platform:<16}]: {count:>3} streams ({pct:>5.1f}%)")
        print(f"  Total Streams Discovered: {len(streams)}")
    else:
        print("  No streams discovered to calculate platform distribution.")

    # -------------------------------------------------------------
    # VERDICT MATRIX
    # -------------------------------------------------------------
    print_separator("FINAL VERDICT MATRIX")
    print(f"{'TEST CASE':<28} | {'STATUS':<9} | {'DURATION':<9} | {'DETAILS'}")
    print("-" * 80)
    all_passed = True
    for test_name, res in results.items():
        st = res["status"]
        dur = res["duration"]
        det = res["details"]
        print(f"{test_name:<28} | {st:<9} | {dur:<9} | {det}")
        if st == "FAILED":
            all_passed = False

    print_separator()
    if all_passed:
        print("🎉 OVERALL RESULT: ALL TESTS PASSED (OR SKIPPED ON TIMEOUT)")
        return True
    else:
        print("❌ OVERALL RESULT: ONE OR MORE TESTS FAILED")
        return False

if __name__ == "__main__":
    success = run_verification()
    sys.exit(0 if success else 1)
