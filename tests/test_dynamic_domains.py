"""
Playro JEV — Dynamic Domain Resolution & Mirror Health Test Suite
-----------------------------------------------------------------
Comprehensive pytest suite validating DomainResolver in domain_resolver.py:
  1. Valid URL resolution for all 9 brands:
     - uhdmovies, hdhub4u, vegamovies, olamovies, 4khdhub, desiremovies, ddlbase, nyaa, bingr
  2. Failure reporting lifecycle:
     - report_failure(brand) increments failure count and persists atomically in mirror_health.json
     - Circuit breaker transitions (warning -> degraded at 3 failures) and cache eviction
     - report_success(brand) resets failure counter to 0 and restores healthy status
  3. Single-flight lock anti-stampede concurrency:
     - Concurrent calls for the same brand execute the resolution logic exactly once
     - Independent brand locks allow concurrent resolution across different brands
     - Double-checked locking serves waiting callers directly from the populated cache
"""

import os
import sys
import json
import time
import asyncio
import urllib.parse
from pathlib import Path
from unittest.mock import patch, AsyncMock
import pytest

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from domain_resolver import (
    DomainResolver,
    get_active_mirror,
    get_all_active_mirrors,
    get_active_mirror_sync,
    report_mirror_failure,
    report_mirror_success,
    default_resolver,
)

ALL_BRANDS = [
    "uhdmovies",
    "hdhub4u",
    "vegamovies",
    "olamovies",
    "4khdhub",
    "desiremovies",
    "ddlbase",
    "nyaa",
    "bingr",
]


def assert_valid_mirror_url(url: str, brand: str = ""):
    """Helper asserting that a URL is non-empty, well-formed, and has a valid domain."""
    assert isinstance(url, str), f"Mirror for '{brand}' must be a string, got {type(url)}"
    assert len(url) > 0, f"Mirror for '{brand}' cannot be empty"
    assert url.startswith(("http://", "https://")), (
        f"Mirror for '{brand}' must start with http:// or https://, got: {url}"
    )
    parsed = urllib.parse.urlparse(url)
    assert parsed.scheme in ("http", "https"), (
        f"Mirror for '{brand}' invalid scheme: {parsed.scheme}"
    )
    assert parsed.netloc, f"Mirror for '{brand}' has empty netloc/domain: {url}"
    assert "." in parsed.netloc, f"Mirror for '{brand}' netloc must contain a dot: {parsed.netloc}"
    assert " " not in url, f"Mirror for '{brand}' must not contain spaces: {url}"


@pytest.fixture
def temp_health_file(tmp_path):
    """Provides an isolated mirror_health.json path in a temporary directory."""
    return tmp_path / "mirror_health.json"


@pytest.fixture
def resolver(temp_health_file):
    """Provides a fresh DomainResolver instance isolated from production health state."""
    return DomainResolver(health_path=str(temp_health_file))


# ==============================================================================
# SECTION 1: Dynamic Domain Resolution for All 9 Brands
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("brand", ALL_BRANDS)
async def test_get_mirror_resolves_valid_url_for_all_nine_brands(brand, resolver):
    """
    Verify that get_mirror(brand) resolves a valid URL with http/https scheme,
    valid domain netloc, and no whitespace for all 9 brands.
    """
    url = await resolver.get_mirror(brand)
    assert_valid_mirror_url(url, brand=brand)


@pytest.mark.asyncio
async def test_get_all_mirrors_contains_all_nine_brands(resolver):
    """
    Verify that get_all_mirrors() returns a dictionary containing all 9 brands,
    each with a valid URL.
    """
    all_mirrors = await resolver.get_all_mirrors()
    assert isinstance(all_mirrors, dict)

    for brand in ALL_BRANDS:
        assert brand in all_mirrors, f"Brand '{brand}' missing from get_all_mirrors() dictionary"
        assert_valid_mirror_url(all_mirrors[brand], brand=brand)


def test_get_mirror_sync_all_nine_brands(resolver):
    """
    Verify synchronous non-blocking get_mirror_sync(brand) returns a valid URL
    for all 9 brands immediately without executing network calls.
    """
    for brand in ALL_BRANDS:
        sync_url = resolver.get_mirror_sync(brand)
        assert_valid_mirror_url(sync_url, brand=brand)


@pytest.mark.asyncio
async def test_case_and_whitespace_insensitivity(resolver):
    """
    Verify get_mirror handles leading/trailing whitespace and uppercase brand names.
    """
    url_upper = await resolver.get_mirror("UHDMOVIES")
    assert_valid_mirror_url(url_upper, brand="UHDMOVIES")

    url_spaced = await resolver.get_mirror("  vegamovies  ")
    assert_valid_mirror_url(url_spaced, brand="  vegamovies  ")


@pytest.mark.asyncio
async def test_in_memory_cache_hit_speed(resolver):
    """
    Verify that after initial resolution, subsequent get_mirror calls hit the
    in-memory cache in sub-millisecond time (< 2ms).
    """
    brand = "bingr"
    # Prime cache
    url_first = await resolver.get_mirror(brand)
    assert_valid_mirror_url(url_first, brand=brand)

    # Cache hit timing
    t0 = time.perf_counter()
    url_cached = await resolver.get_mirror(brand)
    dur_ms = (time.perf_counter() - t0) * 1000.0

    assert url_cached == url_first
    assert dur_ms < 5.0, f"Cache retrieval took {dur_ms:.3f}ms, expected < 5.0ms"


@pytest.mark.asyncio
async def test_fallback_resolution_on_mocked_network_failure(temp_health_file):
    """
    Verify that when all network probes fail or raise exceptions, get_mirror
    falls back cleanly to the configured fallback URL in mirror_registry.json.
    """
    test_resolver = DomainResolver(health_path=str(temp_health_file))
    brand = "ddlbase"
    expected_fallback = test_resolver.registry[brand]["fallback"]

    # Mock _probe_candidates_concurrent to simulate total network failure
    with patch.object(test_resolver, "_probe_candidates_concurrent", return_value=None):
        with patch.object(test_resolver, "_search_dork_fallback", return_value=None):
            resolved = await test_resolver.get_mirror(brand, force_refresh=True)

    assert resolved == expected_fallback
    assert_valid_mirror_url(resolved, brand=brand)


# ==============================================================================
# SECTION 2: Failure Reporting & mirror_health.json Persistence
# ==============================================================================

@pytest.mark.asyncio
async def test_report_failure_increments_count_in_health_json(temp_health_file):
    """
    Verify that calling report_failure(brand) increments consecutive_failures
    and persists the update atomically to mirror_health.json on disk.
    """
    resolver = DomainResolver(health_path=str(temp_health_file))
    brand = "hdhub4u"

    # Initially, report 1 failure
    await resolver.report_failure(brand)
    assert temp_health_file.is_file(), "mirror_health.json was not created on disk"

    with open(temp_health_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert brand in data
    assert data[brand]["consecutive_failures"] == 1
    assert data[brand]["status"] == "warning"

    # Second failure: counter increments to 2
    await resolver.report_failure(brand)
    with open(temp_health_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data[brand]["consecutive_failures"] == 2
    assert data[brand]["status"] == "warning"

    # Third failure: counter increments to 3, status degrades to 'degraded', cache evicted
    resolver._cache[brand] = {"url": "https://hdhub4u.test.mock", "resolved_at": time.time()}
    await resolver.report_failure(brand)

    with open(temp_health_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data[brand]["consecutive_failures"] == 3
    assert data[brand]["status"] == "degraded"
    assert brand not in resolver._cache, f"Cache entry for '{brand}' should have been invalidated"


@pytest.mark.asyncio
async def test_report_success_resets_health_json(temp_health_file):
    """
    Verify that calling report_success(brand) resets consecutive_failures to 0,
    marks status as 'healthy', and records latency and timestamp on disk.
    """
    resolver = DomainResolver(health_path=str(temp_health_file))
    brand = "vegamovies"

    # Introduce failure
    await resolver.report_failure(brand)
    with open(temp_health_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data[brand]["consecutive_failures"] == 1

    # Report success with latency
    await resolver.report_success(brand, latency_ms=45.67)
    with open(temp_health_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data[brand]["consecutive_failures"] == 0
    assert data[brand]["status"] == "healthy"
    assert data[brand]["latency_ms"] == 45.67
    assert data[brand]["last_success"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("brand", ALL_BRANDS)
async def test_report_failure_works_for_all_nine_brands(brand, temp_health_file):
    """
    Verify report_failure updates health tracking on disk for each of the 9 brands.
    """
    resolver = DomainResolver(health_path=str(temp_health_file))
    await resolver.report_failure(brand)

    with open(temp_health_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert brand in data
    assert data[brand]["consecutive_failures"] >= 1


# ==============================================================================
# SECTION 3: Single-Flight Lock Concurrency & Anti-Stampede
# ==============================================================================

@pytest.mark.asyncio
async def test_single_flight_lock_prevents_stampede(temp_health_file):
    """
    Verify single-flight locking: when 15 concurrent calls request the same brand
    simultaneously, the underlying resolution logic executes exactly ONCE.
    All waiting coroutines are served from the cache populated by the winner.
    """
    resolver = DomainResolver(health_path=str(temp_health_file))
    brand = "4khdhub"
    mock_target_url = "https://4khdhub.resolved-single-flight.com"

    execution_count = 0
    concurrent_active = 0
    max_concurrent_active = 0

    async def mock_resolve_redirect_or_probe(client, config, b):
        nonlocal execution_count, concurrent_active, max_concurrent_active
        concurrent_active += 1
        max_concurrent_active = max(max_concurrent_active, concurrent_active)
        execution_count += 1
        # Simulate network latency
        await asyncio.sleep(0.06)
        concurrent_active -= 1
        return mock_target_url

    resolver._cache.clear()

    with patch.object(resolver, "_resolve_redirect_or_probe", side_effect=mock_resolve_redirect_or_probe):
        num_tasks = 15
        coros = [resolver.get_mirror(brand) for _ in range(num_tasks)]
        results = await asyncio.gather(*coros)

    # 1. Exactly one resolution executed despite 15 concurrent callers
    assert execution_count == 1, (
        f"Stampede detected! Resolution ran {execution_count} times instead of 1."
    )
    # 2. Never more than 1 coroutine in the resolution critical section
    assert max_concurrent_active == 1, (
        f"Critical section was entered concurrently {max_concurrent_active} times."
    )
    # 3. All callers received the exact resolved URL
    assert len(results) == num_tasks
    assert all(r == mock_target_url for r in results)
    # 4. Lock is not held after resolution completes
    assert not resolver._get_lock(brand).locked()
    # 5. Cache entry is verified
    assert resolver._cache[brand]["url"] == mock_target_url


@pytest.mark.asyncio
async def test_single_flight_independent_brand_locks(temp_health_file):
    """
    Verify that single-flight locks are brand-scoped: resolving brand A does NOT
    block concurrent resolution of brand B. Both resolve in parallel.
    """
    resolver = DomainResolver(health_path=str(temp_health_file))
    brand_a = "uhdmovies"
    brand_b = "desiremovies"

    active_brands = set()
    max_parallel_brands = 0

    async def mock_resolve(client, config, b):
        nonlocal max_parallel_brands
        active_brands.add(b)
        max_parallel_brands = max(max_parallel_brands, len(active_brands))
        await asyncio.sleep(0.05)
        active_brands.remove(b)
        return f"https://{b}.resolved.io"

    resolver._cache.clear()

    with patch.object(resolver, "_resolve_redirect_or_probe", side_effect=mock_resolve):
        t0 = time.perf_counter()
        results = await asyncio.gather(
            resolver.get_mirror(brand_a),
            resolver.get_mirror(brand_b),
        )
        total_time = time.perf_counter() - t0

    # Verify independent lock instances
    lock_a = resolver._get_lock(brand_a)
    lock_b = resolver._get_lock(brand_b)
    assert lock_a is not lock_b, "Different brands must possess distinct asyncio.Lock instances"

    assert len(results) == 2
    assert results[0] == f"https://{brand_a}.resolved.io"
    assert results[1] == f"https://{brand_b}.resolved.io"
    # Both executed concurrently inside the critical section simultaneously
    assert max_parallel_brands == 2, "Expected 2 brands resolving concurrently in parallel"


@pytest.mark.asyncio
async def test_module_level_helpers_integration():
    """
    Verify module-level exported functions delegate to default_resolver cleanly:
    - get_active_mirror
    - get_active_mirror_sync
    - report_mirror_failure
    - report_mirror_success
    """
    brand = "nyaa"
    mirror_async = await get_active_mirror(brand)
    assert_valid_mirror_url(mirror_async, brand=brand)

    mirror_sync = get_active_mirror_sync(brand)
    assert_valid_mirror_url(mirror_sync, brand=brand)

    # Failure and success lifecycle via module helpers
    await report_mirror_failure(brand)
    assert default_resolver._health[brand]["consecutive_failures"] >= 1

    await report_mirror_success(brand, latency=15.0)
    assert default_resolver._health[brand]["consecutive_failures"] == 0
    assert default_resolver._health[brand]["status"] == "healthy"
