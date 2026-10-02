"""
Background Library Crawler & Auto-Indexer (Zero-Latency Impact)
Crawls catalog archives and sitemaps across platforms and saves them into local media_db.py.
"""

import sys
import os
import time
import asyncio
import re
import httpx
from bs4 import BeautifulSoup

from media_db import MediaDB
from domain_resolver import get_active_mirror_sync

# Dynamic Sync Mirror Endpoints
VEGA_MIRROR = get_active_mirror_sync("vegamovies")
VEGA_SEARCH = f"{VEGA_MIRROR}/ts-search.php"
OLA_MIRROR = get_active_mirror_sync("olamovies")
UHD_MIRROR = get_active_mirror_sync("uhdmovies")
HDHUB_MIRROR = get_active_mirror_sync("hdhub4u")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
}

class LibraryIndexer:
    def __init__(self):
        self.db = MediaDB()
        self.limits = httpx.Limits(max_keepalive_connections=20, max_connections=40)

    async def index_vegamovies_recent(self, client: httpx.AsyncClient, pages: int = 3):
        """Crawl VegaMovies recent popular posts and index."""
        print("[*] Indexing VegaMovies...")
        indexed_count = 0
        try:
            # Query popular trending tags / recent
            queries = ["2026", "2025", "2024", "Dual Audio", "Hindi", "Malayalam", "Tamil", "Anime"]
            for q in queries:
                r = await client.get(f"{VEGA_SEARCH}?q={q}", headers=DEFAULT_HEADERS, timeout=3.0)
                if r.status_code == 200:
                    data = r.json()
                    hits = data.get("hits", [])
                    for h in hits:
                        doc = h.get("document", {})
                        post_title = doc.get("post_title", "")
                        permalink = doc.get("permalink", "")
                        if permalink and post_title:
                            post_url = f"{VEGA_MIRROR}{permalink}" if permalink.startswith("/") else permalink

                            # Clean title
                            year_m = re.search(r'\((\d{4})\)', post_title)
                            year = int(year_m.group(1)) if year_m else None
                            clean_t = re.sub(r'\b(Download|Movie|Dual Audio|Hindi|Tamil|Telugu|English|BluRay|WEB-DL|480p|720p|1080p|2160p|4K|\d+p|\d+MB|\d+GB)\b', '', post_title, flags=re.I).strip()
                            clean_t = re.sub(r'[\(\)\[\]\|:~-]', ' ', clean_t)
                            clean_t = re.sub(r'\s+', ' ', clean_t).strip()

                            title_data = {
                                "canonical_title": clean_t,
                                "formatted_title": f"{clean_t} ({year})" if year else clean_t,
                                "year": year,
                                "poster_url": doc.get("post_thumbnail", ""),
                                "genre": ", ".join(doc.get("category", [])[:3]),
                                "rating": 0.0
                            }

                            # Extract release links from post page
                            try:
                                p_resp = await client.get(post_url, headers=DEFAULT_HEADERS, timeout=2.5)
                                if p_resp.status_code == 200:
                                    p_soup = BeautifulSoup(p_resp.text, "lxml")
                                    buttons = p_soup.find_all("a", href=re.compile(r'nexdrive|vcloud|fast-dl|v-cloud'))
                                    releases = []
                                    for btn in buttons:
                                        label = btn.get_text(strip=True) or btn.find_parent().get_text(strip=True)
                                        href = btn["href"]
                                        res_m = re.search(r'(2160p|4K|1080p|720p|480p)', label, re.I)
                                        size_m = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', label, re.I)
                                        releases.append({
                                            "platform": "VegaMovies",
                                            "label": label[:60],
                                            "resolution": res_m.group(1).upper() if res_m else "1080p",
                                            "codec": "x264" if "x264" in label else "HEVC",
                                            "size": size_m.group(1).upper() if size_m else "Unknown",
                                            "audio": "Dual Audio" if "Dual" in label or "Hindi" in label else "Original",
                                            "target_url": href
                                        })
                                    if releases:
                                        self.db.upsert_title(title_data, releases)
                                        indexed_count += 1
                            except Exception:
                                pass
        except Exception as e:
            print("Vega index error:", e)
        print(f"[+] Indexed {indexed_count} titles from VegaMovies.")

    async def index_olamovies_recent(self, client: httpx.AsyncClient, pages: int = 2):
        """Crawl OlaMovies popular categories and index."""
        print("[*] Indexing OlaMovies...")
        indexed_count = 0
        try:
            r = await client.get(OLA_MIRROR, headers=DEFAULT_HEADERS, timeout=3.0)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "lxml")
                articles = soup.find_all("article") or soup.find_all("div", class_=re.compile(r'post|item'))
                for art in articles[:15]:
                    link = art.find("a", href=True)
                    title_elem = art.find(["h2", "h3", "a"])
                    if link and title_elem:
                        post_url = link["href"]
                        raw_title = title_elem.get_text(strip=True)
                        year_m = re.search(r'\((\d{4})\)', raw_title)
                        year = int(year_m.group(1)) if year_m else None
                        clean_t = re.sub(r'\b(Download|Season|\d+|720p|1080p|2160p|4K|WEB-DL|BluRay|x265|HEVC|Dual Audio|Hindi|Esub)\b', '', raw_title, flags=re.I).strip()
                        clean_t = re.sub(r'[\(\)\[\]\|:~-]', ' ', clean_t)
                        clean_t = re.sub(r'\s+', ' ', clean_t).strip()

                        title_data = {
                            "canonical_title": clean_t,
                            "formatted_title": f"{clean_t} ({year})" if year else clean_t,
                            "year": year,
                            "genre": "Feature"
                        }

                        try:
                            p_resp = await client.get(post_url, headers=DEFAULT_HEADERS, timeout=2.5)
                            if p_resp.status_code == 200:
                                p_soup = BeautifulSoup(p_resp.text, "lxml")
                                dls = p_soup.find_all("a", href=re.compile(r'links\.ol-am|links\.olamovies'))
                                releases = []
                                for dl in dls:
                                    label = dl.get_text(strip=True) or dl.find_parent().get_text(strip=True)
                                    href = dl["href"]
                                    res_m = re.search(r'(2160p|4K|1080p|720p|480p)', label, re.I)
                                    size_m = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', label, re.I)
                                    releases.append({
                                        "platform": "OlaMovies",
                                        "label": label[:60],
                                        "resolution": res_m.group(1).upper() if res_m else "1080p",
                                        "codec": "HEVC 10-bit" if "hevc" in label.lower() or "x265" in label.lower() else "x264",
                                        "size": size_m.group(1).upper() if size_m else "Unknown",
                                        "audio": "Multi Audio" if "multi" in label.lower() or "hindi" in label.lower() else "Original",
                                        "target_url": href
                                    })
                                if releases:
                                    self.db.upsert_title(title_data, releases)
                                    indexed_count += 1
                        except Exception:
                            pass
        except Exception as e:
            print("Ola index error:", e)
        print(f"[+] Indexed {indexed_count} titles from OlaMovies.")

    async def seed_popular_catalog(self, client: httpx.AsyncClient):
        """Seed top 25 global and regional hits into local database."""
        seed_titles = [
            # Malayalam
            "Manichitrathazhu (1993)",
            "Spadikam (1995)",
            "Manjummel Boys (2024)",
            "Lucifer (2019)",
            "Aavesham (2024)",
            "The Goat Life (Aadujeevitham) (2024)",
            "Bheeshma Parvam (2022)",
            "Drishyam (2013)",
            "Premam (2015)",
            "Kumbalangi Nights (2019)",
            # Bollywood / Pan-India
            "Jawan (2023)",
            "Dangal (2016)",
            "Stree 2 (2024)",
            "Kalki 2898 AD (2024)",
            "3 Idiots (2009)",
            "Sholay (1975)",
            "K.G.F: Chapter 2 (2022)",
            "Pushpa 2: The Rule (2024)",
            # Hollywood 4K
            "Interstellar (2014)",
            "The Dark Knight (2008)",
            "House of the Dragon (2022)",
            "Oppenheimer (2023)",
            "Inception (2010)",
            "Avatar: The Way of Water (2022)",
            # Anime
            "Demon Slayer: Mugen Train (2020)",
            "Death Note (2006)",
            "Jujutsu Kaisen 0 (2021)",
            "Pokémon the Movie: Hoopa and the Clash of Ages (2015)"
        ]

        for full_name in seed_titles:
            year_m = re.search(r'\((\d{4})\)', full_name)
            year = int(year_m.group(1)) if year_m else None
            canonical = re.sub(r'\s*\(\d{4}\)', '', full_name).strip()

            title_data = {
                "canonical_title": canonical,
                "formatted_title": full_name,
                "year": year,
                "genre": "Movie"
            }
            # Add seed placeholder releases so local FTS matches immediately
            releases = [
                {
                    "platform": "UHDMovies / VegaMovies",
                    "label": f"{canonical} 2160p 4K UHD Remux HDR10+",
                    "resolution": "2160P",
                    "codec": "HEVC 10-bit",
                    "size": "14.5 GB",
                    "audio": "English Atmos / Multi",
                    "target_url": f"https://auto.seed/{canonical.lower().replace(' ', '-')}-4k"
                },
                {
                    "platform": "VegaMovies",
                    "label": f"{canonical} 1080p Full HD BluRay",
                    "resolution": "1080P",
                    "codec": "x264",
                    "size": "4.8 GB",
                    "audio": "Dual Audio",
                    "target_url": f"https://auto.seed/{canonical.lower().replace(' ', '-')}-1080p"
                }
            ]
            self.db.upsert_title(title_data, releases)

    async def run_full_sync(self):
        t0 = time.perf_counter()
        async with httpx.AsyncClient(http2=True, limits=self.limits, follow_redirects=True) as client:
            await self.seed_popular_catalog(client)
            await asyncio.gather(
                self.index_vegamovies_recent(client),
                self.index_olamovies_recent(client),
                return_exceptions=True
            )
        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        stats = self.db.get_stats()
        print(f"\n[+] Library Index Sync Complete in {elapsed}ms:")
        print(f"  • Total Titles Indexed   : {stats['total_titles']}")
        print(f"  • Total Releases Stored  : {stats['total_releases']}")

if __name__ == "__main__":
    indexer = LibraryIndexer()
    asyncio.run(indexer.run_full_sync())
