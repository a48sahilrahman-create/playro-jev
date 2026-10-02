"""
Ultra-Fast (< 100ms - 200ms) Multi-Layer Title Resolution & Auto-Spell Checking Engine
Features:
  1. Instant in-memory popular catalog match (< 0.1ms) with fuzzy word & token matching
  2. Number & Roman numeral mapping (e.g. 'dune part 2' -> 'Dune: Part Two (2024)')
  3. Parallel async IMDb Suggestion API & Google Suggestion API (< 100ms)
  4. Word-token / Prefix fallback on IMDb (handles phrase typos like 'kesu ee nadinte...' -> 'Keshu Ee Veedinte Nadhan')
  5. Web snippet spell-check extraction (< 200ms)
  6. Multi-source suggestion merger & 'Did You Mean?' generator
  7. Zero-crash fault isolation (silently swallows any network/parsing failure)
"""

import sys
import time
import asyncio
import difflib
import json
import re
import urllib.parse
import httpx
from bs4 import BeautifulSoup

# In-memory fast catalog fallback of common global, Indian, and regional titles
POPULAR_CATALOG = [
    # Global Blockbusters & Franchises
    "Avengers: Endgame (2019)",
    "Avengers: Infinity War (2018)",
    "The Avengers (2012)",
    "Avengers: Age of Ultron (2015)",
    "Oppenheimer (2023)",
    "Interstellar (2014)",
    "Inception (2010)",
    "Dune: Part One (2021)",
    "Dune: Part Two (2024)",
    "The Dark Knight (2008)",
    "The Dark Knight Rises (2012)",
    "Batman Begins (2005)",
    "Avatar (2009)",
    "Avatar: The Way of Water (2022)",
    "Deadpool & Wolverine (2024)",
    "Gladiator II (2024)",
    "Gladiator (2000)",
    "The Lord of the Rings: The Fellowship of the Ring (2001)",
    "The Lord of the Rings: The Two Towers (2002)",
    "The Lord of the Rings: The Return of the King (2003)",
    "Spider-Man: No Way Home (2021)",
    "Spider-Man: Across the Spider-Verse (2023)",
    "Spider-Man: Into the Spider-Verse (2018)",
    "Top Gun: Maverick (2022)",
    "The Batman (2022)",
    "Titanic (1997)",
    "The Matrix (1999)",
    "Fight Club (1999)",
    "Pulp Fiction (1994)",
    "Forrest Gump (1994)",
    "The Shawshank Redemption (1994)",
    "The Godfather (1972)",
    "Coherence (2013)",
    "Stolen Girl (2025)",

    # Top Global TV Series & Anime
    "House of the Dragon (2022)",
    "Game of Thrones (2011)",
    "Stranger Things (2016)",
    "Breaking Bad (2008)",
    "Better Call Saul (2015)",
    "The Boys (2019)",
    "Severance (2022)",
    "Shogun (2024)",
    "The Last of Us (2023)",
    "Succession (2018)",
    "Peaky Blinders (2013)",
    "Attack on Titan (2013)",
    "Death Note (2006)",
    "Jujutsu Kaisen (2020)",
    "Demon Slayer: Kimetsu no Yaiba (2019)",
    "Hajime no Ippo (2000)",
    "Naruto Shippuden (2007)",
    "One Piece (1999)",
    "Vinland Saga (2019)",
    "Solo Leveling (2024)",

    # Malayalam Classics & Hits
    "Keshu Ee Veedinte Nadhan (2021)",
    "Chemmeen (1965)",
    "Ee Parakkum Thalika (2001)",
    "Manichitrathazhu (1993)",
    "Drishyam (2013)",
    "Drishyam 2 (2021)",
    "Premam (2015)",
    "Kumbalangi Nights (2019)",
    "Lucifer (2019)",
    "Bheeshma Parvam (2022)",
    "Aavesham (2024)",
    "Manjummel Boys (2024)",
    "Bramayugam (2024)",
    "The Goat Life (Aadujeevitham) (2024)",
    "Kishkindha Kaandam (2024)",
    "ARM (Ajayante Randam Moshanam) (2024)",
    "Balan The Boy (2026)",
    "Sandesham (1991)",
    "Spadikam (1995)",
    "Devasuram (1993)",
    "Nadodikkattu (1987)",
    "Kilukkam (1991)",
    "Chithram (1988)",
    "Oru Vadakkan Veeragatha (1989)",
    "Yodha (1992)",
    "In Harihar Nagar (1990)",
    "Godfather (1991)",
    "Meesha Madhavan (2002)",
    "Kalyanaraman (2002)",
    "C.I.D. Moosa (2003)",
    "Pulimurugan (2016)",
    "Kaduva (2022)",
    "Kummatty (1979)",
    "Thondimuthalum Driksakshiyum (2017)",
    "Minnal Murali (2021)",
    "Joji (2021)",
    "Great Indian Kitchen (2021)",

    # Bollywood & Pan-India Blockbusters
    "Stree 2 (2024)",
    "Kalki 2898 AD (2024)",
    "Jawan (2023)",
    "Pathaan (2023)",
    "Animal (2023)",
    "RRR (2022)",
    "K.G.F: Chapter 1 (2018)",
    "K.G.F: Chapter 2 (2022)",
    "Pushpa: The Rise (2021)",
    "Pushpa 2: The Rule (2024)",
    "Baahubali: The Beginning (2015)",
    "Baahubali 2: The Conclusion (2017)",
    "Dangal (2016)",
    "Sholay (1975)",
    "3 Idiots (2009)",
    "Mirzapur (2018)",
    "Panchayat (2020)",
    "Sacred Games (2018)",
    "Tumbbad (2018)",
    "Kantara (2022)",
    "Leo (2023)",
    "Jailer (2023)",
    "Vikram (2022)",
    "Salaar: Part 1 - Ceasefire (2023)",
    "Ponniyin Selvan: Part I (2022)",
    "Ponniyin Selvan: Part II (2023)",
]

NUM_WORD_MAP = {
    "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten",
    "i": "one", "ii": "two", "iii": "three", "iv": "four", "v": "five", "vi": "six", "vii": "seven", "viii": "eight", "ix": "nine", "x": "ten",
    "part 1": "part one", "part 2": "part two", "part 3": "part three", "part 4": "part four",
    "chapter 1": "chapter one", "chapter 2": "chapter two", "chapter 3": "chapter three"
}


def _normalize_title_text(text: str) -> str:
    """Normalize text for fuzzy comparison by removing punctuation and normalizing numerals."""
    if not text:
        return ""
    t = re.sub(r'\s*\(\d{4}\)', '', text)
    t = re.sub(r'[^a-zA-Z0-9\s]', ' ', t)
    words = t.lower().split()
    norm_words = [NUM_WORD_MAP.get(w, w) for w in words]
    return re.sub(r'\s+', ' ', " ".join(norm_words)).strip()


class TitleResolver:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9"
        }

    async def _query_imdb_suggest(self, client: httpx.AsyncClient, query: str) -> list:
        """Query IMDb Suggestion API: v3.sg.media-imdb.com (< 120ms)."""
        clean = re.sub(r'[^a-zA-Z0-9\s]', ' ', query).strip().lower()
        clean = re.sub(r'\s+', '_', clean)
        if not clean:
            return []

        urls = [
            f"https://v3.sg.media-imdb.com/suggestion/x/{clean}.json"
        ]
        if clean[0:1].isalnum():
            urls.append(f"https://v3.sg.media-imdb.com/suggestion/{clean[0]}/{clean}.json")

        for url in urls:
            try:
                resp = await client.get(url, timeout=0.20)
                if resp.status_code == 200:
                    data = resp.json()
                    results = []
                    for item in data.get("d", []):
                        title = item.get("l")
                        year = item.get("y")
                        q_type = item.get("q", "")
                        if title and q_type in ["feature", "TV series", "TV mini-series", "movie", "video", "TV special"]:
                            results.append({
                                "title": title,
                                "year": year,
                                "formatted": f"{title} ({year})" if year else title,
                                "type": q_type,
                                "source": "imdb"
                            })
                    if results:
                        return results
            except Exception:
                continue
        return []

    async def _query_google_suggest(self, client: httpx.AsyncClient, query: str) -> list:
        """Query Google Chrome Suggest API (< 100ms)."""
        encoded = urllib.parse.quote_plus(f"{query.strip()} movie")
        url = f"https://suggestqueries.google.com/complete/search?client=chrome&q={encoded}"
        try:
            resp = await client.get(url, timeout=0.20)
            if resp.status_code == 200:
                data = resp.json()
                suggestions = data[1] if len(data) > 1 else []
                results = []
                for s in suggestions[:5]:
                    clean = re.sub(r'\b(movie|full movie|download|film|cast|songs|release date|watch online|hindi dubbed|tamil dubbed|malayalam dubbed|trailer|review|link|budget|collection|in order|order|movies in order|differences|difference|game|wiki|wikipedia|all parts)\b', '', s, flags=re.I).strip()
                    clean = re.sub(r'\s+', ' ', clean)
                    if clean and len(clean) >= 2 and not any(k in clean.lower() for k in ["difference", "in order"]):
                        results.append({
                            "title": clean.title(),
                            "formatted": clean.title(),
                            "source": "google"
                        })
                return results
        except Exception:
            pass
        return []

    async def _query_web_snippet_search(self, client: httpx.AsyncClient, query: str) -> list:
        """Fallback web snippet extractor for heavy typos (< 200ms)."""
        clean = urllib.parse.quote_plus(f"{query.strip()} movie")
        url = f"https://html.duckduckgo.com/html/?q={clean}"
        results = []
        try:
            resp = await client.get(url, timeout=0.25)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                for snippet in soup.find_all('a', class_='result__snippet')[:3]:
                    text = snippet.get_text(strip=True)
                    m = re.search(r'([A-Z][a-zA-Z0-9\s:’\'-]+?)\s*(?:\((\d{4})\)|\s+is an?\s+(\d{4})|:\s+Directed by)', text)
                    if m:
                        raw_title = m.group(1).strip()
                        raw_title = re.sub(r'([a-z])([A-Z])', r'\1 \2', raw_title)
                        year = m.group(2) or m.group(3)
                        if len(raw_title) > 3 and not any(w in raw_title.lower() for w in ['wikipedia', 'youtube', 'watch', 'download', 'enjoy', 'director']):
                            fmt = f"{raw_title} ({year})" if year else raw_title
                            if fmt not in [r['formatted'] for r in results]:
                                results.append({
                                    "title": raw_title,
                                    "year": int(year) if year else None,
                                    "formatted": fmt,
                                    "source": "web_search"
                                })
        except Exception:
            pass
        return results

    def _query_local_catalog(self, query: str) -> list:
        """Fast in-memory fuzzy match (< 0.2ms) with token, numeral and similarity matching."""
        clean_q = _normalize_title_text(query)
        if not clean_q:
            return []

        results = []
        seen = set()
        stop_words = {'the', 'a', 'an', 'of', 'in', 'and', 'to', 'for', 'part', 'chapter', 'movie', 'series', 'season'}

        q_words = [w for w in clean_q.split() if w not in stop_words]
        if not q_words:
            q_words = clean_q.split()

        for original in POPULAR_CATALOG:
            norm_orig = _normalize_title_text(original)
            year_match = re.search(r'\((\d{4})\)', original)
            title_name = re.sub(r'\s*\(\d{4}\)', '', original).strip()

            ratio = difflib.SequenceMatcher(None, clean_q, norm_orig).ratio()

            orig_words = [w for w in norm_orig.split() if w not in stop_words]
            if not orig_words:
                orig_words = norm_orig.split()

            word_match_scores = []
            for qw in q_words:
                best_w = max([difflib.SequenceMatcher(None, qw, ow).ratio() for ow in orig_words]) if orig_words else 0
                word_match_scores.append(best_w)

            avg_word_score = sum(word_match_scores) / max(len(word_match_scores), 1)
            has_keyword_match = any(qw in orig_words or any(difflib.SequenceMatcher(None, qw, ow).ratio() >= 0.85 for ow in orig_words) for qw in q_words)

            if clean_q == norm_orig or (ratio >= 0.80 and has_keyword_match) or (avg_word_score >= 0.85 and has_keyword_match):
                score = max(ratio, avg_word_score)
                if original not in seen:
                    seen.add(original)
                    results.append({
                        "title": title_name,
                        "year": int(year_match.group(1)) if year_match else None,
                        "formatted": original,
                        "score": score,
                        "source": "local_catalog"
                    })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:4]

    async def resolve(self, query: str) -> dict:
        """
        Ultra-fast title resolution and typo check with multi-layer recovery (< 200ms).
        Guarantees zero crashes and silent error fallback.
        """
        t0 = time.perf_counter()
        if not query or not str(query).strip():
            return {
                "status": "NO_MATCH",
                "canonical_title": "",
                "year": None,
                "formatted": "",
                "suggestions": [],
                "elapsed_ms": 0.0
            }

        raw_query = str(query).strip()
        norm_raw = _normalize_title_text(raw_query)
        if not norm_raw:
            return {
                "status": "NO_MATCH",
                "canonical_title": "",
                "year": None,
                "formatted": "",
                "suggestions": [],
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
            }

        clean_input = re.sub(r'\b(movie|full movie|download|series|all episodes|season\s*\d+|episodes?\s*\d+|s\d+\s*e\d+|s\d+|\d+x\d+|e\d+|ep\d+)\b', '', raw_query, flags=re.I).strip()
        if not clean_input:
            clean_input = raw_query

        # Layer 1: Check local catalog exact match (< 0.1ms)
        norm_clean = _normalize_title_text(clean_input)
        for cat_item in POPULAR_CATALOG:
            norm_cat = _normalize_title_text(cat_item)
            cat_name = re.sub(r'\s*\(\d{4}\)', '', cat_item).strip()
            if norm_clean == norm_cat or norm_clean == _normalize_title_text(cat_name):
                year_match = re.search(r'\((\d{4})\)', cat_item)
                elapsed = (time.perf_counter() - t0) * 1000
                return {
                    "status": "EXACT_MATCH",
                    "canonical_title": cat_name,
                    "year": int(year_match.group(1)) if year_match else None,
                    "formatted": cat_item,
                    "suggestions": [],
                    "elapsed_ms": round(elapsed, 2)
                }

        local_fuzzy = self._query_local_catalog(clean_input)

        # Fast path: If exact normalized match or very high confidence local fuzzy match (>=0.90 score), return immediately (< 1ms)
        if local_fuzzy and local_fuzzy[0]["score"] >= 0.90:
            top = local_fuzzy[0]
            top_clean = re.sub(r'\s*\(\d{4}\)', '', top["formatted"]).strip()
            norm_top = _normalize_title_text(top_clean)

            elapsed = (time.perf_counter() - t0) * 1000
            if norm_clean == norm_top:
                return {
                    "status": "EXACT_MATCH",
                    "canonical_title": top.get("title", top_clean),
                    "year": top.get("year"),
                    "formatted": top["formatted"],
                    "suggestions": [],
                    "elapsed_ms": round(elapsed, 2)
                }
            else:
                return {
                    "status": "SUGGESTIONS_FOUND",
                    "original_query": raw_query,
                    "canonical_title": clean_input.title(),
                    "year": None,
                    "formatted": clean_input.title(),
                    "suggestions": [f["formatted"] for f in local_fuzzy[:4]],
                    "elapsed_ms": round(elapsed, 2)
                }

        # Layer 2: Query APIs concurrently using async connection pool with tight timeouts
        imdb_res = []
        google_res = []
        try:
            limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
            async with httpx.AsyncClient(http2=True, limits=limits, headers=self.headers) as client:
                imdb_task = self._query_imdb_suggest(client, clean_input)
                google_task = self._query_google_suggest(client, clean_input)
                res_imdb, res_google = await asyncio.gather(imdb_task, google_task, return_exceptions=True)

                if isinstance(res_imdb, list):
                    imdb_res = res_imdb
                if isinstance(res_google, list):
                    google_res = res_google

                # Layer 3: If IMDb & Google suggestions are empty on a multi-word phrase, try prefix / web snippet
                if not imdb_res and not google_res and not local_fuzzy:
                    words = clean_input.split()
                    if len(words) > 1:
                        prefix_query = " ".join(words[:2])
                        try:
                            prefix_res = await self._query_imdb_suggest(client, prefix_query)
                            if prefix_res:
                                imdb_res.extend(prefix_res)
                        except Exception:
                            pass

                    try:
                        web_res = await self._query_web_snippet_search(client, clean_input)
                        google_res.extend(web_res)
                    except Exception:
                        pass
        except Exception:
            pass

        # Merge suggestions (prioritize Local Fuzzy if available, then IMDb, then Google/Web)
        candidates = []
        seen = set()

        for item in local_fuzzy + imdb_res + google_res:
            key = item["formatted"].lower()
            if key not in seen:
                seen.add(key)
                candidates.append(item)

        elapsed = (time.perf_counter() - t0) * 1000

        if candidates:
            top = candidates[0]
            top_clean = re.sub(r'\s*\(\d{4}\)', '', top["formatted"]).strip()
            norm_top = _normalize_title_text(top_clean)

            if norm_clean == norm_top:
                return {
                    "status": "EXACT_MATCH",
                    "canonical_title": top.get("title", top_clean),
                    "year": top.get("year"),
                    "formatted": top["formatted"],
                    "suggestions": [],
                    "elapsed_ms": round(elapsed, 2)
                }
            else:
                # Typo detected or suggested alternate spelling (preserve user's clean query as canonical_title!)
                unique_suggestions = []
                for c in candidates:
                    fmt = c["formatted"]
                    if fmt not in unique_suggestions:
                        unique_suggestions.append(fmt)

                return {
                    "status": "SUGGESTIONS_FOUND",
                    "original_query": raw_query,
                    "canonical_title": clean_input.title(),
                    "year": None,
                    "formatted": clean_input.title(),
                    "suggestions": unique_suggestions[:4],
                    "elapsed_ms": round(elapsed, 2)
                }

        # Fallback if no suggestions found (non-existent title or random string)
        return {
            "status": "NO_MATCH",
            "canonical_title": clean_input.title(),
            "year": None,
            "formatted": clean_input.title(),
            "suggestions": [],
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)
        }


if __name__ == "__main__":
    test_query = sys.argv[1] if len(sys.argv) > 1 else "kesu ee nadinte abhimanam"
    resolver = TitleResolver()
    result = asyncio.run(resolver.resolve(test_query))
    print(json.dumps(result, indent=2))
