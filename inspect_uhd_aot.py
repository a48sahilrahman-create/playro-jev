import asyncio
import httpx
import re
from bs4 import BeautifulSoup
from parallel_dispatcher import UHD_MIRROR, DEFAULT_HEADERS

async def inspect_uhd_aot():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(f"{UHD_MIRROR}/search/Attack+on+Titan", headers=DEFAULT_HEADERS)
        soup = BeautifulSoup(r.text, "lxml")
        for art in soup.find_all("article"):
            link = art.find("a", href=True)
            title = art.get_text(strip=True)
            print("UHD Art:", title[:80])
            if link:
                p_resp = await client.get(link['href'], headers=DEFAULT_HEADERS)
                p_soup = BeautifulSoup(p_resp.text, "lxml")
                print("Article URL:", link['href'])
                for p in p_soup.find_all(["p", "h3", "h4"]):
                    txt = p.get_text(" ", strip=True)
                    dls = p.find_all("a", href=re.compile(r'driveseed|cloud\.unblocked|drive\.google'))
                    if dls:
                        print(f"  P text: {txt[:60]}")
                        for dl in dls:
                            print(f"    DL: '{dl.get_text(strip=True)}' -> {dl['href'][:50]}")
                    elif "season" in txt.lower():
                        print(f"  Header text: {txt[:60]}")

if __name__ == "__main__":
    asyncio.run(inspect_uhd_aot())
