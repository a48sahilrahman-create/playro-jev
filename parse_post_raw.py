import sys, asyncio, httpx
from bs4 import BeautifulSoup

async def parse_post_raw():
    async with httpx.AsyncClient(headers={'User-Agent': 'Mozilla/5.0'}, follow_redirects=True) as client:
        r = await client.get('https://new2.vegamovies.futbol/avengers-endgame-2019-hindi-english-bluray-full-movie/')
        soup = BeautifulSoup(r.text, 'lxml')
        content = soup.find('div', class_='entry-content') or soup
        # Print all text and links in order
        for child in content.find_all(['p', 'h3', 'h4', 'h5', 'div']):
            a = child.find('a', href=lambda h: h and 'nexdrive' in h)
            if a:
                prev = child.find_previous(['p', 'h3', 'h4', 'h5'])
                print("Header:", prev.get_text(strip=True) if prev else "None")
                print("Link Text:", a.get_text(strip=True), "->", a['href'])
                print("-" * 50)

if __name__ == '__main__':
    asyncio.run(parse_post_raw())
