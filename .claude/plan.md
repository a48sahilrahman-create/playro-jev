# JEV Adaptive Pipeline Upgrade — Story Plan

## Problem Statement
Target websites (UHDMovies, HDHub4u, VegaMovies, OlaMovies) have updated their structures — domains change, CSS selectors mutate, gateway redirect chains evolve, and the hardcoded pipelines in `D:\playro_jev` silently return zero results. The user needs JEV to **automatically discover current domains, auto-detect pipeline changes, and reliably extract ALL available file sizes/links** — preferring login-free "HD Drive" (Cloudflare Workers, R2, Pixeldrain) links over Google Drive.

## Current Architecture (What Exists)
```
User Query → play_app.py → ParallelMediaDispatcher
  → FastScrapers (UHD/Vega/HDHub/Ola) → hardcoded mirrors
  → HTML DOM scrape or Typesense JSON API
  → link_resolver.py → gateway resolution (DriveSeed/V-Cloud/HubCloud/Gamerxyt)
  → stream_verifier.py → magic byte validation
  → VLC playback
```

**Key fragility points:**
1. `UHD_MIRROR = "https://uhdmovies.autos"` — hardcoded, changes every few weeks
2. `VEGA_MIRROR = "https://new2.vegamovies.futbol"` — hardcoded TLD
3. `OLA_MIRROR = "https://v4.olamovies.mov"` — hardcoded subdomain+TLD
4. CSS selectors: `re.compile(r'driveseed|cloud\.unblocked|drive\.google|techy|hubcloud')` — breaks when sites add new gateway domains
5. Gateway handler patterns: `s_343()` cookie extraction, `atob(atob())` double decode — break when sites change obfuscation
6. HDHub4u's Pingora DNS resolver is the ONLY adaptive mechanism currently working

---

## Story Plan: 7 Epics, 23 Stories

---

### EPIC 1: Adaptive Domain Discovery Engine
**Goal**: Automatically find the current working domain for each site, eliminating hardcoded mirror URLs.

#### Story 1.1: Mirror Probe & TLD Rotation Resolver
- Build `domain_resolver.py` with a `MirrorProbe` class
- For each site brand (UHDMovies, VegaMovies, OlaMovies, DesireMovies), maintain a **TLD candidate list** (`[".autos", ".nlp", ".one", ".hair", ".green", ".cloud", ".futbol", ".mov", ".store", ".art", ".wales", ".life", ".ink"]`)
- Probe each candidate with a HEAD request + 2s timeout
- Cache the working mirror for 1 hour (same pattern as HDHub4u's Pingora resolver)
- Fallback: If all TLD probes fail, use Google/DuckDuckGo search dorking (`site:uhdmovies.*`) to discover new domains
- **Subagent work**: 3-5 workers testing TLD candidates concurrently

#### Story 1.2: Pingora-Style DNS Resolver Extension
- HDHub4u already uses `dns.pingora.fyi/v2/host` — extend this pattern
- Research if other sites have similar stable resolver endpoints
- Build a registry of resolver endpoints in `mirror_registry.json`
- If a site's own DNS resolver exists, prefer it over TLD probing
- **Subagent work**: 2 workers researching resolver endpoints

#### Story 1.3: Search Engine Dorking Fallback
- When TLD probing AND DNS resolvers both fail, perform a DuckDuckGo/Google search
- Query: `"uhdmovies" download 2026` or `site:uhdmovies.* intitle:download`
- Extract domain from top 3 search results
- Validate with a HEAD probe before caching
- Rate-limit to max 1 search per site per 6 hours
- **Subagent work**: 1 worker implementing search dorking

#### Story 1.4: Mirror Health Monitoring & Auto-Failover
- Background health check every 30 minutes (when engine is running)
- Track mirror response times, error rates, and last-known-good timestamps
- Persist state to `mirror_health.json`
- Auto-failover: if primary mirror fails 3 consecutive times, trigger re-probe
- **Subagent work**: 1 worker building the health monitor

---

### EPIC 2: Adaptive Gateway Auto-Detection
**Goal**: Automatically identify and resolve NEW gateway services (redirect intermediaries) that sites adopt, without hardcoded patterns.

#### Story 2.1: Gateway Fingerprint Classifier
- Replace hardcoded `re.compile(r'driveseed|cloud\.unblocked|...')` with a **semantic gateway classifier**
- Build `gateway_classifier.py` with heuristic fingerprinting:
  - **Form-based gateways**: Detect pages with `<form>` + hidden inputs + countdown timers
  - **JS redirect gateways**: Detect `window.location`, `meta refresh`, `atob()` patterns
  - **Direct CDN links**: Detect known CDN URL patterns (`.workers.dev`, `.r2.cloudflarestorage.com`, `pixeldrain.dev`, `googleusercontent.com`)
- Classify unknown gateway pages into categories: `FORM_MULTI_STEP`, `JS_DECODE`, `DIRECT_CDN`, `COUNTDOWN_TIMER`, `CLOUDFLARE_PROTECTED`
- **Subagent work**: 3-5 workers analyzing different gateway types

#### Story 2.2: Universal Form Solver
- Generalize the DriveSeed multi-step form solver into a **universal form walker**
- Algorithm:
  1. Fetch page → find all `<form>` elements
  2. Extract hidden inputs (tokens, nonces)
  3. Submit POST with all hidden fields
  4. Follow redirects (301/302/307)
  5. Detect `s_343()`-style cookie injection patterns via regex
  6. Repeat until no more forms or redirect target is a CDN URL
- Max depth: 6 hops (current DriveSeed uses 4-6)
- **Subagent work**: 2 workers — one for form detection, one for cookie injection patterns

#### Story 2.3: Universal JS Decode Engine
- Generalize the V-Cloud `atob(atob())` decoder into a **universal JS evaluator**
- Detect patterns: `atob()`, `atob(atob())`, XOR cipher (`resolve_ddlbase_xor`), hex decode, URL encode
- Use regex-based static analysis (no eval/exec for security):
  - `atob\((['"])(.*?)\1\)` → base64 decode
  - `atob\(atob\((['"])(.*?)\1\)\)` → double base64 decode
  - Hex patterns: `\x68\x74\x74\x70` → decode hex escapes
  - `String.fromCharCode(...)` → decode char codes
- Recursively apply decoders until a URL pattern emerges
- **Subagent work**: 2 workers implementing decoder chain

#### Story 2.4: CDN URL Pattern Registry
- Maintain `cdn_patterns.json` — a registry of known final CDN URL patterns:
  ```json
  {
    "workers_dev": "*.workers.dev/*",
    "r2_storage": "*.r2.cloudflarestorage.com/*",
    "r2_dev": "*.r2.dev/*",
    "pixeldrain": "pixeldrain.dev/api/file/*",
    "google_cdn": "video-downloads.googleusercontent.com/*",
    "google_drive": "drive.usercontent.google.com/*",
    "olamovies_cdn": "dl.olamdrive.com/dl?t=*"
  }
  ```
- When resolving a gateway chain, stop as soon as a URL matches ANY CDN pattern
- Prioritize login-free CDNs (Workers > R2 > Pixeldrain > Google CDN > Google Drive)
- **Subagent work**: 1 worker building the registry matcher

---

### EPIC 3: Post Page Link Extraction Overhaul
**Goal**: Replace brittle CSS-class-based link scanning with semantic, adaptive extraction.

#### Story 3.1: Semantic Download Button Detector
- Replace the current `<a href=re.compile(r'driveseed|cloud\.unblocked|...')>` with semantic detection:
  - **Anchor text signals**: "Download", "Drive", "G-Drive", "Episode", "Direct", "Instant", "Fast", "Link", "Server"
  - **Parent element signals**: `class*=button|btn|maxbutton|download-link`, `role="button"`
  - **Exclude signals**: "admin", "comment", "reply", "read more", "share", "telegram", "join"
  - **href domain signals**: Any domain NOT matching the current site's own domain (external links are gateway candidates)
- Score each `<a>` tag on a 0-100 confidence scale
- Return all candidates with score > 30, sorted by confidence
- **Subagent work**: 3 workers — one for anchor text analysis, one for DOM structure analysis, one for href domain analysis

#### Story 3.2: Resolution & File Size Context Extractor
- Improve the "30-node backward DOM traversal" for resolution/size headers
- Build `metadata_extractor.py`:
  - Walk UP from each download link to find the nearest resolution header (2160p/4K/1080p/720p/480p)
  - Walk UP to find file size (e.g., "2.5 GB", "900 MB")
  - Walk UP to find codec info (x264, x265/HEVC, AV1)
  - Walk UP to find audio info (Dual Audio, Hindi, English, DTS)
  - Walk UP to find season/episode tags
- Group links by resolution → size → codec for clean presentation
- **Subagent work**: 2 workers — one for traversal logic, one for regex pattern extraction

#### Story 3.3: Duplicate Link Deduplication & Ranking
- Multiple links often lead to the same final CDN URL via different gateways
- After resolving all links, deduplicate by final CDN URL
- Rank by preference: `Workers.dev > R2 > Pixeldrain > Google CDN > Google Drive`
- Present to user grouped by: `Resolution → File Size → [Best CDN link]`
- **Subagent work**: 1 worker

---

### EPIC 4: Live Pipeline Validation & Self-Healing
**Goal**: Detect when a pipeline breaks and automatically attempt repair.

#### Story 4.1: Pipeline Health Check on Every Request
- After each scrape attempt, check if results are empty
- If empty: flag the site as potentially broken
- Log: `[PIPELINE BROKEN] UHDMovies returned 0 results for known-good query "Avengers Endgame"`
- Trigger auto-repair sequence (Story 4.2)
- **Subagent work**: 1 worker

#### Story 4.2: Auto-Repair Sequence
- When a pipeline breaks:
  1. **Domain check**: Re-probe mirrors (Epic 1). If domain changed → update and retry
  2. **Structure check**: Fetch the post page raw HTML, check if the DOM structure changed
  3. **Gateway check**: If links are found but resolution fails → re-fingerprint gateways (Epic 2)
  4. **Selector check**: If no links found on post page → run semantic detector (Epic 3) instead of regex
  5. **Report**: If all auto-repair fails, log detailed diagnostics to `pipeline_diagnostics.log`
- **Subagent work**: 2 workers — one for the repair orchestrator, one for diagnostics

#### Story 4.3: Known-Good Query Test Suite
- Maintain `test_queries.json` with movies that are ALWAYS available on each site:
  ```json
  {
    "uhdmovies": "Avengers Endgame 2019",
    "vegamovies": "Dune Part Two 2024",
    "hdhub4u": "Oppenheimer 2023",
    "olamovies": "RRR 2022"
  }
  ```
- On startup or scheduled intervals, run test queries to validate each pipeline
- **Subagent work**: 1 worker

---

### EPIC 5: New Site Support (HDMoviesHub, MoviesFlix)
**Goal**: Extend coverage to additional sites using the adaptive architecture from Epics 1-3.

#### Story 5.1: HDMoviesHub Scraper + Howblogs Gateway
- Implement `search_hdmovieshub()` in `fast_scrapers.py`
- Implement Howblogs gateway resolver in `link_resolver.py`:
  - Howblogs uses a JS countdown with a hidden redirect (atob-encoded or `window.location`)
  - Extract the real URL from the JS code using Story 2.3's universal decoder
- Skip GDToT links (require Google login)
- Prefer R2/Pixeldrain final links
- **Subagent work**: 3 workers — scraper, gateway resolver, integration test

#### Story 5.2: MoviesFlix Scraper + DDLBase Integration
- Implement `search_moviesflix()` in `fast_scrapers.py`
- DDLBase XOR already implemented (`resolve_ddlbase_xor` with key `mySecret123`)
- Wire up the existing DDLBase resolver
- Skip Terabox/MediaFire links (unreliable)
- **Subagent work**: 2 workers — scraper and integration test

---

### EPIC 6: Semi-Automatic UI & Selection Flow
**Goal**: Present ALL discovered links grouped by file size/resolution for user selection.

#### Story 6.1: Structured Results Formatter
- After parallel scraping across all sites, collect ALL links with metadata:
  ```
  ┌─────────────────────────────────────────────────────┐
  │  Avengers Endgame (2019)                           │
  ├─────────────────────────────────────────────────────┤
  │  [4K 2160p] 20.5 GB — x265 HEVC — DriveSeed CDN   │
  │  [1080p]    5.8 GB — x264      — Pixeldrain        │
  │  [1080p]    2.5 GB — x265 HEVC — R2 Direct         │
  │  [720p]     1.2 GB — x264      — Workers.dev       │
  │  [480p]     600 MB — x264      — Pixeldrain        │
  ├─────────────────────────────────────────────────────┤
  │  Sources: UHDMovies ✓  VegaMovies ✓  HDHub4u ✓     │
  └─────────────────────────────────────────────────────┘
  ```
- User selects by number → JEV resolves the gateway → opens in VLC
- **Subagent work**: 1 worker for UI formatting

#### Story 6.2: Persistent Link Cache
- Cache resolved final CDN URLs to `media_index.db` (SQLite, already exists)
- On next request for same movie, show cached links first (instant) while re-scraping in background
- Mark stale links (> 24 hours) with a warning
- **Subagent work**: 1 worker

---

### EPIC 7: Integration Testing & Verification
**Goal**: End-to-end validation of the upgraded adaptive pipeline.

#### Story 7.1: Live Pipeline Integration Tests
- Test each site's complete pipeline: search → scrape → extract links → resolve gateway → verify stream
- Test with 5 known movies per site
- Validate: non-zero results, correct metadata extraction, working final CDN URLs
- **Subagent work**: 5 workers (one per site)

#### Story 7.2: Gateway Mutation Stress Tests
- Simulate DOM mutations (changed CSS classes, new wrapper elements, reordered attributes)
- Feed mutated HTML to the semantic detectors
- Verify extraction still works with mutated DOM
- Reuse pattern from existing `test_live_jev_integration.py` (mutated UHD/HDHub DOM tests)
- **Subagent work**: 3 workers

#### Story 7.3: Domain Failover Tests
- Simulate domain death (return 404/timeout for primary mirror)
- Verify auto-failover discovers new domain
- Verify cached mirror health state updates correctly
- **Subagent work**: 2 workers

---

## Execution Plan: Subagent Allocation

| Phase | Epics | Subagents | Estimated Time |
|-------|-------|-----------|---------------|
| **Phase 1: Foundation** | Epic 1 (Domain Discovery) | 7-10 workers | 15-20 min |
| **Phase 2: Gateway Intelligence** | Epic 2 (Gateway Auto-Detection) | 8-10 workers | 15-20 min |
| **Phase 3: Extraction Overhaul** | Epic 3 (Link Extraction) | 6-8 workers | 10-15 min |
| **Phase 4: Self-Healing** | Epic 4 (Validation & Repair) | 4-5 workers | 10 min |
| **Phase 5: New Sites** | Epic 5 (HDMoviesHub/MoviesFlix) | 5 workers | 10 min |
| **Phase 6: UI & Caching** | Epic 6 (Selection Flow) | 2 workers | 5 min |
| **Phase 7: Verification** | Epic 7 (Integration Tests) | 10 workers | 10-15 min |
| **TOTAL** | 7 Epics, 23 Stories | **42-50 workers** | **~75-95 min** |

---

## Key Design Decisions

1. **Prefer HD Drive (login-free) links**: Workers.dev > R2 > Pixeldrain > Google CDN ≫ Google Drive
2. **Semi-automatic flow**: JEV fetches ALL links → presents grouped by resolution/size → user picks → JEV resolves → VLC plays
3. **Adaptive over hardcoded**: Domain probe + semantic detection + universal decoders replace ALL hardcoded mirrors, selectors, and gateway patterns
4. **Isolated clone**: All changes in `D:\playro_jev` — production `D:\play_stremio_cloud` remains untouched
5. **Graceful degradation**: If adaptive detection fails, fall back to current hardcoded patterns (never worse than today)

---

## Files to Create/Modify

### New Files
- `domain_resolver.py` — Mirror probe, TLD rotation, DNS resolver registry, search dorking
- `gateway_classifier.py` — Semantic gateway fingerprinting & classification
- `metadata_extractor.py` — Resolution/size/codec/audio context extraction
- `mirror_registry.json` — Known DNS resolver endpoints & TLD candidates
- `cdn_patterns.json` — CDN URL pattern registry with priority ranking
- `test_queries.json` — Known-good validation queries per site
- `mirror_health.json` — Runtime mirror health state (auto-generated)

### Modified Files
- `fast_scrapers.py` — Replace hardcoded mirrors with `domain_resolver`, add HDMoviesHub/MoviesFlix scrapers
- `link_resolver.py` — Add universal form solver, JS decode engine, CDN pattern matcher
- `parallel_dispatcher.py` — Wire up new domain resolver and gateway classifier
- `jev_stream_resolver.py` — Integrate gateway classifier as JEV fallback
- `play_app.py` — Update UI to show grouped results with file size selection
- `test_live_jev_integration.py` — Add new test cases for adaptive features
