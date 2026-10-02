# Play Engine (play_stremio_cloud) — Architecture & AI Context

> **Executive Primer for AI Agents & New Chats**:
> This document provides 100% architectural comprehension of `play_stremio_cloud` in under 3,000 tokens. Read this index first. For deep implementation tasks in a specific subsystem, follow the links in the **Deep-Dive Directory Router** at the bottom.

---

## 1. System Identity & Core Stack

- **Project**: Play Engine — Universal Multi-User Cloud Stremio Addon & Standalone OTT Streaming Bridge
- **Version**: 1.1.0 | **Addon ID**: `org.playengine.cloudstreamer`
- **Primary Server**: FastAPI + Python 3.11 (`main.py`)
- **Default Port**: `7860` (configurable via `PORT` env var; matches Hugging Face Spaces & Render defaults)
- **Core Storage**: SQLite 3 with FTS5 trigram full-text search (`media_index.db`, `media_db.py`)
- **Network Engine**: HTTPX (HTTP/2 connection pooling), `curl_cffi` (Chrome TLS impersonation), Telethon (MTProto)
- **Deployment Targets**: Render (`render.yaml`), Docker (`Dockerfile`), Local Windows (`start.ps1`)
- **Zero-Storage Security Invariant**: User credentials (OlaCookies, Telegram tokens) are never stored on the server; they are encoded into URL-safe Base64 path segments and decoded per-request.

---

## 2. Core Module Catalog

| File | Subsystem | Key Exports / Responsibilities |
| :--- | :--- | :--- |
| `main.py` | FastAPI Application | App entrypoint, all HTTP routes, Stremio manifest, PWA static serving, `decode_user_config()`, `STREAM_SEARCH_CACHE` (150 items, 600s TTL). |
| `play_engine.py` | Core Orchestrator | `PlayEngine` class: `.search()`, `.resolve()`, `.play()`. Implements `rank_release()` sorting and VLC subprocess spawning. |
| `parallel_dispatcher.py` | Async Batch Dispatcher | `ParallelMediaDispatcher.stream_all_parallel()` async generator. Implements `is_strict_match()`, series crawler, `decode_release_metadata()`. |
| `fast_scrapers.py` | Source Scrapers | `FastScrapers`: UHDMovies (DOM lookback), VegaMovies (Typesense API), HDHub4u (pingora DNS mirror), OlaMovies. |
| `link_resolver.py` | Gateway Bypass & Security | `LinkResolver.resolve_auto()`: DDLBase XOR, DriveSeed curl_cffi, V-Cloud double-atob, HubCloud, Pixeldrain. SSRF guard (`is_safe_url`). |
| `stream_verifier.py` | Pre-Flight Stream Check | `StreamVerifier.verify()`: Range request `bytes=0-63`. Validates MKV (`1A 45 DF A3`), MP4 (`ftyp`), HLS, MPEG-TS. Filters malware. |
| `title_resolver.py` | Search Normalizer | `TitleResolver.resolve()`: 4-tier pipeline (POPULAR_CATALOG, fuzzy local, IMDb/Google suggest, DuckDuckGo snippet). |
| `media_db.py` | Catalog & FTS5 Index | `MediaDB`: SQLite FTS5 trigram schema (`titles`, `releases`), WAL mode, 64MB cache, 256MB mmap. `<3ms` query latency. |
| `tg_bot_scraper.py` | Telegram MTProto Client | Telethon userbot: Trios group search (`-1001518787755`), Angela bot resolve, 8-bot forwarding cascade. |
| `tg_local_stream_server.py` | Local Telegram Bridge | aiohttp server on `127.0.0.1:8989`: Translates HTTP 206 Partial Content range requests to Telethon MTProto chunk streams. |
| `youtube_scraper.py` | YouTube InnerTube Scraper | Zero-API-key video search via InnerTube API. Duration filter: movies ≥40m, series ≥18m. Anti-junk regex. |
| `auth_bridge.py` | Interactive Auth & Vault | `InteractiveAuthBridge`: Patchright/Playwright popup for Cloudflare Turnstile / Google login. Writes to `session_vault.json`. |
| `play_app.py` | Interactive CLI TUI | Standalone terminal interface with hierarchical series/season/episode navigation and VLC playback. |
| `media_index.db` | Catalog Database | Pre-indexed SQLite database containing titles, releases, and trigram FTS5 search tables. |
| `Dockerfile` & `render.yaml` | Production Deployment | Python 3.11-slim container on port 7860; Render free web service specification with zero server secrets. |

---

## 3. API Routes Quick-Reference

| Method | Endpoint | Description & Payload |
| :--- | :--- | :--- |
| `GET` | `/` | Serves PWA OTT Web App (`static/index.html`) or redirects to `/configure`. |
| `GET` | `/configure` | Zinc-950 dark web portal for generating custom private Stremio addon manifest URLs. |
| `GET` | `/health` | Cloud keepalive check: `{"status": "healthy", "service": "play_engine_stremio_addon"}`. |
| `GET` | `/manifest.json` | Public Stremio Addon manifest (`idPrefixes: ["tt", "play:"]`, resources: `["stream"]`). |
| `GET` | `/{user_config}/manifest.json` | Private manifest; renames addon with user-specific bot name if `tg_token` is present. |
| `GET` | `/{user_config}/stream/{type}/{id}.json` | Master stream dispatcher. Dispatches queries, searches scrapers in parallel, returns `{"streams": [...]}`. |
| `GET` | `/{user_config}/playback/resolve` | On-demand gateway bypass. Resolves intermediary URLs to direct video CDN links; returns HTTP 302. |
| `POST` | `/api/auth/trigger-login` | Spawns non-headless browser popup via `InteractiveAuthBridge` to capture provider cookies. |
| `GET` | `/api/auth/status` | Returns session vault status and cookie count for OlaMovies and Google. |

---

## 4. End-to-End Data Flow

```
                        [ Stremio Client / Web PWA ]
                                     │
                 GET /{user_config}/stream/{type}/{id}.json
                                     ▼
                          ┌─────────────────────┐
                          │       main.py       │
                          │ decode_user_config  │
                          └──────────┬──────────┘
                                     │
           ┌─────────────────────────┴─────────────────────────┐
           ▼                                                   ▼
┌──────────────────────┐                             ┌───────────────────┐
│ resolve_imdb_to_title│                             │ STREAM_SEARCH_    │
│ Cinemeta API (<40ms) │                             │ CACHE (150 items) │
└──────────┬───────────┘                             └───────────────────┘
           │ Title + Year
           ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        play_engine.PlayEngine                          │
│  TitleResolver.resolve()  → 4-tier spell-check (<100ms)                │
│  ParallelMediaDispatcher.stream_all_parallel()                         │
│    ├── FastScrapers  → VegaMovies, UHDMovies, HDHub4u, OlaMovies       │
│    ├── tg_bot_scraper→ Telegram Trios MTProto (-1001518787755)         │
│    ├── YouTubeScraper→ InnerTube API (movies ≥40m)                     │
│    └── MediaDB       → Local SQLite FTS5 trigram search (<3ms)         │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ Raw Releases
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  rank_release() Priority Sorting:                                      │
│  1. Single Episode Bonus (series)  4. Resolution (2160p > 1080p > 720p)│
│  2. Direct Link / Pre-verified     5. Codec (HEVC > AVC > Remux)       │
│  3. Host Score (R2/Pxl > DriveSeed)6. File Size (GB)                   │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ Formatted Stream List
                                   ▼
                    Return {"streams": [{"url": ...}]}
                                   │
              User clicks Play     │
                                   ▼
                 GET /{user_config}/playback/resolve?url=...
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        link_resolver.LinkResolver                      │
│  is_safe_url() SSRF Guard (blocks private IPs, AWS/GCP metadata)       │
│  Route to bypass handler:                                              │
│    • DDLBase XOR       • DriveSeed curl_cffi  • V-Cloud double-atob    │
│    • HubCloud/Gamerxyt • Pixeldrain API       • Cloudflare R2 presign  │
│  StreamVerifier.verify() (HTTP Range bytes=0-63, EBML magic bytes)     │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
                         HTTP 302 Redirect
                                   │
                                   ▼
                     [ VLC / Native Video Player ]
```

---

## 5. The 7 Critical Invariants

1. **Stateless User Configuration**: User configs are never stored in a server database. They are encoded into URL-safe Base64 (`{public, ola, tg_token, tg_chat}`) with an 8192-byte max length and 512-byte per-field cap.
2. **SSRF Guarding on All Outbound Traffic**: Every outgoing HTTP request passes through `is_safe_url()` and `is_safe_ip()`. All RFC-1918 private ranges, CGNAT (`100.64.0.0/10`), AWS metadata (`169.254.169.254`), and GCP metadata (`metadata.google.internal`) are blocked. Maximum 5 redirect hops.
3. **Zero-Trust Stream Verification (EBML Magic Bytes)**: Streams are never trusted by HTTP 200 status code alone. `StreamVerifier` fetches `bytes=0-63` via HTTP Range and validates magic bytes: MKV (`1A 45 DF A3`), MP4 (`ftyp`), HLS (`#EXTM3U`), MPEG-TS (`0x47`). HTML error pages and ZIP/EXE files are rejected.
4. **Fast First-Batch Streaming**: `ParallelMediaDispatcher` is an async generator. As soon as any scraper returns candidates, they are yielded immediately to avoid waiting for slow sources.
5. **In-Memory Stream Caching**: `STREAM_SEARCH_CACHE` stores search results for 600s (10 min) with an LRU capacity of 150 items to eliminate redundant scraping for popular titles.
6. **Isolated MTProto Telegram Bridge**: Telegram downloads run via a local embedded aiohttp server on `127.0.0.1:8989` that proxies MTProto chunk iterators as standard HTTP 206 Partial Content range streams.
7. **Human-Reality Testing Mandate**: Real-world verification is conducted via `verify_10_movies_vlc.py`, playing real streams in a real VLC process for 15–20 continuous seconds.

---

## 6. Deep-Dive Directory Router

When you need detailed technical specifications, schemas, or implementation details for a specific subsystem, read the corresponding modular file in `.context/`:

- [**`.context/01_architecture.md`**](.context/01_architecture.md) — Detailed system design, component relationship graph, `PlayEngine` lifecycle, ranking algorithms, and series parsing.
- [**`.context/02_api_routes.md`**](.context/02_api_routes.md) — Exact specifications for all 9 FastAPI routes, Stremio addon contract, query parameters, response schemas, and Base64 config encoding.
- [**`.context/03_scrapers_resolvers.md`**](.context/03_scrapers_resolvers.md) — Scraper implementations (Vega, UHD, HDHub, Ola), `LinkResolver` bypass engines (DriveSeed, V-Cloud, HubCloud), `StreamVerifier` magic bytes, and Telegram/YouTube scrapers.
- [**`.context/04_security_config.md`**](.context/04_security_config.md) — SSRF denylists, IP range filtering, URL config sanitization, session vault storage (`session_vault.json`), and `InteractiveAuthBridge`.
- [**`.context/05_testing_verification.md`**](.context/05_testing_verification.md) — Pytest contract suites (`tests/`), human-reality VLC playback protocol (`verify_10_movies_vlc.py`), benchmark suites, and root test script catalog.
- [**`.context/06_deployment_ops.md`**](.context/06_deployment_ops.md) — Docker container build, Render cloud deployment (`render.yaml`), local dev launcher (`start.ps1`), keepalive ping strategy, and database path resolution.
