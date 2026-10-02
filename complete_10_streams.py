import sys, asyncio, httpx
from bs4 import BeautifulSoup
import re

sys.path.append(r'C:\Users\sahil\.claude\skills\play\tools')
from link_resolver import LinkResolver
from stream_verifier import StreamVerifier
from fast_scrapers import FastScrapers
from media_db import MediaDB

async def complete_10():
    scrapers = FastScrapers()
    resolver = LinkResolver()
    verifier = StreamVerifier()
    db = MediaDB()

    more_candidates = [
        'Kalki 2898 AD',
        'Stree 2',
        'Jawan',
        'Leo',
        'Jailer',
        'Animal',
        'Salaar',
        'KGF',
        'Pushpa',
        'Vikram'
    ]

    found = 0
    for c in more_candidates:
        print(f"[*] Checking releases for: {c}...")
        cat = await scrapers.search_vegamovies(httpx.AsyncClient(headers={'User-Agent': 'Mozilla/5.0'}, follow_redirects=True), c)
        for r in cat:
            u = r['target_url']
            res = await resolver.resolve_auto(u)
            dir_u = res.get('direct_url')
            if dir_u:
                v = await verifier.verify(dir_u)
                if v.get('valid'):
                    print(f"   [+] VERIFIED: {c} -> {v.get('format')} [{v.get('size')}]")
                    db.upsert_title(
                        {"canonical_title": c, "formatted_title": c, "genre": "Verified Video"},
                        [{
                            "platform": "VegaMovies / R2",
                            "label": f"{c} {v.get('format')} [{v.get('size')}]",
                            "resolution": r.get('resolution', '1080p'),
                            "codec": "HEVC / x264",
                            "size": v.get('size', 'Unknown'),
                            "audio": "Dual Audio / Multi",
                            "target_url": u,
                            "direct_url": dir_u
                        }]
                    )
                    found += 1
                    break
        if found >= 5:
            break
    print(f"\n[+] Successfully added {found} verified real streams to media_db.")

if __name__ == '__main__':
    asyncio.run(complete_10())
