import asyncio
import httpx
import re
from bs4 import BeautifulSoup
from parallel_dispatcher import (
    UHD_MIRROR, VEGA_MIRROR, HDHUB_MIRROR, OLA_MIRROR, DEFAULT_HEADERS,
    is_strict_match, clean_media_title
)

async def test_search_queries():
    queries = [
        "Avengers: Endgame",
        "Avengers Endgame",
        "Kummatty (1979)",
        "Kummatty",
        "Manjummel Boys",
        "Kantara",
        "Dune: Part Two",
        "Dune Part Two",
        "Spirited Away",
        "Spider-Man: No Way Home",
        "Spider-Man Across the Spider-Verse",
        "John Wick: Chapter 4"
    ]

    async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
        for q in queries:
            print(f"\n=================== QUERY: {q} ===================")

            # Test Vega ts-search
            # 1. raw query vs cleaned query
            clean_q = re.sub(r'[\:\(\)\[\]\{\}\-\_\,\.\!]', ' ', q)
            clean_q = re.sub(r'\s+', ' ', clean_q).strip()

            # Vega
            try:
                url = f"{VEGA_MIRROR}/ts-search.php?q={clean_q}"
                r = await client.get(url, headers=DEFAULT_HEADERS, timeout=3.0)
                if r.status_code == 200:
                    hits = r.json().get("hits", [])
                    print(f"Vega ({len(hits)} hits):")
                    for h in hits[:3]:
                        t = h.get("document", {}).get("post_title", "")
                        match = is_strict_match(q, t)
                        print(f"  - [{match}] {t[:70]}")
            except Exception as e:
                print(f"Vega error: {e}")

            # UHD
            try:
                uhd_q = re.sub(r'\s+', '+', clean_q)
                url = f"{UHD_MIRROR}/search/{uhd_q}"
                r = await client.get(url, headers=DEFAULT_HEADERS, timeout=3.0)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "lxml")
                    articles = soup.find_all("article")
                    print(f"UHD ({len(articles)} articles):")
                    for art in articles[:3]:
                        t = art.find(["h1", "h2", "h3", "h4", "a"])
                        t_text = t.get_text(strip=True) if t else ""
                        match = is_strict_match(q, t_text)
                        print(f"  - [{match}] {t_text[:70]}")
            except Exception as e:
                print(f"UHD error: {e}")

            # Ola
            try:
                ola_q = re.sub(r'\s+', '+', clean_q)
                url = f"{OLA_MIRROR}/?s={ola_q}"
                r = await client.get(url, headers=DEFAULT_HEADERS, timeout=3.0)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "lxml")
                    articles = soup.find_all("article")
                    print(f"Ola ({len(articles)} articles):")
                    for art in articles[:3]:
                        links = art.find_all("a", href=True)
                        for l in links:
                            t = l.get_text(strip=True) or l.get("title", "")
                            if t and len(t) > 5 and ("category" not in l["href"] and "tag" not in l["href"]):
                                match = is_strict_match(q, t)
                                print(f"  - [{match}] {t[:70]}")
                                break
            except Exception as e:
                print(f"Ola error: {e}")

if __name__ == "__main__":
    asyncio.run(test_search_queries())
