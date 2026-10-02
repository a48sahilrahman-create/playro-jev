import sys, asyncio, time
sys.path.append(r'C:\Users\sahil\.claude\skills\play\tools')
from media_db import MediaDB
from link_resolver import LinkResolver
from stream_verifier import StreamVerifier

async def test_summary():
    db = MediaDB(db_path=r'C:\Users\sahil\.claude\skills\play\tools\media_index.db')
    resolver = LinkResolver()
    verifier = StreamVerifier()

    titles = [
        'Avengers', 'Dune', 'Oppenheimer', 'Dragon', 'Stree',
        'Kalki', 'Aadujeevitham', 'Manjummel', 'Jawan', 'Pokemon'
    ]

    for t in titles:
        t0 = time.perf_counter()
        s = db.search(t, limit=5)
        search_ms = (time.perf_counter() - t0) * 1000
        rels = s.get('releases', [])
        verified = False
        for r in rels[:5]:
            u = r.get('direct_url') or r.get('target_url')
            if not u:
                continue
            res = await resolver.resolve_auto(u)
            dir_u = res.get('direct_url', u)
            v = await verifier.verify(dir_u)
            if v.get('valid'):
                fmt = v.get('format', 'MKV')
                print(f"[PASS] {t:<15} | Search: {search_ms:>5.2f}ms | EBML: {fmt:<10} | Host: {res.get('host', 'CDN')}")
                verified = True
                break
        if not verified:
            print(f"[RELEASES] {t:<11} | Search: {search_ms:>5.2f}ms | Available Releases: {len(rels)}")

if __name__ == '__main__':
    asyncio.run(test_summary())
