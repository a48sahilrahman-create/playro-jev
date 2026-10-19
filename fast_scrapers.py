"""
High-Performance Async Scrapers (< 500ms) for Direct HTTP/2 Token & Catalog Extraction
Covers:
  1. UHDMovies (DriveSeed / Google CDN)
  2. VegaMovies (V-Cloud / Pixeldrain API / Cloudflare R2)
  3. HDHub4u (HubCloud / Gamerxyt / R2)
  4. 4KHDHub (HubCloud / HubDrive / GreenMotors)
  5. OlaMovies (Next.js SSR / Direct Links)
"""

import sys
import time
import asyncio
import re
import json
import html
import urllib.parse
from typing import Optional, List, Dict, Any
import httpx
from bs4 import BeautifulSoup
from parallel_dispatcher import is_strict_match
from jev_stream_resolver import JevStreamResolver
from link_resolver import LinkResolver
from domain_resolver import (
    DomainResolver,
    get_active_mirror,
    get_active_mirror_sync,
    report_mirror_failure,
    report_mirror_success
)

# Active Mirrors (sync fallback for backward compatibility)
UHD_MIRROR = get_active_mirror_sync("uhdmovies")
VEGA_MIRROR = get_active_mirror_sync("vegamovies")
HDHUB_MIRROR = get_active_mirror_sync("hdhub4u")
DESIRE_MIRROR = get_active_mirror_sync("desiremovies")
OLA_MIRROR = get_active_mirror_sync("olamovies")

async def get_live_hdhub_mirror(client: httpx.AsyncClient = None) -> str:
    """Dynamically resolve active HDHub4u mirror from domain_resolver (< 5ms cached)."""
    return await get_active_mirror("hdhub4u")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

class FastScrapers:
    def __init__(self):
        self.limits = httpx.Limits(max_keepalive_connections=30, max_connections=60)
        self.domain_resolver = DomainResolver()

    async def search_uhdmovies(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        """Fast search & catalog parsing on UHDMovies (< 350ms)."""
        clean = re.sub(r'\s+', '+', query.strip())
        uhd_mirror = await self.domain_resolver.get_mirror("uhdmovies")
        url = f"{uhd_mirror}/?s={clean}"
        results = []

        try:
            resp = await client.get(url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=2.5)
            if resp.status_code == 200:
                await self.domain_resolver.report_success("uhdmovies")
                soup = BeautifulSoup(resp.text, "lxml")
                articles = soup.find_all("article") or soup.find_all("div", class_=re.compile(r'post-item|grid-item|entry'))

                async def fetch_post(art):
                    link = art.find("a", href=True)
                    post_title = ""
                    for h in art.find_all(["h1", "h2", "h3", "h4", "a"]):
                        t = h.get_text(strip=True) or h.get("title", "")
                        if len(t) > 3 and not any(k in t.lower() for k in ["admin", "comment", "reply", "read more"]):
                            post_title = t
                            break
                    if not post_title and link:
                        post_title = link.get("title", "") or link.get_text(strip=True)

                    if not (link and post_title) or not is_strict_match(query, post_title):
                        return []

                    post_url = link["href"]
                    if post_url.startswith("/"):
                        post_url = f"{uhd_mirror}{post_url}"
                    post_results = []
                    try:
                        p_resp = await client.get(post_url, headers=DEFAULT_HEADERS, timeout=2.5)
                        if p_resp.status_code == 200:
                            await self.domain_resolver.report_success("uhdmovies")
                            p_soup = BeautifulSoup(p_resp.text, "lxml")
                            d_links = p_soup.find_all("a", href=re.compile(r'driveseed|cloud\.unblocked|drive\.google|techy|hubcloud|naukriadda|\?sid='))
                            for dl in d_links:
                                label = dl.get_text(strip=True) or dl.find_parent().get_text(strip=True)
                                href = dl["href"]

                                # 1. Filter navigation, category, and intro/outro anchor links
                                label_clean = label.lower().strip()
                                if label_clean in [
                                    "4k", "2160phevc", "1080p uhd", "uhdmovies", "3d movies",
                                    "4k hdr", "4k 2160p", "1080p 60fps", "1080p x264 uhd",
                                    "1080p x265 10bit", "4k movies", "1080p movies", "720p movies"
                                ]:
                                    continue
                                parent_p = dl.find_parent("p")
                                if parent_p and any(k in parent_p.get_text().lower() for k in ["here you can download", "uhd movies is one of the best"]):
                                    continue
                                is_button = bool(dl.find_parent(class_=re.compile(r'maxbutton|btn|button')) or dl.get("class"))
                                is_dl_keyword = any(k in label_clean for k in ["download", "drive", "g-drive", "episode", "ep", "pack", "zip", "link", "direct", "instant", "fast"])
                                if not (is_button or is_dl_keyword):
                                    continue

                                # 2. Deep lookback for release title, season, and exact file size
                                context_text = label
                                season_tag = ""
                                header_text = ""
                                detected_size = ""

                                sm_lbl = re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB)(?:\s*/\s*E)?)\]?', label, re.I)
                                if sm_lbl:
                                    detected_size = sm_lbl.group(1).upper().strip("[] ")

                                for prev in dl.find_all_previous(["p", "h1", "h2", "h3", "h4", "h5", "div", "span", "strong", "b"])[:40]:
                                    if dl in prev.find_all("a"):
                                        continue
                                    pt = prev.get_text(" ", strip=True)
                                    if len(pt) > 600:
                                        continue

                                    if not season_tag:
                                        sm_s = re.search(r'\b(Season\s*\d+|S0*\d{1,2})\b', pt, re.I)
                                        if sm_s:
                                            num_m = re.search(r'\d+', sm_s.group(1))
                                            if num_m:
                                                season_tag = f"Season {int(num_m.group(0))}"

                                    if not header_text and any(k in pt.lower() for k in ["2160p", "4k", "1080p", "720p", "480p"]):
                                        block = prev.find_parent(["p", "h1", "h2", "h3", "h4", "h5", "div"]) if prev.name in ["span", "strong", "b", "em"] else prev
                                        if block and dl not in block.find_all("a"):
                                            header_text = block.get_text(" ", strip=True)
                                        else:
                                            header_text = pt

                                    if not detected_size:
                                        sm_sz = re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB)(?:\s*/\s*E)?)\]?', pt, re.I)
                                        if sm_sz:
                                            detected_size = sm_sz.group(1).upper().strip("[] ")

                                    if header_text and detected_size and (season_tag or "season" not in post_title.lower()):
                                        break

                                if header_text:
                                    context_text = f"{header_text} {label}"
                                if season_tag and season_tag.lower() not in context_text.lower():
                                    context_text = f"{season_tag} {context_text}"

                                if not detected_size:
                                    sm_ctx = re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB)(?:\s*/\s*E)?)\]?', context_text, re.I)
                                    if sm_ctx:
                                        detected_size = sm_ctx.group(1).upper().strip("[] ")

                                res_m = re.search(r'(2160p|4K|1080p|720p|480p)', context_text, re.I)
                                detected_res = res_m.group(1).upper() if res_m else ("4K/UHD" if "2160" in post_title else "1080P")
                                if detected_res == "4K":
                                    detected_res = "2160P"

                                ep_m = re.search(r'\b(Episode\s*0*(\d{1,3})|Ep\s*0*(\d{1,3})|E0*(\d{1,3}))\b', label, re.I)
                                is_range = bool(re.search(r'(?:episodes?|ep|e)\s*\d+\s*-\s*\d+', label, re.I))
                                is_batch_kw = any(k in label.lower() for k in ["batch", "zip", "pack", "full season", "all episodes", "complete"]) or bool(re.search(r'\b(batch|zip|pack|full\s*season|all\s*episodes|complete\s*(?:series|season|batch|pack)|s\d+\s*-\s*s\d+|season\s*\d+\s*zip)\b', label, re.I))

                                s_num_m = re.search(r'\d+', season_tag)
                                s_num = int(s_num_m.group(0)) if s_num_m else (season if season is not None else 1)

                                if season is not None and s_num != season:
                                    continue

                                if ep_m and not (is_range or is_batch_kw):
                                    e_val = int(ep_m.group(2) or ep_m.group(3) or ep_m.group(4))
                                    if episode is not None and e_val != episode:
                                        continue
                                    ep_tag = f"S{s_num:02d}E{e_val:02d}"
                                    is_batch = False
                                elif is_batch_kw or is_range:
                                    if episode is not None:
                                        continue
                                    ep_tag = f"S{s_num:02d} Batch"
                                    is_batch = True
                                else:
                                    is_batch = bool(any(k in context_text.lower() for k in ["batch", "zip", "pack", "full season", "all episodes"]))
                                    if episode is not None and is_batch:
                                        continue
                                    ep_tag = f"S{s_num:02d} Batch" if is_batch and season_tag else (season_tag or "")

                                post_results.append({
                                    "platform": "UHDMovies",
                                    "title": f"{post_title} {season_tag}".strip(),
                                    "label": context_text[:90],
                                    "episode_tag": ep_tag,
                                    "resolution": detected_res,
                                    "codec": "HEVC 10-bit" if any(k in context_text.upper() for k in ["HEVC", "10BIT", "X265"]) else "x264",
                                    "size": detected_size or "Unknown",
                                    "audio": "English Atmos / Dual" if "Dual" in context_text or "Hindi" in context_text else "Original",
                                    "target_url": href,
                                    "is_batch": is_batch,
                                    "source_id": "uhd_res"
                                })

                            # Layer 2 JEV Fallback: If standard DOM parsing found 0 links
                            if not post_results and p_resp.text and episode is None:
                                post_results = await JevStreamResolver.extract_media_streams(
                                    p_resp.text,
                                    query=post_title or query,
                                    platform="UHDMovies"
                                )
                        else:
                            await self.domain_resolver.report_failure("uhdmovies", post_url)
                    except Exception:
                        await self.domain_resolver.report_failure("uhdmovies", post_url)
                    return post_results

                post_tasks = [fetch_post(art) for art in articles[:2]]
                post_lists = await asyncio.gather(*post_tasks, return_exceptions=True)
                for pl in post_lists:
                    if isinstance(pl, list):
                        results.extend(pl)

                # Outer Layer 2 Fallback: If 0 results discovered across articles
                if not results and resp.text and episode is None:
                    results = await JevStreamResolver.extract_media_streams(
                        resp.text,
                        query=query,
                        platform="UHDMovies"
                    )
            else:
                await self.domain_resolver.report_failure("uhdmovies", url)
        except Exception:
            await self.domain_resolver.report_failure("uhdmovies", url)

        return results

    async def search_4khdhub(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        """Fast search & discrete episode catalog extraction on 4KHDHub (< 450ms)."""
        clean = query.strip()
        encoded_query = urllib.parse.quote_plus(clean)
        url = f"https://4khdhub.one/?s={encoded_query}"
        results = []

        try:
            resp = await client.get(url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=4.0)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")

                # Extract candidate post cards
                candidate_posts = []
                cards = soup.find_all("a", href=True)
                seen_hrefs = set()

                for a in cards:
                    href = a.get("href", "")
                    if not href or href in seen_hrefs or href == "#" or any(k in href for k in ["/category/", "/tag/", "/genre/", "/author/"]):
                        continue

                    is_card = bool("movie-card" in a.get("class", []) or re.search(r'/[a-zA-Z0-9_-]+(?:series|movie|\d+)[^/]*/?$', href))
                    if not is_card:
                        continue

                    t_el = a.find(class_=re.compile(r'title|heading'))
                    post_title = t_el.get_text(strip=True) if t_el else (a.get("aria-label", "") or a.get_text(" ", strip=True))
                    post_title = re.sub(r'\s+details$', '', post_title, flags=re.I).strip()
                    if not post_title or not is_strict_match(query, post_title):
                        continue

                    seen_hrefs.add(href)
                    meta_el = a.find(class_=re.compile(r'meta|desc'))
                    meta_text = meta_el.get_text(" ", strip=True) if meta_el else ""
                    full_text = f"{post_title} {meta_text}"

                    score = 0
                    if season is not None:
                        # Check season range (e.g. S01-S03)
                        sm_range = re.search(r'S(\d{1,2})\s*-\s*S?(\d{1,2})', full_text, re.I)
                        if sm_range:
                            s_start, s_end = int(sm_range.group(1)), int(sm_range.group(2))
                            if s_start <= season <= s_end:
                                score += 50
                        elif re.search(rf'\b(?:Season\s*{season}|S0*{season})\b', full_text, re.I):
                            score += 50
                        elif any(k in full_text.lower() for k in ["all seasons", "complete", "series"]):
                            score += 20
                        else:
                            conflicts = re.findall(r'\b(?:Season\s*(\d+)|S(\d{1,2}))\b', full_text, re.I)
                            if conflicts and all(int(c[0] or c[1]) != season for c in conflicts):
                                score -= 30
                    candidate_posts.append((score, post_title, href))

                candidate_posts.sort(key=lambda x: x[0], reverse=True)
                selected_posts = [p for p in candidate_posts if p[0] >= 0][:2] or candidate_posts[:1]

                async def fetch_4khd_post(post_title, post_path):
                    post_url = post_path if post_path.startswith("http") else f"https://4khdhub.one{post_path}"
                    post_results = []
                    try:
                        p_resp = await client.get(post_url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=4.0)
                        if p_resp.status_code == 200:
                            p_soup = BeautifulSoup(p_resp.text, "lxml")

                            # 1. Primary: Parse discrete episode download items
                            ep_items = p_soup.find_all("div", class_="episode-download-item")
                            seen_links = set()

                            for ep_div in ep_items:
                                f_title_el = ep_div.find(class_=re.compile(r'file-title|title'))
                                f_title = f_title_el.get_text(strip=True) if f_title_el else ""
                                info_el = ep_div.find(class_=re.compile(r'file-info|info'))
                                info_text = info_el.get_text(" ", strip=True) if info_el else ""
                                full_ctx = f"{f_title} {info_text}"

                                # Detect season and episode
                                s_num = None
                                e_num = None
                                se_m = re.search(r'\bS(\d{1,2})\s*[-._ ]?E(\d{1,3})\b', full_ctx, re.I)
                                if se_m:
                                    s_num = int(se_m.group(1))
                                    e_num = int(se_m.group(2))
                                else:
                                    s_m = re.search(r'\b(?:Season|S)\s*0*(\d{1,2})\b', full_ctx, re.I)
                                    if not s_m:
                                        s_item = ep_div.find_parent(class_=re.compile(r'season-item|episode-item'))
                                        s_num_el = s_item.find(class_="episode-number") if s_item else None
                                        if s_num_el:
                                            s_m = re.search(r'\bS?(\d{1,2})\b', s_num_el.get_text(strip=True), re.I)
                                    s_num = int(s_m.group(1)) if s_m else (season if season is not None else 1)

                                    ep_m = re.search(r'\b(?:Episode|Ep|E)[-\s]*0*(\d{1,3})\b', full_ctx, re.I)
                                    e_num = int(ep_m.group(1)) if ep_m else None

                                if season is not None and s_num != season:
                                    continue
                                if episode is not None and (e_num is None or e_num != episode):
                                    continue

                                sz_el = ep_div.find(class_=re.compile(r'badge-size|size'))
                                sz = sz_el.get_text(strip=True) if sz_el else ""
                                if not sz:
                                    sm_sz = re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB))\]?', full_ctx, re.I)
                                    sz = sm_sz.group(1).upper() if sm_sz else "Unknown"

                                res_m = re.search(r'(2160p|4K|1080p|720p|480p)', full_ctx, re.I)
                                detected_res = res_m.group(1).upper() if res_m else "1080P"
                                if detected_res == "4K":
                                    detected_res = "2160P"

                                codec = "HEVC 10-bit" if any(k in full_ctx.upper() for k in ["HEVC", "H.265", "H265", "10BIT", "X265"]) else "x264"
                                audio = "Dual Audio" if any(k in full_ctx.lower() for k in ["hindi", "dual"]) else "English"

                                d_links = ep_div.find_all("a", href=re.compile(r'hubcloud|hubdrive|greenmotors'))
                                for dl in d_links:
                                    href = dl["href"]
                                    if href in seen_links:
                                        continue
                                    seen_links.add(href)
                                    dl_label = dl.get_text(strip=True) or "Download"
                                    ep_tag = f"S{s_num:02d}E{e_num:02d}" if e_num is not None else f"S{s_num:02d}"
                                    post_results.append({
                                        "platform": "4KHDHub",
                                        "title": f"{post_title} Season {s_num}".strip(),
                                        "label": f"{ep_tag} [{detected_res}] {sz} - {dl_label}".strip(" -"),
                                        "episode_tag": ep_tag,
                                        "resolution": detected_res,
                                        "codec": codec,
                                        "size": sz,
                                        "audio": audio,
                                        "target_url": href,
                                        "is_batch": False,
                                        "source_id": "4khd_ep"
                                    })

                            # 2. Secondary fallback: Generic HubCloud / GreenMotors links if discrete items missing
                            if not post_results:
                                all_dls = p_soup.find_all("a", href=re.compile(r'hubcloud|hubdrive|greenmotors'))
                                for dl in all_dls:
                                    href = dl["href"]
                                    if href in seen_links:
                                        continue
                                    seen_links.add(href)
                                    label = dl.get_text(strip=True) or dl.find_parent().get_text(strip=True)
                                    prev_h = dl.find_previous(["h2", "h3", "h4", "h5", "p", "div"])
                                    ctx = prev_h.get_text(" ", strip=True) if prev_h else ""
                                    combo = f"{label} {ctx}"

                                    s_num = None
                                    e_num = None
                                    se_m = re.search(r'\bS(\d{1,2})\s*[-._ ]?E(\d{1,3})\b', combo, re.I)
                                    if se_m:
                                        s_num = int(se_m.group(1))
                                        e_num = int(se_m.group(2))
                                    else:
                                        s_m = re.search(r'\b(?:Season|S)\s*0*(\d{1,2})\b', combo, re.I)
                                        s_num = int(s_m.group(1)) if s_m else (season if season is not None else 1)
                                        ep_m = re.search(r'\b(?:Episode|Ep|E)[-\s]*0*(\d{1,3})\b', combo, re.I)
                                        e_num = int(ep_m.group(1)) if ep_m else None

                                    is_batch_kw = any(k in combo.lower() for k in ["batch", "zip", "pack", "full season", "all episodes"])
                                    if episode is not None and (e_num is None or e_num != episode or is_batch_kw):
                                        continue
                                    if season is not None and s_num != season:
                                        continue

                                    ep_tag = f"S{s_num:02d}E{e_num:02d}" if e_num is not None else (f"S{s_num:02d} Batch" if is_batch_kw else "")
                                    res_m = re.search(r'(2160p|4K|1080p|720p|480p)', combo, re.I)
                                    detected_res = res_m.group(1).upper() if res_m else "1080P"
                                    if detected_res == "4K":
                                        detected_res = "2160P"
                                    sz_m = re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB))\]?', combo, re.I)
                                    sz = sz_m.group(1).upper() if sz_m else "Unknown"

                                    post_results.append({
                                        "platform": "4KHDHub",
                                        "title": f"{post_title} Season {s_num}".strip(),
                                        "label": f"{ep_tag or post_title} [{detected_res}] - {label[:50]}".strip(),
                                        "episode_tag": ep_tag,
                                        "resolution": detected_res,
                                        "codec": "HEVC" if "HEVC" in combo.upper() else "x264",
                                        "size": sz,
                                        "audio": "Dual Audio" if "hindi" in combo.lower() or "dual" in combo.lower() else "Original",
                                        "target_url": href,
                                        "is_batch": bool(is_batch_kw),
                                        "source_id": "4khd_res"
                                    })
                    except Exception:
                        pass
                    return post_results

                fetch_tasks = [fetch_4khd_post(title, path) for _, title, path in selected_posts]
                task_lists = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                for tl in task_lists:
                    if isinstance(tl, list):
                        results.extend(tl)
        except Exception:
            pass

        return results

    async def search_vegamovies(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        """Fast search & catalog parsing on VegaMovies via ts-search.php (< 200ms)."""
        clean = query.strip()
        vega_mirror = await get_active_mirror("vegamovies")
        url = f"{vega_mirror}/ts-search.php?q={clean}"
        results = []

        try:
            resp = await client.get(url, headers=DEFAULT_HEADERS, timeout=2.0)
            if resp.status_code == 200:
                await report_mirror_success("vegamovies")
                data = resp.json()
                hits = data.get("hits", [])

                if season is not None:
                    def score_vega_hit(h):
                        doc = h.get("document", {})
                        t = (doc.get("post_title", "") + " " + doc.get("permalink", "")).lower()
                        score = 0
                        s_patterns = [f"season {season}", f"season {season:02d}", f"s{season:02d}", f"s{season}"]
                        if any(re.search(rf'\b{re.escape(sp)}\b', t) for sp in s_patterns):
                            score += 100
                        elif "all seasons" in t or "all episodes" in t or "complete" in t:
                            score += 50
                        else:
                            conflicts = re.findall(r'\b(?:season\s*(\d+)|s(\d{1,2}))\b', t)
                            for c in conflicts:
                                c_num = int(c[0] or c[1])
                                if c_num != season:
                                    score -= 100
                                    break
                        return score

                    hits.sort(key=score_vega_hit, reverse=True)
                    target_hits = [h for h in hits if score_vega_hit(h) >= 0]
                    hits = target_hits if target_hits else hits

                async def fetch_vega_post(h):
                    doc = h.get("document", {})
                    post_title = doc.get("post_title", "")
                    permalink = doc.get("permalink", "")
                    if not (post_title and permalink) or not is_strict_match(query, post_title):
                        return []
                    post_results = []
                    if permalink:
                        post_url = f"{vega_mirror}{permalink}" if permalink.startswith("/") else permalink
                        try:
                            p_resp = await client.get(post_url, headers=DEFAULT_HEADERS, timeout=2.5)
                            if p_resp.status_code == 200:
                                await report_mirror_success("vegamovies")
                                p_soup = BeautifulSoup(p_resp.text, "lxml")
                                buttons = p_soup.find_all("a", href=re.compile(r'nexdrive|vcloud|fast-dl|v-cloud|hubcloud|greenmotors'))
                                for btn in buttons:
                                    label = btn.get_text(strip=True) or btn.find_parent().get_text(strip=True)
                                    href = btn["href"]

                                    # Look up nearest preceding header / block for accurate size & resolution
                                    context_text = label
                                    season_tag = ""
                                    for prev in btn.find_all_previous(["h2", "h3", "h4", "h5", "p", "strong", "span"])[:6]:
                                        pt = prev.get_text(" ", strip=True)
                                        if not season_tag:
                                            sm = re.search(r'\b(Season\s*\d+|S\d{1,2})\b', pt, re.I)
                                            if sm and len(pt) < 60:
                                                season_tag = sm.group(1).title()
                                        if any(k in pt.lower() for k in ["2160p", "4k", "1080p", "720p", "480p", "gb", "mb"]):
                                            context_text = f"{pt} {label}"

                                    if not season_tag:
                                        sm_pt = re.search(r'\b(Season\s*\d+|S\d{1,2})\b', post_title, re.I)
                                        if sm_pt:
                                            season_tag = sm_pt.group(1).title()

                                    s_num_m = re.search(r'\d+', season_tag)
                                    s_num = int(s_num_m.group(0)) if s_num_m else (season if season is not None else 1)

                                    if season is not None and s_num != season:
                                        continue

                                    if season_tag and season_tag.lower() not in context_text.lower():
                                        context_text = f"{season_tag} {context_text}"

                                    res_m = re.search(r'(2160p|4K|1080p|720p|480p)', context_text, re.I)
                                    size_m = re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB)(?:\s*/\s*E)?)\]?', context_text, re.I)
                                    detected_res = res_m.group(1).upper() if res_m else ("4K/UHD" if "2160" in post_title else "1080P")
                                    if detected_res == "4K":
                                        detected_res = "2160P"
                                    detected_size = size_m.group(1).upper().strip("[] ") if size_m else "Unknown"

                                    # Check button label directly for per-episode size (e.g. [570MB/E])
                                    sm_lbl = re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB)(?:\s*/\s*E)?)\]?', label, re.I)
                                    if sm_lbl:
                                        detected_size = sm_lbl.group(1).upper().strip("[] ")

                                    is_range = bool(re.search(r'(?:episodes?|ep|e)\s*\d+\s*-\s*\d+', label, re.I))
                                    is_batch_kw = any(k in label.lower() for k in ["batch", "zip", "pack", "full season", "all episodes", "complete"]) or bool(re.search(r'\b(batch|zip|pack|full\s*season|all\s*episodes|complete\s*(?:series|season|batch|pack)|s\d+\s*-\s*s\d+|season\s*\d+\s*zip)\b', label, re.I))
                                    is_batch = is_batch_kw or is_range

                                    # If intermediate episode links hub, recursively parse individual episodes
                                    hub_parsed = False
                                    if ("episode" in label.lower() or "single" in label.lower()) and not is_batch:
                                        try:
                                            hub_resp = await client.get(href, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=4.0)
                                            if hub_resp.status_code == 200:
                                                hub_soup = BeautifulSoup(hub_resp.text, "lxml")
                                                v_links = [a for a in hub_soup.find_all("a", href=True) if any(k in a["href"] for k in ["vcloud", "fast-dl", "pixeldrain", "drive", "hubcloud", "greenmotors"])]
                                                for idx, v_a in enumerate(v_links, 1):
                                                    v_txt = v_a.get_text(" ", strip=True)
                                                    ep_num_match = re.search(r'\b(?:episode|ep|e)\s*0*(\d{1,3})\b', v_txt, re.I)
                                                    ep_idx = int(ep_num_match.group(1)) if ep_num_match else idx
                                                    if episode is not None and ep_idx != episode:
                                                        continue
                                                    ep_label = f"{season_tag or f'Season {s_num}'} Episode {ep_idx:02d} [{detected_res}]"
                                                    post_results.append({
                                                        "platform": "VegaMovies",
                                                        "title": f"{post_title} {season_tag or f'Season {s_num}'}".strip(),
                                                        "label": ep_label,
                                                        "episode_tag": f"S{s_num:02d}E{ep_idx:02d}",
                                                        "resolution": detected_res,
                                                        "codec": "HEVC 10-bit" if "HEVC" in context_text or "10BIT" in context_text.upper() else "x264",
                                                        "size": detected_size,
                                                        "audio": "Dual Audio" if "Dual" in context_text or "Hindi" in context_text else "Original",
                                                        "target_url": v_a["href"],
                                                        "is_batch": False,
                                                        "source_id": "veg_ep"
                                                    })
                                                if v_links:
                                                    hub_parsed = True
                                        except Exception:
                                            pass

                                    if not hub_parsed:
                                        ep_m = re.search(r'\b(Episode\s*0*(\d{1,3})|Ep\s*0*(\d{1,3})|E0*(\d{1,3}))\b', label, re.I)
                                        if ep_m and not is_batch:
                                            e_val = int(ep_m.group(2) or ep_m.group(3) or ep_m.group(4))
                                            if episode is not None and e_val != episode:
                                                continue
                                            ep_tag = f"S{s_num:02d}E{e_val:02d}"
                                            is_batch = False
                                        elif is_batch:
                                            if episode is not None:
                                                continue
                                            ep_tag = f"S{s_num:02d} Batch"
                                        else:
                                            is_batch = bool(any(k in context_text.lower() for k in ["batch", "zip", "pack", "full season", "all episodes"]))
                                            if episode is not None and is_batch:
                                                continue
                                            ep_tag = f"S{s_num:02d} Batch" if is_batch and season_tag else (season_tag or "")

                                        post_results.append({
                                            "platform": "VegaMovies",
                                            "title": f"{post_title} {season_tag or f'Season {s_num}'}".strip(),
                                            "label": context_text[:90],
                                            "episode_tag": ep_tag,
                                            "resolution": detected_res,
                                            "codec": "HEVC 10-bit" if "HEVC" in context_text or "10BIT" in context_text.upper() else "x264",
                                            "size": detected_size,
                                            "audio": "Dual Audio" if "Dual" in context_text or "Hindi" in context_text else "Original",
                                            "target_url": href,
                                            "is_batch": is_batch,
                                            "source_id": "veg_res"
                                        })
                            else:
                                await report_mirror_failure("vegamovies", post_url)
                        except Exception:
                            await report_mirror_failure("vegamovies", post_url)
                    return post_results

                vega_tasks = [fetch_vega_post(h) for h in hits[:4]]
                vega_lists = await asyncio.gather(*vega_tasks, return_exceptions=True)
                for vl in vega_lists:
                    if isinstance(vl, list):
                        results.extend(vl)
            else:
                await report_mirror_failure("vegamovies", url)
        except Exception:
            await report_mirror_failure("vegamovies", url)

        return results

    async def search_hdhub4u(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        """Fast search & catalog parsing on HDHub4u via Typesense API & Dynamic Mirror (< 200ms)."""
        clean = query.strip()
        api_url = "https://search.pingora.fyi/collections/post/documents/search"
        params = {
            "q": clean,
            "query_by": "post_title,category,stars,director,imdb_id",
            "query_by_weights": "4,2,2,2,4",
            "sort_by": "sort_by_date:desc",
            "limit": 10,
            "highlight_fields": "none",
            "use_cache": "true",
            "page": 1
        }
        live_mirror = await get_active_mirror("hdhub4u")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": f"{live_mirror}/"
        }
        results = []

        try:
            resp = await client.get(api_url, params=params, headers=headers, timeout=2.5)
            if resp.status_code == 200:
                await report_mirror_success("hdhub4u")
                data = resp.json()
                hits = data.get("hits", [])

                if season is not None:
                    def score_hdh_hit(h):
                        doc = h.get("document", {})
                        t = (doc.get("post_title", "") + " " + doc.get("permalink", "")).lower()
                        score = 0
                        s_patterns = [f"season {season}", f"season {season:02d}", f"s{season:02d}", f"s{season}"]
                        if any(re.search(rf'\b{re.escape(sp)}\b', t) for sp in s_patterns):
                            score += 100
                        elif "all episodes" in t or "all seasons" in t or "complete" in t:
                            score += 50
                        else:
                            conflicts = re.findall(r'\b(?:season\s*(\d+)|s(\d{1,2}))\b', t)
                            for c in conflicts:
                                c_num = int(c[0] or c[1])
                                if c_num != season:
                                    score -= 100
                                    break
                        return score

                    hits.sort(key=score_hdh_hit, reverse=True)
                    target_hits = [h for h in hits if score_hdh_hit(h) >= 0]
                    hits = target_hits if target_hits else hits

                async def fetch_hdh_post(h):
                    doc = h.get("document", {})
                    post_title = doc.get("post_title", "")
                    permalink = doc.get("permalink", "")
                    if not (post_title and permalink) or not is_strict_match(query, post_title):
                        return []
                    post_results = []
                    if permalink:
                        # Rewrite dead domain in Typesense document to live active mirror
                        clean_path = re.sub(r'^https?://[^/]+', '', permalink)
                        live_post_url = f"{live_mirror}{clean_path}"
                        try:
                            p_resp = await client.get(live_post_url, headers=headers, timeout=2.5)
                            if p_resp.status_code == 200:
                                await report_mirror_success("hdhub4u")
                                p_soup = BeautifulSoup(p_resp.text, "lxml")

                                sm = re.search(r'\b(Season\s*(\d+)|S(\d{1,2}))\b', post_title, re.I)
                                s_num = int(sm.group(2) or sm.group(3)) if sm else (season if season is not None else 1)
                                if season is not None and s_num != season:
                                    return []

                                seen_targets = set()

                                # Dual-Mode Discrete Episode Extraction
                                # Mode A: div.kp-header / div.kp-hc episode rows (EPiSODE 1 -> greenmotors/hubcloud)
                                kp_elements = p_soup.find_all(class_=re.compile(r'kp-header|kp-hc'))
                                for kp in kp_elements:
                                    for dl in kp.find_all("a", href=re.compile(r'hubcloud|hubdrive|drive|gamerxyt|hubcdn|greenmotors')):
                                        href = dl["href"]
                                        if href in seen_targets:
                                            continue
                                        txt = dl.get_text(" ", strip=True)
                                        p_txt = dl.parent.get_text(" ", strip=True) if dl.parent else ""
                                        combo = f"{txt} {p_txt}"
                                        ep_m = re.search(r'\b(?:EPiSODE|Episode|Ep|E)\s*0*(\d{1,3})\b', combo, re.I)
                                        if ep_m:
                                            e_val = int(ep_m.group(1))
                                            if episode is not None and e_val != episode:
                                                continue
                                            seen_targets.add(href)
                                            prev_h = kp.find_previous(["h2", "h3", "h4", "h5", "p", "strong", "span"])
                                            ctx_str = prev_h.get_text(" ", strip=True) if prev_h else ""
                                            res_m = re.search(r'(2160p|4K|1080p|720p|480p)', f"{combo} {ctx_str}", re.I)
                                            detected_res = res_m.group(1).upper() if res_m else ("4K/UHD" if "2160" in post_title else "1080P")
                                            if detected_res == "4K":
                                                detected_res = "2160P"
                                            size_m = re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB)(?:\s*/\s*E)?)\]?', f"{combo} {ctx_str}", re.I)
                                            detected_size = size_m.group(1).upper().strip("[] ") if size_m else "Unknown"
                                            lbl = dl.get_text(strip=True) or f"EPiSODE {e_val}"

                                            post_results.append({
                                                "platform": "HDHub4u",
                                                "title": f"{post_title} Season {s_num}".strip(),
                                                "label": f"S{s_num:02d}E{e_val:02d} [{detected_res}] - {lbl}",
                                                "episode_tag": f"S{s_num:02d}E{e_val:02d}",
                                                "resolution": detected_res,
                                                "codec": "HEVC" if any(k in f"{combo} {ctx_str}".upper() for k in ["HEVC", "10BIT"]) else "x264",
                                                "size": detected_size,
                                                "audio": "Dual Audio (Hindi + Eng)" if "Dual" in post_title or "Hindi" in post_title else "Original",
                                                "target_url": href,
                                                "is_batch": False,
                                                "source_id": "hdh_mode_a"
                                            })

                                # Mode B: Heading sequences <h4>EPiSODE {e}</h4> followed by sibling quality links (Drive, Instant, Watch)
                                for h in p_soup.find_all(["h2", "h3", "h4", "h5", "p"]):
                                    h_text = h.get_text(" ", strip=True)
                                    ep_m = re.search(r'\b(?:EPiSODE|Episode|Ep|E)\s*0*(\d{1,3})\b', h_text, re.I)
                                    if not ep_m:
                                        continue
                                    if re.search(r'\b(?:pack|batch|zip|\d+\s*-\s*\d+)\b', h_text, re.I):
                                        continue
                                    e_val = int(ep_m.group(1))
                                    if episode is not None and e_val != episode:
                                        continue

                                    target_links = []
                                    curr = h.next_sibling
                                    while curr:
                                        if hasattr(curr, "name") and curr.name:
                                            if curr.name in ["h1", "h2", "hr"]:
                                                break
                                            c_txt = curr.get_text(" ", strip=True)
                                            # If this is the next episode header, stop
                                            if re.search(r'\b(?:EPiSODE|Episode|Ep|E)\s*0*(\d{1,3})\b', c_txt, re.I) and not any(r in c_txt.lower() for r in ["720p", "1080p", "480p", "2160p", "drive", "instant", "watch"]):
                                                break
                                            for a in curr.find_all("a", href=re.compile(r'hubcloud|hubdrive|drive|gamerxyt|hubcdn|greenmotors')):
                                                target_links.append((a, c_txt))
                                            if curr.name == "a" and re.search(r'hubcloud|hubdrive|drive|gamerxyt|hubcdn|greenmotors', curr.get("href", "")):
                                                target_links.append((curr, c_txt))
                                        curr = curr.next_sibling

                                    if not target_links:
                                        continue

                                    prev_h = h.find_previous(["h2", "h3"])
                                    ctx_str = prev_h.get_text(" ", strip=True) if prev_h else ""

                                    for a, sibling_ctx in target_links:
                                        href = a["href"]
                                        if href in seen_targets:
                                            continue
                                        seen_targets.add(href)
                                        lbl = a.get_text(strip=True) or "Watch"
                                        res_m = re.search(r'(2160p|4K|1080p|720p|480p)', f"{sibling_ctx} {h_text} {ctx_str}", re.I)
                                        detected_res = res_m.group(1).upper() if res_m else ("4K/UHD" if "2160" in post_title else "1080P")
                                        if detected_res == "4K":
                                            detected_res = "2160P"
                                        size_m = re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB)(?:\s*/\s*E)?)\]?', f"{sibling_ctx} {h_text} {ctx_str}", re.I)
                                        detected_size = size_m.group(1).upper().strip("[] ") if size_m else "Unknown"

                                        post_results.append({
                                            "platform": "HDHub4u",
                                            "title": f"{post_title} Season {s_num}".strip(),
                                            "label": f"S{s_num:02d}E{e_val:02d} [{detected_res}] - {lbl}",
                                            "episode_tag": f"S{s_num:02d}E{e_val:02d}",
                                            "resolution": detected_res,
                                            "codec": "HEVC" if any(k in f"{sibling_ctx} {h_text} {ctx_str}".upper() for k in ["HEVC", "10BIT"]) else "x264",
                                            "size": detected_size,
                                            "audio": "Dual Audio (Hindi + Eng)" if "Dual" in post_title or "Hindi" in post_title else "Original",
                                            "target_url": href,
                                            "is_batch": False,
                                            "source_id": "hdh_mode_b"
                                        })

                                # Fallback: Generic link parsing if neither Mode A nor Mode B found discrete episodes
                                if not post_results:
                                    dls = p_soup.find_all("a", href=re.compile(r'hubcloud|hubdrive|drive|gamerxyt|hubcdn|greenmotors'))
                                    season_tag = f"Season {s_num}"
                                    for dl in dls:
                                        href = dl["href"]
                                        if href in seen_targets:
                                            continue
                                        seen_targets.add(href)
                                        label = dl.get_text(strip=True) or dl.find_parent().get_text(strip=True)
                                        prev_h = dl.find_previous(["h2", "h3", "h4", "h5", "p", "strong", "span"])
                                        context_str = prev_h.get_text(" ", strip=True) if prev_h else ""

                                        is_range = bool(re.search(r'(?:episodes?|ep|e)\s*\d+\s*-\s*\d+', f"{label} {context_str}", re.I))
                                        is_batch_kw = any(k in f"{label} {context_str}".lower() for k in ["batch", "zip", "pack", "full season", "all episodes", "complete"]) or bool(re.search(r'\b(batch|zip|pack|full\s*season|all\s*episodes|complete\s*(?:series|season|batch|pack)|s\d+\s*-\s*s\d+|season\s*\d+\s*zip)\b', f"{label} {context_str}", re.I))

                                        ep_m = re.search(r'\b(Episode\s*0*(\d{1,3})|Ep\s*0*(\d{1,3})|E0*(\d{1,3}))\b', label, re.I)
                                        if not ep_m:
                                            ep_m = re.search(r'\b(Episode\s*0*(\d{1,3})|Ep\s*0*(\d{1,3})|E0*(\d{1,3}))\b', context_str, re.I)

                                        if ep_m and not (is_range or is_batch_kw):
                                            e_val = int(ep_m.group(2) or ep_m.group(3) or ep_m.group(4))
                                            if episode is not None and e_val != episode:
                                                continue
                                            ep_tag = f"S{s_num:02d}E{e_val:02d}"
                                            is_batch = False
                                        elif is_batch_kw or is_range:
                                            if episode is not None:
                                                continue
                                            ep_tag = f"S{s_num:02d} Batch"
                                            is_batch = True
                                        else:
                                            if episode is not None:
                                                continue
                                            is_batch = False
                                            ep_tag = season_tag or ""

                                        res_m = re.search(r'(2160p|4K|1080p|720p|480p)', f"{label} {context_str}", re.I)
                                        size_m = re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB)(?:\s*/\s*E)?)\]?', f"{label} {context_str}", re.I)
                                        detected_res = res_m.group(1).upper() if res_m else ("4K/UHD" if "2160" in post_title else "1080P")
                                        if detected_res == "4K":
                                            detected_res = "2160P"

                                        post_results.append({
                                            "platform": "HDHub4u",
                                            "title": f"{post_title} {season_tag}".strip(),
                                            "label": f"{context_str[:40]} - {label}" if context_str and len(label) < 15 else label[:70],
                                            "episode_tag": ep_tag,
                                            "resolution": detected_res,
                                            "codec": "HEVC" if "HEVC" in f"{label} {context_str}" or "10Bit" in f"{label} {context_str}" else "x264",
                                            "size": size_m.group(1).upper().strip("[] ") if size_m else "Unknown",
                                            "audio": "Dual Audio (Hindi + Eng)" if "Dual" in post_title or "Hindi" in post_title else "Original",
                                            "target_url": href,
                                            "is_batch": is_batch,
                                            "source_id": "hdh_res"
                                        })

                                # Layer 2 JEV Fallback: If heuristic parsing yielded 0 links
                                if not post_results and p_resp.text and episode is None:
                                    post_results = await JevStreamResolver.extract_media_streams(
                                        p_resp.text,
                                        query=post_title or query,
                                        platform="HDHub4u"
                                    )
                            else:
                                await report_mirror_failure("hdhub4u", live_post_url)
                        except Exception:
                            await report_mirror_failure("hdhub4u", live_post_url)
                    return post_results

                hdh_tasks = [fetch_hdh_post(h) for h in hits[:4]]
                hdh_lists = await asyncio.gather(*hdh_tasks, return_exceptions=True)
                for hl in hdh_lists:
                    if isinstance(hl, list):
                        results.extend(hl)

                # Outer Layer 2 Fallback: If 0 results discovered across all hits
                if not results and resp.text and episode is None:
                    results = await JevStreamResolver.extract_media_streams(
                        resp.text,
                        query=query,
                        platform="HDHub4u"
                    )
            else:
                await report_mirror_failure("hdhub4u", api_url)
        except Exception:
            await report_mirror_failure("hdhub4u", api_url)

        return results

    async def search_olamovies(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        """Fast multi-strategy search & catalog parsing on OlaMovies (JSON API + SSR + JEV, < 450ms)."""
        clean = query.strip()
        ola_mirror = await get_active_mirror("olamovies")
        encoded_q = urllib.parse.quote_plus(clean)
        results = []

        # Query words with stopword exclusion for intelligent ranking
        STOPWORDS = {"the", "and", "all", "movie", "movies", "collection", "full", "series", "season", "part"}
        words = [w.lower() for w in re.split(r'[\s+._:-]+', clean) if len(w) >= 3 and w.lower() not in STOPWORDS]
        if not words:
            words = [w.lower() for w in re.split(r'[\s+._:-]+', clean) if len(w) >= 2]

        all_candidates = []

        # -------------------------------------------------------------
        # Strategy 1 (Primary): Direct JSON API Search (< 120ms)
        # -------------------------------------------------------------
        try:
            api_url = f"{ola_mirror}/api/search?q={encoded_q}"
            api_resp = await client.get(api_url, headers=DEFAULT_HEADERS, timeout=3.5)
            if api_resp.status_code == 200:
                await report_mirror_success("olamovies")
                data = api_resp.json()
                items = data.get("items", []) if isinstance(data, dict) else []
                for it in items:
                    href = it.get("url") or f"/tv-series/{it.get('slug')}" if it.get("category") == "English TV Series" else f"/movies/{it.get('slug')}"
                    slug = (it.get("slug") or href.split("/")[-1]).lower()
                    title = it.get("title") or slug.replace("-", " ").title()
                    is_tv_series = "/tv-series/" in href or "tv series" in str(it.get("category", "")).lower() or "series" in slug

                    score = 0
                    if any(k in slug for k in ["universe-collection", "all-movies-collection", "complete-collection", "imdb-top-250"]):
                        score -= 15

                    matched = sum(1 for w in words if w in slug or w in title.lower())
                    score += matched * 8

                    if words and (slug.startswith(words[0]) or title.lower().startswith(words[0])):
                        score += 5

                    if season is not None:
                        if is_tv_series:
                            score += 25  # Unified TV series page hosts all seasons
                        s_patterns = [f"season-{season}", f"season{season}", f"s{season:02d}", f"s{season}"]
                        if any(sp in slug or sp in title.lower() for sp in s_patterns):
                            score += 30
                        else:
                            conflicts = re.findall(r'\b(?:season\s*(\d+)|s(\d{1,2}))\b', f"{slug} {title}".lower())
                            for c in conflicts:
                                c_num = int(c[0] or c[1])
                                if c_num != season:
                                    score -= 30
                                    break

                    if matched > 0 and href not in [p["href"] for p in all_candidates]:
                        all_candidates.append({"score": score, "href": href, "title": title, "slug": slug})
        except Exception:
            pass

        # -------------------------------------------------------------
        # Strategy 2 (Fallback): HTML SSR Search
        # -------------------------------------------------------------
        if not all_candidates:
            try:
                url = f"{ola_mirror}/search?q={encoded_q}"
                resp = await client.get(url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=4.0)
                if resp.status_code == 200:
                    await report_mirror_success("olamovies")
                    soup = BeautifulSoup(resp.text, "lxml")

                    for a in soup.find_all("a", href=re.compile(r"/(?:movies|series|tv-series)/[a-zA-Z0-9_-]+$")):
                        href = a["href"]
                        slug = href.split("/")[-1].lower()
                        text = a.get_text(" ", strip=True)

                        if any(k in href for k in ["comment-", "category", "tag"]):
                            continue

                        score = 0
                        if any(k in slug for k in ["universe-collection", "all-movies-collection", "complete-collection", "imdb-top-250"]):
                            score -= 10

                        matched = sum(1 for w in words if w in slug or w in text.lower())
                        score += matched * 5

                        if words and slug.startswith(words[0]):
                            score += 3

                        if season is not None:
                            s_patterns = [f"season-{season}", f"season{season}", f"s{season:02d}", f"s{season}"]
                            if any(sp in slug or sp in text.lower() for sp in s_patterns):
                                score += 25
                            else:
                                conflicts = re.findall(r'\b(?:season\s*(\d+)|s(\d{1,2}))\b', f"{slug} {text}".lower())
                                for c in conflicts:
                                    c_num = int(c[0] or c[1])
                                    if c_num != season:
                                        score -= 25
                                        break

                        if matched > 0 and href not in [p["href"] for p in all_candidates]:
                            title = text.strip() or slug.replace("-", " ").title()
                            all_candidates.append({"score": score, "href": href, "title": title, "slug": slug})
            except Exception:
                pass

        if not all_candidates:
            return results

        all_candidates.sort(key=lambda x: x["score"], reverse=True)
        matching_posts = [p for p in all_candidates if p["score"] >= 0][:3] or all_candidates[:2]

        async def fetch_ola_post(p):
            post_url = f"{ola_mirror}{p['href']}" if p['href'].startswith("/") else p['href']
            post_results = []
            try:
                p_resp = await client.get(post_url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=4.0)
                if p_resp.status_code == 200:
                    await report_mirror_success("olamovies")
                    clean_html = p_resp.text.replace('\\"', '"').replace('\\\\', '\\')

                    # 1. Primary Architecture: Next.js self.__next_f Structured JSON (Sections / Sources / Blocks / Episodes / Packs)
                    try:
                        chunks = re.findall(r"self\.__next_f\.push\(\[1,\s*\"(.*?)\"\]\)", p_resp.text, re.DOTALL)
                        if chunks:
                            full_stream = "".join(c.encode("utf-8").decode("unicode_escape", errors="ignore") for c in chunks)
                            idx = full_stream.find("\"sections\":[")
                            if idx != -1:
                                start = idx + len("\"sections\":")
                                decoder = json.JSONDecoder()
                                sections, _ = decoder.raw_decode(full_stream[start:])

                                for s in sections:
                                    s_id = s.get("id", "")
                                    s_label = s.get("label", "")
                                    s_season = s.get("season")

                                    sec_num = None
                                    if s_season is not None:
                                        try:
                                            sec_num = int(s_season)
                                        except Exception:
                                            pass
                                    if sec_num is None and s_id:
                                        m = re.search(r"season-?(\d+)", s_id, re.I)
                                        if m:
                                            sec_num = int(m.group(1))
                                    if sec_num is None and s_label:
                                        m = re.search(r"season\s*(\d+)", s_label, re.I)
                                        if m:
                                            sec_num = int(m.group(1))

                                    # If season is requested and this section represents a different season, skip
                                    if season is not None and sec_num is not None and sec_num != season:
                                        continue

                                    curr_s = sec_num if sec_num is not None else (season if season is not None else 1)

                                    blocks_to_process = []
                                    for src in s.get("sources", []):
                                        for blk in src.get("blocks", []):
                                            blocks_to_process.append((src.get("label", ""), blk))
                                    for blk in s.get("blocks", []):
                                        blocks_to_process.append((s_label, blk))

                                    for src_lbl, blk in blocks_to_process:
                                        res = blk.get("resolution") or "1080P"
                                        group = blk.get("group") or ""
                                        heading = blk.get("heading") or ""
                                        specs = blk.get("specs", [])
                                        specs_str = " ".join(specs) if isinstance(specs, list) else str(specs)

                                        # A) Full Season Batch Packs (ZIP archives)
                                        packs = blk.get("packs", [])
                                        if packs and episode is None:
                                            for pk in packs:
                                                pk_url = pk.get("url")
                                                if not pk_url:
                                                    continue
                                                pk_lbl = pk.get("label") or "Season Zip"
                                                pk_fname = pk.get("filename") or heading or pk_lbl
                                                pk_meta = f"{pk_lbl} {pk_fname} {specs_str}"
                                                size_m = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', pk_meta, re.I)
                                                sz = size_m.group(1).upper() if size_m else "ZIP"
                                                post_results.append({
                                                    "platform": "OlaMovies",
                                                    "title": p["title"][:80],
                                                    "label": f"OlaMovies | [{res.upper()}] S{curr_s:02d} Batch - {group} ({sz})".strip(),
                                                    "episode_tag": f"S{curr_s:02d} Batch",
                                                    "resolution": res.upper(),
                                                    "codec": "HEVC 10-bit" if any(k in pk_meta.upper() for k in ["HEVC", "X265", "10BIT"]) else "x264",
                                                    "size": sz,
                                                    "audio": "Multi Audio" if any(k in pk_meta.lower() for k in ["multi", "hindi", "dual", "atmos", "ddp"]) else "Original",
                                                    "target_url": pk_url,
                                                    "is_batch": True,
                                                    "source_id": "ola_nextjs_pack"
                                                })

                                        # B) Discrete TV Episodes
                                        episodes = blk.get("episodes", [])
                                        for ep in episodes:
                                            ep_num = ep.get("episode")
                                            ep_url = ep.get("url")
                                            if not ep_url:
                                                continue
                                            if episode is not None and ep_num is not None and ep_num != episode:
                                                continue
                                            ep_fname = ep.get("filename") or ""
                                            ep_filesize = ep.get("filesize") or ""
                                            ep_lbl = ep.get("label") or f"Episode {ep_num}"
                                            ep_meta = f"{ep_lbl} {ep_fname} {specs_str} {heading}"
                                            actual_ep = ep_num if ep_num is not None else episode
                                            ep_tag = f"S{curr_s:02d}E{actual_ep:02d}" if actual_ep is not None else f"S{curr_s:02d}E01"
                                            res_m = re.search(r'(2160p|4K|1080p|720p|480p)', f"{res} {ep_meta}", re.I)
                                            clean_res = res_m.group(1).upper() if res_m else res.upper()
                                            size_m = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', ep_meta, re.I)
                                            size_val = ep_filesize or (size_m.group(1).upper() if size_m else "Unknown")
                                            post_results.append({
                                                "platform": "OlaMovies",
                                                "title": p["title"][:80],
                                                "label": f"OlaMovies | [{clean_res}] {ep_tag} {group} ({size_val})".strip(),
                                                "episode_tag": ep_tag,
                                                "resolution": clean_res,
                                                "codec": "HEVC 10-bit" if any(k in ep_meta.upper() for k in ["HEVC", "X265", "10BIT"]) else "x264",
                                                "size": size_val.upper(),
                                                "audio": "Multi Audio" if any(k in ep_meta.lower() for k in ["multi", "hindi", "dual", "atmos", "ddp", "5.1"]) else "Original",
                                                "target_url": ep_url,
                                                "is_batch": False,
                                                "source_id": "ola_nextjs_ep"
                                            })

                                        # C) Movies (Files Array)
                                        files = blk.get("files", [])
                                        for f in files:
                                            f_url = f.get("url")
                                            if not f_url:
                                                continue
                                            f_fname = f.get("filename") or ""
                                            f_lbl = f.get("label") or ""
                                            f_meta = f"{f_lbl} {f_fname} {specs_str} {res}"
                                            res_m = re.search(r'(2160p|4K|1080p|720p|480p)', f_meta, re.I)
                                            clean_res = res_m.group(1).upper() if res_m else res.upper()
                                            size_m = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', f_meta, re.I)
                                            sz = size_m.group(1).upper() if size_m else "Unknown"
                                            post_results.append({
                                                "platform": "OlaMovies",
                                                "title": p["title"][:80],
                                                "label": f"OlaMovies | [{clean_res}] {f_lbl or clean_res} - {group} ({sz})".strip(),
                                                "episode_tag": "",
                                                "resolution": clean_res,
                                                "codec": "HEVC 10-bit" if any(k in f_meta.upper() for k in ["HEVC", "X265", "10BIT"]) else "x264",
                                                "size": sz,
                                                "audio": "Multi Audio" if any(k in f_meta.lower() for k in ["multi", "hindi", "dual", "atmos", "ddp", "5.1"]) else "Original",
                                                "target_url": f_url,
                                                "is_batch": False,
                                                "source_id": "ola_nextjs_file"
                                            })
                    except Exception:
                        pass

                    # 2. Secondary Fallback: Regex Parsing of Next.js chunks if structured sections was empty
                    if not post_results:
                        file_matches = list(re.finditer(
                            r'\{"id":"itm_[^"]+","btnId":"[^"]+","url":"([^"]+)","label":"([^"]+)",(?:.*?"filename":"([^"]+)")?',
                            clean_html
                        ))

                        for fm in file_matches:
                            dl_url = fm.group(1)
                            if not any(k in dl_url for k in ["links.", "ol-am", "olamovies", "drive."]):
                                continue
                            lbl = fm.group(2)
                            fname = fm.group(3) or lbl
                            meta_str = f"{lbl} {fname}"

                            # Extract season and episode numbers from lbl / fname
                            s_val = None
                            e_val = None
                            se_m = re.search(r'\bS(\d{1,2})\s*[-._ ]?E(\d{1,3})\b', meta_str, re.I)
                            if se_m:
                                s_val = int(se_m.group(1))
                                e_val = int(se_m.group(2))
                            else:
                                s_m = re.search(r'\b(?:Season|S)\s*0*(\d{1,2})\b', meta_str, re.I)
                                if s_m:
                                    s_val = int(s_m.group(1))
                                e_m = re.search(r'\b(?:Episode|Ep|E)\s*0*(\d{1,3})\b', meta_str, re.I)
                                if e_m:
                                    e_val = int(e_m.group(1))

                            if s_val is None:
                                s_val = season if season is not None else 1

                            is_range = bool(re.search(r'(?:episodes?|ep|e)\s*\d+\s*-\s*\d+', meta_str, re.I))
                            is_batch_kw = any(k in meta_str.lower() for k in ["batch", "zip", "pack", "full season", "all episodes", "complete"]) or bool(re.search(r'\b(?:batch|zip|pack|full\s*season|all\s*episodes|complete)\b', meta_str, re.I))

                            if e_val is not None and not (is_range or is_batch_kw):
                                if season is not None and s_val != season:
                                    continue
                                if episode is not None and e_val != episode:
                                    continue
                                ep_tag = f"S{s_val:02d}E{e_val:02d}"
                                is_batch = False
                            elif is_batch_kw or is_range:
                                if episode is not None:
                                    continue
                                ep_tag = f"S{s_val:02d} Batch"
                                is_batch = True
                            else:
                                is_batch = bool("season" in meta_str.lower() and e_val is None)
                                if episode is not None and is_batch:
                                    continue
                                ep_tag = f"S{s_val:02d} Batch" if is_batch else ""

                            res_m = re.search(r'(2160p|4K|1080p|720p|480p)', meta_str, re.I)
                            size_m = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', meta_str, re.I)

                            post_results.append({
                                "platform": "OlaMovies",
                                "title": p["title"][:80],
                                "label": f"{lbl} - {fname[:60]}",
                                "episode_tag": ep_tag,
                                "resolution": res_m.group(1).upper() if res_m else "1080P",
                                "codec": "HEVC 10-bit" if any(k in meta_str.upper() for k in ["HEVC", "X265", "10BIT"]) else "x264",
                                "size": size_m.group(1).upper() if size_m else "Unknown",
                                "audio": "Multi Audio" if any(k in meta_str.lower() for k in ["multi", "hindi", "dual", "tamil", "telugu"]) else "Original",
                                "target_url": dl_url,
                                "is_batch": is_batch,
                                "source_id": "ola_nextjs_legacy"
                            })

                    # 2. Fallback: Classic DOM anchors
                    if not post_results:
                        p_soup = BeautifulSoup(p_resp.text, "lxml")
                        for a_tag in p_soup.find_all("a", href=re.compile(r'https?://(?:links\.|drive\.|dl\.)(?:ol-am|olamovies)\.[^/]+/[^\s"\'<>]+')):
                            dl_url = a_tag["href"]
                            lbl = a_tag.get_text(strip=True) or a_tag.find_parent().get_text(strip=True)
                            meta_str = lbl

                            s_val = None
                            e_val = None
                            se_m = re.search(r'\bS(\d{1,2})\s*[-._ ]?E(\d{1,3})\b', meta_str, re.I)
                            if se_m:
                                s_val = int(se_m.group(1))
                                e_val = int(se_m.group(2))
                            else:
                                s_m = re.search(r'\b(?:Season|S)\s*0*(\d{1,2})\b', meta_str, re.I)
                                if s_m:
                                    s_val = int(s_m.group(1))
                                e_m = re.search(r'\b(?:Episode|Ep|E)\s*0*(\d{1,3})\b', meta_str, re.I)
                                if e_m:
                                    e_val = int(e_m.group(1))

                            if s_val is None:
                                s_val = season if season is not None else 1

                            is_range = bool(re.search(r'(?:episodes?|ep|e)\s*\d+\s*-\s*\d+', meta_str, re.I))
                            is_batch_kw = any(k in meta_str.lower() for k in ["batch", "zip", "pack", "full season", "all episodes", "complete"]) or bool(re.search(r'\b(?:batch|zip|pack|full\s*season|all\s*episodes|complete)\b', meta_str, re.I))

                            if e_val is not None and not (is_range or is_batch_kw):
                                if season is not None and s_val != season:
                                    continue
                                if episode is not None and e_val != episode:
                                    continue
                                ep_tag = f"S{s_val:02d}E{e_val:02d}"
                                is_batch = False
                            elif is_batch_kw or is_range:
                                if episode is not None:
                                    continue
                                ep_tag = f"S{s_val:02d} Batch"
                                is_batch = True
                            else:
                                is_batch = bool("season" in meta_str.lower() and e_val is None)
                                if episode is not None and is_batch:
                                    continue
                                ep_tag = f"S{s_val:02d} Batch" if is_batch else ""

                            res_m = re.search(r'(2160p|4K|1080p|720p|480p)', lbl, re.I)
                            size_m = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', lbl, re.I)

                            post_results.append({
                                "platform": "OlaMovies",
                                "title": p["title"][:80],
                                "label": lbl[:60],
                                "episode_tag": ep_tag,
                                "resolution": res_m.group(1).upper() if res_m else "1080P",
                                "codec": "HEVC 10-bit" if "hevc" in lbl.lower() or "x265" in lbl.lower() else "x264",
                                "size": size_m.group(1).upper() if size_m else "Unknown",
                                "audio": "Multi Audio" if "multi" in lbl.lower() or "hindi" in lbl.lower() else "Original",
                                "target_url": dl_url,
                                "is_batch": is_batch,
                                "source_id": "ola_dom"
                            })
                else:
                    await report_mirror_failure("olamovies", post_url)
            except Exception:
                await report_mirror_failure("olamovies", post_url)
            return post_results

        ola_tasks = [fetch_ola_post(p) for p in matching_posts[:2]]
        ola_lists = await asyncio.gather(*ola_tasks, return_exceptions=True)
        for ol in ola_lists:
            if isinstance(ol, list):
                results.extend(ol)

        return results

    async def search_desiremovies(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        """Fast search & catalog parsing on DesireMovies via WP REST API (< 400ms)."""
        clean = query.strip()
        encoded_query = urllib.parse.quote(clean)
        mirror = await self.domain_resolver.get_mirror("desiremovies")
        url = f"{mirror}/wp-json/wp/v2/posts?search={encoded_query}&_fields=id,title,link,content"
        results = []

        try:
            resp = await client.get(url, headers=DEFAULT_HEADERS, timeout=3.5, follow_redirects=True)
            if resp.status_code == 200:
                await self.domain_resolver.report_success("desiremovies")
                posts = resp.json()
                if not isinstance(posts, list):
                    posts = []

                for p in posts:
                    raw_title = p.get("title", {})
                    post_title = html.unescape(raw_title.get("rendered", "") if isinstance(raw_title, dict) else str(raw_title))
                    post_title = re.sub(r'<[^>]+>', '', post_title).strip()
                    if not post_title or not is_strict_match(query, post_title):
                        continue

                    content_raw = p.get("content", {})
                    content_html = content_raw.get("rendered", "") if isinstance(content_raw, dict) else str(content_raw)
                    if not content_html:
                        continue

                    soup = BeautifulSoup(content_html, "lxml")
                    # Extract Gyanigurus / HubCloud / GDFlix / PixelDrain / Drive links
                    target_links = soup.find_all("a", href=re.compile(r'gyanigurus|hubcloud|gdflix|pixeldrain|hubdrive|drive|fast-dl|vcloud', re.I))
                    seen_links = set()

                    for a in target_links:
                        href = a.get("href", "")
                        if not href or href in seen_links or href.startswith("#"):
                            continue
                        seen_links.add(href)

                        label = a.get_text(strip=True) or (a.find_parent().get_text(strip=True) if a.find_parent() else "")
                        # Look back for nearest headings/paragraphs for resolution, season, episode, size
                        prev_elements = a.find_all_previous(["h1", "h2", "h3", "h4", "h5", "p", "strong", "div"])[:8]
                        ctx_parts = [label]
                        for el in prev_elements:
                            t = el.get_text(" ", strip=True)
                            if 0 < len(t) < 300:
                                ctx_parts.append(t)
                        full_ctx = " ".join(ctx_parts)

                        # Detect season
                        s_num = None
                        sm = re.search(r'\b(?:Season|S)\s*0*(\d{1,2})\b', full_ctx, re.I)
                        if sm:
                            s_num = int(sm.group(1))
                        elif season is not None:
                            s_num = season
                        else:
                            s_num = 1

                        if season is not None and s_num != season:
                            continue

                        # Detect episode
                        ep_m = re.search(r'\b(?:Episode|Ep|E)[-\s]*0*(\d{1,3})\b', label, re.I)
                        if not ep_m:
                            ep_m = re.search(r'\b(?:Episode|Ep|E)[-\s]*0*(\d{1,3})\b', full_ctx, re.I)

                        is_range = bool(re.search(r'(?:episodes?|ep|e)\s*\d+\s*-\s*\d+', label, re.I) or re.search(r'(?:episodes?|ep|e)\s*\d+\s*-\s*\d+', full_ctx, re.I))
                        is_batch_kw = any(k in label.lower() or k in full_ctx.lower() for k in ["batch", "zip", "pack", "full season", "all episodes", "complete"])

                        if episode is not None:
                            if is_batch_kw or is_range or not ep_m:
                                continue
                            e_val = int(ep_m.group(1))
                            if e_val != episode:
                                continue
                            ep_tag = f"S{s_num:02d}E{e_val:02d}"
                            is_batch = False
                        else:
                            if ep_m and not (is_batch_kw or is_range):
                                e_val = int(ep_m.group(1))
                                ep_tag = f"S{s_num:02d}E{e_val:02d}"
                                is_batch = False
                            elif is_batch_kw or is_range:
                                ep_tag = f"S{s_num:02d} Batch"
                                is_batch = True
                            else:
                                is_batch = False
                                ep_tag = f"S{s_num:02d}" if "season" in post_title.lower() else ""

                        # Resolution
                        res_m = re.search(r'(2160p|4K|1080p|720p|480p)', f"{label} {full_ctx} {post_title}", re.I)
                        detected_res = res_m.group(1).upper() if res_m else "1080P"
                        if detected_res == "4K":
                            detected_res = "2160P"

                        # Size
                        size_m = re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB)(?:\s*/\s*E)?)\]?', f"{label} {full_ctx}", re.I)
                        detected_size = size_m.group(1).upper().strip("[] ") if size_m else "Unknown"

                        results.append({
                            "platform": "DesireMovies",
                            "title": f"{post_title} S{s_num:02d}".strip() if s_num and "season" in full_ctx.lower() else post_title,
                            "label": f"DesireMovies | [{detected_res}] {ep_tag} {label[:40]} ({detected_size})".strip(),
                            "episode_tag": ep_tag,
                            "resolution": detected_res,
                            "codec": "HEVC" if any(k in full_ctx.upper() for k in ["HEVC", "10BIT", "X265"]) else "x264",
                            "size": detected_size,
                            "audio": "Dual Audio" if any(k in full_ctx.lower() for k in ["dual", "hindi"]) else "Original",
                            "target_url": href,
                            "is_batch": is_batch,
                            "source_id": "desire_wp"
                        })
            else:
                await self.domain_resolver.report_failure("desiremovies", url)
        except Exception:
            await self.domain_resolver.report_failure("desiremovies", url)

        return results

    async def search_ddlbase(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        """Fast search & scene release extraction on DDLBase with XOR button decryption (< 450ms)."""
        clean = query.strip()
        encoded_query = urllib.parse.quote(clean)
        mirror = await self.domain_resolver.get_mirror("ddlbase")
        url = f"{mirror}/?s={encoded_query}"
        results = []

        try:
            resp = await client.get(url, headers=DEFAULT_HEADERS, timeout=3.5, follow_redirects=True)
            if resp.status_code == 200:
                await self.domain_resolver.report_success("ddlbase")
                soup = BeautifulSoup(resp.text, "lxml")

                # Parse candidate scene release links: <a href="/post/...">Release Title</a>
                candidate_links = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if not href.startswith("/post/"):
                        continue
                    release_title = a.get_text(strip=True)
                    if not release_title or len(release_title) < 5:
                        continue
                    clean_for_match = re.sub(r'[\._]', ' ', release_title)
                    clean_for_match = re.sub(r'\s*-[A-Za-z0-9_]+$', '', clean_for_match)
                    clean_for_match = re.sub(r'\b(2160p|4k|1080p|720p|480p|bluray|web-dl|webrip|uhd|hevc|x265|x264|h264|h265|dts|ddp5\.1|atmos|dual|audio|aac|remux)\b.*', '', clean_for_match, flags=re.I).strip()
                    if not is_strict_match(query, clean_for_match or release_title.replace(".", " ")):
                        continue
                    if href not in [c[0] for c in candidate_links]:
                        candidate_links.append((href, release_title))

                async def fetch_ddlbase_post(post_path, release_title):
                    post_url = f"{mirror}{post_path}" if post_path.startswith("/") else post_path
                    post_results = []
                    try:
                        p_resp = await client.get(post_url, headers=DEFAULT_HEADERS, timeout=3.5, follow_redirects=True)
                        if p_resp.status_code == 200:
                            await self.domain_resolver.report_success("ddlbase")
                            p_soup = BeautifulSoup(p_resp.text, "lxml")
                            p_text = p_soup.get_text(" ", strip=True)

                            # Parse scene release metadata: e.g. Title.S01E01.2160p...
                            s_num = None
                            e_num = None
                            se_m = re.search(r'\bS(\d{1,2})\s*[-._ ]?E(\d{1,3})\b', release_title, re.I)
                            if se_m:
                                s_num = int(se_m.group(1))
                                e_num = int(se_m.group(2))
                            else:
                                s_m = re.search(r'\b(?:Season|S)\s*0*(\d{1,2})\b', release_title, re.I)
                                if s_m:
                                    s_num = int(s_m.group(1))
                                elif season is not None:
                                    s_num = season

                            is_range = bool(re.search(r'(?:episodes?|ep|e)\s*\d+\s*-\s*\d+', release_title, re.I))
                            is_batch_kw = any(k in release_title.lower() for k in ["batch", "zip", "pack", "full season", "all episodes", "complete"])

                            if season is not None and s_num is not None and s_num != season:
                                return []

                            if episode is not None:
                                if is_batch_kw or is_range or e_num is None or e_num != episode:
                                    return []
                                ep_tag = f"S{s_num:02d}E{e_num:02d}" if s_num else f"E{e_num:02d}"
                                is_batch = False
                            else:
                                if e_num is not None and not (is_batch_kw or is_range):
                                    ep_tag = f"S{s_num:02d}E{e_num:02d}" if s_num else f"E{e_num:02d}"
                                    is_batch = False
                                elif is_batch_kw or is_range:
                                    ep_tag = f"S{s_num:02d} Batch" if s_num else "Batch"
                                    is_batch = True
                                else:
                                    ep_tag = f"S{s_num:02d}" if s_num else ""
                                    is_batch = False

                            # Resolution
                            res_m = re.search(r'(2160p|4K|1080p|720p|480p)', f"{release_title} {p_text}", re.I)
                            detected_res = res_m.group(1).upper() if res_m else "1080P"
                            if detected_res == "4K":
                                detected_res = "2160P"

                            # Codec
                            codec = "HEVC" if any(k in release_title.upper() for k in ["H.265", "H-265", "X265", "HEVC"]) else "x264"

                            # Size
                            size_m = re.search(r'\[(\d+(?:\.\d+)?\s*(?:GB|GiB|MB|MiB))\]', p_text, re.I)
                            if not size_m:
                                size_m = re.search(r'FileSize\.*:\s*(\d+(?:\.\d+)?\s*(?:GiB|GB|MiB|MB))', p_text, re.I)
                            detected_size = size_m.group(1).upper() if size_m else "Unknown"

                            seen_targets = set()

                            # 1. Decode XOR buttons (elements with ddllk / data-cipher / data-key)
                            for btn in p_soup.find_all(attrs={"ddllk": True}):
                                cipher = btn.get("ddllk")
                                if not cipher:
                                    continue
                                decoded_url = LinkResolver.resolve_ddlbase_xor(cipher)
                                if decoded_url and decoded_url.startswith("http") and decoded_url not in seen_targets:
                                    seen_targets.add(decoded_url)
                                    btn_lbl = btn.get_text(strip=True) or "Direct Download"
                                    post_results.append({
                                        "platform": "DDLBase",
                                        "title": release_title.replace(".", " "),
                                        "label": f"DDLBase | [{detected_res}] {ep_tag} {btn_lbl} ({detected_size})".strip(),
                                        "episode_tag": ep_tag,
                                        "resolution": detected_res,
                                        "codec": codec,
                                        "size": detected_size,
                                        "audio": "Dual Audio" if any(k in release_title.lower() for k in ["dual", "atmos", "truehd"]) else "Original",
                                        "target_url": decoded_url,
                                        "is_batch": is_batch,
                                        "source_id": "ddlbase_xor"
                                    })

                            # 2. Extract standard download links (PixelDrain, 1Fichier, Rapidgator, etc.)
                            for dl_a in p_soup.find_all("a", href=re.compile(r'pixeldrain|1fichier|rapidgator|clicknupload|gofile|mega\.nz|nitroflare', re.I)):
                                dl_url = dl_a["href"]
                                if dl_url in seen_targets:
                                    continue
                                seen_targets.add(dl_url)
                                dl_lbl = dl_a.get_text(strip=True) or dl_url.split("/")[2]
                                post_results.append({
                                    "platform": "DDLBase",
                                    "title": release_title.replace(".", " "),
                                    "label": f"DDLBase | [{detected_res}] {ep_tag} {dl_lbl} ({detected_size})".strip(),
                                    "episode_tag": ep_tag,
                                    "resolution": detected_res,
                                    "codec": codec,
                                    "size": detected_size,
                                    "audio": "Dual Audio" if any(k in release_title.lower() for k in ["dual", "atmos", "truehd"]) else "Original",
                                    "target_url": dl_url,
                                    "is_batch": is_batch,
                                    "source_id": "ddlbase_link"
                                })
                        else:
                            await self.domain_resolver.report_failure("ddlbase", post_url)
                    except Exception:
                        await self.domain_resolver.report_failure("ddlbase", post_url)
                    return post_results

                fetch_tasks = [fetch_ddlbase_post(path, title) for path, title in candidate_links[:3]]
                fetch_lists = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                for fl in fetch_lists:
                    if isinstance(fl, list):
                        results.extend(fl)
            else:
                await self.domain_resolver.report_failure("ddlbase", url)
        except Exception:
            await self.domain_resolver.report_failure("ddlbase", url)

        return results

    @staticmethod
    def build_nyaa_queries(query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        """Construct targeted anime queries on Nyaa for discrete episode precision."""
        clean = query.strip()
        if episode is not None:
            return [
                f'"{clean}" {episode:02d}',
                f'"{clean}" {episode:03d}'
            ]
        return [clean]

    async def search_nyaa(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        """Fast search & catalog parsing on Nyaa (< 350ms)."""
        clean = query.strip()
        mirror = await self.domain_resolver.get_mirror("nyaa")
        results = []
        seen_magnets = set()

        has_ep_in_query = bool(re.search(r'\b(?:episodes?|ep|e|s\d+e)\s*0*\d+\b|\s+-\s+\d+\b|\s+0\d+\b', query, re.I))

        candidate_queries = self.build_nyaa_queries(clean, season=season, episode=episode)
        fallback_query = clean if episode is not None else None

        async def fetch_nyaa_query(q_str: str) -> list:
            sub_results = []
            encoded_query = urllib.parse.quote(q_str)
            url = f"{mirror}/?f=0&c=0_0&q={encoded_query}"
            try:
                resp = await client.get(url, headers=DEFAULT_HEADERS, timeout=6.0, follow_redirects=True)
                if resp.status_code == 200:
                    await self.domain_resolver.report_success("nyaa")
                    soup = BeautifulSoup(resp.text, "lxml")
                    table = soup.find("table", class_=re.compile(r'torrent-list|table'))
                    rows = table.find_all("tr") if table else []

                    for tr in rows[1:]:
                        tds = tr.find_all("td")
                        if len(tds) < 6:
                            continue

                        # Title
                        title_links = tds[1].find_all("a")
                        if not title_links:
                            continue
                        title_a = title_links[-1]
                        item_title = title_a.get_text(strip=True)
                        if not item_title:
                            continue

                        clean_nyaa = re.sub(r'^\[[^\]]+\]\s*', '', item_title)
                        clean_nyaa = re.sub(r'\s*\[[0-9A-Fa-f]{8}\].*', '', clean_nyaa)
                        clean_nyaa = re.sub(r'\s*\.(mkv|mp4|avi)$', '', clean_nyaa, flags=re.I)
                        if not has_ep_in_query:
                            clean_nyaa = re.sub(r'\s*-\s*\d+.*', '', clean_nyaa)
                            clean_nyaa = re.sub(r'\s*\(\d+-\d+\).*', '', clean_nyaa)
                            clean_nyaa = re.sub(r'\s*\(\d+p\).*', '', clean_nyaa, flags=re.I)

                        if not is_strict_match(query, clean_nyaa.strip() or item_title):
                            continue

                        # Magnet link
                        magnet_a = tds[2].find("a", href=re.compile(r'^magnet:', re.I))
                        if not magnet_a:
                            torrent_a = tds[2].find("a", href=re.compile(r'\.torrent$', re.I))
                            target_url = f"{mirror}{torrent_a['href']}" if torrent_a and torrent_a['href'].startswith("/") else (torrent_a['href'] if torrent_a else "")
                        else:
                            target_url = magnet_a["href"]

                        if not target_url:
                            continue

                        size_val = tds[3].get_text(strip=True)
                        seeders_val = tds[5].get_text(strip=True)

                        # Anime episode and season detection
                        s_num = None
                        e_num = None

                        se_m = re.search(r'\bS(\d{1,2})\s*[-._ ]?E(\d{1,3})\b', item_title, re.I)
                        if se_m:
                            s_num = int(se_m.group(1))
                            e_num = int(se_m.group(2))
                        else:
                            ord_m = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)\s+season\b', item_title, re.I)
                            if ord_m:
                                s_num = int(ord_m.group(1))
                            else:
                                s_m = re.search(r'\b(?:Season|S)\s*0*(\d{1,2})\b', item_title, re.I)
                                if s_m:
                                    s_num = int(s_m.group(1))
                                elif season is not None:
                                    s_num = season
                                else:
                                    s_num = 1

                            ep_m = re.search(r'(?:[-_ ]\s*0*(\d{1,3})\s*(?:v\d+)?\s*(?:\[|\(|\.|$))|\b(?:Episode|Ep|E)[-\s]*0*(\d{1,3})\b', item_title, re.I)
                            if ep_m:
                                e_num = int(ep_m.group(1) or ep_m.group(2))

                        is_batch_kw = any(k in item_title.lower() for k in ["[batch]", "(batch)", "complete series", "complete season", "all episodes", "batch release", "batch pack", "vol."])
                        is_range = bool(re.search(r'\b(?:episodes?|ep|e)?\s*\d+\s*[-~]\s*\d+\b', item_title, re.I))
                        if is_range:
                            range_m = re.search(r'\b(?:episodes?|ep|e)?\s*\d+\s*[-~]\s*\d+\b', item_title, re.I)
                            range_prefix = item_title[:range_m.start()].rstrip()
                            if re.search(r'\b(?:season|s)\b$', range_prefix, re.I):
                                is_range = False

                        if season is not None and s_num != season:
                            continue

                        if episode is not None:
                            if is_batch_kw or is_range or e_num is None or e_num != episode:
                                continue
                            ep_tag = f"S{s_num:02d}E{e_num:02d}"
                            is_batch = False
                        else:
                            if e_num is not None and not (is_batch_kw or is_range):
                                ep_tag = f"S{s_num:02d}E{e_num:02d}"
                                is_batch = False
                            elif is_batch_kw or is_range:
                                ep_tag = f"S{s_num:02d} Batch"
                                is_batch = True
                            else:
                                ep_tag = f"S{s_num:02d}" if "season" in item_title.lower() else ""
                                is_batch = False

                        # Resolution
                        res_m = re.search(r'(2160p|4K|1080p|720p|480p)', item_title, re.I)
                        detected_res = res_m.group(1).upper() if res_m else "1080P"
                        if detected_res == "4K":
                            detected_res = "2160P"

                        # Codec
                        codec = "HEVC" if any(k in item_title.upper() for k in ["HEVC", "X265", "10BIT"]) else ("AV1" if "AV1" in item_title.upper() else "x264")

                        sub_results.append({
                            "platform": "Nyaa",
                            "title": item_title,
                            "label": f"Nyaa | [{detected_res}] {ep_tag} ({size_val}, {seeders_val} seeders)".strip(),
                            "episode_tag": ep_tag,
                            "resolution": detected_res,
                            "codec": codec,
                            "size": size_val,
                            "seeders": seeders_val,
                            "audio": "Japanese / Multi" if "multi" in item_title.lower() or "dual" in item_title.lower() else "Original",
                            "target_url": target_url,
                            "is_batch": is_batch,
                            "source_id": "nyaa_torrent"
                        })
                elif resp.status_code >= 500:
                    await self.domain_resolver.report_failure("nyaa", url)
            except httpx.ConnectError:
                pass
            except Exception:
                pass

            return sub_results

        tasks = [fetch_nyaa_query(qc) for qc in candidate_queries]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        for res_list in gathered:
            if isinstance(res_list, list):
                for item in res_list:
                    mag = item.get("target_url")
                    if mag and mag not in seen_magnets:
                        seen_magnets.add(mag)
                        results.append(item)

        if not results and fallback_query and fallback_query not in candidate_queries:
            fallback_res = await fetch_nyaa_query(fallback_query)
            for item in fallback_res:
                mag = item.get("target_url")
                if mag and mag not in seen_magnets:
                    seen_magnets.add(mag)
                    results.append(item)

        return results

    async def search_bingr(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None, tmdb_id: Optional[str] = None) -> list:
        """Fast search & embed streaming endpoint generation on Bingr (< 300ms)."""
        clean = query.strip()
        mirror = await self.domain_resolver.get_mirror("bingr")
        api_url = "https://api.bingr.one/api/search"
        params = {"q": clean, "type": "multi", "page": 1}
        results = []

        try:
            resp = await client.get(api_url, params=params, headers=DEFAULT_HEADERS, timeout=3.5, follow_redirects=True)
            if resp.status_code == 200:
                await self.domain_resolver.report_success("bingr")
                data = resp.json()
                items = data.get("results", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

                for item in items[:4]:
                    item_title = item.get("title") or item.get("name") or ""
                    if not item_title or not is_strict_match(query, item_title):
                        continue

                    found_tmdb_id = item.get("id")
                    if not found_tmdb_id:
                        continue

                    media_type = item.get("type") or ("tv" if season is not None or episode is not None else "movie")
                    year = item.get("year", "")

                    if media_type == "tv":
                        s = season if season is not None else 1
                        e = episode if episode is not None else 1
                        ep_tag = f"S{s:02d}E{e:02d}"

                        embeds = [
                            ("FilmU Embed (HLS)", f"https://embed.filmu.in/tv/{found_tmdb_id}/{s}/{e}"),
                            ("Cinezo Player", f"https://player.cinezo.live/embed/tv/{found_tmdb_id}/{s}/{e}"),
                            ("Vidbolt Stream", f"https://vidbolt.xyz/tv/{found_tmdb_id}/{s}/{e}")
                        ]
                    else:
                        ep_tag = ""
                        embeds = [
                            ("FilmU Embed (HLS)", f"https://embed.filmu.in/movie/{found_tmdb_id}"),
                            ("Cinezo Player", f"https://player.cinezo.live/embed/movie/{found_tmdb_id}"),
                            ("Vidbolt Stream", f"https://vidbolt.xyz/movie/{found_tmdb_id}")
                        ]

                    for source_name, embed_url in embeds:
                        results.append({
                            "platform": "Bingr",
                            "title": f"{item_title} ({year})".strip() if year else item_title,
                            "label": f"Bingr | [1080P] {ep_tag} {source_name}".strip(),
                            "episode_tag": ep_tag,
                            "resolution": "1080P",
                            "quality": "1080P",
                            "codec": "H.264",
                            "size": "Stream",
                            "audio": "Multi Audio",
                            "target_url": embed_url,
                            "is_batch": False,
                            "source_id": "bingr_embed"
                        })
            else:
                await self.domain_resolver.report_failure("bingr", api_url)
        except Exception:
            await self.domain_resolver.report_failure("bingr", api_url)

        # Fallback if search returns 0 results but tmdb_id is supplied
        if not results and tmdb_id:
            s = season if season is not None else 1
            e = episode if episode is not None else 1
            ep_tag = f"S{s:02d}E{e:02d}"
            embeds = [
                ("FilmU Embed (HLS)", f"https://embed.filmu.in/tv/{tmdb_id}/{s}/{e}"),
                ("Cinezo Player", f"https://player.cinezo.live/embed/tv/{tmdb_id}/{s}/{e}"),
                ("Vidbolt Stream", f"https://vidbolt.xyz/tv/{tmdb_id}/{s}/{e}")
            ]
            for source_name, embed_url in embeds:
                results.append({
                    "platform": "Bingr",
                    "title": clean,
                    "label": f"Bingr | [1080P] {ep_tag} {source_name}".strip(),
                    "episode_tag": ep_tag,
                    "resolution": "1080P",
                    "quality": "1080P",
                    "codec": "H.264",
                    "size": "Stream",
                    "audio": "Multi Audio",
                    "target_url": embed_url,
                    "is_batch": False,
                    "source_id": "bingr_embed"
                })

        return results

    async def search_all_concurrent(self, query: str, priority_platform: str = "UHDMovies", season: Optional[int] = None, episode: Optional[int] = None, tmdb_id: Optional[str] = None) -> list:
        """Search across all 9 platforms concurrently in parallel (< 800ms)."""
        async with httpx.AsyncClient(http2=True, limits=self.limits, follow_redirects=True) as client:
            t_uhd = self.search_uhdmovies(client, query, season=season, episode=episode)
            t_4khd = self.search_4khdhub(client, query, season=season, episode=episode)
            t_hdh = self.search_hdhub4u(client, query, season=season, episode=episode)
            t_vega = self.search_vegamovies(client, query, season=season, episode=episode)
            t_ola = self.search_olamovies(client, query, season=season, episode=episode)
            t_desire = self.search_desiremovies(client, query, season=season, episode=episode)
            t_ddl = self.search_ddlbase(client, query, season=season, episode=episode)
            t_nyaa = self.search_nyaa(client, query, season=season, episode=episode)
            t_bingr = self.search_bingr(client, query, season=season, episode=episode, tmdb_id=tmdb_id)

            task_results = await asyncio.gather(
                t_uhd, t_4khd, t_hdh, t_vega, t_ola, t_desire, t_ddl, t_nyaa, t_bingr,
                return_exceptions=True
            )

            all_results = []
            for r in task_results:
                if isinstance(r, list):
                    all_results.extend(r)

            return all_results

if __name__ == "__main__":
    test_q = sys.argv[1] if len(sys.argv) > 1 else "House of the Dragon"
    scrapers = FastScrapers()
    res = asyncio.run(scrapers.search_all_concurrent(test_q))
    print(f"[+] Found {len(res)} results:")
    print(json.dumps(res[:5], indent=2))
