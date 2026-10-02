import asyncio
import httpx
from bs4 import BeautifulSoup
from parallel_dispatcher import HDHUB_MIRROR, DEFAULT_HEADERS

async def inspect_hdhub():
    async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
        r = await client.get(f"{HDHUB_MIRROR}/?s=Inception", headers=DEFAULT_HEADERS, timeout=4.0)
        soup = BeautifulSoup(r.text, "lxml")
        posts = soup.find_all("li", class_=lambda c: c and "post" in c) or soup.find_all("div", class_=lambda c: c and ("post" in c or "item" in c or "thumb" in c or "content" in c))
        print("Found posts:", len(posts))
        for p in soup.find_all(["h2", "h3"])[:5]:
            print("Header:", p.get_text(strip=True), "Link:", p.find("a")["href"] if p.find("a") else "None")

if __name__ == "__main__":
    asyncio.run(inspect_hdhub())
