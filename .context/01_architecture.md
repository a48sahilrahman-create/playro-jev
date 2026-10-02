# Play Engine: Architecture & System Design Specification

## 1. System Design & Component Relationships

Play Engine is a high-concurrency, low-latency streaming middleware and Stremio addon server. It coordinates multi-source web scrapers, link bypass resolvers, Telegram MTProto userbots, stream verification probes, and local SQLite caches to deliver verified 4K/1080p media streams in under 800 milliseconds.

```
+-----------------------------------------------------------------------------------------------+
|                                       FastAPI Server                                          |
|                                         (main.py)                                             |
|   - Config Portal & PWA Serving        - Stream Search Cache (150 items / 600s TTL)          |
|   - Manifest Endpoints (Public/Private)- Episode Filtering & Regex Matcher                    |
|   - Master Stream Dispatcher           - Cinemeta Dual API Resolver                           |
+-----------------------------------------------+-----------------------------------------------+
                        |                       |                       |
                        | (search / play)       | (direct link resolve) | (auth trigger / status)
                        v                       v                       v
            +-----------------------+   +-------------------+   +--------------------+
            |      PlayEngine       |   |   LinkResolver    |   | InteractiveAuth-   |
            |   (play_engine.py)    |   | (link_resolver.py)|   |      Bridge        |
            +-----------+-----------+   +---------+---------+   |  (auth_bridge.py)  |
                        |                         |             +---------+----------+
       +----------------+----------------+        |                       |
       |                |                |        | (bypass logic)        | (session vault)
       v                v                v        v                       v
+-------------+  +-------------+  +-------------+----------------+  +--------------------+
|TitleResolver|  |   MediaDB   |  |     ParallelMediaDispatcher   |  | session_vault.json |
| (fuzzy/IMDb)|  | (SQLite FTS)|  |   (parallel_dispatcher.py)    |  | olamovies_session_ |
+-------------+  +-------------+  +---+---------+------------+----+  |    cookies.json    |
                                      |         |            |       +--------------------+
             +------------------------+         |            +-----------------------+
             v                                  v                                    v
   +--------------------+             +--------------------+               +--------------------+
   |    FastScrapers    |             |   Telegram Scraper |               |  YouTube Scraper   |
   | (fast_scrapers.py) |             | (tg_bot_scraper.py)|               |(youtube_scraper.py)|
   | - UHDMovies        |             | - Telethon Client  |               | - InnerTube API    |
   | - VegaMovies       |             | - Angela2 Bot      |               | - Full Movie Filter|
   | - HDHub4u          |             | - FileToLink Bot   |               +--------------------+
   | - OlaMovies        |             +--------------------+
   +---------+----------+                       |
             |                                  |
             +-----------------+----------------+
                               |
                               v
                     +-------------------+
                     |  StreamVerifier   |
                     |(stream_verifier.py|
                     | - HTTP Range 0-63 |
                     | - EBML/MP4 Header |
                     +-------------------+
```

### Component Interconnect Matrix

| Component | File | Responsibilities & Downstream Dependencies |
| :--- | :--- | :--- |
| **FastAPI Core** | `main.py` | Addon protocol endpoints, base64 user config decoding, in-memory stream caching, episode regex filtering, Cinemeta metadata resolution, VLC process dispatching. |
| **PlayEngine** | `play_engine.py` | Top-level execution orchestrator unifying search, link resolution, stream verification, and VLC spawning. |
| **ParallelMediaDispatcher** | `parallel_dispatcher.py` | Concurrently fans out search queries across all scrapers via `stream_all_parallel()`, parses multi-tier TV seasons and episode batches, deduplicates mirror links. |
| **FastScrapers** | `fast_scrapers.py` | Async HTTP/2 scrapers for UHDMovies, VegaMovies, HDHub4u (dynamic DNS mirror lookup), and OlaMovies. |
| **LinkResolver** | `link_resolver.py` | Ultra-fast (< 200ms) link resolver with per-hop SSRF validation, private IP denylists, Cloudflare R2 / Pixeldrain / Google CDN extractors, DriveSeed / V-Cloud / HubCloud bypasses. |
| **StreamVerifier** | `stream_verifier.py` | Zero-trust pre-flight inspection via HTTP `Range: bytes=0-63` streaming chunks. Validates EBML container (`1A 45 DF A3`), MP4 boxes (`ftyp`, `moov`, `mdat`), TS sync bytes (`0x47`), and rejects malware/HTML errors. |
| **MediaDB** | `media_db.py` | Local SQLite FTS5 database configured with Write-Ahead Logging (WAL), 64MB cache, and 256MB memory-mapped I/O (`mmap_size`) for < 3ms catalog lookups. |
| **TitleResolver** | `title_resolver.py` | Spell-checking, Roman-to-numeric token mapping, and concurrent IMDb Suggestion + Google Suggestion API normalization (< 50ms). |
| **TelegramBotScraper**| `tg_bot_scraper.py` | Telethon MTProto userbot querying private media index bots (`@Angela2_moviebot`, `@FileToLinkBot`) in Trios group (-1001518787755). |
| **YouTubeScraper** | `youtube_scraper.py` | InnerTube API direct HTTP/2 queries for full-length feature films and anime (> 40 min runtime filtering, junk regex suppression). |
| **InteractiveAuthBridge**| `auth_bridge.py`| Patchright/Playwright automated browser daemon. Spawns visible login popup for Cloudflare Turnstile / Google OAuth, captures session cookies, and persists to `session_vault.json`. |

---

## 2. PlayEngine Lifecycle

The `PlayEngine` class encapsulates the complete lifecycle across four distinct stages: initialization, search, resolution, and playback execution.

### 2.1 `__init__()`
Instantiates foundational subsystems:
- `self.db`: `MediaDB` SQLite instance.
- `self.resolver`: `TitleResolver` instance.
- `self.scrapers`: `FastScrapers` instance.
- `self.dispatcher`: `ParallelMediaDispatcher` instance.
- `self.verifier`: `StreamVerifier` instance.
- `self.link_resolver`: `LinkResolver` instance.
- `self.vlc_path`: Resolved VLC binary path using prioritized scan: `C:\Program Files\VideoLAN\VLC\vlc.exe`, `C:\Program Files (x86)\VideoLAN\VLC\vlc.exe`, or system `PATH`.

### 2.2 `search(query: str) -> dict`
Unified live multi-source parallel search across web sources without stale local database contamination:
1. **Title Normalization**: Calls `self.resolver.resolve(query)`. Obtains `canonical_query`, `formatted_title`, and `year`.
2. **Query Expansion**: Builds permutation list: `[query.strip(), canonical_query, f"{query} {year}", formatted_title]`.
3. **Fleet Execution**: Streams releases via `self.dispatcher.stream_all_parallel(sq)`:
   - Excludes local DB results (`platform == "Local DB"`).
   - Deduplicates items against a global `seen_targets` set based on `target_url` or `direct_url`.
4. **Ranking**: Sorts all candidate releases using `rank_release` in descending order.
5. **Output Schema**: Returns structured dictionary containing `status` (`CATALOG_FOUND` or `NO_RESULTS`), `source`, `query`, `canonical_title`, `formatted_title`, `year`, `count`, `releases`, and `total_ms`.

### 2.3 `resolve(target_url: str, platform: str = "auto") -> dict`
Targeted two-step link resolution:
1. **Gateway Bypass**: Invokes `self.link_resolver.resolve_auto(target_url)` to unwrap intermediary gates (V-Cloud, DriveSeed, Gamerxyt, HubCloud, Pixeldrain, dl.olamdrive) into a direct media CDN URL.
2. **Pre-flight Range Check**: Passes direct URL to `self.verifier.verify(direct_url)`.
3. **Output Schema**: Returns `status` (`RESOLVED` if verified valid, otherwise `FAILED`), `target_gateway_url`, `direct_stream_url`, `gateway_host`, `gateway_ms`, `verification`, and `total_ms`.

### 2.4 `play(query: str, auto_launch: bool = True) -> dict`
One-shot millisecond pipeline designed for instant desktop playback:
1. **Local DB Lookup (< 3ms)**: Executes `self.db.search(query)` in an asynchronous thread worker (`asyncio.to_thread`) to avoid blocking the event loop.
2. **Live Scraping Fallback**: If local DB yields no match, calls `self.resolver.resolve()` followed by `self.scrapers.search_all_concurrent()`, caching discovered records back into `MediaDB`.
3. **Candidate Sorting**: Sorts releases prioritizing pre-verified direct URLs: `(1 if (r.get('direct_url') and 'http' in r.get('direct_url')) else 0, rank_release(r))`.
4. **Resolution & Verification Loop**: Evaluates sorted candidates sequentially. Rejects non-video links (`.html`, domain homepages). Validates binary container magic bytes via `StreamVerifier`. If no candidate passes verification, halts with `STREAM_VERIFICATION_FAILED`.
5. **Player Spawn**: Spawns non-blocking VLC process via `subprocess.Popen([self.vlc_path, resolved_stream])` with suppressed stdio streams (`subprocess.DEVNULL`).

---

## 3. In-Memory Stream Cache (`STREAM_SEARCH_CACHE`)

Defined in `main.py` to prevent redundant network scraping on repeated searches and rapid UI navigation:

```python
STREAM_SEARCH_CACHE: Dict[str, dict] = {}
CACHE_EXPIRY_SECONDS = 600  # 10 minutes TTL
```

### Cache Operational Parameters
- **Maximum Item Capacity**: 150 items.
- **TTL Duration**: 600 seconds (10 minutes).
- **Key Normalization**: `key.lower().strip()`. For series requests, formatted as `{base_show_title}_s{season}_e{episode}` or `{base_show_title}_s{season}`.
- **Data Payload**:
  ```python
  {
      "timestamp": float,  # time.time()
      "data": dict         # Complete search response payload
  }
  ```

### Eviction and Retrieval Semantics
- **LRU Timestamp Eviction**: When cache insertion causes size to exceed 150 entries:
  ```python
  if len(STREAM_SEARCH_CACHE) > 150:
      oldest_key = min(STREAM_SEARCH_CACHE.keys(), key=lambda k: STREAM_SEARCH_CACHE[k].get("timestamp", 0))
      STREAM_SEARCH_CACHE.pop(oldest_key, None)
  ```
- **Conditional Cache Bypass**: `get_cached_streams(key)` invalidates and ignores cached entries if `releases` has fewer than 2 items, allowing fresh live scraping to discover more streams.

---

## 4. `rank_release()` Sorting Algorithm

The sorting heuristic evaluates release dictionaries into a 5-element tuple:

$$\text{Sort Key} = (\text{single\_ep\_bonus}, \text{host\_score}, \text{res\_score}, \text{codec\_score}, \text{size\_gb})$$

```python
def rank_release(release: dict) -> tuple:
    return (single_ep_bonus, host_score, res_score, codec_score, size_gb)
```

### 4.1 Scoring Tier Definitions

#### 1. Single Episode Bonus (`single_ep_bonus: int`)
- **`0`**: Release label or episode tag contains `batch`, `zip`, `complete series`, `all episodes`, or `full season`.
- **`1`**: Discrete standalone episode release.

#### 2. Host Score (`host_score: int`)
Prioritizes direct playback capability and CDN throughput:
- **`100`**: Instant 1-click playable video stream hosts: `nexdrive`, `vcloud`, `r2.`, `pixeldrain`, `googleusercontent`, `worker`.
- **`70`**: Intermediate fast cloud mirrors: `hubdrive`, `gamerxyt`, `hubcloud`.
- **`50`**: Telegram streaming bot bridges: `t.me`, `tg://`.
- **`10`**: Multi-step gateways subject to Cloudflare challenge delays: `unblockedgames`, `driveseed`.
- **`0`**: Unrecognized / generic hosts.

#### 3. Resolution Score (`res_score: int`)
- **`100`**: `2160P` or `4K`
- **`80`**: `1080P`
- **`50`**: `720P`
- **`30`**: `480P` / unrecognized resolution

#### 4. Codec & Audio Score (`codec_score: int`)
- **`30`**: `REMUX`
- **`20`**: `HEVC`, `X265`, or `10BIT`
- **`15`**: `BLURAY`
- **`10`**: Standard encode (`H.264`, `WEB-DL`, default)

#### 5. File Size (`size_gb: float`)
Extracted from `size`, `title`, and `label` via regex `(\d+(?:\.\d+)?)\s*(GB|MB|GiB|MiB)`.
- If unit is `MB` or `MiB`, converted via `val / 1024.0`.
- **Resolution Heuristic Fallback** (when size string is missing):
  - `2160P` / `4K`: `18.0` GB
  - `1080P`: `4.0` GB
  - `720P`: `1.2` GB
  - Other: `0.4` GB

---

## 5. Series Hierarchical Parsing & S/E Regex Filtering

### 5.1 Episode Filtering Logic (`filter_episode_streams`)
Located in `main.py`, isolates specific episode streams from heterogeneous post listings:

```python
def filter_episode_streams(releases: list, season: int, episode: int) -> list
```

#### Exact Episode Regex Patterns
```regex
1. \bs0*{s}\s*e0*{e}\b
2. \b{s}x0*{e}\b
3. \bseason\s*0*{s}\b.*?\b(?:episodes?|ep)\s*0*{e}\b
4. \bs0*{s}\b.*?\b(?:episodes?|ep)\s*0*{e}\b
5. \b(?:episodes?|ep)\s*0*{e}\b
```

#### Batch Exclusion Regex
Applied strictly to the button label (`label`), avoiding false exclusions caused by series titles:
```regex
\b(s\d+\s*-\s*s\d+|s\d+\s*to\s*s\d+|season\s*\d+\s*-\s*\d+|season\s*\d+\s*to\s*\d+|complete\s*series|all\s*episodes|full\s*season\s*zip|season\s*\d+\s*zip|complete\s*batch|full\s*batch|batch\s*zip|pack)\b
```

#### Two-Stage Resolution Strategy
1. **Target Episode Match**: Checks `full_text = f"{title} {label} {ep_tag}"`. Matches against exact patterns 1-4 and verifies `is_batch_btn` is `False`.
2. **Compound Match**: Checks if season matches in `full_text` (`\b(?:s|season)\s*0*{s}\b`) and episode matches in label or tag (`\b(?:episodes?|ep|e)\s*0*{e}\b`).
3. **Fallback Tiers**:
   - Return `matched` list if populated.
   - Else return `season_matched` list if populated.
   - Else fallback to unfiltered `releases`.

### 5.2 Hierarchical Series Aggregator (`ParallelMediaDispatcher`)
The dispatcher groups TV series into hierarchical structures before flattening:
```
Show Title
└── Season N
    ├── Episode X
    │   ├── 4K UHD variant (DriveSeed / V-Cloud)
    │   ├── 1080p variant (NexDrive / Pixeldrain)
    │   └── 720p variant (HubCloud)
    └── Complete Batch [Zip]
```

---

## 6. Cinemeta Dual API Metadata Fallback

Located in `main.py`, `resolve_imdb_to_title` maps IMDb IDs (`tt0468569`) to canonical metadata in under 40 milliseconds:

```python
async def resolve_imdb_to_title(imdb_id: str, media_type: Optional[str] = None) -> Optional[Dict[str, Any]]
```

### Execution Protocol
1. **ID Sanitation**: Strips compound episode keys (e.g. `tt0468569:1:1` -> `tt0468569`).
2. **Endpoint Matrix**:
   - Movie Endpoint: `https://v3-cinemeta.strem.io/meta/movie/{clean_id}.json`
   - Series Endpoint: `https://v3-cinemeta.strem.io/meta/series/{clean_id}.json`
3. **Concurrent Dual Dispatch**: Dispatches both HTTP GET requests simultaneously via `asyncio.gather(*tasks, return_exceptions=True)`.
4. **Adaptive Priority Ordering**:
   - If `media_type == "series"`, queries series first, followed by movie.
   - Otherwise, queries movie first, followed by series.
5. **Extraction**: Inspects first successful 200 OK response containing a valid `meta.name` property:
   ```python
   return {
       "name": data.get("name"),
       "year": str(data.get("year", "")).split("–")[0].strip(),
       "type": order[idx]
   }
   ```
6. **Graceful Fallback**: Returns `None` if Cinemeta is unreachable, allowing raw ID fallback without raising exceptions.
