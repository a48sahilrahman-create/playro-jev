import asyncio
import httpx
from bs4 import BeautifulSoup
import re

async def test():
    url = 'https://driveseed.org/zfile/jzRgLlPGz1vlbh6vCA0l'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0.0.0 Safari/537.36',
        'Referer': 'https://driveseed.org/file/jzRgLlPGz1vlbh6vCA0l'
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=8.0) as client:
        r = await client.get(url, headers=headers)
        workers = re.findall(r'https?://[a-zA-Z0-9.-]+\.workers\.dev/[^\s\'"<>]+', r.text)
        print('Workers found in zfile:', workers)
        soup = BeautifulSoup(r.text, 'lxml')
        scripts = [s.get_text() for s in soup.find_all('script')]
        for i, s in enumerate(scripts):
            print(f'Script {i}:', s[:300])

        # Also test Instant Download button:
        inst_url = 'https://cdn.video-gen.xyz/2190cb7fe3ef50419b9e1a97fe26608d2b7707dac8f315e091bf7455e5b27695f7d68768fbe7ec51bf4395237c3a640a7834bcfa497174b1f320798dcd6b0a653ead2dbcc19f4ea254003303c3b8e566c7c41e1742a215379151e2234fd67a9c5282f2b94009ccb52a5284d3d01d0399::a94f92268239cddf8f1405985bd7816f'
        print('\nTesting Instant Download URL:', inst_url[:60])
        r_inst = await client.get(inst_url, headers={'Referer': 'https://driveseed.org/file/jzRgLlPGz1vlbh6vCA0l', 'User-Agent': headers['User-Agent']})
        print('Instant download status:', r_inst.status_code, 'url:', r_inst.url)
        print('Instant download text preview:', r_inst.text[:400])

if __name__ == '__main__':
    asyncio.run(test())
