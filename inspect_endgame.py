import sys, asyncio, httpx
from bs4 import BeautifulSoup

async def inspect():
    async with httpx.AsyncClient(headers={'User-Agent': 'Mozilla/5.0'}, follow_redirects=True) as client:
        r = await client.get('https://new2.vegamovies.futbol/ts-search.php?q=Avengers+Endgame')
        hits = r.json().get('hits', [])
        for h in hits:
            doc = h.get('document', {})
            permalink = doc.get('permalink')
            post_title = doc.get('post_title')
            print(f"=== {post_title} ===")
            p_resp = await client.get(f"https://new2.vegamovies.futbol{permalink}")
            soup = BeautifulSoup(p_resp.text, 'lxml')
            for a in soup.find_all('a', href=True):
                if 'nexdrive' in a['href']:
                    # search backwards for header
                    prev = a.find_previous(['p', 'h3', 'h4', 'h5', 'strong'])
                    prev_text = prev.get_text(strip=True) if prev else ""
                    print(f"  {prev_text[:70]} -> {a['href']}")

if __name__ == '__main__':
    asyncio.run(inspect())
