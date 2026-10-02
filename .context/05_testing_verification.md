# Testing & Verification Systems

## 1. Overview & Outside-In Verification Philosophy

The Play Engine testing architecture implements the **Zero-Mock Outside-In Reality Verification Protocol** (as mandated by `CLAUDE.md` Section 5). In video streaming pipelines, internal API returns (such as HTTP `200 OK` or synthetic JSON mock payloads) do not guarantee that end consumers will receive playable video frames. Network stream endpoints often encounter Cloudflare Turnstile barriers, invalid redirects, IP blocks, HTML error pages disguised as 200 OK, corrupt EBML headers, or stalled video buffers.

```
+-----------------------------------------------------------------------------------+
|                        PLAY ENGINE VERIFICATION HIERARCHY                         |
+-----------------------------------------------------------------------------------+
| 1. Human Reality Tests   | Real desktop player execution (VLC 15-20s buffer)      |
|    (Outside-In)          | verify_10_movies_vlc.py, verify_swarm.py               |
+--------------------------+--------------------------------------------------------+
| 2. Universal Benchmarks  | Cross-genre multi-platform matrix (30 & 10 cases)      |
|    (Cross-Platform)      | benchmark_30_universal.py, benchmark_suite_10.py       |
+--------------------------+--------------------------------------------------------+
| 3. Formal Test Suite     | CI/CD contracts, concurrency, SSRF & gateway security  |
|    (pytest / tests/)     | test_api_contract.py, test_dispatcher_concurrency.py, |
|                          | test_resolver_security.py                              |
+--------------------------+--------------------------------------------------------+
| 4. Core Root Test Suites | Magic bytes, container detection, anti-malware,        |
|    (unittest / root)     | adversarial stress, strict title/franchise matcher    |
|                          | test_verification_suite.py, test_adversarial_suite.py, |
|                          | test_strict_matcher.py                                 |
+--------------------------+--------------------------------------------------------+
| 5. Diagnostic Probes     | Targeted DOM, scraper, Turnstile, and gateway probes   |
|    (Root Utilities)      | ~35 ad-hoc diagnostic and inspection scripts           |
+-----------------------------------------------------------------------------------+
```

---

## 2. Formal Test Suite (`tests/`)

The `tests/` directory contains asynchronous pytest suites executed against the live or ASGI-mounted FastAPI application and core subsystems.

### 2.1 `tests/test_api_contract.py`
Validates Stremio protocol specifications, route determinism, user configuration serialization, and W3C CORS compliance using `httpx.ASGITransport(app=app)`.

| Test Function | Verification Scope | Target Contract / Invariant |
| :--- | :--- | :--- |
| `test_route_determinism_and_ordering` | Route resolution precedence | • `/health` returns HTTP 200 with `status: healthy`<br>• `/` and `/configure` render the Dark Zinc web UI<br>• `/manifest.json` returns base manifest (`id: org.playengine.cloudstreamer`, resource `stream`)<br>• `/{user_config}/manifest.json` decodes user base64 config and injects custom manifest name |
| `test_user_config_decoding_edge_cases` | `decode_user_config()` robustness | • Empty, `None`, or `"default"` returns `{}`<br>• Unpadded base64 decodes back to dictionary<br>• Malformed base64 strings (`invalid_base64!@#$`) return `{}` without crashing<br>• Valid base64 encoding non-JSON strings returns `{}`<br>• Valid JSON encoding non-dictionary payloads (lists `[1,2,3]`, booleans `true`, integers `12345`, raw strings) returns `{}` |
| `test_stream_id_parsing_and_contract` | Stremio stream ID schema parsing | • Standard movie ID (`tt0468569.json`) triggers Cinemeta metadata resolution and PlayEngine search<br>• Series episode ID (`tt0903747:1:1.json`) extracts season 1, episode 1 and formats search query as `Breaking Bad S01E01`<br>• Strips optional `.json` URL suffixes correctly<br>• Supports `play:` prefix override (`play:tt0468569.json` and custom query `play:Interstellar.json`)<br>• Retains user configuration prefix in generated stream playback URLs |
| `test_playback_resolve_endpoint_contract` | `/playback/resolve` redirection | • Direct container streams (`.mp4`, `.mkv`) bypass resolver and issue immediate HTTP 302 redirect to source URL<br>• Gateway URLs (e.g. HubCloud) invoke `link_resolver.resolve_auto()` and redirect HTTP 302 to resolved fast CDN URL<br>• Respects `/{user_config}/playback/resolve` route<br>• Returns HTTP 400 when query parameter `url` is missing or empty |
| `test_cors_headers_spec_compliance` | W3C / Fetch CORS specification | Verifies that when `Access-Control-Allow-Origin: *`, `Access-Control-Allow-Credentials` is NOT set to `true` (preventing browser CORS specification violation) |

### 2.2 `tests/test_dispatcher_concurrency.py`
Asserts that multi-source stream aggregation executes concurrently rather than sequentially.

- **`test_stream_all_parallel_concurrency_and_timeout`**:
  - Mocks scraper methods (`fetch_vegamovies`, `fetch_uhdmovies`, `fetch_hdhub4u`, `fetch_olamovies`, `fetch_telegram`, `fetch_youtube`) with artificial sleep delays totaling `0.35s` sequentially.
  - Verifies that `dispatcher.stream_all_parallel()` completes in `< 2.5s` wall-clock time (typically `~0.10s`), proving true non-blocking `asyncio.gather` / generator execution.
  - Asserts that at least one fast batch is yielded immediately to reduce Time-To-First-Byte (TTFB) for the consumer.
- **`test_play_engine_search_deduplication_and_isolation`**:
  - Simulates duplicate releases across scraper yields.
  - Verifies that `PlayEngine.search()` filters duplicate releases sharing identical `target_url` or `direct_url` values while preserving unique multi-source releases.

### 2.3 `tests/test_resolver_security.py`
Enforces strict security boundaries on the automated link resolver to prevent Server-Side Request Forgery (SSRF), infinite redirect resource exhaustion, and denial-of-service via memory bombs.

#### SSRF Protection Matrix (16 Blocked Patterns)
The resolver verifies every URL against `is_safe_url()` and `is_safe_ip()` before dispatching network requests:

| Category | Tested Blocked Pattern | Security Rationale |
| :--- | :--- | :--- |
| **Localhost & Loopback** | `http://localhost:7860/api`<br>`http://127.0.0.1:8000/secret`<br>`http://127.0.0.1`<br>`http://0.0.0.0:80/`<br>`http://[::1]:8080/` | Prevents scanning or interacting with local container services and internal daemon ports |
| **Cloud Metadata Services** | `http://169.254.169.254/latest/meta-data/`<br>`http://metadata.google.internal/computeMetadata/v1/` | Blocks extraction of instance IAM credentials, token keys, and cloud host metadata |
| **RFC 1918 Private Subnets** | `http://10.0.0.1/admin` (10.0.0.0/8)<br>`http://192.168.1.1/router` (192.168.0.0/16)<br>`http://172.16.0.10/` (172.16.0.0/12) | Prevents pivoting into private enterprise subnets, router admin portals, and internal databases |
| **Carrier-Grade NAT (CGNAT)** | `http://100.64.0.1/cgnat` (100.64.0.0/10) | Blocks access to ISP/host internal CGNAT management interfaces |
| **Non-HTTP Schemes** | `file:///etc/passwd`<br>`gopher://127.0.0.1:6379/_flushall`<br>`ftp://192.168.1.50/data` | Eliminates local filesystem disclosure, Redis protocol injection, and FTP tunneling |
| **Internal Domain Names** | `http://server.local/page`<br>`http://internal-db.localhost/query` | Blocks mDNS `.local` and internal `.localhost` hostname resolution |

#### Redirect Loop & Size Bomb Enforcement
- **Redirect Loop Protection (`test_redirect_cap_and_ssrf_redirect`)**:
  - Uses `httpx.MockTransport` generating infinite HTTP 302 chains.
  - `resolver.safe_fetch()` strictly caps hops at `max_redirects=5`, raising `ValueError("Exceeded maximum redirects cap")`.
  - Re-evaluates destination IP safety on every intermediate redirect hop; redirects targeting metadata addresses (e.g. `169.254.169.254`) raise `ValueError("SSRF blocked")`.
- **Size Bomb Defense (`test_response_size_bomb_protection`)**:
  - Intercepts chunked gateway responses with `MockTransport` generating 10MB payloads.
  - Enforces `MAX_GATEWAY_BYTES = 1048576` (1MB + 64KB margin) read window, terminating ingestion before memory exhaustion occurs.
- **Malformed Gateway Fallback (`test_malformed_gateway_fallback`)**:
  - Asserts graceful `status: FAILED` return with non-zero `elapsed_ms` when gateways return HTTP 500, empty bodies, or corrupted base64 eval JavaScript scripts.

---

## 3. Core Root Test Suites

### 3.1 `test_adversarial_suite.py`
Runs 16 adversarial stress tests across `TitleResolver`, `LinkResolver`, and `StreamVerifier` using `unittest.IsolatedAsyncioTestCase`.

```
======================================================================
test_adversarial_suite.py Execution Summary
----------------------------------------------------------------------
[1] TitleResolver Typo Handling:
    • test_01_typo_oppenheimer: "Oppenhimer" -> "Oppenheimer" (<200ms)
    • test_02_typo_avengers_endgame: "avengrs endgme" -> "Avengers: Endgame" (<200ms)
    • test_03_numeral_mapping_dune_part_2: "dune part 2" -> "Dune: Part Two" (2024, <50ms)
    • test_04_regional_typo_kesu: "kesu ee nadinte" -> "Keshu Ee Veedinte Nadhan" (<400ms)
    • test_05_non_existent_title: "asdfqwerty12345xyznonexistent" -> NO_MATCH
    • test_06_empty_and_symbols: "", "   ", "!!!@@#$$%^^&*()", None -> NO_MATCH

[2] LinkResolver Edge Cases:
    • test_07_pixeldrain_instant_transform: /u/ -> /api/file/ (<5ms)
    • test_08_youtube_instant_passthrough: Watch URLs preserved (<5ms)
    • test_09_ddlbase_xor_decoder: Bidirectional XOR decoding with arbitrary key
    • test_10_dead_domain_timeout: Unresolvable hosts fallback gracefully
    • test_11_empty_and_none: Empty URLs handled with FAILED status

[3] StreamVerifier Stream Integrity:
    • test_12_youtube_instant: Direct recognition without network probe (<10ms)
    • test_13_genuine_mp4: Range request 206 validation
    • test_14_403_xml_rejection: Amazon S3 / Google Cloud 403 XML rejected
    • test_15_dead_links: Connection timeouts return valid=False
    • test_16_empty_and_none: Empty parameters rejected cleanly
----------------------------------------------------------------------
Result: 16/16 Passed | Total Execution Time: ~0.85s
======================================================================
```

### 3.2 `test_verification_suite.py`
Provides 15 integration tests utilizing a custom `MockTransport` to assert stream verification and binary format detection without live external dependencies.

#### Binary Magic Byte Detection Matrix
`StreamVerifier` samples the initial 64 bytes via HTTP `Range: bytes=0-63`:

| Container Format | Magic Byte / Box Signature | Test Assertion |
| :--- | :--- | :--- |
| **MKV / WebM** | `1A 45 DF A3` (EBML Header) | Identifies `MKV (EBML)`, asserts `magic_bytes_hex == 1A45DFA39F428681` |
| **MP4 Container** | `00 00 00 .. 66 74 79 70` (`ftyp` box) | Detects `ftyp` box, confirms HTTP 206 partial response |
| **HLS Playlist** | `#EXTM3U` text header | Matches `application/vnd.apple.mpegurl` and `#EXTM3U` |
| **MPEG-TS** | `0x47` sync byte | Detects transport stream sync byte (`47 40 11 10`) |
| **AVI Container** | `RIFF....AVI ` header | Matches `RIFF` header with `AVI ` fourcc |

#### Anti-Malware Rejection Tests
- **ZIP Archives**: Header `PK\x03\x04` marked `malware_detected: True`, rejected.
- **Windows Executables**: Header `MZ` (`4D 5A`) marked `malware_detected: True`, rejected.
- **Image Formats**: Rejects PNG (`\x89PNG`), JPEG (`\xff\xd8\xff`), and GIF (`GIF89a`).
- **HTML Error Pages**: Rejects HTTP 200 responses containing `<!DOCTYPE html>` or `<html>` (such as Cloudflare Captcha landing pages).

#### Resolver Gateway Emulation Tests
- **V-Cloud / NexDrive**: Extracts Cloudflare R2 direct stream URL (`pub-*.r2.dev`) from HTML anchor tags.
- **HubCloud / Gamerxyt**: Resolves direct Cloudflare R2 storage bucket URLs.
- **OlaMovies / OMDrive**: Resolves direct CDN tokens from `dl.olamdrive.com`.
- **DDLBase XOR**: Validates cryptographic XOR decryption against key strings.

### 3.3 `test_strict_matcher.py`
Evaluates title parsing against false positives caused by website SEO noise, sequels, franchises, and multi-part releases across 17 test cases.

```python
# Matching Algorithm Pipeline:
1. clean_media_title(): Strips audio formats, resolutions (4K, 1080p), rip types (BluRay, WEB-DL), and languages.
2. normalize_tokens(): Converts Roman numerals (I, II, III) and word numbers (one, two) to digits (1, 2, 3).
3. Number Integrity Check: If query specifies a part (e.g. "2", "4", "0"), candidate MUST contain that number.
4. Year Extraction: If years exist in both query and candidate, difference must be <= 1 year.
5. Keyword Intersection & Difflib: Requires 75%+ keyword match or SequenceMatcher ratio >= 0.75.
```

#### 17 Strict Match Test Cases

| # | Query | Candidate Title | Expected | Outcome | Rationale |
| :--- | :--- | :--- | :---: | :---: | :--- |
| 1 | Avengers: Endgame | `Download Avengers: Endgame (2019) Dual Audio 480p` | True | PASS | Exact title match after noise strip |
| 2 | Kummatty (1979) | `Kummatty (1979) Malayalam Movie WEB-DL` | True | PASS | Regional classic with exact year match |
| 3 | Kummatty (1979) | `Download 1941 (1979) Directors Cut` | False | PASS | Different movie sharing release year |
| 4 | Dune: Part Two | `Download Dune: Part Two (2024) Dual Audio` | True | PASS | Exact match on sequel |
| 5 | Dune: Part Two | `Dune – Part Two (2024) 720p BluRay` | True | PASS | En-dash delimiter normalized |
| 6 | Dune: Part Two | `Dune (2021) 1080p BluRay` | False | PASS | Digit mismatch (part 2 missing in candidate) |
| 7 | Dune Part 2 | `Dune: Part Two (2024)` | True | PASS | Numeral normalization ("2" matches "Two") |
| 8 | John Wick: Chapter 4 | `Download John Wick: Chapter 4 (2023) BluRay` | True | PASS | Chapter 4 match |
| 9 | John Wick: Chapter 4 | `Download John Wick: Chapter 2 (2017) BluRay` | False | PASS | Chapter mismatch ("4" != "2") |
| 10 | Spider-Man Across the Spider-Verse | `Download Kiss of the Spider Woman (2025)` | False | PASS | Partial word overlap rejected |
| 11 | Spider-Man Across the Spider-Verse | `Spider-Man: Across the Spider-Verse (2023) 720p` | True | PASS | Valid franchise sequel |
| 12 | Jujutsu Kaisen 0 | `Jujutsu Kaisen Season 1 (2020)` | False | PASS | Digit integrity check ("0" != "1") |
| 13 | Jujutsu Kaisen 0 | `Jujutsu Kaisen 0 The Movie (2021)` | True | PASS | Digit "0" preserved and matched |
| 14 | Manjummel Boys | `Download The Boys (Season 5)` | False | PASS | Partial word "Boys" rejected |
| 15 | Manjummel Boys | `Manjummel Boys (2024) 1080p DSNP WEB-DL` | True | PASS | Exact Malayalam hit |
| 16 | Kantara | `Download Den of Thieves 2: Pantera (2025)` | False | PASS | False-positive rhyming word rejected |
| 17 | Kantara | `Kantara (2022) 1080p AMZN WEB-DL` | True | PASS | Exact Kannada hit |

---

## 4. Human-Reality Testing Protocol

### 4.1 Primary Reality Test: `verify_10_movies_vlc.py`
Automates outside-in validation against a live Play Engine server running at `http://localhost:7860`.

```
+------------------------------------------------------------------------------------+
|                         10-MOVIE REALITY TEST EXECUTION FLOW                       |
+------------------------------------------------------------------------------------+
| 1. Health Probe     | GET http://localhost:7860/health (Assert 200 OK)             |
+---------------------+--------------------------------------------------------------+
| 2. Stream Discovery | GET /stream/{type}/{imdb_id}.json (Assert streams count > 0) |
+---------------------+--------------------------------------------------------------+
| 3. On-Demand Resolve| GET stream[0].url with follow_redirects=False (Assert 302)   |
+---------------------+--------------------------------------------------------------+
| 4. Desktop Player   | Subprocess spawn VLC Media Player                            |
|    Verification     | vlc <direct_url> --start-time 60 --run-time 15 --play-and-exit|
|                     | Assert 15-20s continuous buffer stability without crashes     |
+------------------------------------------------------------------------------------+
```

#### Test Title Roster

| # | IMDb ID | Media Type | Title | Genre / Category |
| :---: | :--- | :--- | :--- | :--- |
| 1 | `tt0468569` | movie | The Dark Knight (2008) | Action / Superhero |
| 2 | `tt0816692` | movie | Interstellar (2014) | Sci-Fi / Space |
| 3 | `tt15398776` | movie | Oppenheimer (2023) | Historical / Drama |
| 4 | `tt1375666` | movie | Inception (2010) | Sci-Fi / Mind-Bending |
| 5 | `tt15309808` | movie | Michael (2023) | Regional / Indian Action |
| 6 | `tt8178634` | movie | RRR (2022) | Indian Epic Action |
| 7 | `tt10698680` | movie | K.G.F: Chapter 2 (2022) | South Indian Blockbuster |
| 8 | `tt4633694` | movie | Spider-Man: Into the Spider-Verse (2018) | Animation / Comic |
| 9 | `tt0137523` | movie | Fight Club (1999) | Cult Classic / Thriller |
| 10 | `tt0903747:1:1`| series | Breaking Bad S01E01 (2008) | TV Series / Episode |

### 4.2 Swarm Sanity Check: `verify_swarm.py`
A 4-step consumer journey test designed for CI verification and deployment smoke testing:

1. **Step 1 (`/health`)**: Confirms HTTP 200 and `{status: "healthy"}`.
2. **Step 2 (`/manifest.json`)**: Asserts Stremio contract compliance (`id`, resources containing `"stream"`, supported `types`).
3. **Step 3 (`/stream/movie/tt0468569.json`)**: Fetches stream release list within a 90-second upstream scraper budget.
4. **Step 4 (`/playback/resolve`)**: Verifies top stream resolves to HTTP 302/307 with a valid `Location` header pointing to an authenticated CDN/R2 stream.
5. **Telemetry Breakdown**: Aggregates platform distribution statistics (`[VEGAMOVIES]`, `[UHDMOVIES]`, `[LOCAL DB]`, etc.).

---

## 5. Benchmark Suites

### 5.1 `benchmark_30_universal.py`
Evaluates catalog depth, multi-platform presence, and latency across 30 media items in 6 categories:

- **Category 1 (Malayalam Cinema)**: *Thondimuthalum Driksakshiyum* (2017), *Kummatty* (1979), *Ustad Hotel* (2012), *Manjummel Boys* (2024), *Bramayugam* (2024).
- **Category 2 (Hindi Blockbusters & Cult Classics)**: *Andhadhun* (2018), *Tumbbad* (2018), *Sholay* (1975), *Gangs of Wasseypur* (2012), *3 Idiots* (2009).
- **Category 3 (Hollywood Blockbusters)**: *Avengers: Endgame* (2019), *Interstellar* (2014), *Oppenheimer* (2023), *The Dark Knight* (2008), *Avatar: The Way of Water* (2022).
- **Category 4 (Vintage Classics & World Cinema)**: *The Godfather* (1972), *12 Angry Men* (1957), *Seven Samurai* (1954), *Memories of Murder* (2003), *Cinema Paradiso* (1988).
- **Category 5 (Multi-Season Anime)**: *Attack on Titan* (2013–2023), *Jujutsu Kaisen* (2020–2023), *Death Note* (2006–2007), *Demon Slayer* (2019–2024), *Fullmetal Alchemist: Brotherhood* (2009–2010).
- **Category 6 (TV Series & Tricky Titles)**: *House of the Dragon* (2022–), *Breaking Bad* (2008–2013), *Stranger Things* (2016–), *Game of Thrones* (2011–2019), *Dark* (2017–2020).

**Metrics Captured**:
- Platform Presence Matrix: Local DB, VegaMovies, UHDMovies, OlaMovies, HDHub4u, Telegram, YouTube.
- Resolution Breakdown: 4K UHD, 1080p, 720p, 480p, HDR, HEVC.
- End-to-End Latency: Average query resolution time in milliseconds.

### 5.2 `benchmark_suite_10.py`
A 10-title live streaming benchmark testing titles such as *Memories of Murder*, *Perfect Blue*, *Ergo Proxy*, *The Fall*, and *Coherence*. Performs on-demand resolution, updates `media_index.db` cache, and validates playback via VLC subprocess for 12 seconds per title.

---

## 6. Diagnostic & Ad-Hoc Scripts Catalog

The root directory contains targeted diagnostic and probe utilities developed during reverse-engineering and resolver hardening:

### A. Cloudflare & Bot Protection Diagnostics
- `test_cloudflare_stealth_solver.py`: Evaluates headless bypass workflows against Cloudflare protection.
- `test_cf_turnstile_click.py`: Coordinates DOM click coordinates against Turnstile checkboxes.
- `test_cf_complete_solve.py`: End-to-end Turnstile challenge solve pipeline.
- `inspect_cf_frames.py`: Dumps iframe hierarchies and shadow DOM trees from Cloudflare challenge pages.

### B. Gateway Token & Link Resolver Probes
- `inspect_driveseed_steps.py`, `inspect_driveseed_step3.py`, `inspect_driveseed_step4.py`: Multi-step form analysis for DriveSeed token generation.
- `test_driveseed_pure.py`, `test_driveseed_deep.py`: Token flow extraction and CDN link validation for DriveSeed.
- `test_vcloud_extract.py`, `test_vcloud_step2.py`: V-Cloud and NexDrive anchor parser and token extractor.
- `test_r2_vs_pxl.py`: Latency and bandwidth comparison between Cloudflare R2 pre-signed URLs and Pixeldrain CDN.
- `test_shortener_flow.py`: Resolves multi-hop URL shorteners.
- `resolve_opt2.py`: Diagnostic script evaluating alternative gateway resolution routines.

### C. Scraper & DOM Inspectors
- `debug_vega_aot.py`, `inspect_all_vega.py`, `list_vega_posts.py`: VegaMovies HTML DOM structure diagnostics.
- `inspect_aot_dom.py`, `test_aot_detail.py`: Episode table parser diagnostics for *Attack on Titan*.
- `inspect_uhd_aot.py`, `debug_obsession_uhd.py`: UHDMovies post structure and link card inspector.
- `inspect_hdhub.py`, `test_hdhub_matrix.py`: HDHub4u layout and mirror extraction validation.
- `inspect_ola_full.py`: OlaMovies DOM and download button parser.
- `inspect_mirrors.py`: Probes availability and response status of mirror domains.
- `get_avengers.py`, `inspect_endgame.py`: Scraper diagnostics for *Avengers* releases.
- `get_sections.py`, `parse_post_raw.py`: Raw HTML parsers for extracting release sections.
- `test_movie_scrapers.py`: General scraper sanity test across all platforms.
- `test_fastdl.py`: FastDL direct download link extraction test.
- `debug_unblocked_full.py`, `debug_fallback_pool.py`: Diagnostic tools inspecting unblocked mirror pools.

### D. Database & Indexing Utilities
- `library_indexer.py`: Bulk indexer creating titles and releases inside `media_index.db`.
- `bulk_crawler.py`: Async crawler indexing upstream catalogs.
- `clean_dups.py`: De-duplicates redundant releases from the SQLite database.
- `rebuild_fts.py`: Re-indexes SQLite FTS5 trigram virtual tables.
- `sweep_verified.py`: Scans stored releases and verifies direct streams.
- `update_direct_streams.py`: Updates existing database rows with fresh direct URLs.

### E. Session & Authentication Probes
- `test_google_login.py`: Automates Google account authentication for Drive mirrors.
- `complete_oauth_flow.py`: Handles Google OAuth token exchange.
- `login_paybase_direct.py`: Authenticates with Paybase/DDLBase token providers.

### F. Ad-Hoc / Single-Title Test Probes
- `test_michael_stream.py`: End-to-end stream probe for *Michael* (2023).
- `test_live_jaibhim_vlc.py`: End-to-end stream and VLC playback test for *Jai Bhim*.
- `test_vega_obsession.py`: Target probe for specific Vega release links.
- `test_worker2_series.py`: Diagnostic for series scraper worker pipelines.
- `test_dynamic_retriever.py`, `test_pool_indices.py`: Internal worker diagnostic scripts.
- `test_query_sanitization.py`: Validates input query cleaning.
- `test_match_logic.py`: Evaluates title normalization and word matching.
- `test_play_app_verification.py`: Verifies `play_app.py` CLI functions.
- `test_live_engine_suite.py`: Multi-title live engine verification.
- `test_live_user_10.py`, `preverify_user_10.py`, `quick_test_10.py`, `test_10_final.py`, `complete_10_streams.py`, `find_10_real.py`: Intermediate 10-title test harness scripts.
- `test_api_generate.py`: Stremio API stream JSON structure builder test.
- `fetch_chunk.py`: Tests HTTP Range header chunk streaming.
