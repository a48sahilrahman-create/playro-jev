import sys, asyncio, httpx
from bs4 import BeautifulSoup
import re

async def get_avengers_catalog():
    async with httpx.AsyncClient(headers={'User-Agent': 'Mozilla/5.0'}, follow_redirects=True) as client:
        # Vega
        r = await client.get('https://new2.vegamovies.futbol/ts-search.php?q=Avengers+Endgame')
        hits = r.json().get('hits', [])
        print("Vega Hits:", len(hits))
        for h in hits[:2]:
            doc = h.get('document', {})
            permalink = doc.get('permalink')
            p_resp = await client.get(f'https://new2.vegamovies.futbol{permalink}')
            p_soup = BeautifulSoup(p_resp.text, 'lxml')
            for a in p_soup.find_all('a', href=re.compile(r'nexdrive')):
                prev = a.find_previous(['p', 'h3', 'h4', 'h5'])
                prev_t = prev.get_text(strip=True) if prev else ''
                print("Vega Option:", prev_t[:80], "->", a['href'])

if __name__ == '__main__':
    asyncio.run(get_avengers_catalog())
