"""
Micro-benchmark for GreenMotors 5-layer token deobfuscation transform.
Transforms: Base64 -> Base64 -> ROT13 -> Base64 -> JSON parse -> Base64 decode data['o']
Target: < 0.1 ms average execution time per decode.
"""

import sys
import os
import time
import base64
import codecs
import json

# Ensure parent directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link_resolver import _decode_greenmotors_token

SAMPLE_TOKEN = (
    "Y214WE0xWjNZbXRhVUdwMmIxQldObFo2ZFRCeFZVOXRRbmxxYVV0UU9XRndla2w1YjNveGFY"
    "RlVPV3h3YkRWM1RERnFhVzVVT1dkTlNtdDFiM3BGZVhCNWFtbFdkbXAyYjJ4V05sWjZVMVpJ"
    "ZDA5M1JsSXdNa2RWZURWdk1rVkxSbnBqZGtWdGVHdEtlRm94Y0ZSYWJVaExUVzVHVW1OcVRW"
    "UXhTWEY1ZVV0YVZYVnVXakp3YTB4NWRVWnVWRk5MV2twbmJrVXlPVFJXWVRBOQ=="
)

EXPECTED_URL = "https://hubcloud.ist/drive/am1gx5mtaimddj1"

def run_benchmark(iterations: int = 1000):
    # 1. Warm-up
    for _ in range(50):
        url = _decode_greenmotors_token(SAMPLE_TOKEN)
        assert url == EXPECTED_URL, f"Warmup assertion failed: {url} != {EXPECTED_URL}"

    # 2. Benchmark loop
    timings = []
    t_start = time.perf_counter()
    for _ in range(iterations):
        t0 = time.perf_counter()
        res = _decode_greenmotors_token(SAMPLE_TOKEN)
        t1 = time.perf_counter()
        timings.append(t1 - t0)
    t_total = time.perf_counter() - t_start

    avg_s = t_total / iterations
    avg_ms = avg_s * 1000.0
    avg_us = avg_s * 1_000_000.0
    min_us = min(timings) * 1_000_000.0
    max_us = max(timings) * 1_000_000.0

    # 3. Verification checks
    assert res == EXPECTED_URL, f"Result mismatch: {res} != {EXPECTED_URL}"

    target_met = avg_ms < 0.1

    print("=" * 60)
    print("GreenMotors 5-Layer Token Deobfuscation Micro-Benchmark")
    print("=" * 60)
    print(f"Iterations:          {iterations}")
    print(f"Decoded HubCloud URL: {res}")
    print(f"Matches Expected:    {res == EXPECTED_URL}")
    print(f"Total Time:          {t_total * 1000:.3f} ms")
    print(f"Average Latency:     {avg_ms:.4f} ms ({avg_us:.2f} µs)")
    print(f"Min Latency:         {min_us:.2f} µs")
    print(f"Max Latency:         {max_us:.2f} µs")
    print(f"Target (< 0.1 ms):   {'PASSED' if target_met else 'FAILED'}")
    print(f"Speedup vs 180s:     {180.0 / avg_s:,.0f}x faster than browser timer")
    print(f"Network Requests:    0 (pure CPU mathematical decode)")
    print("=" * 60)

    return {
        "status": "SUCCESS" if target_met and res == EXPECTED_URL else "FAILED",
        "iterations": iterations,
        "avg_ms": avg_ms,
        "avg_us": avg_us,
        "total_ms": t_total * 1000.0,
        "target_met": target_met,
        "url": res,
    }

if __name__ == "__main__":
    run_benchmark(1000)
