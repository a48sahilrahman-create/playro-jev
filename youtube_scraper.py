"""
YouTube Full Movie & Anime Fast Stream Discoverer (< 120ms)
Direct InnerTube API interaction in pure async Python:
  - Sub-100ms HTTP/2 queries without browser overhead.
  - Multi-tier duration filtering (Full Movies >= 40 mins, Series/Anime >= 18 mins).
  - Anti-junk regex rejection (trailers, clips, reactions, recaps, songs, podcasts, shorts).
  - High-precision fuzzy relevance matching.
"""

import sys
import time
import asyncio
import re
import json
import difflib
import httpx

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.youtube.com",
    "Referer": "https://www.youtube.com/"
}

INNERTUBE_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"

JUNK_REGEX = re.compile(
    r'\b(trailer|teaser|review|reaction|recap|explaining|explained|scene|scenes|clip|clips|'
    r'shorts|soundtrack|ost|bgm|song|songs|interview|making\s+of|behind\s+the\s+scenes|'
    r'status|whatsapp\s+status|parody|spoof|tribute|edit|fmv|amv|summary|ending\s+explained|'
    r'breakdown|mistakes|facts|podcast|gameplay|walkthrough|teaser\s+trailer)\b',
    re.I
)

SERIES_ANIME_HINTS = re.compile(
    r'\b(episode|ep\s*\d+|s\d+\s*e\d+|season\s*\d+|part\s*\d+|anime|ova|ona)\b',
    re.I
)

class YouTubeScraper:
    def __init__(self):
        self.limits = httpx.Limits(max_keepalive_connections=20, max_connections=40)

    @staticmethod
    def parse_duration_seconds(duration_str: str) -> int:
        """Convert 'HH:MM:SS' or 'MM:SS' into total seconds."""
        if not duration_str:
            return 0
        try:
            parts = [int(p) for p in duration_str.strip().split(":") if p.strip().isdigit()]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            elif len(parts) == 2:
                return parts[0] * 60 + parts[1]
            elif len(parts) == 1:
                return parts[0]
        except Exception:
            return 0
        return 0

    @staticmethod
    def format_duration(seconds: int) -> str:
        """Format seconds into readable string like '1h 45m' or '24m'."""
        if seconds <= 0:
            return "N/A"
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"

    @staticmethod
    def clean_title(title: str, query: str = "") -> str:
        """Clean noise tags from YouTube title while preserving best segment matching query."""
        t = re.sub(r'\[.*?\]|\(.*?\)', '', title)
        t = re.sub(r'(?i)\b(full movie|full hd|4k ultra hd|1080p|720p|malayalam movie|hindi movie|tamil movie|telugu movie|english movie|kannada movie|blockbuster movie|superhit movie|hd|free movie|official movie|restored)\b', '', t)

        if "|" in t and query:
            segments = [s.strip() for s in t.split("|") if s.strip()]
            q_words = [w.lower() for w in re.findall(r'\w+', query) if len(w) > 2 and w.lower() not in ["movie", "full", "hd", "free", "download", "watch"]]
            best_seg = segments[0]
            best_score = 0
            for seg in segments:
                seg_lower = seg.lower()
                if q_words and all(qw in seg_lower for qw in q_words):
                    best_seg = seg
                    best_score = 100
                    break
                overlap = sum(1 for qw in q_words if qw in seg_lower)
                if overlap > best_score:
                    best_score = overlap
                    best_seg = seg
            if best_score > 0:
                t = best_seg
        else:
            t = re.sub(r'\s*\|\s*.*$', '', t)

        t = re.sub(r'\s+', ' ', t).strip()
        return t

    def is_relevant(self, query: str, video_title: str) -> bool:
        """Check if video title strictly matches query with year verification."""
        q_norm = re.sub(r'[^a-zA-Z0-9\s]', ' ', query.lower())
        t_norm = re.sub(r'[^a-zA-Z0-9\s]', ' ', video_title.lower())

        # Year cross-check: if user queried a specific year (e.g. 2026, 2024, 1979), enforce year in title or reject older ones
        q_years = re.findall(r'\b(19\d\d|20\d\d)\b', q_norm)
        t_years = re.findall(r'\b(19\d\d|20\d\d)\b', t_norm)
        if q_years:
            target_year = q_years[0]
            if t_years and target_year not in t_years:
                # Video has a different year tag (e.g. 2001 or 1968 instead of 2026) -> REJECT
                return False

        q_words = [w for w in q_norm.split() if len(w) > 1 and w not in ["movie", "full", "hd", "free", "download", "watch"]]
        if not q_words:
            return True

        matches = sum(1 for w in q_words if w in t_norm)
        if matches / len(q_words) >= 0.70:
            return True

        ratio = difflib.SequenceMatcher(None, " ".join(q_words), t_norm).ratio()
        return ratio >= 0.60

    async def search_full_videos(self, client: httpx.AsyncClient, query: str, max_results: int = 5) -> list:
        """
        Execute sub-100ms InnerTube search and filter for full-length movies / anime episodes.
        """
        t0 = time.perf_counter()
        results = []

        is_series_or_anime = bool(SERIES_ANIME_HINTS.search(query))
        min_duration_seconds = 1080 if is_series_or_anime else 2400 # 18 mins for episodes, 40 mins for movies

        # Search query enhancement for full movie discovery
        search_query = query
        if not is_series_or_anime and not any(k in query.lower() for k in ["full movie", "movie", "cinema", "film"]):
            search_query = f"{query} full movie"

        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240101.00.00",
                    "hl": "en",
                    "gl": "IN"
                }
            },
            "query": search_query
        }

        try:
            resp = await client.post(INNERTUBE_URL, json=payload, headers=DEFAULT_HEADERS, timeout=4.0)
            if resp.status_code != 200:
                return []

            data = resp.json()
            contents = []

            # Drill into section contents
            try:
                sections = data.get("contents", {}).get("twoColumnSearchResultsRenderer", {}).get("primaryContents", {}).get("sectionListRenderer", {}).get("contents", [])
                for s in sections:
                    item_section = s.get("itemSectionRenderer", {}).get("contents", [])
                    contents.extend(item_section)
            except Exception:
                pass

            for item in contents:
                vr = item.get("videoRenderer")
                if not vr:
                    continue

                video_id = vr.get("videoId")
                if not video_id:
                    continue

                # Title extraction
                title_runs = vr.get("title", {}).get("runs", [])
                raw_title = "".join([r.get("text", "") for r in title_runs]) if title_runs else vr.get("title", {}).get("simpleText", "")
                if not raw_title:
                    continue

                # Anti-junk rejection regex
                if JUNK_REGEX.search(raw_title):
                    continue

                # Duration extraction & filtering
                length_text = vr.get("lengthText", {}).get("simpleText", "")
                if not length_text:
                    # Might be a live stream or invalid video
                    continue

                duration_sec = self.parse_duration_seconds(length_text)
                if duration_sec < min_duration_seconds:
                    # Skip short clips, trailers, scene snippets
                    continue

                # Strict query relevance check
                if not self.is_relevant(query, raw_title):
                    continue

                # Channel / Owner info
                owner_runs = vr.get("ownerText", {}).get("runs", [])
                channel_name = owner_runs[0].get("text", "YouTube") if owner_runs else "YouTube"

                # Badges / Resolution
                badges = [b.get("metadataBadgeRenderer", {}).get("label", "") for b in vr.get("badges", [])]
                is_4k = any("4k" in b.lower() for b in badges)
                is_hd = is_4k or any("hd" in b.lower() or "cc" in b.lower() for b in badges)

                resolution = "2160p 4K UHD" if is_4k else ("1080p Full HD" if is_hd else "720p HD")

                # Audio / Language detection from title or channel
                audio_lang = "Original / Stereo"
                title_lower = raw_title.lower()
                if "malayalam" in title_lower:
                    audio_lang = "Malayalam"
                elif "hindi" in title_lower:
                    audio_lang = "Hindi"
                elif "tamil" in title_lower:
                    audio_lang = "Tamil"
                elif "telugu" in title_lower:
                    audio_lang = "Telugu"
                elif "japanese" in title_lower or "sub" in title_lower:
                    audio_lang = "Japanese (Sub/Dub)"
                elif "english" in title_lower:
                    audio_lang = "English"

                formatted_dur = self.format_duration(duration_sec)

                results.append({
                    "title": self.clean_title(raw_title, query) or raw_title[:45],
                    "raw_title": raw_title,
                    "resolution": resolution,
                    "codec": "YouTube Stream",
                    "audio": audio_lang,
                    "size": f"[{formatted_dur}]",
                    "duration_seconds": duration_sec,
                    "platform": f"YouTube ({channel_name[:20]})",
                    "direct_url": f"https://www.youtube.com/watch?v={video_id}",
                    "channel": channel_name,
                    "is_youtube": True
                })

                if len(results) >= max_results:
                    break

        except Exception:
            pass

        return results

    async def search(self, query: str) -> list:
        """Standalone search method with internal client."""
        async with httpx.AsyncClient(http2=True, limits=self.limits, follow_redirects=True) as client:
            return await self.search_full_videos(client, query)

if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "Kummatty 1979"
    scraper = YouTubeScraper()
    t0 = time.perf_counter()
    items = asyncio.run(scraper.search(q))
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"Found {len(items)} items for '{q}' in {elapsed:.2f}ms:")
    print(json.dumps(items, indent=2))
