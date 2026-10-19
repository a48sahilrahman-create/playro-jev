"""
Hierarchical Media Dispatcher & Multi-Platform Simultaneous TV Series Aggregator
Features:
  1. Simultaneous Multi-Platform TV Series Crawling (UHDMovies + VegaMovies + Telegram Bot + Local DB).
  2. Multi-Tier Series Grouping:
     - Extracts Seasons (1, 2, 3, 4) and Individual Episodes (Episode 01, Episode 02, ..., Complete Batch Zip).
     - Merges 4K UHD, 1080p, 720p, and 480p variants under each distinct episode.
  3. Smart Mirror Collapse & Deduplication (G-Direct, V-Cloud, Direct CDN).
  4. Asset/GIF Exclusion & Strict Video Extension Validation.
"""

import os
import sys
import time
import asyncio
import re
import json
import base64
import subprocess
import difflib
from typing import Optional
import httpx
from bs4 import BeautifulSoup
from pathlib import Path

# Local tool imports
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from media_db import MediaDB, resolve_db_path
from stream_verifier import StreamVerifier
from tg_bot_scraper import load_tg_config, search_telegram, clean_query
from youtube_scraper import YouTubeScraper
from domain_resolver import (
    get_active_mirror,
    get_active_mirror_sync,
    report_mirror_failure,
    report_mirror_success
)

try:
    from curl_cffi import requests as stealth_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

# Active Platform Mirrors (dynamic sync fallback)
UHD_MIRROR = get_active_mirror_sync("uhdmovies")
VEGA_MIRROR = get_active_mirror_sync("vegamovies")
HDHUB_MIRROR = get_active_mirror_sync("hdhub4u")
OLA_MIRROR = get_active_mirror_sync("olamovies")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

def clean_media_title(raw_title: str) -> str:
    """Removes website noise from title."""
    noise = (
        r'\b(Download|Full Movie|Watch Online|Direct Link|ESubs|Multi Audio|Dual Audio|ORG 5\.1|'
        r'WEB-DL|WEBRip|BluRay|HDRip|DVDRip|\d{3,4}p|2160p|4K|1080p|720p|480p|Restored|'
        r'Malayalam|Hindi|Tamil|Telugu|English|Kannada|the Movie|Hindi Dubbed|Line Audio|'
        r'DTS-HD|HQ|Clean Audio|HQ-Line Audio|Remastered|Unrated|Extended|IMAX|'
        r'AMZN|DSNP|NF|ZEE5|HOTSTAR|SONYLIV|JIO|ATVP|CR|HULU|MAX)\b'
    )
    cleaned = re.sub(noise, '', raw_title, flags=re.I)
    cleaned = re.sub(r'[\{\}\[\]\(\)\|–—:]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

ROMAN_NUMS = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"
}

def normalize_tokens(s: str) -> list:
    """Tokenize and convert roman / word numbers to digits for robust comparison."""
    words = re.findall(r'[a-zA-Z0-9]+', s.lower())
    norm = []
    for w in words:
        if w in ROMAN_NUMS:
            norm.append(ROMAN_NUMS[w])
        else:
            norm.append(w)
    return norm

def clean_search_query(query: str) -> str:
    """Sanitize query for HTTP search endpoints by removing break-inducing punctuation."""
    q = re.sub(r'[\:\(\)\[\]\{\}\-\_\,\.\!\?\"\']', ' ', query)
    q = re.sub(r'\s+', ' ', q).strip()
    return q

def extract_clean_numeric_tokens(text: str) -> set:
    """Extract numeric sequel/part tokens while explicitly ignoring file sizes (e.g. 2GB, 700MB), resolutions, and episode/disc labels."""
    # Strip episode tokens so Episode numbers (e.g. Ep 1, Episode 02, E03, 1x02) don't act as sequel filters
    clean = re.sub(r'\b(?:episodes?|ep|e)\s*\d+\b', '', text, flags=re.I)
    clean = re.sub(r'\b\d+x\d+\b', '', clean, flags=re.I)
    # Strip disc labels (e.g. 2-Disc, 2 Disc, Disc 2, CD 2)
    clean = re.sub(r'\b(?:\d+\s*-?\s*disc|disc\s*-?\s*\d+|\d+\s*-?\s*cd|cd\s*-?\s*\d+)\b', '', clean, flags=re.I)
    # Strip file sizes (2GB, 1.5 GB, 800MB, 4K, 1080p, etc.)
    clean = re.sub(r'\b\d+(?:\.\d+)?\s*(?:GB|MB|TB|KB)\b', '', clean, flags=re.I)
    clean = re.sub(r'\b(2160p|1080p|720p|480p|360p|4k|8k)\b', '', clean, flags=re.I)
    tokens = normalize_tokens(clean)
    return set(w for w in tokens if w.isdigit() and len(w) <= 2)

KNOWN_FRANCHISE_SUBTITLES = {
    ("avatar", "2"): "way of water",
    ("avatar", "3"): "fire and ash",
    ("spider man", "far from home"): "far from home",
    ("spider man", "no way home"): "no way home",
    ("spider man", "homecoming"): "homecoming"
}

def is_strict_match(query: str, candidate_title: str) -> bool:
    """Returns True only if candidate title is genuinely the intended movie or series."""
    q = query.strip().lower().replace('é', 'e')
    c = clean_media_title(candidate_title).lower().replace('é', 'e')

    # Strip SxxExx and season/episode specifics from base comparison
    q_base = re.sub(r'\b(s\d+\s*e\d+|s\d+|season\s*\d+|episode\s*\d+|e\d+|ep\d+|\d+x\d+)\b', '', q, flags=re.I).strip()
    c_base = re.sub(r'\b(s\d+\s*e\d+|s\d+|season\s*\d+|episode\s*\d+|e\d+|ep\d+|\d+x\d+)\b', '', c, flags=re.I).strip()

    # Check digit / number integrity without file size / episode collision
    q_nums = extract_clean_numeric_tokens(q_base or q)
    c_nums = extract_clean_numeric_tokens(c_base or c)

    # Check franchise alias mapping (e.g. "Avatar 2" -> "Way of Water")
    for (franchise, sub_key), subtitle in KNOWN_FRANCHISE_SUBTITLES.items():
        if franchise in q and sub_key in q and subtitle in c:
            return True

    # If query specifically asks for a sequel part/chapter/number (like 2, 4) and candidate does not have it
    if q_nums:
        for num in q_nums:
            if num not in c_nums:
                return False

    # Extract year if present in both
    q_year = re.search(r'\b(19\d\d|20\d\d)\b', q)
    c_year = re.search(r'\b(19\d\d|20\d\d)\b', candidate_title)
    if q_year and c_year:
        if abs(int(q_year.group(1)) - int(c_year.group(1))) > 1:
            return False

    q_no_year = re.sub(r'\b(19\d\d|20\d\d)\b', '', q_base or q).strip()
    c_no_year = re.sub(r'\b(19\d\d|20\d\d)\b', '', c_base or c).strip()

    q_no_y_tokens = normalize_tokens(q_no_year or q_base or q)
    c_no_y_tokens = normalize_tokens(c_no_year or c_base or c)

    # Direct match
    q_str = " ".join(q_no_y_tokens)
    c_str = " ".join(c_no_y_tokens)

    if q_str == c_str:
        return True

    def _is_token_subsequence(sub: list, full: list) -> bool:
        """Checks if sub appears as a contiguous subsequence in full."""
        if not sub or not full or len(sub) > len(full):
            return False
        sub_len = len(sub)
        return any(full[i : i + sub_len] == sub for i in range(len(full) - sub_len + 1))

    # Token sequence match on full tokens (e.g. "The Boys" in "The Boys Season 1")
    # Disallows matching unrelated titles where query is not a sequence (e.g. "The Boys" vs "Walter Boys")
    if len(q_no_y_tokens) >= 2 and _is_token_subsequence(q_no_y_tokens, c_no_y_tokens):
        return True

    stop_words = {'movie', 'series', 'download', 'watch', 'online', 'the', 'a', 'an', 'and', 'of', 'in', 'part', 'chapter', 'full', 'hd', 'bluray', 'amzn', 'dsnp', 'nf'}
    q_keywords = [w for w in q_no_y_tokens if len(w) > 1 and w not in stop_words]
    c_keywords = [w for w in c_no_y_tokens if len(w) > 1 and w not in stop_words]

    if not q_keywords:
        q_keywords = [w for w in q_no_y_tokens if len(w) > 1]
    if not c_keywords:
        c_keywords = [w for w in c_no_y_tokens if len(w) > 1]

    if not q_keywords or not c_keywords:
        return False

    # Contiguous sequence match on keywords
    if len(q_keywords) >= 2 and _is_token_subsequence(q_keywords, c_keywords):
        return True

    # Strict token containment & overlap (disallowing single-token permissive fallback)
    q_set = set(q_keywords)
    c_set = set(c_keywords)
    intersection = q_set & c_set
    union = q_set | c_set

    q_overlap = len(intersection) / len(q_set) if q_set else 0.0
    c_overlap = len(intersection) / len(c_set) if c_set else 0.0
    jaccard = len(intersection) / len(union) if union else 0.0

    # Require at least 80% overlap or full sequence
    if jaccard >= 0.8 or (q_overlap >= 0.8 and c_overlap >= 0.8):
        return True

    if len(q_keywords) >= 2 and q_overlap >= 0.8 and c_overlap >= 0.7:
        matched_indices = [c_keywords.index(w) for w in q_keywords if w in c_keywords]
        if matched_indices == sorted(matched_indices):
            return True

    ratio = difflib.SequenceMatcher(None, q_str, c_str).ratio()
    return ratio >= 0.80

def decode_release_metadata(context_text: str, button_text: str = "", post_title: str = "") -> dict:
    """Universal regex decoder for resolution, codec, size, and audio.
    Prioritizes specific button / section context over general post title."""
    # 1. Season & Episode (button -> context -> post_title)
    ep_m = (
        re.search(r'\b(S\d{1,2}\s*E\d{1,2}|Season\s*\d+(?:\s*-\s*Part\s*\d+)?|Episodes?\s*\d+(?:-\d+)?|Part\s*\d+|Complete\s*Batch|Full\s*Batch|Batch/Zip|Batch|OVA|Special)\b', button_text, re.I) or
        re.search(r'\b(S\d{1,2}\s*E\d{1,2}|Season\s*\d+(?:\s*-\s*Part\s*\d+)?|Episodes?\s*\d+(?:-\d+)?|Part\s*\d+|Complete\s*Batch|Full\s*Batch|Batch/Zip|Batch|OVA|Special)\b', context_text, re.I) or
        re.search(r'\b(S\d{1,2}\s*E\d{1,2}|Season\s*\d+(?:\s*-\s*Part\s*\d+)?|Episodes?\s*\d+(?:-\d+)?|Part\s*\d+|Complete\s*Batch|Full\s*Batch|Batch/Zip|Batch|OVA|Special)\b', post_title, re.I)
    )
    ep_tag = ep_m.group(1).title() if ep_m else ""

    # 2. Resolution (button -> context -> post_title)
    res_m = (
        re.search(r'\b(2160p|4K|1080p|720p|480p|360p)\b', button_text, re.I) or
        re.search(r'\b(2160p|4K|1080p|720p|480p|360p)\b', context_text, re.I) or
        re.search(r'\b(2160p|4K|1080p|720p|480p|360p)\b', post_title, re.I)
    )
    resolution = res_m.group(1).upper() if res_m else "1080P"
    if resolution == "4K":
        resolution = "2160P"

    # 3. File Size (button -> context -> post_title)
    size_m = (
        re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB)(?:/E)?)\]?', button_text, re.I) or
        re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB)(?:/E)?)\]?', context_text, re.I) or
        re.search(r'\[?(\d+(?:\.\d+)?\s*(?:GB|MB)(?:/E)?)\]?', post_title, re.I)
    )
    size = size_m.group(1).upper().strip("[] ") if size_m else "N/A"

    full = f"{button_text} {context_text} {post_title}".strip()

    # 4. Codec & Quality
    codec = "x264"
    if re.search(r'\b(HEVC|x265|10bit|10-bit)\b', full, re.I):
        codec = "HEVC 10-bit"
    elif re.search(r'\b(BluRay|Blu-Ray)\b', full, re.I):
        codec = "BluRay x264"
    elif re.search(r'\b(WEB-DL|WEBRip)\b', full, re.I):
        codec = "WEB-DL"
    elif re.search(r'\b(HDRip|DVDRip)\b', full, re.I):
        codec = "HDRip"

    # 5. Audio Channels & Languages
    audio = "Original Audio"
    if re.search(r'\{(Hindi[^\}]*)\}|\b(Dual Audio|Hindi-English)\b', full, re.I):
        audio = "Dual (Hindi + Eng)"
    elif re.search(r'\b(Multi Audio|Multi-Audio|Hindi-English-Japanese)\b', full, re.I):
        audio = "Multi-Audio"
    elif "Malayalam" in full:
        audio = "Malayalam (Original)"
    elif "Tamil" in full:
        audio = "Tamil"
    elif "Japanese" in full:
        audio = "Japanese (Original)"
    elif "English" in full:
        audio = "English (Original)"

    return {
        "episode_tag": ep_tag,
        "resolution": resolution,
        "codec": codec,
        "size": size,
        "audio": audio
    }

try:
    from fast_scrapers import FastScrapers
    if not hasattr(FastScrapers, "search_4khdhub"):
        async def _default_search_4khdhub(self, client, query, season=None, episode=None, **kwargs):
            if hasattr(self, "search_hdhub4u"):
                try:
                    return await self.search_hdhub4u(client, query, season=season, episode=episode, **kwargs)
                except TypeError:
                    return await self.search_hdhub4u(client, query)
            return []
        FastScrapers.search_4khdhub = _default_search_4khdhub
except ImportError:
    pass

class _FastScraperFleetAdapter:
    """Fleet adapter wrapping FastScrapers to support season/episode parameters,
    platform tagging ('UHDMovies', '4KHDHub', 'HDHub4u', 'VegaMovies', 'OlaMovies',
    'DesireMovies', 'DDLBase', 'Nyaa', 'Bingr'),
    and graceful fallback if underlying scrapers or test mocks lack kwargs."""
    def __init__(self, raw_fs):
        self._fs = raw_fs

    async def _execute(self, method_name: str, platform_tag: str, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        method = getattr(self._fs, method_name, None)
        if method is None:
            if method_name == "search_4khdhub" and hasattr(self._fs, "search_hdhub4u"):
                method = getattr(self._fs, "search_hdhub4u")
            else:
                return []
        try:
            results = await method(client, query, season=season, episode=episode)
        except TypeError:
            results = await method(client, query)

        tagged_results = []
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict):
                    tagged = dict(item)
                    tagged["platform"] = platform_tag
                    tagged_results.append(tagged)
                else:
                    tagged_results.append(item)
        return tagged_results

    async def search_uhdmovies(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        return await self._execute("search_uhdmovies", "UHDMovies", client, query, season=season, episode=episode)

    async def search_4khdhub(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        return await self._execute("search_4khdhub", "4KHDHub", client, query, season=season, episode=episode)

    async def search_hdhub4u(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        return await self._execute("search_hdhub4u", "HDHub4u", client, query, season=season, episode=episode)

    async def search_vegamovies(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        return await self._execute("search_vegamovies", "VegaMovies", client, query, season=season, episode=episode)

    async def search_olamovies(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        return await self._execute("search_olamovies", "OlaMovies", client, query, season=season, episode=episode)

    async def search_desiremovies(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        return await self._execute("search_desiremovies", "DesireMovies", client, query, season=season, episode=episode)

    async def search_ddlbase(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        return await self._execute("search_ddlbase", "DDLBase", client, query, season=season, episode=episode)

    async def search_nyaa(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        return await self._execute("search_nyaa", "Nyaa", client, query, season=season, episode=episode)

    async def search_bingr(self, client: httpx.AsyncClient, query: str, season: Optional[int] = None, episode: Optional[int] = None) -> list:
        return await self._execute("search_bingr", "Bingr", client, query, season=season, episode=episode)

    def __getattr__(self, name):
        return getattr(self._fs, name)

class ParallelMediaDispatcher:
    def __init__(self, db_path: str = None):
        self.db = MediaDB(db_path=db_path or resolve_db_path())
        self.verifier = StreamVerifier()
        self.tg_cfg = load_tg_config()
        self.yt = YouTubeScraper()
        self.limits = httpx.Limits(max_keepalive_connections=40, max_connections=80)

    async def fetch_series_uhd(self, client: httpx.AsyncClient, query: str) -> dict:
        """Parses UHDMovies TV series posts across all seasons for 4K UHD and 1080p episodes."""
        seasons_dict = {}
        try:
            clean = re.sub(r'\s+', '+', query.strip())
            uhd_mirror = await get_active_mirror("uhdmovies")
            url = f"{uhd_mirror}/search/{clean}"
            r = await client.get(url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=2.5)
            if r.status_code != 200:
                return {}

            soup = BeautifulSoup(r.text, "lxml")
            articles = soup.find_all("article")
            for art in articles[:6]:
                link = art.find("a", href=True)
                p_title = ""
                for h in art.find_all(["h1", "h2", "h3", "h4", "a"]):
                    t = h.get_text(strip=True) or h.get("title", "")
                    if len(t) > 5 and ("season" in t.lower() or "series" in t.lower() or "episode" in t.lower()):
                        p_title = t
                        break
                if not p_title and link:
                    t = link.get("title", "") or link.get_text(strip=True)
                    if len(t) > 5 and ("season" in t.lower() or "series" in t.lower() or "episode" in t.lower()):
                        p_title = t

                if not (link and p_title):
                    continue

                if not is_strict_match(query, p_title):
                    continue

                # Only treat as series if title explicitly mentions Season / Series / Episode
                if not re.search(r'\b(Season\s*\d+|Series|Episodes?\s*\d+)\b', p_title, re.I):
                    continue

                p_url = link["href"]
                if p_url.startswith("/"):
                    p_url = f"{uhd_mirror}{p_url}"
                p_resp = await client.get(p_url, headers=DEFAULT_HEADERS, timeout=2.5)
                if p_resp.status_code == 200:
                    p_soup = BeautifulSoup(p_resp.text, "lxml")
                    current_season = "Season 1"
                    s_match = re.search(r'Season\s*(\d+)', p_title, re.I)
                    if s_match:
                        current_season = f"Season {int(s_match.group(1))}"

                    seen_urls = set()
                    for elem in p_soup.find_all(["p", "h4"]):
                        txt = elem.get_text(" ", strip=True)
                        season_m = re.search(r'\b(Season\s*\d+)\b', txt, re.I)
                        if season_m and len(txt) < 40 and not any(k in txt for k in ["Episode", "Zip", "Download"]):
                            current_season = season_m.group(1).title()

                        dls = elem.find_all("a", href=re.compile(r'driveseed|cloud\.unblocked|drive\.google|naukriadda|\?sid='))
                        if dls:
                            prev_elem = elem.find_previous(["p", "h2", "h3", "h4", "h5"])
                            prev_txt = prev_elem.get_text(" ", strip=True) if prev_elem else ""
                            for dl in dls:
                                target_url = dl["href"]
                                if target_url in seen_urls:
                                    continue
                                seen_urls.add(target_url)

                                btn_txt = dl.get_text(strip=True)
                                btn_clean = btn_txt.lower().strip()
                                if btn_clean in [
                                    "4k", "2160phevc", "1080p uhd", "uhdmovies", "3d movies",
                                    "4k hdr", "4k 2160p", "1080p 60fps", "1080p x264 uhd",
                                    "1080p x265 10bit", "4k movies", "1080p movies", "720p movies"
                                ]:
                                    continue
                                ep_m = re.search(r'\b(Episode\s*\d+|Ep\s*\d+|Zip|Batch)\b', btn_txt, re.I)
                                ep_name = ep_m.group(1).title() if ep_m else "Complete Batch [Zip]"
                                if "zip" in ep_name.lower() or "batch" in ep_name.lower():
                                    ep_name = "Complete Batch [Zip]"

                                meta = decode_release_metadata(prev_txt or txt, btn_txt, p_title)
                                if current_season not in seasons_dict:
                                    seasons_dict[current_season] = {}
                                if ep_name not in seasons_dict[current_season]:
                                    seasons_dict[current_season][ep_name] = []

                                if not any(q["target_url"] == target_url or (q["resolution"] == meta["resolution"] and q["platform"] == "UHDMovies") for q in seasons_dict[current_season][ep_name]):
                                    seasons_dict[current_season][ep_name].append({
                                        "resolution": meta["resolution"],
                                        "codec": meta["codec"],
                                        "size": meta["size"],
                                        "audio": meta["audio"],
                                        "target_url": target_url,
                                        "platform": "UHDMovies",
                                        "source_origin": "UHDMovies 4K/1080p"
                                    })
        except Exception:
            pass
        return seasons_dict

    async def fetch_series_vega(self, client: httpx.AsyncClient, query: str) -> dict:
        """Parses VegaMovies TV series posts across ALL matching season posts."""
        seasons_dict = {}
        try:
            clean = re.sub(r'\s+', '+', query.strip())
            vega_mirror = await get_active_mirror("vegamovies")
            url = f"{vega_mirror}/ts-search.php?q={clean}"
            r = await client.get(url, headers=DEFAULT_HEADERS, timeout=2.2)
            if r.status_code != 200:
                return {}

            hits = r.json().get("hits", [])
            for h in hits[:5]:
                doc = h.get("document", {})
                post_title = doc.get("post_title", "")
                if not is_strict_match(query, post_title):
                    continue

                # Only treat as series if title explicitly mentions Season / Series / Episode
                if not re.search(r'\b(Season\s*\d+|Series|Episodes?\s*\d+|Anime\s+Series)\b', post_title, re.I):
                    continue

                permalink = doc.get("permalink", "")
                post_url = f"{vega_mirror}{permalink}" if permalink.startswith("/") else permalink
                p_resp = await client.get(post_url, headers=DEFAULT_HEADERS, timeout=2.5)
                if p_resp.status_code == 200:
                    soup = BeautifulSoup(p_resp.text, "lxml")
                    current_season = "Season 1"
                    s_match = re.search(r'Season\s*(\d+)', post_title, re.I)
                    if s_match:
                        current_season = f"Season {int(s_match.group(1))}"

                    for elem in soup.find_all(["h2", "h3", "h4", "h5", "p", "div"]):
                        txt = elem.get_text(" ", strip=True)
                        season_m = re.search(r'\b(Season\s*\d+|Part\s*\d+)\b', txt, re.I)
                        if season_m and len(txt) < 80 and not any(k in txt for k in ["Episode", "Zip", "Added", "Recent"]):
                            current_season = season_m.group(1).title()

                        buttons = elem.find_all("a", href=re.compile(r'nexdrive|vcloud|fast-dl'))
                        if buttons:
                            prev_p = elem.find_previous_sibling(["p", "h3", "h4", "h5", "div"])
                            context_str = prev_p.get_text(" ", strip=True) if prev_p else ""
                            is_batch = any(k in f"{txt} {context_str}".lower() for k in ["batch", "zip", "complete"])
                            ep_match = re.search(r'\b(Episode\s*\d+|Ep\s*\d+|E\d{1,2})\b', f"{txt} {context_str}", re.I)

                            if is_batch:
                                ep_name = "Complete Batch [Zip]"
                            elif ep_match:
                                ep_name = ep_match.group(1).title()
                            else:
                                ep_name = "Full Season"

                            if current_season not in seasons_dict:
                                seasons_dict[current_season] = {}
                            if ep_name not in seasons_dict[current_season]:
                                seasons_dict[current_season][ep_name] = []

                            for btn in buttons:
                                btn_txt = btn.get_text(strip=True)
                                meta = decode_release_metadata(context_str or txt, btn_txt, post_title)
                                target_url = btn["href"]

                                if "episode" in btn_txt.lower() or "single" in btn_txt.lower():
                                    try:
                                        hub_resp = await client.get(target_url, headers=DEFAULT_HEADERS, timeout=2.5)
                                        if hub_resp.status_code == 200:
                                            hub_soup = BeautifulSoup(hub_resp.text, "lxml")
                                            v_links = [a for a in hub_soup.find_all("a", href=True) if "vcloud" in a["href"]]
                                            for idx, v_a in enumerate(v_links, 1):
                                                ep_name = f"Episode {idx:02d}"
                                                if current_season not in seasons_dict:
                                                    seasons_dict[current_season] = {}
                                                if ep_name not in seasons_dict[current_season]:
                                                    seasons_dict[current_season][ep_name] = []

                                                if not any(q["target_url"] == v_a["href"] for q in seasons_dict[current_season][ep_name]):
                                                    seasons_dict[current_season][ep_name].append({
                                                        "resolution": meta["resolution"],
                                                        "codec": meta["codec"],
                                                        "size": meta["size"],
                                                        "audio": meta["audio"],
                                                        "target_url": v_a["href"],
                                                        "platform": "VegaMovies",
                                                        "source_origin": "VegaMovies Episode"
                                                    })
                                    except Exception:
                                        pass
                                else:
                                    already_has_res = any(q["resolution"] == meta["resolution"] for q in seasons_dict[current_season][ep_name])
                                    if not already_has_res or "v-cloud" in btn_txt.lower() or "vcloud" in btn_txt.lower():
                                        if already_has_res:
                                            seasons_dict[current_season][ep_name] = [q for q in seasons_dict[current_season][ep_name] if q["resolution"] != meta["resolution"]]

                                        seasons_dict[current_season][ep_name].append({
                                            "resolution": meta["resolution"],
                                            "codec": meta["codec"],
                                            "size": meta["size"],
                                            "audio": meta["audio"],
                                            "target_url": target_url,
                                            "platform": "VegaMovies",
                                            "source_origin": "VegaMovies Series"
                                        })
        except Exception:
            pass
        return seasons_dict

    async def fetch_series_simultaneous(self, query: str) -> dict:
        """Crawl UHDMovies and VegaMovies simultaneously across all seasons and merge."""
        # Fast series hint check: don't classify obvious standalone movie queries as series
        movie_hints = [r'\bmovie\b', r'\bfilm\b', r'\bcinema\b', r'\bpart\s*\d+\b', r'\bendgame\b', r'\binfinity war\b']
        if any(re.search(p, query, re.I) for p in movie_hints) and not re.search(r'\b(season|series|episode|s\d+)\b', query, re.I):
            return {}

        async with httpx.AsyncClient(http2=True, limits=self.limits, follow_redirects=True) as client:
            vega_task = self.fetch_series_vega(client, query)
            uhd_task = self.fetch_series_uhd(client, query)
            vega_seasons, uhd_seasons = await asyncio.gather(vega_task, uhd_task, return_exceptions=True)

        merged_seasons = {}
        for s_dict in [vega_seasons, uhd_seasons]:
            if isinstance(s_dict, dict):
                for s_name, eps in s_dict.items():
                    if s_name not in merged_seasons:
                        merged_seasons[s_name] = {}
                    for ep_name, quals in eps.items():
                        if ep_name not in merged_seasons[s_name]:
                            merged_seasons[s_name][ep_name] = []
                        for q in quals:
                            if not any(eq["resolution"] == q["resolution"] and eq["platform"] == q["platform"] for eq in merged_seasons[s_name][ep_name]):
                                merged_seasons[s_name][ep_name].append(q)

        # Ensure genuine TV series:
        has_multiple_seasons = len(merged_seasons) > 1
        has_ep_numbers = any(re.search(r'\b(Episode\s*\d+|Ep\s*\d+|E\d{1,3})\b', ep, re.I) for eps in merged_seasons.values() for ep in eps)
        is_series_query = bool(re.search(r'\b(season|series|episode|s\d+|show|anime)\b', query, re.I))
        has_batch_or_zip = any("batch" in ep.lower() or "zip" in ep.lower() for eps in merged_seasons.values() for ep in eps)

        # Genuine TV show if: multiple seasons OR explicit numbered episodes OR series keywords + batch/season links
        is_genuine_series = (has_multiple_seasons or has_ep_numbers) or (is_series_query and (has_batch_or_zip or len(merged_seasons) >= 1))

        if not is_genuine_series:
            return {}

        # Sort seasons numerically (Season 1, Season 2, Season 3, ...)
        sorted_seasons = {}
        def season_key(s_str):
            m = re.search(r'\d+', s_str)
            return int(m.group(0)) if m else 999

        for s_name in sorted(merged_seasons.keys(), key=season_key):
            sorted_seasons[s_name] = merged_seasons[s_name]

        if sorted_seasons:
            return {
                "is_series": True,
                "show_title": query.title(),
                "seasons": sorted_seasons
            }
        return {}

    async def stream_all_parallel(self, query: str, season: Optional[int] = None, episode: Optional[int] = None):
        """Simultaneous multi-source stream generator with series-first detection.

        Every source is wrapped in a per-source timeout so a single hung scraper
        (e.g. Telegram MTProto cold start) cannot stall the whole fleet response.
        """
        FLEET_SOURCE_TIMEOUT = 8.0  # seconds per source
        is_series_query = bool(season is not None or episode is not None or re.search(r'\b(season|series|episode|ep|s\d+|e\d+|\d+x\d+)\b', query, re.I))

        # Yield high-speed FastScrapers immediately (UHDMovies, 4KHDHub, HDHub4u, VegaMovies, OlaMovies, DesireMovies, DDLBase, Nyaa, Bingr)
        async with httpx.AsyncClient(http2=True, limits=self.limits, follow_redirects=True) as client:
            from fast_scrapers import FastScrapers
            fs = _FastScraperFleetAdapter(FastScrapers())

            fast_tasks = [
                fs.search_uhdmovies(client, query, season=season, episode=episode),
                fs.search_4khdhub(client, query, season=season, episode=episode),
                fs.search_hdhub4u(client, query, season=season, episode=episode),
                fs.search_vegamovies(client, query, season=season, episode=episode),
                fs.search_olamovies(client, query, season=season, episode=episode),
                fs.search_desiremovies(client, query, season=season, episode=episode),
                fs.search_ddlbase(client, query, season=season, episode=episode),
                fs.search_nyaa(client, query, season=season, episode=episode),
                fs.search_bingr(client, query, season=season, episode=episode),
                self.fetch_local_db(query)
            ]

            # If series query, also run dedicated hierarchical series crawler concurrently
            if is_series_query:
                async def run_series_crawler():
                    s_data = await self.fetch_series_simultaneous(query)
                    flat = []
                    if s_data and isinstance(s_data, dict):
                        show_name = s_data.get("show_title", query)
                        for s_name, eps in s_data.get("seasons", {}).items():
                            for ep_name, quals in eps.items():
                                for q in quals:
                                    flat.append({
                                        "platform": q.get("platform", "SeriesScraper"),
                                        "title": f"{show_name} {s_name}",
                                        "label": f"{ep_name} [{q.get('resolution', '1080P')}] ({q.get('size', 'HD')})",
                                        "episode_tag": ep_name,
                                        "resolution": q.get("resolution", "1080P"),
                                        "codec": q.get("codec", "x264"),
                                        "size": q.get("size", "N/A"),
                                        "audio": q.get("audio", "Original"),
                                        "target_url": q.get("target_url"),
                                        "source_origin": q.get("source_origin", "Series Scraper")
                                    })
                    return flat

                fast_tasks.append(run_series_crawler())

            for future in asyncio.as_completed(fast_tasks):
                try:
                    batch = await asyncio.wait_for(future, timeout=FLEET_SOURCE_TIMEOUT)
                    if batch and isinstance(batch, list) and len(batch) > 0:
                        yield batch
                except Exception:
                    pass

            # Yield secondary sources (Telegram, YouTube)
            secondary_tasks = [
                self.fetch_telegram(query),
                self.fetch_youtube(client, query)
            ]
            for future in asyncio.as_completed(secondary_tasks):
                try:
                    batch = await asyncio.wait_for(future, timeout=FLEET_SOURCE_TIMEOUT)
                    if batch and isinstance(batch, list) and len(batch) > 0:
                        yield batch
                except Exception:
                    pass

    async def fetch_local_db(self, query: str) -> list:
        res = self.db.search(query, limit=15)
        if not res.get("releases"):
            # Try with sanitized query without punctuation/year
            clean_q = clean_search_query(query)
            if clean_q != query:
                res = self.db.search(clean_q, limit=15)

        formatted = []
        for r in res.get("releases", []):
            can_title = r.get("canonical_title", query)
            if not is_strict_match(query, can_title):
                continue
            meta = decode_release_metadata(r.get("label", ""), post_title=can_title)
            formatted.append({
                "platform": "Local DB",
                "title": can_title,
                "label": r.get("label", ""),
                "resolution": r.get("resolution") or meta["resolution"],
                "codec": r.get("codec") or meta["codec"],
                "size": r.get("size") or meta["size"],
                "audio": r.get("audio") or meta["audio"],
                "target_url": r.get("target_url") or r.get("direct_url"),
                "source_origin": "Local FTS5 Database"
            })
        return formatted

    async def fetch_telegram(self, query: str) -> list:
        results = []
        try:
            tg_res = await search_telegram(self.tg_cfg["api_id"], self.tg_cfg["api_hash"], query, phone=self.tg_cfg.get("phone"))
            for r in tg_res:
                meta = decode_release_metadata(r.get("label", ""), post_title=query)
                results.append({
                    "platform": "Telegram Bot",
                    "title": meta.get("episode_tag") or query,
                    "label": r.get("label", "")[:60],
                    "resolution": r.get("resolution", "1080p"),
                    "codec": r.get("codec", "x264"),
                    "size": r.get("size", "N/A"),
                    "audio": r.get("audio", "Original"),
                    "target_url": r.get("url") or f"tg://{r.get('start_token')}",
                    "start_token": r.get("start_token"),
                    "source_origin": "Telegram MTProto"
                })
        except Exception:
            pass
        return results

    async def fetch_vegamovies(self, client: httpx.AsyncClient, query: str) -> list:
        results = []
        try:
            clean_q = clean_search_query(query)
            vega_mirror = await get_active_mirror("vegamovies")
            url = f"{vega_mirror}/ts-search.php?q={clean_q}"
            r = await client.get(url, headers=DEFAULT_HEADERS, timeout=2.5)
            if r.status_code == 200:
                hits = r.json().get("hits", [])
                for hit in hits[:5]:
                    doc = hit.get("document", {})
                    post_title = doc.get("post_title", "")
                    if is_strict_match(query, post_title):
                        permalink = doc.get("permalink", "")
                        p_url = f"{vega_mirror}{permalink}" if permalink.startswith("/") else permalink
                        p_resp = await client.get(p_url, headers=DEFAULT_HEADERS, timeout=2.5)
                        if p_resp.status_code == 200:
                            soup = BeautifulSoup(p_resp.text, "lxml")
                            for a in soup.find_all("a", href=re.compile(r'nexdrive|vcloud|fast-dl|fastdl|gdlink|hubcloud')):
                                p = a.find_parent("p")
                                prev_p = p.find_previous_sibling(["p", "h3", "h4", "h5", "div"]) if p else None
                                context_str = prev_p.get_text(" ", strip=True) if prev_p else ""
                                btn_txt = a.get_text(strip=True)
                                meta = decode_release_metadata(context_str, btn_txt, post_title)
                                results.append({
                                    "platform": "VegaMovies",
                                    "title": clean_media_title(post_title),
                                    "label": btn_txt[:60],
                                    "resolution": meta["resolution"],
                                    "codec": meta["codec"],
                                    "size": meta["size"],
                                    "audio": meta["audio"],
                                    "target_url": a["href"],
                                    "source_origin": "VegaMovies Live"
                                })
        except Exception:
            pass
        return results

    async def fetch_uhdmovies(self, client: httpx.AsyncClient, query: str) -> list:
        results = []
        try:
            clean_q = clean_search_query(query)
            clean_url = re.sub(r'\s+', '+', clean_q)
            uhd_mirror = await get_active_mirror("uhdmovies")
            url = f"{uhd_mirror}/search/{clean_url}"
            r = await client.get(url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=2.8)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "lxml")
                for art in soup.find_all("article")[:5]:
                    link = art.find("a", href=True)
                    p_title = ""
                    for h in art.find_all(["h1", "h2", "h3", "h4", "a"]):
                        t = h.get_text(strip=True) or h.get("title", "")
                        if len(t) > 5 and ("download" in t.lower() or "watch" in t.lower() or is_strict_match(query, t)):
                            p_title = t
                            break
                    if not p_title and link:
                        p_title = link.get("title", "") or link.get_text(strip=True)

                    if link and p_title and is_strict_match(query, p_title):
                        post_href = link["href"]
                        if post_href.startswith("/"):
                            post_href = f"{uhd_mirror}{post_href}"
                        p_resp = await client.get(post_href, headers=DEFAULT_HEADERS, timeout=2.5)
                        if p_resp.status_code == 200:
                            p_soup = BeautifulSoup(p_resp.text, "lxml")
                            for dl in p_soup.find_all("a", href=re.compile(r'driveseed|cloud\.unblocked|drive\.google|hubcloud|techy|naukriadda|\?sid=')):
                                p = dl.find_parent(["p", "div", "h4"])
                                prev_p = p.find_previous_sibling(["p", "h3", "h4", "h5", "div"]) if p else None
                                context_str = prev_p.get_text(" ", strip=True) if prev_p else (p.get_text(" ", strip=True) if p else "")
                                btn_txt = dl.get_text(strip=True)
                                meta = decode_release_metadata(context_str, btn_txt, p_title)
                                results.append({
                                    "platform": "UHDMovies",
                                    "title": clean_media_title(p_title),
                                    "label": f"{context_str[:30]} ({btn_txt})" if context_str else btn_txt[:60],
                                    "resolution": meta["resolution"],
                                    "codec": meta["codec"],
                                    "size": meta["size"],
                                    "audio": meta["audio"],
                                    "target_url": dl["href"],
                                    "source_origin": "UHDMovies Live"
                                })
        except Exception:
            pass
        return results

    async def fetch_olamovies(self, client: httpx.AsyncClient, query: str) -> list:
        """Fetch high-bitrate releases from OlaMovies."""
        results = []
        try:
            clean_q = clean_search_query(query)
            clean_url = re.sub(r'\s+', '+', clean_q)
            ola_mirror = await get_active_mirror("olamovies")
            url = f"{ola_mirror}/?s={clean_url}"
            r = await client.get(url, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=3.5)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "lxml")
                for art in soup.find_all("article")[:5]:
                    links = art.find_all("a", href=True)
                    for l in links:
                        t = l.get_text(strip=True) or l.get("title", "")
                        if t and len(t) > 5 and is_strict_match(query, t) and ("category" not in l["href"] and "tag" not in l["href"]):
                            post_url = l["href"]
                            if post_url.startswith("/"):
                                post_url = f"{ola_mirror}{post_url}"
                            post_title = t
                            p_resp = await client.get(post_url, headers=DEFAULT_HEADERS, timeout=3.0)
                            if p_resp.status_code == 200:
                                p_soup = BeautifulSoup(p_resp.text, "lxml")
                                seen_urls = set()
                                for a in p_soup.find_all("a", href=True):
                                    href = a["href"]
                                    btn_txt = a.get_text(strip=True)
                                    if any(dom in href for dom in ["links.ol-am.top", "links.olamovies", "olamovies.pro", "drivehub", "pixeldrain"]):
                                        if href in seen_urls:
                                            continue
                                        seen_urls.add(href)
                                        meta = decode_release_metadata("", btn_txt, post_title)
                                        results.append({
                                            "platform": "OlaMovies",
                                            "title": clean_media_title(post_title),
                                            "label": btn_txt[:60],
                                            "resolution": meta["resolution"],
                                            "codec": meta["codec"],
                                            "size": meta["size"],
                                            "audio": meta["audio"],
                                            "target_url": href,
                                            "source_origin": "OlaMovies Live"
                                        })
                            break
        except Exception:
            pass
        return results

    async def fetch_hdhub4u(self, client: httpx.AsyncClient, query: str) -> list:
        """Fetch high-bitrate releases from HDHub4u via Typesense API (< 150ms)."""
        results = []
        try:
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
            r = await client.get(api_url, params=params, headers=headers, timeout=2.5)
            if r.status_code == 200:
                hits = r.json().get("hits", [])
                for h in hits[:3]:
                    doc = h.get("document", {})
                    post_title = doc.get("post_title", "")
                    permalink = doc.get("permalink", "")
                    if is_strict_match(query, post_title) and permalink:
                        clean_path = re.sub(r'^https?://[^/]+', '', permalink)
                        live_post_url = f"{live_mirror}{clean_path}"
                        p_resp = await client.get(live_post_url, headers=headers, timeout=2.5)
                        if p_resp.status_code == 200:
                            p_soup = BeautifulSoup(p_resp.text, "lxml")
                            for dl in p_soup.find_all("a", href=re.compile(r'hubcloud|hubdrive|gamerxyt|drive|hubcdn')):
                                p = dl.find_parent(["p", "div", "h4"])
                                context_str = p.get_text(" ", strip=True) if p else ""
                                btn_txt = dl.get_text(strip=True)
                                meta = decode_release_metadata(context_str, btn_txt, post_title)
                                results.append({
                                    "platform": "HDHub4u",
                                    "title": clean_media_title(post_title),
                                    "label": btn_txt[:60],
                                    "resolution": meta["resolution"],
                                    "codec": meta["codec"],
                                    "size": meta["size"],
                                    "audio": meta["audio"],
                                    "target_url": dl["href"],
                                    "source_origin": "HDHub4u Live"
                                })
        except Exception:
            pass
        return results

    async def fetch_youtube(self, client: httpx.AsyncClient, query: str) -> list:
        """Fetch verified full movies and real anime episodes from YouTube (< 120ms)."""
        results = []
        try:
            yt_items = await self.yt.search_full_videos(client, query, max_results=4)
            for item in yt_items:
                results.append({
                    "platform": "YouTube",
                    "title": item["title"],
                    "label": f"{item['channel']} ({item['size']})",
                    "resolution": item["resolution"],
                    "codec": item["codec"],
                    "size": item["size"],
                    "audio": item["audio"],
                    "target_url": item["direct_url"],
                    "stream_url": item["direct_url"],
                    "direct_url": item["direct_url"],
                    "is_youtube": True,
                    "source_origin": f"YouTube ({item['channel']})"
                })
        except Exception:
            pass
        return results

    async def resolve_and_verify(self, target_url: str) -> dict:
        """Resolve target URL and verify EBML headers on demand."""
        t0 = time.perf_counter()
        from link_resolver import LinkResolver
        resolver = LinkResolver()

        res = await resolver.resolve_auto(target_url)
        direct_url = res.get("direct_url", target_url)
        v = await self.verifier.verify(direct_url)

        if not v.get("valid") and direct_url != target_url:
            res = await resolver.resolve_auto(target_url)
            direct_url = res.get("direct_url", target_url)
            v = await self.verifier.verify(direct_url)

        return {
            "resolved": res,
            "direct_url": direct_url,
            "verification": v,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
        }

if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "House of the Dragon"
    dispatcher = ParallelMediaDispatcher()
    print(f"Simultaneous search for: '{q}'...")

    async def test():
        async for batch in dispatcher.stream_all_parallel(q):
            if batch and isinstance(batch[0], dict) and batch[0].get("is_series"):
                s_data = batch[0]
                print(f"\n[+] TV Series: '{s_data.get('show_title')}'")
                for s_name, eps in s_data.get("seasons", {}).items():
                    print(f"\n=== {s_name.upper()} ===")
                    for ep_name, quals in eps.items():
                        platforms = list(set([q['platform'] for q in quals]))
                        qual_str = ", ".join([f"{q['resolution']} ({q['size']})" for q in quals])
                        print(f"  * {ep_name:<26} | Available: {qual_str:<25} | Sources: {', '.join(platforms)}")

    asyncio.run(test())
