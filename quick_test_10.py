import sys, asyncio, time, subprocess
sys.path.append(r'C:\Users\sahil\.claude\skills\play\tools')
from media_db import MediaDB
from link_resolver import LinkResolver
from stream_verifier import StreamVerifier
from play_engine import find_vlc

async def test_fast_10():
    db = MediaDB(db_path=r'C:\Users\sahil\.claude\skills\play\tools\media_index.db')
    resolver = LinkResolver()
    verifier = StreamVerifier()
    vlc = find_vlc()

    titles = ['Avengers', 'Dune', 'Oppenheimer', 'Dragon', 'Stree', 'Kalki', 'Aadujeevitham', 'Manjummel', 'Jawan', 'Pokemon']

    print('Testing 10 titles directly...')
    for t in titles:
        t0 = time.perf_counter()
        s_res = db.search(t, limit=5)
        search_ms = (time.perf_counter() - t0) * 1000
        rels = s_res.get('releases', [])
        print(f'\nTitle: {t} -> {len(rels)} releases found (Search: {search_ms:.2f}ms)')

        verified = False
        for r in rels[:5]:
            u = r.get('direct_url') or r.get('target_url')
            if not u:
                continue
            try:
                res = await resolver.resolve_auto(u)
                dir_u = res.get('direct_url', u)
                v = await verifier.verify(dir_u)
                if v.get('valid'):
                    res_lbl = r.get('resolution', '1080p')
                    plat = r.get('platform', 'Web')
                    fmt = v.get('format', 'MKV')
                    el = res.get('elapsed_ms', 0)
                    print(f'  [+] VERIFIED: {res_lbl} on {plat} ({fmt}) in {el}ms')
                    verified = True
                    break
            except Exception as e:
                pass
        if not verified:
            print(f'  [-] In-depth resolving required for candidate releases.')

if __name__ == '__main__':
    asyncio.run(test_fast_10())
