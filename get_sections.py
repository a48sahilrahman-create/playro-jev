import sys, asyncio, httpx
from bs4 import BeautifulSoup
import re

async def get_sections():
    async with httpx.AsyncClient(headers={'User-Agent': 'Mozilla/5.0'}, follow_redirects=True) as client:
        r = await client.get('https://new2.vegamovies.futbol/avengers-endgame-2019-hindi-english-bluray-full-movie/')
        soup = BeautifulSoup(r.text, 'lxml')
        for block in soup.find_all(['h3', 'h4', 'h5', 'p']):
            txt = block.get_text(strip=True)
            if any(q in txt for q in ['480p', '720p', '1080p', '2160p', '4K', 'HEVC', 'BluRay', 'GB', 'MB']):
                # Find link inside or immediately following
                a = block.find('a', href=lambda h: h and 'nexdrive' in h)
                if not a:
                    nxt = block.find_next(['p', 'div'])
                    if nxt:
                        a = nxt.find('a', href=lambda h: h and 'nexdrive' in h)
                if a:
                    print(f"RELEASE: {txt[:80]} -> {a['href']}")

if __name__ == '__main__':
    asyncio.run(get_sections())
