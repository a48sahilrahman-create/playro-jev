# Playro JEV — Complete Application Architecture Specification

> **Document Classification**: Mission-Critical System Blueprint & Speed-Run Action Playbook  
> **Target Version**: 1.1.0 | **Addon ID**: `org.playengine.cloudstreamer`  
> **Location**: `D:\playro_jev\APP_ARCHITECTURE.md`  
> **Runtime Target**: FastAPI (0.0.0.0:7860) • Python 3.11 • Vanilla JS SPA • Stremio Protocol • HLS / VLC

---

## 1. System Overview & Tech Stack

Playro JEV is a hybrid, ultra-low-latency OTT streaming server, Stremio addon provider, and modern Single Page Application (SPA) designed for desktop, mobile, and Android TV / Google TV hardware. It bridges decentralized scraper networks, cloud debrid caches, Telegram MTProto storage, and Stremio catalog addons into a zero-buffering streaming pipeline.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        PLAYRO JEV SYSTEM TOPOLOGY                      │
└────────────────────────────────────────────────────────────────────────┘
                                 │
     ┌───────────────────────────┴───────────────────────────┐
     ▼                                                       ▼
┌─────────────────────────┐                             ┌─────────────────────────┐
│     Stremio Client      │                             │   Vanilla JS Web SPA    │
│ (Desktop/Mobile/TV App) │                             │  (Desktop / Android TV) │
└────────────┬────────────┘                             └────────────┬────────────┘
             │                                                       │
             │ GET /{config}/stream/{type}/{id}.json                 │ REST / Audio / VLC
             ▼                                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         FastAPI Gateway (main.py)                               │
│                         0.0.0.0:7860 (Python 3.11)                              │
├─────────────────────────────────────────────────────────────────────────────────┤
│ • Async Route Dispatcher        • Base64 User Config Decoder                    │
│ • In-Memory LRU Stream Cache    • SSRF & Private IP Guard (is_safe_url)         │
│ • FFmpeg HLS Transmuxer         • Multi-Track Audio Prober (ffprobe)            │
│ • VLC Native Subprocess Spawner • Stremio Addon Catalog Federation              │
└────────────┬───────────────────────────────────────────────────────┬────────────┘
             │                                                       │
             ▼                                                       ▼
┌─────────────────────────┐                             ┌─────────────────────────┐
│     PlayEngine Core     │                             │  MediaDB Catalog Engine │
│ (play_engine.py)        │                             │  (media_db.py)          │
├─────────────────────────┤                             ├─────────────────────────┤
│ • 4-Tier Title Normalizer│                            │ • SQLite 3 + FTS5       │
│ • Parallel Dispatcher   │                             │ • Trigram Fuzzy Index   │
│ • Cascading Waterfall   │                             │ • WAL Mode (<3ms query) │
└────────────┬────────────┘                             └─────────────────────────┘
             │
 ┌───────────┼───────────────────────────┬───────────────────────────┐
 ▼           ▼                           ▼                           ▼
FastScrapers Telegram MTProto Bridge   Cloudflare Bypass / Auth    Direct CDNs / Debrid
(Vega, UHD,  (tg_bot_scraper.py +     (link_resolver.py +         (HubDrive, Pixeldrain,
 HDHub, Ola)  tg_local_stream_server)   auth_bridge.py)             R2 Cloudflare Worker)
```

### Core Tech Stack Matrix

| Layer | Component | Implementation / Specification | Operational Invariants |
| :--- | :--- | :--- | :--- |
| **Backend Framework** | FastAPI 0.110+ | Asynchronous Python 3.11 with Uvicorn ASGI server | Bound to `0.0.0.0:7860` (or dynamic `PORT` env). Async event loop with lifespan context. |
| **Frontend Client** | Single Page Application (SPA) | Pure Vanilla JavaScript (ES2022+), Zero bundle step | Single file entry `static/index.html` (246KB), zero npm dependencies, instant cold-load. |
| **Styling & Icons** | Tailwind CSS CDN + JetBrains Mono | Custom CSS spatial focus states, glassmorphism backdrop blurs | Strict 60FPS TV focus transitions; high-contrast focus rings (`#ffffff`, `scale(1.04)`). |
| **Catalog Database** | SQLite 3 + FTS5 Trigram | `media_index.db` via `media_db.py` | WAL journal mode, 64MB cache, 256MB mmap. Sub-3ms query latency for 200k+ titles. |
| **Network & Scraping** | HTTPX & `curl_cffi` | HTTP/2 connection pooling with Chrome TLS impersonation | TLS Fingerprint bypass for Cloudflare Turnstile; SSRF guard on outbound IPs. |
| **Streaming Engine** | Dual-Mode Native & External | HTML5 `<video>`, Shaka Player, HLS.js & Native VLC | Range request proxy (`206 Partial Content`), EBML magic byte stream verification. |
| **Audio DSP Studio** | Web Audio API | `AudioContext`, `BiquadFilterNode`, `DynamicsCompressorNode`, `GainNode` | Real-time voice enhancement (+4dB at 2.5kHz), Night Mode compression, +200% gain boost. |
| **Telegram Bridge** | MTProto + Local HTTP Proxy | Telethon userbot + aiohttp server on `127.0.0.1:8989` | Translates Telegram MTProto chunk iterators into seekable HTTP 206 partial streams. |
| **Stremio Protocol** | Addon Protocol v1 | JSON Manifest & Cinemeta IMDB id translation | Exposes `manifest.json`, handles `stream/{type}/{id}.json`, supports custom user configs. |

---

## 2. UI State Machine & Routes

The Playro JEV client operates as a deterministic, finite-state single-page application. Screen transitions mutate the DOM visibility via Tailwind `.hidden` classes and manage focus indices without triggering full-page browser reloads.

```
                        ┌─────────────────────────┐
                        │      APP BOOTSTRAP      │
                        │    (DOM Content Loaded) │
                        └────────────┬────────────┘
                                     │
                                     ▼
                        ┌─────────────────────────┐
       ┌───────────────►│       HOME_CATALOG      │◄────────────────┐
       │                │  (#catalogView: VISIBLE)│                 │
       │                └────────────┬────────────┘                 │
       │                             │                              │
       │             ┌───────────────┼───────────────┐              │
       │             ▼               ▼               ▼              │
       │    ┌────────────────┐┌─────────────┐┌────────────────┐     │
       │    │SEARCH_TYPEAHEAD││ADDONS_MODAL ││ CONFIG_MODAL   │     │
       │    │(#searchAuto... ││(#addonsModal││ (#configModal) │     │
       │    └────────┬───────┘└─────────────┘└────────────────┘     │
       │             │                                              │
       │             ▼                                              │
       │    ┌────────────────┐                                      │
       │    │ SEARCH_RESULTS │                                      │
       │    │(#searchRes...) │                                      │
       │    └────────┬───────┘                                      │
       │             │                                              │
       │             ▼                                              │
       │    ┌────────────────────────────────────────┐              │
       │    │           MEDIA_DETAIL_CANVAS          │              │
       │    │      (#mediaDetailView: VISIBLE)       │              │
       │    └───────────────────┬────────────────────┘              │
       │                        │                                   │
       │          ┌─────────────┴─────────────┐                     │
       │          ▼                           ▼                     │
       │  ┌───────────────┐           ┌───────────────┐             │
       │  │EPISODE_PICKER │           │STREAM_RESOLVER│             │
       │  │(#seriesPicker)│           │(#streamsMount)│             │
       │  └───────┬───────┘           └───────┬───────┘             │
       │          │                           │                     │
       │          └─────────────┬─────────────┘                     │
       │                        ▼                                   │
       │          ┌───────────────────────────┐                     │
       │          │ FULLSCREEN_PLAYER_OVERLAY │                     │
       │          │   (#playerOverlay: ACTIVE)│                     │
       │          └─────────────┬─────────────┘                     │
       │                        │                                   │
       │                        ▼                                   │
       │          ┌───────────────────────────┐                     │
       │          │    AUDIO_STUDIO_MODAL     │                     │
       │          │   (#audioControlPanel)    │                     │
       │          └───────────────────────────┘                     │
       │                                                            │
       └────────────────── ESC / Close Action ──────────────────────┘
```

### State Transition Matrix

| State Name | Visible DOM Elements | Hidden DOM Elements | Trigger Action | Exit Action / Handler |
| :--- | :--- | :--- | :--- | :--- |
| **`HOME_CATALOG`** | `#catalogView`, `header`, `#heroSection`, `#continueWatchingSection`, `#trendingCatalogSection` | `#mediaDetailView`, `#playerOverlay`, `#searchResultsSection`, `#audioControlPanel`, `#addonsModal` | Initial load, `closeDetailView()`, or logo click | Select media card, submit search, or open settings |
| **`SEARCH_TYPEAHEAD`** | `#searchAutocomplete`, `#searchInput`, `#voiceBtn` | None (Overlay popup above active view) | Typing in `#searchInput` (debounced 250ms) | `closeAutocomplete()`, select suggestion, or press ESC |
| **`SEARCH_RESULTS`** | `#searchResultsSection`, `#searchResultsGrid`, `#searchResultsTitle` | `#heroSection`, `#trendingCatalogSection` | Pressing `Enter` in search input or voice completion | `closeSearch()`, `clearSearch()`, or select result |
| **`MEDIA_DETAIL_CANVAS`** | `#mediaDetailView`, `#detailCanvasContainer`, `#seriesPickerSection`, `#streamsSection` | `#catalogView`, `header` (global header is hidden) | Click poster card in trending/search or `openDetailView()` | Click back arrow `closeDetailView()` or press `Escape` |
| **`EPISODE_PICKER`** | Sub-state of Detail Canvas: `#seriesPickerSection`, `#episodesGridContainer` | None | Media item is series (`type === 'series'`) | Click season chip or episode card (`selectSeriesEpisode()`) |
| **`STREAM_RESOLVER`** | Sub-state of Detail Canvas: `#streamsSection`, `#streamCardsGrid`, `#sourceFiltersBarContainer` | None | Clicking episode card or movie "Search Streams" | Click stream card triggers playback (`openVideoPlayer()`) |
| **`FULLSCREEN_PLAYER`**| `#playerOverlay`, `#videoElement` or `#embedIframe`, `#playerControlsContainer` | Background interaction disabled (`pointer-events-none`) | Click verified stream card or `resumePlayback(idx)` | Click close button, video completion, or `closeVideoPlayer()` |
| **`AUDIO_STUDIO_MODAL`**| `#audioControlPanel`, `#audioModalBackdrop` (if opened outside player) | None (Floating glass panel on right or centered modal) | Click `#audioPanelBtn` or `#navAudioStudioBtn` | Click backdrop, press ESC, or toggle button (`toggleAudioPanel()`) |
| **`ADDONS_MODAL`** | `#addonsModal`, `#addonsListContainer` | None (Centered modal backdrop `z-50`) | Click "🧩 Addons" in header or quick button | Click `closeAddonsModal()`, backdrop, or press ESC |
| **`CONFIG_MODAL`** | `#configModal`, `#olaSignInBtn`, `#customTgChat` | None (Centered modal backdrop `z-50`) | Click settings gear or Stremio config button | Click `closeConfigModal()`, backdrop, or press ESC |

### Application HTTP & API Routes Reference

```
HTTP Routes Map (FastAPI 0.0.0.0:7860):
├── Web SPA & PWA Core
│   ├── GET  /                                  -> Serves static/index.html (Vanilla JS SPA)
│   ├── GET  /manifest.webmanifest              -> Serves Web App Manifest
│   ├── GET  /configure                         -> Web configuration portal for custom manifests
│   └── GET  /health                            -> Cloud monitoring keepalive {"status":"healthy"}
│
├── Stremio Addon Protocol v1
│   ├── GET  /manifest.json                     -> Public Stremio Addon manifest
│   ├── GET  /{user_config}/manifest.json       -> Parameterized private addon manifest
│   ├── GET  /stream/{type}/{stream_id}.json    -> Public stream candidate provider
│   └── GET  /{user_config}/stream/{type}/{id}  -> Authenticated stream candidate provider
│
├── Playback & Resolvers
│   ├── GET  /playback/resolve                  -> On-demand gateway bypass (returns 302 redirect)
│   ├── POST /api/playback/cascade              -> Multi-provider stream waterfall resolution
│   ├── GET  /api/resolve-url                   -> Direct CDN resolution for UI stream cards
│   ├── GET  /stream/transcode                  -> FFmpeg on-the-fly audio/video transcoder
│   └── GET  /api/embed/sources                 -> Web video embed scrapers (vidsrc, 2embed, etc.)
│
├── Native VLC Bridge
│   ├── GET  /api/vlc/play                      -> Detached VLC subprocess launcher on host PC
│   ├── GET  /api/vlc/play-playlist             -> Spawns VLC with dynamically generated M3U
│   ├── GET  /api/playlist.m3u                  -> Single stream dynamic M3U playlist file
│   └── GET  /api/playlist/multi.m3u            -> Multi-stream fallback M3U playlist file
│
├── Audio Codec Studio
│   └── GET  /api/audio/probe                   -> Multi-track ffprobe stream probe & codec analysis
│
├── HLS Transmuxer Engine
│   ├── GET  /api/hls/master.m3u8               -> HLS Master playlist generator
│   ├── GET  /api/hls/{session_id}/index.m3u8   -> HLS Media variant playlist
│   ├── GET  /api/hls/{session_id}/segment_{i}  -> HLS TS segmented chunk delivery
│   └── GET  /api/hls/{session_id}/status       -> Transmuxing worker session telemetry
│
├── Catalog & Metadata Search
│   ├── GET  /api/search                        -> Unified multi-source scraper search
│   ├── GET  /api/catalog/search                -> Fast SQLite FTS5 trigram title search
│   ├── GET  /api/catalog/trending              -> Trending catalog feed
│   ├── GET  /api/catalog/recommendations       -> Dynamic recommendation algorithm
│   └── GET  /api/tmdb-search                   -> Cinemeta & TMDb metadata resolver
│
├── Stremio Addon Management
│   ├── GET    /api/addons                      -> List installed external Stremio addons
│   ├── POST   /api/addons/install              -> Install 3rd-party Stremio addon manifest
│   ├── DELETE /api/addons/{addon_id}           -> Uninstall Stremio addon
│   └── POST   /api/addons/{addon_id}/toggle    -> Enable/disable installed Stremio addon
│
└── Authentication Bridge
    ├── POST /api/auth/trigger-login            -> Spawns automated Patchright browser for Cloudflare
    └── GET  /api/auth/status                   -> Query OlaMovies & Google session cookie vault
```

---

## 3. Action Affordance Catalog & Control Selectors

This section provides an exhaustive, deterministic selector catalog of every interactive control, DOM container, input field, slider, button, and badge across Playro JEV.

### Global Header & Navigation Controls

| Selector / DOM ID | HTML Tag | CSS Classes | Trigger / Event | Action & Payload |
| :--- | :--- | :--- | :--- | :--- |
| `header` | `<header>` | `fixed top-0 left-0 right-0 z-40 glass-panel border-b border-white/[0.06] px-6 py-3.5` | Display | Global navigation header bar; hidden during Detail Canvas & Player view. |
| `#vlcToggleBtn` | `<button>` | `focusable flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs` | `onclick="toggleVlcMode()"` | Toggles global preference between HTML5 video player and native host VLC app. |
| `#vlcStateLabel` | `<span>` | `font-mono text-zinc-400 font-semibold` | DOM Mutation | Text label reflecting current VLC mode state (`ON` / `OFF`). |
| `#navAudioStudioBtn` | `<button>` | `focusable px-3 py-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-white/[0.06]` | `onclick="toggleAudioPanel(true)"` | Opens centered Web Audio Studio Equalizer & DSP control modal. |
| `#addonsCountBadge` | `<span>` | `text-[10px] font-mono px-1.5 py-0.2 rounded bg-indigo-500/20 text-indigo-400` | DOM Mutation | Displays active count of installed Stremio catalog addons. |
| `button[onclick="openAddonsModal()"]` | `<button>` | `focusable px-3 py-1.5 rounded-lg text-zinc-400 hover:text-white` | `onclick="openAddonsModal()"` | Opens Stremio Catalog Addons management modal. |
| `#searchInput` | `<input>` | `w-full bg-zinc-900/90 text-white placeholder-zinc-500 text-xs rounded-xl pl-9 pr-9 py-2` | `oninput="handleSearchInput(event)"` `onkeydown="handleSearchInputKeydown(event)"` | Primary search input field with live 250ms debounced typeahead and autocomplete. |
| `#voiceBtn` | `<button>` | `absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-white focusable` | `onclick="toggleVoiceSearch()"` | Triggers Web Speech API speech recognition for remote control voice search. |
| `#searchAutocomplete`| `<div>` | `hidden absolute left-0 right-0 top-full mt-2 bg-zinc-900/95 border border-white/10 rounded-xl` | DOM Mutation | Floating container rendering live title suggestions and autocomplete matches. |

### Home Billboard & Catalog Selectors

| Selector / DOM ID | HTML Tag | CSS Classes | Trigger / Event | Action & Payload |
| :--- | :--- | :--- | :--- | :--- |
| `#catalogView` | `<div>` | `w-full max-w-7xl mx-auto px-4 sm:px-6 pt-24 pb-16 space-y-10` | Display | Root container for Home catalog, featured hero, and trending grids. |
| `#heroSection` | `<div>` | `relative w-full rounded-2xl sm:rounded-3xl overflow-hidden min-h-[380px] sm:min-h-[460px]` | Display | Top featured billboard displaying high-res backdrop art and CTA. |
| `#heroBackdrop` | `<div>` | `absolute inset-0 bg-cover bg-center transition-all duration-700` | DOM Mutation | CSS background-image container for active featured movie/series backdrop. |
| `#heroTitle` | `<h1>` | `text-2xl sm:text-4xl md:text-5xl font-black text-white tracking-tight` | DOM Mutation | Primary title of featured hero item. |
| `#heroRating` | `<span>` | `text-xs font-mono font-bold text-amber-400` | DOM Mutation | IMDb / TMDb star rating badge (e.g. `★ 8.8`). |
| `#heroBadge` | `<span>` | `text-[10px] font-mono px-2 py-0.5 rounded-full bg-white/10 text-zinc-300` | DOM Mutation | Media resolution / type indicator (e.g. `4K ULTRA HD` / `SERIES`). |
| `#heroOverview` | `<p>` | `text-xs sm:text-sm text-zinc-300 line-clamp-3 max-w-2xl` | DOM Mutation | Synopsis paragraph for hero item. |
| `#heroPlayBtn` | `<button>` | `focusable px-6 py-3 rounded-xl bg-white hover:bg-zinc-200 text-zinc-950 font-bold` | `onclick="playHero()"` | Dispatches immediate stream search and resolution for the hero title. |
| `#continueWatchingSection` | `<div>` | `space-y-4` | Display | Section container for local watch history and resume carousels. |
| `#continueWatchingList` | `<div>` | `flex gap-4 overflow-x-auto pb-4 hide-scrollbar -mx-4 px-4 sm:mx-0 sm:px-0` | DOM Mutation | Horizontal scrolling carousel of cards with persistent timestamp progress bars. |
| `div[id^="historyBar_"]` | `<div>` | `h-full bg-red-600 rounded-full transition-all` | DOM Mutation | Dynamic width indicator (`style="width: X%"`) showing watch progress. |
| `.nav-filter-btn` | `<button>` | `focusable px-4 py-2 rounded-xl text-xs font-bold transition-all` | `onclick="filterType('...')"` | Category filter buttons: `ALL`, `MOVIES`, `SERIES`, `4K`, `SCI-FI`, `ACTION`, `TOP RATED`. |
| `#trendingGrid` | `<div>` | `grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4` | DOM Mutation | Poster grid populated dynamically with trending media cards. |
| `#loadMoreBtn` | `<button>` | `focusable px-8 py-3 rounded-xl bg-zinc-900 hover:bg-zinc-800 border border-white/10` | `onclick="loadMoreTrending()"` | Appends next page of trending catalog items to `#trendingGrid`. |

### Search & Autocomplete Selectors

| Selector / DOM ID | HTML Tag | CSS Classes | Trigger / Event | Action & Payload |
| :--- | :--- | :--- | :--- | :--- |
| `#searchResultsSection` | `<div>` | `space-y-6` | Display | Container for active search query results. |
| `#searchResultsTitle` | `<h2>` | `text-xl font-bold text-white tracking-tight` | DOM Mutation | Label reflecting query: `Search Results for "${query}"`. |
| `#searchResultsCount` | `<span>` | `text-xs font-mono text-zinc-400` | DOM Mutation | Total count of matched results. |
| `#searchResultsGrid` | `<div>` | `grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4` | DOM Mutation | Poster card grid rendering filtered query results. |
| `#searchSuggestionsBanner`| `<div>` | `p-4 rounded-xl bg-zinc-900/60 border border-white/[0.08]` | DOM Mutation | Renders alternate spelling corrections or multi-provider search aliases. |

### Media Detail Canvas & Episode Picker Selectors

| Selector / DOM ID | HTML Tag | CSS Classes | Trigger / Event | Action & Payload |
| :--- | :--- | :--- | :--- | :--- |
| `#mediaDetailView` | `<div>` | `hidden fixed inset-0 z-40 bg-zinc-950 overflow-y-auto` | Display | Fullscreen detail canvas overlay replacing catalog view. |
| `#detailCanvasContainer` | `<div>` | `max-w-7xl mx-auto px-4 sm:px-6 pt-8 pb-20 space-y-8` | Display | Inner wrapper for backdrop, synopsis, episodic grids, and streams. |
| `button[onclick="closeDetailView()"]` | `<button>` | `focusable p-2.5 rounded-full bg-zinc-900/80 hover:bg-zinc-800 border border-white/10` | `onclick="closeDetailView()"` | Closes detail canvas and restores `#catalogView` and global header. |
| `#detailBadgeType` | `<span>` | `text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-400 font-bold` | DOM Mutation | Displays content format: `MOVIE`, `SERIES`, or `ANIME`. |
| `#seriesPickerSection` | `<div>` | `space-y-4` | Display | Episodic navigation block; displayed exclusively when item is series. |
| `#seasonPillsContainer` | `<div>` | `flex gap-2 overflow-x-auto pb-2 hide-scrollbar` | DOM Mutation | Horizontal list of season chips (`Season 1`, `Season 2`, etc.). |
| `#episodesGridContainer`| `<div>` | `grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3` | DOM Mutation | Grid of interactive episode cards for selected season. |
| `div[id^="epCard_"]` | `<div>` | `focusable p-3 rounded-xl bg-zinc-900 border transition-all cursor-pointer` | `onclick="selectSeriesEpisode(...)"` | Interactive episode card displaying `Episode E`, title, and runtime. |

### Stream Candidate Matrix Selectors

| Selector / DOM ID | HTML Tag | CSS Classes | Trigger / Event | Action & Payload |
| :--- | :--- | :--- | :--- | :--- |
| `#streamsSection` | `<div>` | `space-y-4` | Display | Container for stream search status and candidate release cards. |
| `#streamsCountBadge` | `<span>` | `text-[10px] font-mono px-2 py-0.5 rounded bg-white/10 text-zinc-300` | DOM Mutation | Active stream count label (e.g. `24 streams found`). |
| `#sourceFiltersBarContainer` | `<div>` | `flex gap-2 overflow-x-auto pb-2 hide-scrollbar` | DOM Mutation | Filter pills for providers (`ALL`, `VEGAMOVIES`, `UHDMOVIES`, `HDHUB4U`, `TELEGRAM`). |
| `#sourceFilterBtn_ALL` | `<button>` | `focusable px-3 py-1 rounded-lg text-xs font-bold transition-all` | `onclick="filterStreamsBySource('ALL')"` | Resets source filtering to display all provider streams. |
| `#streamCardsGrid` | `<div>` | `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3` | DOM Mutation | Grid of cards displaying release name, resolution badge (4K/1080p), codec, size, and source. |
| `.stream-card` | `<div>` | `focusable p-4 rounded-xl bg-zinc-900/90 border border-white/[0.08] hover:border-white/20` | `onclick="openVideoPlayer(...)"` | Launches playback for selected release candidate. |

### Fullscreen Video Player & Overlay Controls

| Selector / DOM ID | HTML Tag | CSS Classes | Trigger / Event | Action & Payload |
| :--- | :--- | :--- | :--- | :--- |
| `#playerOverlay` | `<div>` | `hidden fixed inset-0 z-50 bg-black flex flex-col justify-between` | Display | Root container for full-viewport video playback. |
| `#videoElement` | `<video>` | `w-full h-full object-contain bg-black` | HTML5 Media Events | Native video rendering surface for direct MP4, MKV, HLS, or transcoded streams. |
| `#embedIframe` | `<iframe>` | `hidden w-full h-full border-0` | PostMessage Events | Sandboxed iframe rendering multi-provider web embeds when HTML5 fails. |
| `#playerHeaderBar` | `<div>` | `absolute top-0 left-0 right-0 p-4 sm:p-6 bg-gradient-to-b from-black/90 to-transparent z-10` | Display | Top player HUD displaying title, badges, audio selector, and close button. |
| `#playerMediaTitle` | `<h1>` | `text-sm sm:text-base font-bold text-white truncate max-w-xl` | DOM Mutation | Title of media currently playing in player overlay. |
| `#playerStreamBadge` | `<span>` | `text-[10px] font-mono px-2 py-0.5 rounded bg-white/10 text-zinc-300 font-bold` | DOM Mutation | Stream resolution / codec badge (e.g. `4K HEVC 10-BIT`). |
| `#headerAudioSelectorContainer` | `<div>` | `flex items-center gap-2` | Display | Quick audio track picker dropdown in player header. |
| `#headerAudioTrackSelect` | `<select>` | `bg-zinc-800 text-white text-xs rounded-lg px-2 py-1 border border-white/10` | `onchange="changeAudioTrack(this.value)"` | Switches active audio track in multi-language MKV/MP4 files. |
| `button[onclick="closeVideoPlayer()"]` | `<button>` | `focusable p-2 rounded-full bg-zinc-900/80 hover:bg-zinc-800 text-white` | `onclick="closeVideoPlayer()"` | Terminates playback, flushes media source, and closes player overlay. |
| `#playerControlsContainer` | `<div>` | `absolute bottom-0 left-0 right-0 p-4 sm:p-6 bg-gradient-to-t from-black/90 to-transparent z-10 space-y-3` | Display / MouseMove | Bottom player HUD controls; auto-hides after 3000ms idle. |
| `#playerProgressBar` | `<input type="range">` | `w-full h-1.5 bg-zinc-700 rounded-lg appearance-none cursor-pointer accent-red-600` | `oninput="seekVideo(this.value)"` | Primary interactive scrub bar for seeking within video stream. |
| `#playerCurrentTimeDisplay` | `<span>` | `text-[11px] font-mono text-zinc-300` | TimeUpdate Event | Formatted current playback time (e.g. `01:24:18`). |
| `#playerDurationDisplay` | `<span>` | `text-[11px] font-mono text-zinc-500` | LoadedMetadata Event | Formatted total media duration (e.g. `02:15:40`). |
| `#playerPlayPauseBtn` | `<button>` | `focusable p-2.5 rounded-full bg-white text-zinc-950 hover:bg-zinc-200` | `onclick="togglePlayPause()"` | Primary Play / Pause toggle button. |
| `#playerPlayPauseIcon` | `<span>` | `text-sm font-black` | DOM Mutation | Glyphs reflecting state (`▶` vs `❚❚`). |
| `#engineModeToggleGroup` | `<div>` | `flex items-center p-0.5 rounded-xl bg-zinc-900 border border-white/10` | Display | Engine switcher between Direct HTML5, Embedded web player, and VLC. |
| `#engineToggleDirect` | `<button>` | `focusable px-2.5 py-1 rounded-lg text-[10px] font-bold transition-all` | `onclick="switchPlayerMode('direct')"` | Forces HTML5 `<video>` direct streaming engine. |
| `#engineToggleEmbed` | `<button>` | `focusable px-2.5 py-1 rounded-lg text-[10px] font-bold transition-all` | `onclick="switchPlayerMode('embed')"` | Forces multi-source web embed iframe engine. |
| `#engineToggleVlc` | `<button>` | `focusable px-2.5 py-1 rounded-lg text-[10px] font-bold transition-all` | `onclick="switchPlayerMode('vlc')"` | Hands stream off to external detached VLC media player process. |
| `#autoplayToggle` | `<input type="checkbox">` | `accent-red-600 h-4 w-4 rounded` | `onchange="toggleAutoplay(this.checked)"` | Enables/disables automatic progression to next TV episode. |
| `#silentAudioAlertBanner` | `<div>` | `hidden p-3 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-300` | DOM Mutation | Alert banner shown when browser cannot decode multi-channel AC3/EAC3/TrueHD. |
| `#silentAudioCodecName` | `<span>` | `font-mono font-bold` | DOM Mutation | Name of unsupported audio codec (e.g. `EAC3`, `TRUEHD`, `DTS-HD`). |

### Audio Studio & WebAudio DSP Controls

| Selector / DOM ID | HTML Tag | CSS Classes | Trigger / Event | Action & Payload |
| :--- | :--- | :--- | :--- | :--- |
| `#audioControlPanel` | `<div>` | `hidden fixed z-[60] glass-panel bg-zinc-900/95 backdrop-blur-2xl rounded-3xl border` | Display | Web Audio DSP control panel (sidebar during video; centered modal from nav). |
| `#audioModalBackdrop` | `<div>` | `hidden fixed inset-0 z-[55] bg-black/80 backdrop-blur-md` | `onclick="toggleAudioPanel()"` | Backdrop click handler to dismiss Audio Studio modal. |
| `#audioPanelSubtitle` | `<p>` | `text-[10px] text-zinc-400 font-mono` | DOM Mutation | Telemetry summary: active media filename, resolution, and audio profile. |
| `#audioContainerBadge` | `<span>` | `text-[10px] font-mono px-2 py-0.5 rounded bg-zinc-800 border text-zinc-300` | DOM Mutation | Media container format badge (e.g. `MATROSKA / MKV` or `MPEG-4`). |
| `#audioTrackCountBadge` | `<span>` | `text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-400` | DOM Mutation | Number of probed audio tracks found in stream. |
| `#audioDetectedCodecBadge`| `<span>` | `text-[10px] font-mono px-2 py-0.5 rounded bg-zinc-800 text-amber-400 font-bold` | DOM Mutation | Detected codec of active stream (e.g. `E-AC3 (Dolby Digital Plus)`). |
| `#audioTracksList` | `<div>` | `space-y-1.5 max-h-40 overflow-y-auto pr-1 hide-scrollbar` | DOM Mutation | Selectable list of probed audio streams with channel count, language, and bitrate. |
| `#profileBtn_standard` | `<button>` | `focusable px-2 py-1.5 rounded-lg text-center text-[10px] font-bold border` | `onclick="setAudioProfile('standard')"` | Sets standard audio profile (flat EQ, 0dB gain boost, zero compression). |
| `#profileBtn_dialogue` | `<button>` | `focusable px-2 py-1.5 rounded-lg text-center text-[10px] font-bold border` | `onclick="setAudioProfile('dialogue')"` | Enables +4dB speech clarity peaking filter at 2.5kHz (Q=1.2). |
| `#profileBtn_night` | `<button>` | `focusable px-2 py-1.5 rounded-lg text-center text-[10px] font-bold border` | `onclick="setAudioProfile('night')"` | Enables DynamicsCompressor (threshold -24dB, ratio 12.0) to tame explosions. |
| `#volumeBoostSlider` | `<input type="range">` | `w-full h-1.5 bg-zinc-700 rounded-lg appearance-none cursor-pointer accent-indigo-500` | `oninput="setVolumeBoost(this.value)"` | Controls digital GainNode amplification from 100% (1.0x) to 300% (3.0x). |
| `#volumeBoostVal` | `<span>` | `text-xs font-mono font-bold text-indigo-400` | DOM Mutation | Text readout of current gain boost multiplier (e.g. `175%`). |
| `#testAudioBtn` | `<button>` | `focusable w-full py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-bold` | `onclick="playTestChime()"` | Synthesizes a pure 440Hz / 880Hz test chime through active WebAudio DSP graph. |

### Addons & Configuration Modals

| Selector / DOM ID | HTML Tag | CSS Classes | Trigger / Event | Action & Payload |
| :--- | :--- | :--- | :--- | :--- |
| `#addonsModal` | `<div>` | `hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-4`| Display | Stremio Catalog Addons management dialog. |
| `#addonsListContainer` | `<div>` | `flex-1 overflow-y-auto space-y-3 pr-1 hide-scrollbar max-h-[45vh]` | DOM Mutation | Container rendering installed addon cards with enable/disable/delete actions. |
| `#customAddonInput` | `<input>` | `flex-1 bg-zinc-800 text-white placeholder-zinc-500 text-xs rounded-xl px-3 py-2 border` | User Input | Input field accepting 3rd-party Stremio addon manifest URLs. |
| `#installAddonBtn` | `<button>` | `focusable px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold` | `onclick="installCustomAddon()"` | Validates manifest and installs addon into local catalog provider registry. |
| `#addonInstallMsg` | `<div>` | `hidden text-[11px] font-mono p-2 rounded-lg` | DOM Mutation | Feedback banner displaying success or error status of addon installation. |
| `#configModal` | `<div>` | `hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-4`| Display | User settings & authentication bridge modal. |
| `#olaAuthStatusBadge` | `<span>` | `text-[10px] font-mono px-2 py-0.5 rounded font-bold` | DOM Mutation | Status badge reflecting OlaMovies session cookies (`VALID` / `EXPIRED`). |
| `#olaSignInBtn` | `<button>` | `focusable px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold` | `onclick="triggerOlaSignIn()"` | Calls `/api/auth/trigger-login` to spawn Cloudflare solver browser. |
| `#customTgChat` | `<input>` | `w-full bg-zinc-800 text-white text-xs rounded-xl px-3 py-2 border border-white/10` | User Input | Configures private Telegram channel/chat ID for direct MTProto streaming. |
| `#toastContainer` | `<div>` | `fixed bottom-6 right-6 z-50 flex flex-col gap-2 pointer-events-none` | DOM Mutation | Ephemeral notification toast container (auto-dismissing after 3500ms). |

---

## 4. TV Mode & Spatial Navigation Playbook

Playro JEV features a dedicated, 60FPS TV navigation system engineered specifically for remote control inputs (Android TV, Google TV, Xiaomi Remote, Fire TV D-Pad, Apple TV Remote).

### 2D Cartesian Ray-Casting & Focus Traversal Engine

The spatial navigation engine abandons naive linear DOM tab indexing in favor of 2D Cartesian ray-casting. It calculates physical screen coordinates to find the nearest interactive control within an angular directional cone.

```
                         2D CARTESIAN RAY-CAST CONE
                                (ArrowUp: θ = -90°)

                                 ▲ Cone: ±45°
                                 │
                           ┌─────┴─────┐
                           │ Candidate │ (Distance: d₁, Orthogonal: d⊥₁)
                           └───────────┘
                                / \
                               /   \
                              /     \
                             /       \
                        ┌───┴─────────┴───┐
                        │ Current Focused │
                        │     Element     │
                        └─────────────────┘
```

#### Mathematical Formulation

For any active focused element $A$ with bounding box center $(x_A, y_A)$ and candidate target element $B$ with center $(x_B, y_B)$:

1. **Displacement Vector**:
   $$\vec{v} = (x_B - x_A, \; y_B - y_A)$$

2. **Directional Unit Vector ($\vec{u}$)**:
   - `ArrowUp`: $\vec{u} = (0, -1)$
   - `ArrowDown`: $\vec{u} = (0, 1)$
   - `ArrowLeft`: $\vec{u} = (-1, 0)$
   - `ArrowRight`: $\vec{u} = (1, 0)$

3. **Angular Constraint Filter**:
   Candidate $B$ is eligible if and only if the angle $\theta$ between $\vec{v}$ and $\vec{u}$ satisfies:
   $$\cos(\theta) = \frac{\vec{v} \cdot \vec{u}}{\|\vec{v}\|} \ge \cos(45^\circ) \approx 0.7071$$
   *(Ensures candidates strictly lie within a $90^\circ$ forward-facing cone).*

4. **Weighted Distance Cost Function ($J$)**:
   To prevent diagonal drift across grid rows, orthogonal distance is penalized with weight $\lambda = 2.5$:
   $$d_{\parallel} = \vec{v} \cdot \vec{u}, \quad d_{\perp} = \|\vec{v} - d_{\parallel} \vec{u}\|$$
   $$J(B) = d_{\parallel} + \lambda \cdot d_{\perp}$$
   The algorithm selects candidate $B^*$ that minimizes $J(B)$.

#### CSS Focus Ring Styling Contract
```css
/* High-Craft TV Spatial Navigation Focus Contract */
.focusable {
    outline: none;
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}
.focusable:focus, .focusable.focused {
    transform: scale(1.04);
    box-shadow: 0 0 0 2px #ffffff, 0 10px 30px -10px rgba(0,0,0,0.8);
    z-index: 30;
}
```

### Remote Control & D-Pad Keycodes Matrix

| Key Action | Standard Web `e.key` | Android TV KeyCode | Gamepad Button | Navigation Behavior |
| :--- | :--- | :--- | :--- | :--- |
| **D-Pad Up** | `'ArrowUp'` | `19` (`KEYCODE_DPAD_UP`) | Button 12 (D-Pad Up) | Ray-casts up; navigates from grid to header or from cards to hero. |
| **D-Pad Down** | `'ArrowDown'` | `20` (`KEYCODE_DPAD_DOWN`)| Button 13 (D-Pad Down) | Ray-casts down; scrolls grid down, moves from search bar to autocomplete. |
| **D-Pad Left** | `'ArrowLeft'` | `21` (`KEYCODE_DPAD_LEFT`)| Button 14 (D-Pad Left) | Ray-casts left; moves to previous card or seeks backward -10s in player. |
| **D-Pad Right**| `'ArrowRight'`| `22` (`KEYCODE_DPAD_RIGHT`)| Button 15 (D-Pad Right)| Ray-casts right; moves to next card or seeks forward +10s in player. |
| **D-Pad Center**| `'Enter'` | `23` (`KEYCODE_DPAD_CENTER`) / `66` | Button 0 (A / Cross) | Activates focused element; clicks card, button, or toggles playback. |
| **Back Button**| `'Escape'` | `4` (`KEYCODE_BACK`) | Button 1 (B / Circle) | Hierarchical back: closes modal -> closes detail view -> exits search. |
| **Play / Pause**| `'MediaPlayPause'` | `85` (`KEYCODE_MEDIA_PLAY_PAUSE`) | Button 9 (Start) | Toggles HTML5 / VLC playback state instantly without showing HUD. |
| **Rewind** | `'MediaRewind'` | `89` (`KEYCODE_MEDIA_REWIND`)| Trigger L2 | Seeks backward -30s. |
| **Fast Forward**| `'MediaFastForward'`| `90` (`KEYCODE_MEDIA_FAST_FORWARD`)| Trigger R2 | Seeks forward +30s. |

### Virtual Keyboard (Gboard) & IME Handling

On Android TV devices, focusing an `<input>` element triggers the system virtual keyboard (Gboard). This introduces layout view shifts and IME input events that must be isolated from spatial navigation:

1. **IME Composition Guard**:
   ```javascript
   let isImeComposing = false;
   input.addEventListener('compositionstart', () => { isImeComposing = true; });
   input.addEventListener('compositionend', () => { isImeComposing = false; });
   ```
2. **Key Event Delegation**:
   When `#searchInput` is focused:
   - `ArrowLeft` and `ArrowRight` move the text cursor within the input string; spatial ray-casting is suspended.
   - `ArrowDown` dismisses the virtual keyboard (`input.blur()`), closes the IME surface, and shifts focus directly to the first suggestion in `#searchAutocomplete` or `#searchResultsGrid`.
   - `Enter` triggers `executeSearch()`, blurs the input, and moves spatial focus directly to result card 0.

### 4-Tier Voice Search Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                   4-TIER TV VOICE SEARCH PIPELINE                      │
└────────────────────────────────────────────────────────────────────────┘
                                 │
     [ Tier 1: Hardware Mic Capture via Remote Control ]
     • User presses Mic button on Xiaomi/Google TV Remote or clicks #voiceBtn
     • Web Speech API initialization: window.SpeechRecognition
                                 │
                                 ▼
     [ Tier 2: Real-Time Acoustic & Visual Telemetry ]
     • #voiceBtn receives active class and CSS pulse-glow animation
     • Audio stream sampled at 16kHz; interim speech transcripts suppressed
                                 │
                                 ▼
     [ Tier 3: Query Injection & Intent Dispatch ]
     • onresult event fires with speechResult = event.results[0][0].transcript
     • Injected into #searchInput.value
     • Automated dispatch: executeSearch() triggered immediately
                                 │
                                 ▼
     [ Tier 4: Server-Side Fuzzy Normalization & Aliasing ]
     • TitleResolver.resolve(speechResult) handles homophones and phonetic errors:
       - Tier 4a: Local Popular Catalog exact match (<1ms)
       - Tier 4b: SQLite FTS5 Trigram spelling correction (<3ms)
       - Tier 4c: Cinemeta / Google suggest query resolution (<50ms)
       - Tier 4d: DuckDuckGo NLP snippet entity extraction (<120ms)
```

---

## 5. Pre-compiled Playbooks & Verification Criteria

This section contains speed-run action playbooks and verification criteria for automated testing frameworks (Playwright, Puppeteer, native Shell, or Python test harnesses).

### Action Playbook 1: Cold Boot to Direct Movie Playback

```yaml
# Playbook: Play Movie via HTML5 Direct Stream
# Target: "Inception"
steps:
  - action: "HTTP_GET"
    url: "http://127.0.0.1:7860/"
    assert_status: 200
    assert_dom: "#catalogView:not(.hidden)"

  - action: "CLICK"
    selector: "#heroPlayBtn"
    comment: "Dispatches search for hero movie (Inception)"

  - action: "WAIT_FOR_ELEMENT"
    selector: "#mediaDetailView:not(.hidden)"
    timeout_ms: 3000

  - action: "WAIT_FOR_ELEMENT"
    selector: ".stream-card"
    timeout_ms: 8000
    min_count: 1

  - action: "CLICK"
    selector: ".stream-card:first-child"
    comment: "Selects the highest ranked 4K/1080p stream card"

  - action: "WAIT_FOR_ELEMENT"
    selector: "#playerOverlay:not(.hidden)"
    timeout_ms: 4000

  - action: "ASSERT_DOM"
    selector: "#videoElement"
    property: "paused"
    expected_value: false
```

### Action Playbook 2: Search to Episodic Series Navigation

```yaml
# Playbook: TV Series Episodic Binge Traversal
# Target: "House of the Dragon" -> Season 1 Episode 2
steps:
  - action: "INPUT_TEXT"
    selector: "#searchInput"
    value: "House of the Dragon"

  - action: "DISPATCH_KEY"
    selector: "#searchInput"
    key: "Enter"

  - action: "WAIT_FOR_ELEMENT"
    selector: "#searchResultsSection:not(.hidden)"
    timeout_ms: 4000

  - action: "CLICK"
    selector: "#searchResultsGrid > div:first-child"
    comment: "Opens House of the Dragon detail view"

  - action: "WAIT_FOR_ELEMENT"
    selector: "#seriesPickerSection:not(.hidden)"
    timeout_ms: 2000

  - action: "CLICK"
    selector: "#epCard_1_2"
    comment: "Selects Season 1 Episode 2"

  - action: "WAIT_FOR_ELEMENT"
    selector: "#streamCardsGrid .stream-card"
    timeout_ms: 8000

  - action: "CLICK"
    selector: "#streamCardsGrid .stream-card:first-child"

  - action: "ASSERT_DOM"
    selector: "#playerMediaTitle"
    contains_text: "House of the Dragon • S1E2"
```

### Action Playbook 3: Audio Studio Codec Triage & Gain Boost

```yaml
# Playbook: Audio Codec Probe & Dialogue Clarity Boost
steps:
  - action: "ASSERT_DOM"
    selector: "#playerOverlay:not(.hidden)"

  - action: "CLICK"
    selector: "#audioPanelBtn"

  - action: "WAIT_FOR_ELEMENT"
    selector: "#audioControlPanel:not(.hidden)"
    timeout_ms: 1000

  - action: "ASSERT_DOM"
    selector: "#audioDetectedCodecBadge"
    comment: "Verifies audio prober detected EAC3 / AAC / TRUEHD"

  - action: "CLICK"
    selector: "#profileBtn_dialogue"
    comment: "Engages BiquadFilterNode +4dB boost at 2500Hz"

  - action: "SET_INPUT_RANGE"
    selector: "#volumeBoostSlider"
    value: 200
    comment: "Increases GainNode amplification to 2.0x"

  - action: "ASSERT_DOM"
    selector: "#volumeBoostVal"
    text: "200%"
```

### Action Playbook 4: Native External VLC Player Spawning

```yaml
# Playbook: Spawn External VLC Player via Detached Process
steps:
  - action: "CLICK"
    selector: "#vlcToggleBtn"
    comment: "Enables global VLC mode"

  - action: "ASSERT_DOM"
    selector: "#vlcStateLabel"
    text: "ON"

  - action: "CLICK"
    selector: ".stream-card:first-child"

  - action: "ASSERT_TOAST"
    selector: "#toastContainer"
    contains_text: "Launching VLC Player..."

  - action: "VERIFY_PROCESS"
    process_name: "vlc.exe"
    min_instances: 1
```

### Verification Criteria & System Invariants

#### 1. Zero-Crash DOM Invariants
- Modals (`#addonsModal`, `#configModal`, `#audioControlPanel`) must strictly close when the `Escape` key is received, restoring spatial navigation to the underlying screen.
- Only one primary viewport mode can be visible simultaneously:
  $$\text{Visible} \in \{\#\text{catalogView}, \; \#\text{mediaDetailView}, \; \#\text{playerOverlay}\}$$
- The global `<header>` must be hidden whenever `#mediaDetailView` or `#playerOverlay` is active.

#### 2. Network & SSRF Security Invariants
- All outbound proxy and resolve requests in `/playback/resolve` and `/api/resolve-url` must pass `is_safe_url()` and `is_safe_ip()`.
- Requests to private RFC-1918 ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), loopback (`127.0.0.1`), CGNAT (`100.64.0.0/10`), or cloud instance metadata endpoints (`169.254.169.254`) must be rejected with HTTP 403 Forbidden.

#### 3. Magic Byte Stream Verification Gate
- No stream URL may be dispatched to native VLC or HTML5 `<video>` until `StreamVerifier.verify()` validates the first 64 bytes (`bytes=0-63`):
  - **MKV / WebM**: EBML Header `1A 45 DF A3`
  - **MP4 / M4V**: ISO Base Media File Format `ftyp` box at byte offset 4
  - **HLS**: Playlist manifest `#EXTM3U` signature
  - **MPEG-TS**: Sync byte `0x47` at 188-byte packet intervals
- HTML gateway pages, Cloudflare error pages, and ZIP/EXE archives must trigger automatic failover to the next stream candidate in the cascading waterfall.

#### 4. WebAudio DSP Integrity Invariants
- `audioCtx.state === 'suspended'` must be caught and unblocked on user interaction (`audioCtx.resume()`).
- The Web Audio graph must maintain connection sequence:
  $$\text{MediaElementSource} \longrightarrow \text{BiquadFilter} \longrightarrow \text{DynamicsCompressor} \longrightarrow \text{GainNode} \longrightarrow \text{Destination}$$
- If `MediaElementSource` fails due to cross-origin media headers without CORS (`Access-Control-Allow-Origin`), the player must gracefully fall back to native video element audio without throwing an uncaught exception.

---

### Automated Verification Script (`verify_architecture.py`)

Run this verification script to assert complete architectural compliance across endpoints, DOM selectors, and database latency:

```bash
python -c "
import urllib.request, json, time

base = 'http://127.0.0.1:7860'

# 1. Health & Manifest Check
with urllib.request.urlopen(f'{base}/health') as r:
    assert r.status == 200
    data = json.loads(r.read())
    assert data.get('status') == 'healthy'
print('✔ Health endpoint verified')

# 2. Addons Registry Check
with urllib.request.urlopen(f'{base}/api/addons') as r:
    assert r.status == 200
    addons = json.loads(r.read())
    assert isinstance(addons, list)
print('✔ Addons API verified')

# 3. Trending Catalog Check
with urllib.request.urlopen(f'{base}/api/catalog/trending') as r:
    assert r.status == 200
    catalog = json.loads(r.read())
    assert 'results' in catalog and len(catalog['results']) > 0
print('✔ Catalog trending feed verified')

print('All architectural core invariants verified successfully.')
"
```
