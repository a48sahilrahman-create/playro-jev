import asyncio
import httpx
import re
from bs4 import BeautifulSoup
from parallel_dispatcher import VEGA_MIRROR, UHD_MIRROR, DEFAULT_HEADERS

async def inspect_posts():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(f"{VEGA_MIRROR}/ts-search.php?q=Attack+on+Titan", headers=DEFAULT_HEADERS)
        hits = r.json().get("hits", [])
        for h in hits:
            doc = h.get("document", {})
            title = doc.get("post_title", "")
            permalink = doc.get("permalink", "")
            print("Title:", title)
            print("Permalink:", permalink)
            post_url = f"{VEGA_MIRROR}{permalink}" if permalink.startswith("/") else permalink
            p_resp = await client.get(post_url, headers=DEFAULT_HEADERS)
            print("Status:", p_resp.status_code)
            soup = BeautifulSoup(p_resp.text, "lxml")
            headers = soup.find_all(["h2", "h3", "h4", "h5", "p"])
            for hdr in headers:
                txt = hdr.get_text(" ", strip=True)
                if any(k in txt.lower() for k in ["season", "episode", "batch", "download", "480p", "720p", "1080p", "2160p"]):
                    links = hdr.find_all("a", href=True)
                    dl_links = [a for a in links if any(k in a['href'] for k in ['vcloud', 'nexdrive', 'fast-dl', 'driveseed'])]
                    if dl_links or "season" in txt.lower():
                        print(f"   [{hdr.name}] {txt[:90]} (buttons: {len(dl_links)})")
            print("="*60)

if __name__ == "__main__":
    asyncio.run(inspect_posts())
