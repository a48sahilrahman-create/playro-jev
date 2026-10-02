# JEV Adaptive Pipeline Upgrade: Phase 1 Integration Report

## Executive Summary
Phase 1 Integration connects the dynamic, health-aware mirror resolution engine (`domain_resolver.py`) with all upstream catalog scrapers (`fast_scrapers.py`), dispatchers (`parallel_dispatcher.py`), and catalog indexers (`library_indexer.py`). All hardcoded domain constants and stale mirror URLs were replaced with dynamic async and synchronized resolvers backed by single-flight in-memory caching (< 0.02ms lookup).

Live execution against active production domains confirmed:
1. **Dynamic Resolution & Low-Latency Cache**:
   - `uhdmovies` -> `https://uhdmovies.my` (First: 332.15ms, Cached: 0.0131ms)
   - `hdhub4u` -> `https://new1.hdhub4u.free` (First: 702.07ms, Cached: 0.0100ms)
   - `vegamovies` -> `https://vegamovies.gallery` (First: 1628.67ms, Cached: 0.0127ms)
   - `olamovies` -> `https://v6.olamovies.mov` (First: 420.18ms, Cached: 0.0137ms)

2. **Scraper Live Extraction Verification (Query: 'Deadpool')**:
   - **UHDMovies**: 23 results extracted in 302.2ms. Modern gateway pattern (`en.thenaukriadda.in/?sid=...`) extracted across 2160p, 1080p, 720p, 480p.
   - **HDHub4u**: 9 results extracted in 818.4ms. Typesense 403 Forbidden resolved via dynamic `Referer: {live_mirror}/`. Live download stream links extracted (`https://hubcdn.wiki/file/...`).
   - **VegaMovies**: 21 results extracted in 2377.8ms via `ts-search.php` and article link parsing on `https://vegamovies.gallery` (`https://nexdrive.fit/...`).
   - **Concurrent Fleet Search**: 18 combined releases extracted in 1497.4ms across UHDMovies, VegaMovies, and HDHub4u for "Avengers Endgame".

3. **Mirror Health Telemetry**:
   - `mirror_health.json` tracks latency, consecutive failures, and status transitions. All 4 platforms updated to `healthy` with 0 failures.

---

## Detailed Code Modifications

### 1. `D:\playro_jev\fast_scrapers.py`
- Imported `get_active_mirror`, `get_active_mirror_sync`, `report_mirror_failure`, `report_mirror_success` from `domain_resolver`.
- Replaced static strings with `get_active_mirror_sync` for module-level compatibility:
  - `UHD_MIRROR = get_active_mirror_sync("uhdmovies")`
  - `VEGA_MIRROR = get_active_mirror_sync("vegamovies")`
  - `HDHUB_MIRROR = get_active_mirror_sync("hdhub4u")`
  - `DESIRE_MIRROR = get_active_mirror_sync("desiremovies")`
  - `OLA_MIRROR = get_active_mirror_sync("olamovies")`
- `get_live_hdhub_mirror`: Delegates to `await get_active_mirror("hdhub4u")`.
- `search_uhdmovies`: Resolves `await get_active_mirror("uhdmovies")`, constructs `f"{uhd_mirror}/?s={clean}"`, reports success/failure. Expanded link regex to match current UHD download gateway `re.compile(r'driveseed|cloud\.unblocked|drive\.google|techy|hubcloud|naukriadda|\?sid=')`.
- `search_vegamovies`: Resolves `await get_active_mirror("vegamovies")`, queries `f"{vega_mirror}/ts-search.php?q={clean}"`, extracts permalinks and reports health telemetry.
- `search_hdhub4u`: Resolves `await get_active_mirror("hdhub4u")`, sets `"Referer": f"{live_mirror}/"` to prevent Typesense 403 Forbidden errors, and dynamically rewrites permalinks `live_post_url = f"{live_mirror}{clean_path}"`.
- `search_olamovies`: Resolves `await get_active_mirror("olamovies")`, queries `f"{ola_mirror}/?s={clean}"` with health telemetry reporting.

### 2. `D:\playro_jev\parallel_dispatcher.py`
- Imported dynamic mirror resolvers and health reporters from `domain_resolver`.
- Replaced hardcoded mirrors with `get_active_mirror_sync(...)`.
- `fetch_series_uhd` & `fetch_uhdmovies`: Updated to `await get_active_mirror("uhdmovies")` with expanded regex for gateway tokens.
- `fetch_series_vega` & `fetch_vegamovies`: Dynamically resolves `vega_mirror = await get_active_mirror("vegamovies")`.
- `fetch_olamovies`: Dynamically resolves `ola_mirror = await get_active_mirror("olamovies")`.
- `fetch_hdhub4u`: Dynamically resolves `live_mirror = await get_active_mirror("hdhub4u")`, applies `"Referer": f"{live_mirror}/"` and rewrites post permalinks to `f"{live_mirror}{clean_path}"`.

### 3. `D:\playro_jev\library_indexer.py`
- Replaced static strings with `get_active_mirror_sync` for `VEGA_MIRROR`, `VEGA_SEARCH`, `OLA_MIRROR`, `UHD_MIRROR`, and `HDHUB_MIRROR`.
- Updated Vega indexer to use `f"{VEGA_MIRROR}{permalink}"`.

### 4. `D:\playro_jev\domain_resolver.py` & `mirror_registry.json`
- Hardened `_resolve_edge_refresh` to verify `/ts-search.php?q=test` before accepting edge redirects or candidates for VegaMovies.
- Removed invalid candidate `1vegamovies.lat` (which returns 404 on search) in favor of responsive mirrors `https://vegamovies.gallery` and `https://new2.vegamovies.futbol`.

---

## Live Verification Suite Output
```text
======================================================================
  PHASE 1 INTEGRATION LIVE TEST: JEV ADAPTIVE DYNAMIC MIRROR ENGINE
======================================================================

[STEP 1] Testing Dynamic Mirror Resolution...
  [uhdmovies]
    - Active Mirror : https://uhdmovies.my
    - First lookup  : 332.15ms
    - Cached lookup : 0.0131ms (Target: < 5ms)
    - Sync lookup   : https://uhdmovies.my
  [hdhub4u]
    - Active Mirror : https://new1.hdhub4u.free
    - First lookup  : 702.07ms
    - Cached lookup : 0.0100ms (Target: < 5ms)
    - Sync lookup   : https://new1.hdhub4u.free
  [vegamovies]
    - Active Mirror : https://vegamovies.gallery
    - First lookup  : 1628.67ms
    - Cached lookup : 0.0127ms (Target: < 5ms)
    - Sync lookup   : https://vegamovies.gallery
  [olamovies]
    - Active Mirror : https://v6.olamovies.mov
    - First lookup  : 420.18ms
    - Cached lookup : 0.0137ms (Target: < 5ms)
    - Sync lookup   : https://v6.olamovies.mov

[STEP 2] Testing Live Scrapers with query: 'Deadpool'...

  --> Querying UHDMovies...
      [OK] UHDMovies returned 23 results in 302.2ms
           Sample: Download Deadpool & Wolverine (2024) Dual Audio {Hindi-English} 2160p || 4k || 1080p || 1080p 10bit || x264 || HEVC || REMUX || HDR DoVi Blu-ray Esubs | 2160P | https://en.thenaukriadda.in/?sid=THpveVRFRjJpV3pwVUxzNUZOQk5...

  --> Querying HDHub4u...
      [OK] HDHub4u returned 9 results in 818.4ms
           Sample: Deadpool & Wolverine (2024) BluRay [Hindi (ORG 5.1) & English] 4K 1080p 720p & 480p Dual Audio [x264/10Bit-HEVC] | Full Movie | 480P | https://hubcdn.wiki/file/B36z6BvX5DSVOA6i0hNy0HWTT...

  --> Querying VegaMovies...
      [OK] VegaMovies returned 21 results in 2377.8ms
           Sample: Download Deadpool & Wolverine (2024) Disney+ Hotstar - Dual Audio (Hindi ORG DD5.1 + English ORG DD5.1) 480p [590MB] | 720p [1.3GB] | 1080p [2.6GB] & 2160p 4K SDR | 480P | https://nexdrive.fit/genxfm784776393879/...

[STEP 3] Testing search_all_concurrent across all scrapers...
  [OK] Concurrent fleet returned 18 total results in 1497.4ms
       Platforms represented: ['UHDMovies', 'VegaMovies', 'HDHub4u']

[STEP 4] Checking Mirror Health Tracking...
  [uhdmovies] Status: healthy | Active: https://uhdmovies.my | Failures: 0
  [hdhub4u] Status: healthy | Active: https://new1.hdhub4u.free | Failures: 0
  [vegamovies] Status: healthy | Active: https://vegamovies.gallery | Failures: 0
  [olamovies] Status: healthy | Active: https://v6.olamovies.mov | Failures: 0

======================================================================
  VERIFICATION RUN FINISHED
======================================================================
```
