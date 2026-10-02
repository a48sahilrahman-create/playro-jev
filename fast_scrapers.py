"""
High-Performance Async Scrapers (< 500ms) for Direct HTTP/2 Token & Catalog Extraction
Covers:
  1. UHDMovies (DriveSeed / Google CDN)
  2. VegaMovies (V-Cloud / Pixeldrain API / Cloudflare R2)
  3. HDHub4u (HubCloud / Gamerxyt / R2)
"""

import sys
import time
import asyncio
import re
import json
import urllib.parse
import httpx
from bs4 import BeautifulSoup
from parallel_dispatcher import is_strict_match
from jev_stream_resolver import JevStreamResolver
from domain_resolver import (
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

    async def search_uhdmovies(self, client: httpx.AsyncClient, query: str) -> list:
        """Fast search & catalog parsing on UHDMovies (< 350ms)."""
        clean = re.sub(r'\s+', '+', query.strip())
        uhd_mirror = await get_active_mirror("uhdmovies")
        url = f"{uhd_mirror}/?s={clean}"
        results = []

        try:
            resp = await client.get(url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=2.5)
            if resp.status_code == 200:
                await report_mirror_success("uhdmovies")
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
                            await report_mirror_success("uhdmovies")
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

                                ep_m = re.search(r'\b(Episode\s*\d+|Ep\s*\d+|E\d{1,2})\b', label, re.I)
                                ep_tag = f"{season_tag} {ep_m.group(1).title()}" if (season_tag and ep_m) else (ep_m.group(1).title() if ep_m else "")

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
                                    "source_id": "uhd_res"
                                })

                            # Layer 2 JEV Fallback: If standard DOM parsing found 0 links
                            if not post_results and p_resp.text:
                                post_results = await JevStreamResolver.extract_media_streams(
                                    p_resp.text,
                                    query=post_title or query,
                                    platform="UHDMovies"
                                )
                        else:
                            await report_mirror_failure("uhdmovies", post_url)
                    except Exception:
                        await report_mirror_failure("uhdmovies", post_url)
                    return post_results

                post_tasks = [fetch_post(art) for art in articles[:2]]
                post_lists = await asyncio.gather(*post_tasks, return_exceptions=True)
                for pl in post_lists:
                    if isinstance(pl, list):
                        results.extend(pl)

                # Outer Layer 2 Fallback: If 0 results discovered across articles
                if not results and resp.text:
                    results = await JevStreamResolver.extract_media_streams(
                        resp.text,
                        query=query,
                        platform="UHDMovies"
                    )
            else:
                await report_mirror_failure("uhdmovies", url)
        except Exception:
            await report_mirror_failure("uhdmovies", url)

        return results

    async def search_vegamovies(self, client: httpx.AsyncClient, query: str) -> list:
        """Fast search & catalog parsing on VegaMovies via ts-search.php (< 200ms)."""
        clean = query.strip()
        vega_mirror = await get_active_mirror("vegamovies")
        url = f"{vega_mirror}/ts-search.php?q={clean}"
        results = []

        try:
            resp = await client.get(url, headers=DEFAULT_HEADERS, timeout=1.8)
            if resp.status_code == 200:
                await report_mirror_success("vegamovies")
                data = resp.json()
                hits = data.get("hits", [])

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
                                buttons = p_soup.find_all("a", href=re.compile(r'nexdrive|vcloud|fast-dl|v-cloud|hubcloud'))
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

                                    if season_tag and season_tag.lower() not in context_text.lower():
                                        context_text = f"{season_tag} {context_text}"

                                    res_m = re.search(r'(2160p|4K|1080p|720p|480p)', context_text, re.I)
                                    size_m = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', context_text, re.I)
                                    detected_res = res_m.group(1).upper() if res_m else ("4K/UHD" if "2160" in post_title else "1080P")
                                    detected_size = size_m.group(1).upper() if size_m else "Unknown"

                                    # If intermediate episode links hub, recursively parse individual episodes
                                    if "episode" in label.lower() or "single" in label.lower():
                                        try:
                                            hub_resp = await client.get(href, headers=DEFAULT_HEADERS, timeout=2.0)
                                            if hub_resp.status_code == 200:
                                                hub_soup = BeautifulSoup(hub_resp.text, "lxml")
                                                v_links = [a for a in hub_soup.find_all("a", href=True) if any(k in a["href"] for k in ["vcloud", "fast-dl", "pixeldrain", "drive"])]
                                                for idx, v_a in enumerate(v_links, 1):
                                                    ep_label = f"{season_tag or 'Season 1'} Episode {idx:02d} [{detected_res}]"
                                                    post_results.append({
                                                        "platform": "VegaMovies",
                                                        "title": f"{post_title} {season_tag}".strip(),
                                                        "label": ep_label,
                                                        "episode_tag": f"S{season_tag[-1] if season_tag else '1'}E{idx:02d}",
                                                        "resolution": detected_res,
                                                        "codec": "HEVC 10-bit" if "HEVC" in context_text or "10BIT" in context_text.upper() else "x264",
                                                        "size": detected_size,
                                                        "audio": "Dual Audio" if "Dual" in context_text or "Hindi" in context_text else "Original",
                                                        "target_url": v_a["href"],
                                                        "source_id": "veg_ep"
                                                    })
                                        except Exception:
                                            pass

                                    ep_m = re.search(r'\b(Episode\s*\d+|Ep\s*\d+|E\d{1,2})\b', label, re.I)
                                    ep_tag = f"{season_tag} {ep_m.group(1).title()}" if (season_tag and ep_m) else (ep_m.group(1).title() if ep_m else "")

                                    post_results.append({
                                        "platform": "VegaMovies",
                                        "title": f"{post_title} {season_tag}".strip(),
                                        "label": context_text[:90],
                                        "episode_tag": ep_tag,
                                        "resolution": detected_res,
                                        "codec": "HEVC 10-bit" if "HEVC" in context_text or "10BIT" in context_text.upper() else "x264",
                                        "size": detected_size,
                                        "audio": "Dual Audio" if "Dual" in context_text or "Hindi" in context_text else "Original",
                                        "target_url": href,
                                        "source_id": "veg_res"
                                    })
                            else:
                                await report_mirror_failure("vegamovies", post_url)
                        except Exception:
                            await report_mirror_failure("vegamovies", post_url)
                    return post_results

                vega_tasks = [fetch_vega_post(h) for h in hits[:2]]
                vega_lists = await asyncio.gather(*vega_tasks, return_exceptions=True)
                for vl in vega_lists:
                    if isinstance(vl, list):
                        results.extend(vl)
            else:
                await report_mirror_failure("vegamovies", url)
        except Exception:
            await report_mirror_failure("vegamovies", url)

        return results

    async def search_hdhub4u(self, client: httpx.AsyncClient, query: str) -> list:
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
                                dls = p_soup.find_all("a", href=re.compile(r'hubcloud|hubdrive|drive|gamerxyt|hubcdn'))
                                season_tag = ""
                                sm = re.search(r'\b(Season\s*\d+|S\d{1,2})\b', post_title, re.I)
                                if sm:
                                    season_tag = sm.group(1).title()

                                for dl in dls:
                                    label = dl.get_text(strip=True) or dl.find_parent().get_text(strip=True)
                                    href = dl["href"]

                                    # Context lookup
                                    prev_h = dl.find_previous(["h2", "h3", "h4", "h5", "p", "strong", "span"])
                                    context_str = prev_h.get_text(" ", strip=True) if prev_h else ""

                                    ep_m = re.search(r'\b(Episode\s*\d+|Ep\s*\d+|E\d{1,2}|Pack)\b', f"{label} {context_str}", re.I)
                                    ep_tag = f"{season_tag} {ep_m.group(1).title()}" if (season_tag and ep_m) else (ep_m.group(1).title() if ep_m else "")

                                    res_m = re.search(r'(2160p|4K|1080p|720p|480p)', f"{label} {context_str}", re.I)
                                    size_m = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', f"{label} {context_str}", re.I)
                                    detected_res = res_m.group(1).upper() if res_m else ("4K/UHD" if "2160" in post_title else "1080P")

                                    post_results.append({
                                        "platform": "HDHub4u",
                                        "title": f"{post_title} {season_tag}".strip(),
                                        "label": f"{context_str[:40]} - {label}" if context_str and len(label) < 15 else label[:70],
                                        "episode_tag": ep_tag,
                                        "resolution": detected_res,
                                        "codec": "HEVC" if "HEVC" in f"{label} {context_str}" or "10Bit" in f"{label} {context_str}" else "x264",
                                        "size": size_m.group(1).upper() if size_m else "Unknown",
                                        "audio": "Dual Audio (Hindi + Eng)" if "Dual" in post_title or "Hindi" in post_title else "Original",
                                        "target_url": href,
                                        "source_id": "hdh_res"
                                    })

                                # Layer 2 JEV Fallback: If heuristic parsing yielded 0 links
                                if not post_results and p_resp.text:
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
                if not results and resp.text:
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

    async def search_olamovies(self, client: httpx.AsyncClient, query: str) -> list:
        """Fast search & catalog parsing on OlaMovies Next.js SSR (< 450ms)."""
        clean = query.strip()
        ola_mirror = await get_active_mirror("olamovies")
        encoded_q = urllib.parse.quote_plus(clean)
        url = f"{ola_mirror}/search?q={encoded_q}"
        results = []

        try:
            resp = await client.get(url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=4.0)
            if resp.status_code == 200:
                await report_mirror_success("olamovies")
                soup = BeautifulSoup(resp.text, "lxml")

                # Query words with stopword exclusion for intelligent ranking
                STOPWORDS = {"the", "and", "all", "movie", "movies", "collection", "full", "series", "season", "part"}
                words = [w.lower() for w in re.split(r'[\s+._:-]+', clean) if len(w) >= 3 and w.lower() not in STOPWORDS]
                if not words:
                    words = [w.lower() for w in re.split(r'[\s+._:-]+', clean) if len(w) >= 2]

                all_candidates = []
                for a in soup.find_all("a", href=re.compile(r"^/(?:movies|series)/[a-zA-Z0-9_-]+$")):
                    href = a["href"]
                    slug = href.split("/")[-1].lower()
                    text = a.get_text(" ", strip=True)

                    if any(k in href for k in ["comment-", "category", "tag"]):
                        continue

                    # Score post
                    score = 0
                    if any(k in slug for k in ["universe-collection", "all-movies-collection", "complete-collection", "imdb-top-250"]):
                        score -= 10

                    matched = sum(1 for w in words if w in slug or w in text.lower())
                    score += matched * 5

                    if words and slug.startswith(words[0]):
                        score += 3

                    if matched > 0 and href not in [p["href"] for p in all_candidates]:
                        title = text.strip() or slug.replace("-", " ").title()
                        all_candidates.append({"score": score, "href": href, "title": title, "slug": slug})

                all_candidates.sort(key=lambda x: x["score"], reverse=True)
                matching_posts = all_candidates[:3]

                async def fetch_ola_post(p):
                    post_url = f"{ola_mirror}{p['href']}"
                    post_results = []
                    try:
                        p_resp = await client.get(post_url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=4.0)
                        if p_resp.status_code == 200:
                            await report_mirror_success("olamovies")
                            clean_html = p_resp.text.replace('\\"', '"').replace('\\\\', '\\')

                            # 1. Primary: Extract from Next.js self.__next_f JSON chunks
                            file_matches = list(re.finditer(
                                r'\{"id":"itm_[^"]+","btnId":"[^"]+","url":"(https?://links\.[^"]+)","label":"([^"]+)",(?:.*?"filename":"([^"]+)")?',
                                clean_html
                            ))

                            for fm in file_matches:
                                dl_url = fm.group(1)
                                lbl = fm.group(2)
                                fname = fm.group(3) or lbl
                                meta_str = f"{lbl} {fname}"

                                res_m = re.search(r'(2160p|4K|1080p|720p|480p)', meta_str, re.I)
                                size_m = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', meta_str, re.I)

                                post_results.append({
                                    "platform": "OlaMovies",
                                    "title": p["title"][:80],
                                    "label": f"{lbl} - {fname[:60]}",
                                    "resolution": res_m.group(1).upper() if res_m else "1080P",
                                    "codec": "HEVC 10-bit" if any(k in meta_str.upper() for k in ["HEVC", "X265", "10BIT"]) else "x264",
                                    "size": size_m.group(1).upper() if size_m else "Unknown",
                                    "audio": "Multi Audio" if any(k in meta_str.lower() for k in ["multi", "hindi", "dual", "tamil", "telugu"]) else "Original",
                                    "target_url": dl_url,
                                    "source_id": "ola_nextjs"
                                })

                            # 2. Fallback: Classic DOM anchors
                            if not post_results:
                                p_soup = BeautifulSoup(p_resp.text, "lxml")
                                for a_tag in p_soup.find_all("a", href=re.compile(r'https?://links\.(?:ol-am|olamovies)\.[^/]+/[^\s"\'<>]+')):
                                    dl_url = a_tag["href"]
                                    lbl = a_tag.get_text(strip=True) or a_tag.find_parent().get_text(strip=True)
                                    res_m = re.search(r'(2160p|4K|1080p|720p|480p)', lbl, re.I)
                                    size_m = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', lbl, re.I)
                                    post_results.append({
                                        "platform": "OlaMovies",
                                        "title": p["title"][:80],
                                        "label": lbl[:60],
                                        "resolution": res_m.group(1).upper() if res_m else "1080P",
                                        "codec": "HEVC 10-bit" if "hevc" in lbl.lower() or "x265" in lbl.lower() else "x264",
                                        "size": size_m.group(1).upper() if size_m else "Unknown",
                                        "audio": "Multi Audio" if "multi" in lbl.lower() or "hindi" in lbl.lower() else "Original",
                                        "target_url": dl_url,
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
            else:
                await report_mirror_failure("olamovies", url)
        except Exception:
            await report_mirror_failure("olamovies", url)

        return results

    async def search_all_concurrent(self, query: str, priority_platform: str = "UHDMovies") -> list:
        """Search across platforms concurrently in parallel (< 800ms)."""
        async with httpx.AsyncClient(http2=True, limits=self.limits, follow_redirects=True) as client:
            t_uhd = self.search_uhdmovies(client, query)
            t_vega = self.search_vegamovies(client, query)
            t_hdh = self.search_hdhub4u(client, query)
            t_ola = self.search_olamovies(client, query)

            res_uhd, res_vega, res_hdh, res_ola = await asyncio.gather(t_uhd, t_vega, t_hdh, t_ola, return_exceptions=True)

            all_results = []
            if isinstance(res_uhd, list):
                all_results.extend(res_uhd)
            if isinstance(res_vega, list):
                all_results.extend(res_vega)
            if isinstance(res_hdh, list):
                all_results.extend(res_hdh)
            if isinstance(res_ola, list):
                all_results.extend(res_ola)

            return all_results

if __name__ == "__main__":
    test_q = sys.argv[1] if len(sys.argv) > 1 else "House of the Dragon"
    scrapers = FastScrapers()
    res = asyncio.run(scrapers.search_all_concurrent(test_q))
    print(f"[+] Found {len(res)} results:")
    print(json.dumps(res[:5], indent=2))
