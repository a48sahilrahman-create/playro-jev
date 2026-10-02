# Phase 1 (Epic 1): JEV Adaptive Domain Resolver & Mirror Registry

## Executive Overview
Phase 1 implements a production-grade, highly resilient dynamic domain resolution and health tracking subsystem for the JEV pipeline in `D:\playro_jev`.
It eliminates hardcoded domain failure points across 5 core platforms:
1. **UHDMovies**: Dynamic redirect probing and candidate racing.
2. **HDHub4u**: Pingora DNS host endpoint query with base64 decoding and candidate failover.
3. **VegaMovies**: Edge endpoint scraping for `<meta http-equiv="refresh">` and JS location redirect targets.
4. **OlaMovies**: Dynamic subdomain generation (`v3`–`v8`) and concurrent candidate racing.
5. **DesireMovies**: Multi-candidate concurrent probe and automatic failover.

---

## File Deliverables

### 1. `D:\playro_jev\mirror_registry.json`
Configuration schema defining candidate lists, endpoints, probe types, search dorks, and fallbacks:
```json
{
  "uhdmovies": {
    "type": "redirect_or_probe",
    "resolvers": [
      "https://uhdmovies.autos/",
      "https://uhdmovies.my/",
      "https://uhdmovies.mov/"
    ],
    "candidates": [
      "https://uhdmovies.my",
      "https://uhdmovies.autos",
      "https://uhdmovies.mov",
      "https://uhdmovies.one",
      "https://uhdmovies.hair",
      "https://uhdmovies.cloud",
      "https://uhdmovies.store"
    ],
    "fallback": "https://uhdmovies.my",
    "search_dork": "uhdmovies official website"
  },
  "hdhub4u": {
    "type": "pingora_dns",
    "dns_endpoint": "https://dns.pingora.fyi/v2/host",
    "fallback": "https://new1.hdhub4u.free",
    "backup_domain_from_field_h": "hdhub4u.bi",
    "candidates": [
      "https://new1.hdhub4u.free",
      "https://hdhub4u.bi",
      "https://new5.hdhub4u.cl",
      "https://new1.hdhub4u.af"
    ]
  },
  "vegamovies": {
    "type": "edge_refresh",
    "edge_endpoint": "https://vglist.lol/?re=vegamovies",
    "fallback": "https://1vegamovies.lat",
    "candidates": [
      "https://1vegamovies.lat",
      "https://vegamovies.gallery",
      "https://new2.vegamovies.futbol",
      "https://vegamovies.mov"
    ],
    "search_dork": "vegamovies official new domain"
  },
  "olamovies": {
    "type": "subdomain_probe",
    "base_domain": "olamovies.mov",
    "subdomains": ["v6", "v7", "v5", "v4", "v3", "v8"],
    "candidates": [
      "https://v6.olamovies.mov",
      "https://v4.olamovies.mov",
      "https://olamovies.dad",
      "https://olamovies.mov"
    ],
    "fallback": "https://v6.olamovies.mov"
  },
  "desiremovies": {
    "type": "redirect_or_probe",
    "candidates": [
      "https://1desiremovies.rodeo",
      "https://1desiremovies.wales",
      "https://1desiremovies.nexus",
      "https://1desiremovies.cymru"
    ],
    "fallback": "https://1desiremovies.rodeo"
  }
}
```

### 2. `D:\playro_jev\domain_resolver.py`
Key architectural properties:
- **Embedded Fallback Registry**: Built-in copy of `EMBEDDED_REGISTRY` guarantees zero crash if `mirror_registry.json` is missing or corrupted.
- **In-Memory Cache (TTL = 3600s)**: High-speed in-memory resolution cache delivers < 0.02ms lookups on cache hits.
- **Stampede Prevention**: Lazy per-brand `asyncio.Lock` ensures single-flight resolution passes when multiple concurrent requests ask for the same brand.
- **Health State Persistence**: Updates `D:\playro_jev\mirror_health.json` atomically via temporary file replacement (`.tmp` -> replace).
- **Probing Engine**: Low-overhead `HEAD` probing with automatic fallback to streaming `GET` for servers responding with 403 or 405.
- **Failure Triage & Circuit Breaker**: Tracks consecutive failures; upon 3 consecutive failures, invalidates cache and triggers background re-probing.
- **Synchronous & Asynchronous APIs**: Provides async methods for event loops and non-blocking `get_mirror_sync` / `get_active_mirror_sync` for legacy or sync contexts.

---

## Live Verification & Test Output

Test script `D:\playro_jev\test_domain_resolver.py` verified all 5 tests:

```text
=== TEST 1: Sync helper retrieval before dynamic run ===
  [SYNC] uhdmovies: https://uhdmovies.my
  [SYNC] hdhub4u: https://new1.hdhub4u.free
  [SYNC] vegamovies: https://1vegamovies.lat
  [SYNC] olamovies: https://v6.olamovies.mov
  [SYNC] desiremovies: https://1desiremovies.rodeo

=== TEST 2: Concurrent resolution of all 5 brands ===
Resolved all 5 brands in 2180.6ms:
  -> uhdmovies     : https://uhdmovies.my
  -> hdhub4u       : https://new1.hdhub4u.free
  -> vegamovies    : https://1vegamovies.lat
  -> olamovies     : https://v6.olamovies.mov
  -> desiremovies  : https://1desiremovies.rodeo

=== TEST 3: Cache verification (< 1ms hits) ===
  [CACHE HIT] uhdmovies: https://uhdmovies.my (0.017ms)
  [CACHE HIT] hdhub4u: https://new1.hdhub4u.free (0.008ms)
  [CACHE HIT] vegamovies: https://1vegamovies.lat (0.007ms)
  [CACHE HIT] olamovies: https://v6.olamovies.mov (0.007ms)
  [CACHE HIT] desiremovies: https://1desiremovies.rodeo (0.005ms)

=== TEST 4: Mirror Health JSON Inspection ===
Health Data content:
  uhdmovies: mirror=https://uhdmovies.my latency=1106.58ms status=healthy
  hdhub4u: mirror=https://new1.hdhub4u.free latency=349.89ms status=healthy
  vegamovies: mirror=https://1vegamovies.lat latency=1833.58ms status=healthy
  olamovies: mirror=https://v6.olamovies.mov latency=183.22ms status=healthy
  desiremovies: mirror=https://1desiremovies.rodeo latency=916.08ms status=healthy

=== TEST 5: Failure & Degradation Triage ===
  Reported 1 failure on uhdmovies: status=warning
  Reported 3 failures on uhdmovies: status=degraded
  Restored uhdmovies: status=healthy, latency=45.2ms

ALL VERIFICATION CHECKS PASSED SUCCESSFULLY!
```

---

## Live Mirror State (`mirror_health.json`)

```json
{
  "uhdmovies": {
    "active_mirror": "https://uhdmovies.my",
    "latency_ms": 45.2,
    "last_success": 1790837272.25822,
    "consecutive_failures": 0,
    "status": "healthy"
  },
  "hdhub4u": {
    "active_mirror": "https://new1.hdhub4u.free",
    "latency_ms": 349.89,
    "last_success": 1790837270.6975,
    "consecutive_failures": 0,
    "status": "healthy"
  },
  "vegamovies": {
    "active_mirror": "https://1vegamovies.lat",
    "latency_ms": 1833.58,
    "last_success": 1790837272.23472,
    "consecutive_failures": 0,
    "status": "healthy"
  },
  "olamovies": {
    "active_mirror": "https://v6.olamovies.mov",
    "latency_ms": 183.22,
    "last_success": 1790837270.61133,
    "consecutive_failures": 0,
    "status": "healthy"
  },
  "desiremovies": {
    "active_mirror": "https://1desiremovies.rodeo",
    "latency_ms": 916.08,
    "last_success": 1790837271.37157,
    "consecutive_failures": 0,
    "status": "healthy"
  }
}
```
