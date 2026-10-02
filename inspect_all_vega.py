import asyncio
import httpx
from parallel_dispatcher import VEGA_MIRROR, UHD_MIRROR, DEFAULT_HEADERS

async def inspect_veg():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for q in ['Attack on Titan', 'Death Note', 'Jujutsu Kaisen', 'Breaking Bad']:
            clean = q.replace(' ', '+')
            r = await client.get(f'{VEGA_MIRROR}/ts-search.php?q={clean}', headers=DEFAULT_HEADERS)
            print(f'=== Hits for {q} ===')
            for h in r.json().get('hits', [])[:4]:
                doc = h.get('document', {})
                print(' ', doc.get('post_title'))
                print('   permalink:', doc.get('permalink'))

if __name__ == '__main__':
    asyncio.run(inspect_veg())
