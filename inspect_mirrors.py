import asyncio
import httpx
from bs4 import BeautifulSoup
import re
from parallel_dispatcher import HDHUB_MIRROR, OLA_MIRROR, DEFAULT_HEADERS

async def inspect_mirrors():
    async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
        # Test HDHub4u
        print(f"Testing HDHub4u mirror: {HDHUB_MIRROR}")
        try:
            r = await client.get(f"{HDHUB_MIRROR}/?s=Inception", headers=DEFAULT_HEADERS, timeout=4.0)
            print(f"HDHub status: {r.status_code}, length: {len(r.text)}")
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "lxml")
                articles = soup.find_all("article") or soup.find_all("div", class_=re.compile(r'post|item|grid'))
                print(f"HDHub elements found: {len(articles)}")
                for art in articles[:3]:
                    print("  Art:", art.get_text(strip=True)[:60])
        except Exception as e:
            print("HDHub error:", e)

        # Test Ola download links structure
        print(f"\nTesting OlaMovies download links structure...")
        try:
            r = await client.get(f"{OLA_MIRROR}/?s=Inception", headers=DEFAULT_HEADERS, timeout=4.0)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "lxml")
                art = soup.find("article")
                if art:
                    l = art.find("a", href=True)
                    print(f"Ola Post URL: {l['href']}")
                    p_resp = await client.get(l["href"], headers=DEFAULT_HEADERS, timeout=4.0)
                    p_soup = BeautifulSoup(p_resp.text, "lxml")
                    all_links = p_soup.find_all("a", href=True)
                    print(f"Total links on page: {len(all_links)}")
                    dl_links = [a for a in all_links if any(d in a["href"] for d in ["links.ol-am.top", "links.olamovies", "olamovies.pro", "drivehub", "mega.nz", "pixeldrain"])]
                    print(f"Relevant download links: {len(dl_links)}")
                    for dl in dl_links[:5]:
                        print("  DL Link:", dl.get_text(strip=True), "->", dl["href"])
        except Exception as e:
            print("Ola error:", e)

if __name__ == "__main__":
    asyncio.run(inspect_mirrors())
