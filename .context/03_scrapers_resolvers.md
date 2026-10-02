# Scrapers, Resolvers & Verification Engine

Comprehensive technical specification for content scrapers, link bypass resolvers, stream verifiers, and secondary content pipelines in `play_stremio_cloud`.

---

## 1. Scraper Fleet Architecture (`fast_scrapers.py`)

The scraper fleet provides low-latency (<500ms) catalog searching and extraction across primary streaming aggregators using an HTTP/2 connection-pooled `httpx.AsyncClient` (limits: 30 keepalive, 60 max connections).

```
                            +---------------------------------------+
                            |     FastScrapers.search_all_concurrent|
                            +-------------------+-------------------+
                                                |
                 +-------------------+----------+----------+-------------------+
                 |                   |                     |                   |
                 v                   v                     v                   v
        +-----------------+ +-----------------+  +------------------+ +-----------------+
        |   VegaMovies    | |    UHDMovies    |  |     HDHub4u      | |    OlaMovies    |
        | Typesense Search| | HTML DOM Search |  | Dynamic Pingora  | | HTML Search +   |
        | /ts-search.php  | | 30-Node Lookback|  | Typesense API    | | Base64 Form     |
        +--------+--------+ +--------+--------+  +--------+---------+ +--------+--------+
                 |                   |                     |                   |
                 +-------------------+----------+----------+-------------------+
                                                v
                                  Unified Candidate Releases
```

### Scraper Specifications

| Platform | Target Mirror | Query Protocol | Context Extraction Technique |
| :--- | :--- | :--- | :--- |
| **VegaMovies** | `https://new2.vegamovies.futbol` | HTTP GET `/ts-search.php?q={query}` (Typesense JSON endpoint) | JSON hits parsing -> follow permalink -> scan anchor tags matching `nexdrive\|vcloud\|fast-dl\|v-cloud\|hubcloud`. Preceding 6 DOM nodes inspected for season tag and resolution/size headers. Recursive episode extraction for intermediate hubs. |
| **UHDMovies** | `https://uhdmovies.autos` | HTTP GET `/?s={query}` | Article search results (`article`, `.post-item`) -> fetch top 2 post URLs -> find download links (`driveseed\|cloud.unblocked\|drive.google\|techy\|hubcloud`). Executes 30-node backward DOM traversal (`find_all_previous`) to associate closest resolution, codec, file size, and season header. |
| **HDHub4u** | Dynamic (resolved via Pingora) | Typesense HTTP GET `https://search.pingora.fyi/collections/post/documents/search` | Dynamic mirror lookup via `dns.pingora.fyi/v2/host` (hourly TTL cache). Typesense search over `post_title`, `category`, `stars`, `director`, `imdb_id`. Post permalink rewritten to active mirror -> scan anchor links (`hubcloud\|hubdrive\|drive\|gamerxyt\|hubcdn`). Preceding header scanned for resolution/size context. |
| **OlaMovies** | `https://v4.olamovies.mov` | HTTP GET `/?s={query}` | Article scraping -> top 2 posts matching keyword relevance -> fetch post HTML -> extract anchor tags targeting `links.ol-am` or `links.olamovies`. Regex extraction of resolution, codec (HEVC 10-bit), and file size. |

### Dynamic Mirror Resolution (`get_live_hdhub_mirror`)
- Queries `https://dns.pingora.fyi/v2/host` with Chrome 130 User-Agent.
- Decodes Base64 payload in response field `c` to extract current active host.
- Caches active mirror for 3600 seconds (1 hour). Defaults to `https://new5.hdhub4u.cl` on network or decode failures.

---

## 2. Parallel Media Dispatcher (`parallel_dispatcher.py`)

`ParallelMediaDispatcher` coordinates simultaneous multi-platform searches and hierarchical TV series crawling.

### 2.1 Async Stream Batch Generator (`stream_all_parallel`)
- Yields batches progressively as an `AsyncGenerator` to minimize time-to-first-stream for clients.
- Wraps each scraper source in `asyncio.wait_for` with an 8.0-second timeout (`FLEET_SOURCE_TIMEOUT`) to prevent hung scrapers from blocking the response pipeline.
- Uses `asyncio.as_completed` across primary scrapers (`VegaMovies`, `UHDMovies`, `OlaMovies`, `HDHub4u`, and optional series crawler), followed by secondary scrapers (`Telegram`, `YouTube`).

### 2.2 Strict Matching Algorithm (`is_strict_match`)
To prevent false-positive matches (e.g., matching a sequel when Part 1 was requested, or matching an unrelated film sharing a common word), `is_strict_match(query, candidate_title)` executes a multi-stage validation:
1. **Normalization**: Strips diacritics (`é` -> `e`), normalizes Roman numerals (`ii` -> `2`, `iv` -> `4`, etc.), and removes site noise (e.g., `WEB-DL`, `BluRay`, `Dual Audio`, `Hindi Dubbed`).
2. **Season/Episode Token Isolation**: Strips `SxxExx`, `Season X`, and `Episode Y` tags so episodic labels do not trigger false sequel mismatches.
3. **Sequel & Numeric Token Verification**:
   - `extract_clean_numeric_tokens()` extracts standalone numbers (<=2 digits) while ignoring file sizes (`2GB`, `700MB`), resolutions (`4K`, `1080p`), disc numbers (`2-Disc`), and episode tags.
   - If the query specifies a numeric sequel (e.g., `2` or `4`), the candidate must contain the identical numeric token.
4. **Franchise Alias Table**: Resolves known subtitle franchises via `KNOWN_FRANCHISE_SUBTITLES`:
   - `("avatar", "2")` -> `way of water`
   - `("avatar", "3")` -> `fire and ash`
   - `("spider man", "far from home")`, `("spider man", "no way home")`, `("spider man", "homecoming")`
5. **Year Cross-Check**: If a 4-digit year exists in both query and candidate, the difference must be `<= 1` year.
6. **Token Intersection**: Strips stop words; requires `>= 75%` keyword overlap (`len(matched) >= len(q_keywords) * 0.75`).
7. **Fuzzy Fallback**: Employs `difflib.SequenceMatcher` requiring a similarity ratio `>= 0.65`.

### 2.3 Universal Release Metadata Decoder (`decode_release_metadata`)
Extracts normalized release parameters from button text, adjacent DOM context, and post titles:
- **Resolution**: Detects `2160p`, `4K` (normalized to `2160P`), `1080p`, `720p`, `480p`, `360p`. Defaults to `1080P`.
- **Codec**: Prioritizes `HEVC 10-bit` (`HEVC`, `x265`, `10bit`), `BluRay x264`, `WEB-DL`, `HDRip`, `DVDRip`. Defaults to `x264`.
- **File Size**: Matches patterns like `[1.4 GB]`, `700 MB`, `2.1 GB/E`.
- **Audio Channels / Languages**: Identifies `Dual (Hindi + Eng)`, `Multi-Audio`, `Malayalam (Original)`, `Tamil`, `Japanese (Original)`, `English (Original)`.
- **Season/Episode**: Matches `S01E02`, `Season 1`, `Episode 03`, `Complete Batch [Zip]`, `Part 1`, `OVA`, `Special`.

### 2.4 Concurrent Series Crawler (`run_series_crawler` / `fetch_series_simultaneous`)
- Runs concurrent season and episode discovery on both UHDMovies (`fetch_series_uhd`) and VegaMovies (`fetch_series_vega`).
- Merges discovered season structures (`Season 1`, `Season 2`, ...) and deduplicates qualities per episode.
- Groups individual episodes and complete batch zip links, sorting seasons numerically.

---

## 3. Link Resolver Engine (`link_resolver.py`)

`LinkResolver` bypasses intermediate gateways, interstitial ad pages, and URL shorteners without spawning full browsers for each request.

```
                           +-------------------------------+
                           |    LinkResolver.resolve_auto   |
                           +---------------+---------------+
                                           |
                   +-----------------------+-----------------------+
                   | (Domain / Protocol Router)                    |
                   v                                               v
    +------------------------------+                +-------------------------------+
    | Specialized Bypasses         |                | Universal Fallback            |
    | - DDLBase XOR Cipher         |                | harvest_all_options()         |
    | - DriveSeed / UnblockedGames |                | (DOM regex & link extraction) |
    |   (curl_cffi Chrome120 TLS)  |                +---------------+---------------+
    | - V-Cloud (double atob)      |                                |
    | - HubCloud / Gamerxyt        |                                v
    | - Pixeldrain API transform   |                +-------------------------------+
    | - Cloudflare R2 Presigned    |                | asyncio.as_completed()        |
    +--------------+---------------+                | Concurrent Candidate Racing   |
                   |                                +---------------+---------------+
                   +-----------------------+------------------------+
                                           v
                              Direct Video Stream URL
```

### Provider Bypass Mechanisms

| Provider / Pattern | Method | Implementation Details |
| :--- | :--- | :--- |
| **DDLBase** | `resolve_ddlbase_xor` | Decodes client-side obfuscation in <1ms: Base64-decodes payload, applies XOR with repeating key (default `mySecret123`). |
| **DriveSeed / UnblockedGames** | `resolve_driveseed_multi` | Multi-step form solver. Uses `curl_cffi` (Chrome 120 impersonation) when available. Solves Step 1 -> Step 2 form posts -> extracts `s_343` cookie -> follows `?go=` redirect -> meta refresh -> JS `replace()` to `driveseed.org` -> extracts Google CDN or R2 streaming links. |
| **V-Cloud / NexDrive** | `resolve_vcloud` | Inspects landing page for double base64 JavaScript decoding: `atob(atob('...'))`. Decodes nested base64 string to discover Step 2 redirect URL -> extracts Pixeldrain API or Cloudflare R2 links. |
| **HubCloud / Gamerxyt / Gyanigurus** | `resolve_hubcloud_gamerxyt` | Follows HubDrive to HubCloud -> scans HTML for embedded Gamerxyt links (`gamerxyt.com/...` or `hubcloud.php?id=...`) -> harvests direct download targets. |
| **Pixeldrain** | Regex rewrite | Instant transformation (<1ms): rewrites `pixeldrain.com/u/{id}` to `https://pixeldrain.dev/api/file/{id}`. |
| **Cloudflare R2** | Direct extract | Identifies `*.r2.cloudflarestorage.com` or `*.r2.dev` presigned URLs directly from gateway page DOM. |
| **Google Drive** | `resolve_google_drive` | Bypasses virus scan warnings on large files: queries `drive.google.com/uc?export=download&id={id}`, extracts dynamic CSRF confirm tokens from download form or `download_warning` cookie, constructs `drive.usercontent.google.com` URL. |

### Multi-Option Candidate Racing (`harvest_all_options` & `asyncio.as_completed`)
When a landing page provides multiple mirrors or download options:
1. `harvest_all_options()` extracts all candidate targets: Cloudflare Workers (`*.workers.dev`), Resume Cloud (`/zfile/*`), Cloudflare R2, Instant CDN (`video-gen.xyz`, `video-seed.dev`, `googleusercontent.com`), Pixeldrain, and Google Drive links.
2. `resolve_auto()` creates concurrent tasks (`resolve_candidate`) for each candidate.
3. Uses `asyncio.as_completed()` to race all candidates simultaneously. The first valid direct video stream returned is chosen, aborting slower or failed candidates.

---

## 4. Pre-Flight Stream Verifier (`stream_verifier.py`)

`StreamVerifier` provides pre-flight zero-trust validation of video stream URLs in <200ms without downloading entire files.

### 4.1 Byte-Range Probe Protocol
- Sends an HTTP Range request: `Range: bytes=0-63` with a 2.5-second timeout (`STREAM_CHECK_TIMEOUT`).
- Uses `client.stream("GET", ...)` to consume only the initial chunk (up to 4096 bytes) and immediately aborts the connection. This prevents multi-gigabyte memory buffering when servers ignore the `Range` header and respond with HTTP 200.
- Extracts total file size from `Content-Range: bytes 0-63/{total_bytes}` or `Content-Length`.

### 4.2 Magic Byte Signatures

| Format | Magic Bytes / Signature Pattern | Classification |
| :--- | :--- | :--- |
| **MKV / WebM** | `1A 45 DF A3` (EBML Header) | Valid Video |
| **MP4** | `66 74 79 70` (`ftyp` at offset 4-8), `moov`, `mdat` in first 64 bytes | Valid Video |
| **HLS** | `#EXTM3U` (`23 45 58 54 4D 33 55`) or `.m3u8` in URL/Content-Type | Valid Video |
| **MPEG-TS** | `0x47` Sync Byte at index 0 or `video/mp2t` Content-Type | Valid Video |
| **AVI** | `RIFF` prefix with `AVI ` marker within first 16 bytes | Valid Video |
| **ZIP (Malware / Archive)** | `50 4B 03 04` (`PK..`) | **Rejected** |
| **EXE (Malware / Binary)** | `4D 5A` (`MZ`) | **Rejected** |
| **HTML Error / Gateway** | `<!DOCTYPE`, `<html`, `<?xml`, or `text/html` Content-Type | **Rejected** |
| **Image Files** | `GIF8`, `\x89PNG`, `\xff\xd8\xff` (JPEG), `image/` Content-Type | **Rejected** |

---

## 5. Secondary Content Sources

### 5.1 Telegram MTProto Userbot (`tg_bot_scraper.py` & `tg_local_stream_server.py`)
- **Search (Phase 1)**: Connects via Telethon client (`TelegramClient`) using a shared singleton (`get_shared_tg_client`) to avoid SQLite database locks. Sends sanitized query to the Trios group (`-1001518787755`) and captures inline keyboard buttons containing `/start <token>` parameters.
- **Resolve (Phase 2)**: Sends `/start <token>` to `@Angela2_moviebot`. Automatically joins required update channels if prompted. Receives the media document (`MessageMediaDocument`).
- **Local HTTP 206 Stream Server (`tg_local_stream_server.py`)**:
  - Runs an embedded `aiohttp` web server on `127.0.0.1:8989`.
  - Maps incoming HTTP/1.1 `Range: bytes={start}-{end}` requests to Telethon `client.iter_download()` chunk iterators.
  - Serves full-bitrate video chunks (128KB chunk alignment) directly from MTProto to local video players.
- **Fallback Multi-Bot Forwarding Cascade**: If the local stream server is unavailable, forwards the media document through an 8-bot cascade (`File_To_Link_2Bot`, `FileToLinkbot`, `Filestreamxbot`, `filestreamrobot`, `MegaStreamBot`, `StreamW_Bot`, `TG_FileStreamBot`, `FileStreamerRoBot`) to obtain a public HTTP download URL.

### 5.2 YouTube InnerTube Scraper (`youtube_scraper.py`)
- Direct interaction with the YouTube InnerTube API (`https://www.youtube.com/youtubei/v1/search?prettyPrint=false`) with zero API keys and zero browser overhead (<100ms).
- **Duration Filtering**:
  - Full Movies: Requires video duration `>= 40 minutes` (2400 seconds).
  - Series / Anime: Requires video duration `>= 18 minutes` (1080 seconds).
- **Anti-Junk Filter**: Regex rejects clips, teasers, reactions, trailers, recaps, song tracks, podcasts, and shorts:
  `\b(trailer|teaser|review|reaction|recap|explaining|explained|scene|scenes|clip|clips|shorts|soundtrack|ost|bgm|song|songs|interview|making of|behind the scenes|status|parody|spoof|tribute|edit|fmv|amv|summary|ending explained|breakdown|mistakes|facts|podcast|gameplay|walkthrough)\b`
- **Stream Extraction**: On playback resolve, uses `yt-dlp` with Android/Web/iOS player client extraction to obtain direct CDN playback URLs.
