# Playro JEV — TV Series Discrete Episode Extraction & Scraper Hardening Plan

> **File Location:** `D:\playro_jev\TV_SERIES_EPISODE_EXTRACTION_PLAN.md`  
> **Status:** Architecture & Implementation Ready  
> **Target Subsystem:** Multi-Source TV Episode Scraping, Strict Title Matching & Single-Episode Filtering  
> **Target Files:** `D:\playro_jev\parallel_dispatcher.py`, `D:\playro_jev\fast_scrapers.py`, `D:\playro_jev\main.py`, `D:\playro_jev\link_resolver.py`

---

## 1. Executive Summary & Root Cause Analysis

In previous versions, clicking **Season 1 Episode 1** (e.g. for *"The Boys"*) resulted in the UI displaying:
- **Season 5** posts (`The Boys (Season 5) WEB-DL ... [ALL Episodes] (55.8 GB)`)
- **Unrelated shows** (`My Life with the Walter Boys (Season 3) (54.8 GB)`)
- **Full season batch/zip packs** instead of single episode streaming links.

### The Four Root Causes Identified:

1. **Title Matcher Stop-Word Bleed (`parallel_dispatcher.py` -> `is_strict_match`)**:
   - `is_strict_match` strips common stop words: `{'movie', 'series', 'the', 'a', 'an', ...}`.
   - For query `"The Boys"`, `"the"` is removed, leaving only `['boys']`.
   - The single-keyword fallback evaluates `if q_keywords[0] in c_keywords: return True`.
   - As a result, *"My Life with the Walter Boys"* matched because it contained `"boys"`.
   - Furthermore, season numbers in titles were stripped prior to checking, allowing Season 5 posts to pass as valid candidate matches for Season 1 queries.

2. **Scraper Grabs Default View Only (`fast_scrapers.py` -> `search_hdhub4u`)**:
   - HDHub4u series post pages load with the **`Zip/Pack`** tab active by default.
   - `search_hdhub4u` performs a simple `find_all("a", href=re.compile(r'hubcloud|hubdrive|drive|gamerxyt|hubcdn'))` on the post body.
   - On the default post view, the only links matching those patterns are the **full season zip/batch packs** (15GB - 55GB).
   - The scraper **never opens, queries, or parses `<div class="series-tab-content" id="episodes">`** (the **Single EP's** tab).

3. **Episode Links Use Gateway Domains Filtered Out by Regex**:
   - Inside the Single EP's tab, discrete episode buttons point to gateway links (e.g., `https://greenmotors.club/?id=...` or `https://greenmotors.cc/...`).
   - The button text explicitly says `Download HubCloud` or `Download HubDrive`.
   - Because the scraper's regex only looked for `hubcloud|hubdrive` in the `href` attribute rather than inspecting the anchor text / data attributes, it ignored all individual episode links.

4. **Empty Match `safe_fallback` Dump (`main.py` -> `filter_episode_streams`)**:
   - When discrete `S01E01` links were not found by the scrapers, `matched` was empty (`0 items`).
   - `filter_episode_streams` contained a fallback: `if not matched: return safe_fallback`.
   - `safe_fallback` contained all post-level items that lacked explicit opposing episode tags, dumping 300+ batch files, out-of-season posts, and "Walter Boys" releases onto the screen.

---

## 2. Target Website Reality & DOM Anatomy (HDHub4u / 4KHDHub)

Live DOM inspection of `4khdhub.one` (and HDHub4u mirrors) proves that **both tabs are present directly in the server-rendered HTML**:

```html
<!-- Tab Selector Buttons -->
<div class="series-tabs" id="series-tabs">
  <button class="series-tab" data-tab="complete-pack">Zip/Pack</button>
  <button class="series-tab active" data-tab="episodes">Single EP's</button>
</div>

<!-- Complete Season Zip Packs (Currently what scraper grabs by mistake) -->
<div class="series-tab-content" id="complete-pack">
  <h2>Download Complete Season</h2>
  <!-- Season 3, Season 2, Season 1 Batch Packs (17GB - 55GB) -->
</div>

<!-- Discrete Single Episodes Tab (What we MUST parse) -->
<div class="series-tab-content" id="episodes">
  <h2>Download Individual Episodes</h2>
  
  <!-- Season Container (e.g. S03, S02, S01) -->
  <div class="season-block" data-season="3">
    <!-- Format / Quality Accordion -->
    <div class="format-header">
      <h3>JUJUTSU KAISEN S03 (AMZN 1080p H.264) | Episodes 12 | Hindi, Japanese</h3>
    </div>
    
    <!-- Episode Item Rows -->
    <div class="episode-row">
      <div class="file-title">Jujutsu.Kaisen.S03E01.Execution.1080p.AMZN.WEB-DL...mkv</div>
      <span class="badge">Episode-01</span>
      <span class="size">1.16 GB</span>
      
      <!-- Primary Stream Buttons -->
      <a href="https://greenmotors.club/?id=..." class="btn btn-sm">Download HubCloud</a>
      <a href="https://greenmotors.club/?id=..." class="btn btn-sm">Download HubDrive</a>
    </div>
    
    <div class="episode-row">
      <div class="file-title">Jujutsu.Kaisen.S03E02.Once.Again.1080p.AMZN.WEB-DL...mkv</div>
      <span class="badge">Episode-02</span>
      <span class="size">0.96 GB</span>
      <a href="https://greenmotors.club/?id=..." class="btn btn-sm">Download HubCloud</a>
      <a href="https://greenmotors.club/?id=..." class="btn btn-sm">Download HubDrive</a>
    </div>
  </div>
</div>
```

### Key Extraction Rules:
1. **Target Tab**: Target `<div id="episodes">` or elements under `Single EP's` / `Download Individual Episodes`.
2. **Season Context**: Extract season number from the container heading or format label (`S01`, `Season 1`, `S03`).
3. **Episode Context**: Extract episode number from `Episode-01`, `E01`, or the `.mkv` filename.
4. **Cloud Drive Priority (Zero-Login)**:
   - **Priority 1: `Download HubCloud`** &rarr; Leads directly to HubCloud CDN (Cloudflare R2, Workers CDN, Pixeldrain) without any login requirement.
   - **Priority 2: `Download HubDrive`** &rarr; Gateway to HubCloud.
5. **Gateway Anchor Matching**: Match `<a>` tags where `href` contains `hubcloud|hubdrive|drive|greenmotors` OR where button text contains `"HubCloud"` / `"HubDrive"` / `"Episode"`.

---

## 3. Step-by-Step Implementation Blueprint

### Step 1: Harden Title Matcher (`D:\playro_jev\parallel_dispatcher.py`)

**File:** `D:\playro_jev\parallel_dispatcher.py`  
**Function:** `is_strict_match(query: str, candidate_title: str) -> bool`

```python
# Fix single-word stopword collapse:
# When query is "The Boys", do not allow single-token "boys" to match any title containing "boys".
if len(q_keywords) == 1:
    q_kw = q_keywords[0]
    # Candidate base tokens must be an exact match or anchored start
    if len(c_keywords) == 1 and q_kw == c_keywords[0]:
        return True
    # If candidate has multiple words, only match if the query phrase (e.g. "the boys") appears as a full phrase
    if f" {q.strip()} " in f" {candidate_title.lower()} ":
        return True
    return False
```

Also enforce season validation in title matching when a specific season is requested:
- If `season` is specified (e.g., Season 1), reject post candidates whose title explicitly specifies a different season (e.g., `Season 5`, `S05`, `Season 4`).

---

### Step 2: Implement Single-Episode Parser for HDHub4u (`D:\playro_jev\fast_scrapers.py`)

**File:** `D:\playro_jev\fast_scrapers.py`  
**Function:** `search_hdhub4u(client, query)` (and helper `parse_hdhub_episodes_tab`)

```python
def parse_hdhub_episodes_tab(soup, post_title: str, target_season: Optional[int] = None) -> list:
    """
    Parses <div class="series-tab-content" id="episodes"> on HDHub4u/4KHDHub pages.
    Extracts individual episode streams prioritizing Download HubCloud over HubDrive.
    """
    ep_results = []
    ep_tab = soup.find("div", id="episodes") or soup.find("div", class_=re.compile(r'episodes', re.I))
    if not ep_tab:
        return []

    # Find all season containers or format accordions
    # Traverse child sections
    # Detect current season number (s_num)
    # For each episode block:
    #   extract ep_num (e.g. Episode-01 -> 1)
    #   extract size (e.g. 1.16 GB)
    #   extract resolution (1080p, 720p, 4K)
    #   find HubCloud button first; fallback to HubDrive button
    #   append release dict:
    #     title = f"{post_title} S{s_num:02d}E{ep_num:02d}"
    #     label = f"S{s_num:02d}E{ep_num:02d} - {btn_text} [{size}]"
    #     episode_tag = f"S{s_num:02d}E{ep_num:02d}"
    #     resolution = detected_res
    #     size = size_str
    #     target_url = btn['href']
    #     is_batch = False
    return ep_results
```

When `type == "series"` or `season` is passed:
1. First, invoke `parse_hdhub_episodes_tab`.
2. Only if `ep_results` is empty, fallback to post-level parsing.
3. Mark post-level season packs explicitly with `is_batch = True`.

---

### Step 3: Integrate HDHub4u into `ParallelMediaDispatcher` Series Fleet (`D:\playro_jev\parallel_dispatcher.py`)

**File:** `D:\playro_jev\parallel_dispatcher.py`  
Add `fetch_series_hdhub` to `ParallelMediaDispatcher`:

```python
async def fetch_series_hdhub(self, client: httpx.AsyncClient, query: str) -> dict:
    """Fetches TV series episodes from HDHub4u including Single EP's tab."""
    # Resolves live mirror
    # Queries Typesense for series posts
    # For each matching post, parses both Single EP's and season blocks
    # Returns seasons_dict[season][episode] = [releases]
```

In `fetch_series_simultaneous(query)`:
```python
vega_task = self.fetch_series_vega(client, query)
uhd_task = self.fetch_series_uhd(client, query)
hdhub_task = self.fetch_series_hdhub(client, query)
# Gather all 3 concurrently
```

---

### Step 4: Zero-Leak Enforcement in `filter_episode_streams` (`D:\playro_jev\main.py`)

**File:** `D:\playro_jev\main.py`  
**Function:** `filter_episode_streams(releases: list, season: int, episode: int) -> list`

Eliminate the loose `safe_fallback` return:
```python
# 1. Collect all strict SxxExx matches
matched = []
for r in releases:
    # Reject is_batch == True
    # Reject batch_kw_pattern in label/title/episode_tag
    # Reject conflicting seasons (e.g. S02, S03 when querying S01)
    # Match exact S01E01 in episode_tag or label or title
    if is_matched:
        matched.append(r)

# If matched has streams, return them sorted by resolution/size
if matched:
    return matched

# ZERO LEAK MANDATE:
# Never return full season batches or unrelated releases when a discrete episode is requested.
# If no discrete episode stream was found, return [] so UI shows clean state rather than wrong season packs.
return []
```

---

### Step 5: Resolver Support for Gateway Domains (`D:\playro_jev\link_resolver.py`)

**File:** `D:\playro_jev\link_resolver.py`  
Ensure `resolve_auto` handles `greenmotors.club` and `greenmotors.cc` mediator domains:
- If a target URL is `greenmotors.club/?id=...` or `greenmotors.cc/...`:
  - Fetch page with referer of original mirror.
  - Extract the destination redirect or follow the client location.
  - Hand off to `resolve_hubcloud_gamerxyt`.
  - Direct streams are resolved to Cloudflare R2 / Workers CDN / Pixeldrain API in <200ms without logins.

---

## 4. Verification & Testing Checklist (`/test` Protocol)

| Test Item | Command / Action | Expected Result |
| :--- | :--- | :--- |
| **1. Strict Matcher Unit Test** | `python test_strict_matcher.py` | `"The Boys"` matches `"The Boys (Season 1)"`, but rejects `"My Life with the Walter Boys"`. |
| **2. HDHub4u Single EP DOM Test** | Script testing `parse_hdhub_episodes_tab` against `https://4khdhub.one/jujutsu-kaisen-series-224/` | Returns discrete list of episodes: `S03E01`, `S03E02`, etc., with `Download HubCloud` links and `1.16 GB` size. |
| **3. Zero-Leak Filter Test** | Call `filter_episode_streams` with Season 1 Episode 1 for a mix of S01E01, S05 Batch, and Walter Boys | Only exact `S01E01` releases pass; `S05` and `Walter Boys` are 100% rejected. |
| **4. Live E2E API Test** | `GET /api/search?q=The+Boys&type=series&season=1&episode=1` | Returns only Season 1 Episode 1 releases; 0 Season 5 batches, 0 Walter Boys. |
| **5. Playback Resolve Test** | Call `/playback/resolve` with extracted HubCloud link | Returns direct playable stream URL (R2 / Workers / Pixeldrain) without requiring login. |

---

## 5. Quick-Start Guide for Next Session

To execute this plan in a new chat:
1. Ask the assistant:
   > *"Read `D:\playro_jev\TV_SERIES_EPISODE_EXTRACTION_PLAN.md` and execute the 5-step implementation plan across `parallel_dispatcher.py`, `fast_scrapers.py`, `main.py`, and `link_resolver.py`."*
2. Run `/test` to verify with live API calls.
