import asyncio
import httpx
from bs4 import BeautifulSoup
from parallel_dispatcher import ParallelMediaDispatcher, VEGA_MIRROR, UHD_MIRROR, DEFAULT_HEADERS

async def test_sources():
    disp = ParallelMediaDispatcher()
    async with httpx.AsyncClient(follow_redirects=True) as client:
        print("=== VEGA ATTACK ON TITAN ===")
        v_aot = await disp.fetch_series_vega(client, "Attack on Titan")
        for s, eps in v_aot.items():
            print(f"Season: {s} -> {len(eps)} eps: {list(eps.keys())}")
            for ep, q in eps.items():
                print(f"   {ep}: {[x['resolution'] + ' ' + x['size'] for x in q]}")

        print("\n=== UHD ATTACK ON TITAN ===")
        u_aot = await disp.fetch_series_uhd(client, "Attack on Titan")
        for s, eps in u_aot.items():
            print(f"Season: {s} -> {len(eps)} eps: {list(eps.keys())[:5]}")
            for ep in list(eps.keys())[:3]:
                print(f"   {ep}: {[x['resolution'] + ' ' + x['size'] for x in eps[ep]]}")

if __name__ == "__main__":
    asyncio.run(test_sources())
