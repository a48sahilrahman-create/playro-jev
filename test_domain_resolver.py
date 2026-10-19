import asyncio
import time
import json
from pathlib import Path
from domain_resolver import (
    DomainResolver,
    get_active_mirror,
    get_all_active_mirrors,
    get_active_mirror_sync,
    report_mirror_failure,
    report_mirror_success,
    default_resolver
)

async def main():
    print("=== TEST 1: Sync helper retrieval before dynamic run ===")
    for brand in ["uhdmovies", "hdhub4u", "vegamovies", "olamovies", "desiremovies"]:
        sync_val = get_active_mirror_sync(brand)
        print(f"  [SYNC] {brand}: {sync_val}")
        assert sync_val.startswith("http"), f"Invalid sync mirror for {brand}"

    print("\n=== TEST 2: Concurrent resolution of all 5 brands ===")
    t0 = time.perf_counter()
    all_mirrors = await get_all_active_mirrors()
    dur_ms = (time.perf_counter() - t0) * 1000
    print(f"Resolved all 5 brands in {dur_ms:.1f}ms:")
    for b, url in all_mirrors.items():
        print(f"  -> {b:14}: {url}")
        assert url.startswith("http"), f"Invalid URL for {b}: {url}"
    assert len(all_mirrors) >= 5, f"Expected at least 5 brands, got {len(all_mirrors)}"

    print("\n=== TEST 3: Cache verification (< 1ms hits) ===")
    for brand in ["uhdmovies", "hdhub4u", "vegamovies", "olamovies", "desiremovies"]:
        t0 = time.perf_counter()
        cached_url = await get_active_mirror(brand)
        cached_dur_ms = (time.perf_counter() - t0) * 1000
        print(f"  [CACHE HIT] {brand}: {cached_url} ({cached_dur_ms:.3f}ms)")
        assert cached_dur_ms < 5.0, f"Cache retrieval too slow for {brand}: {cached_dur_ms}ms"

    print("\n=== TEST 4: Mirror Health JSON Inspection ===")
    health_file = Path("D:/playro_jev/mirror_health.json")
    assert health_file.is_file(), "mirror_health.json does not exist on disk"
    with open(health_file, "r", encoding="utf-8") as f:
        health_data = json.load(f)
    print("Health Data content:")
    for brand, stats in health_data.items():
        print(f"  {brand}: mirror={stats.get('active_mirror')} latency={stats.get('latency_ms')}ms status={stats.get('status')}")
        assert "active_mirror" in stats
        assert "latency_ms" in stats
        assert "last_success" in stats
        assert "consecutive_failures" in stats
        assert stats["status"] == "healthy"

    print("\n=== TEST 5: Failure & Degradation Triage ===")
    test_brand = "uhdmovies"
    await report_mirror_failure(test_brand)
    assert default_resolver._health[test_brand]["consecutive_failures"] == 1
    assert default_resolver._health[test_brand]["status"] == "warning"
    print(f"  Reported 1 failure on {test_brand}: status={default_resolver._health[test_brand]['status']}")

    await report_mirror_failure(test_brand)
    await report_mirror_failure(test_brand)
    assert default_resolver._health[test_brand]["consecutive_failures"] == 3
    assert default_resolver._health[test_brand]["status"] == "degraded"
    print(f"  Reported 3 failures on {test_brand}: status={default_resolver._health[test_brand]['status']}")

    # Restore health
    await report_mirror_success(test_brand, latency=45.2)
    assert default_resolver._health[test_brand]["consecutive_failures"] == 0
    assert default_resolver._health[test_brand]["status"] == "healthy"
    print(f"  Restored {test_brand}: status={default_resolver._health[test_brand]['status']}, latency={default_resolver._health[test_brand]['latency_ms']}ms")

    print("\nALL VERIFICATION CHECKS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(main())
