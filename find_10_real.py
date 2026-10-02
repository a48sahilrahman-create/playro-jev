import sys, asyncio, httpx
from bs4 import BeautifulSoup
import re

sys.path.append(r'C:\Users\sahil\.claude\skills\play\tools')
from link_resolver import LinkResolver
from stream_verifier import StreamVerifier
from fast_scrapers import FastScrapers
from media_db import MediaDB

async def find_and_index_10_verified():
    scrapers = FastScrapers()
    resolver = LinkResolver()
    verifier = StreamVerifier()
    db = MediaDB()

    candidates = [
        'Deadpool',
        'Pokemon Hoopa',
        'Gladiator Underground',
        'The Goat Life',
        'Ghost in the Shell',
        'Eye for an Eye',
        'Smoking Behind the Supermarket with You',
        'Snake 5',
        'Backrooms',
        'Taxi Driver',
        'Transformers One',
        'Alien Romulus',
        'Twisters',
        'Blink Twice',
        'Inside Out 2'
    ]

    verified_streams = []

    for c in candidates:
        print(f'[*] Checking releases for: {c}...')
        cat = await scrapers.search_vegamovies(httpx.AsyncClient(headers={'User-Agent': 'Mozilla/5.0'}, follow_redirects=True), c)
        for r in cat:
            u = r['target_url']
            res = await resolver.resolve_auto(u)
            dir_u = res.get('direct_url')
            if dir_u:
                v = await verifier.verify(dir_u)
                if v.get('valid'):
                    print(f'   [+] VERIFIED STREAM: {c} -> {v.get("format")} [{v.get("size")}]')
                    verified_streams.append({
                        'title': c,
                        'stream_url': dir_u,
                        'format': v.get('format'),
                        'size': v.get('size'),
                        'target_url': u
                    })
                    # Save into SQLite DB for instant playback
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
                    break
        if len(verified_streams) >= 10:
            break

    print(f'\n[+] Total Verified Real Streams Indexed: {len(verified_streams)} / 10')

if __name__ == '__main__':
    asyncio.run(find_and_index_10_verified())
