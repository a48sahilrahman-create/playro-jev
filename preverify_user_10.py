import sys, asyncio, sqlite3
sys.path.append(r'C:\Users\sahil\.claude\skills\play\tools')
from link_resolver import LinkResolver
from stream_verifier import StreamVerifier

async def test_and_preverify():
    conn = sqlite3.connect(r'D:\play skill\media_index.db')
    cur = conn.cursor()
    resolver = LinkResolver()
    verifier = StreamVerifier()

    titles = [
        'Thondimuthalum Driksakshiyum',
        'Kummatty',
        'Ustad Hotel',
        'Andhadhun',
        'Tumbbad',
        'Memories of Murder',
        'Perfect Blue',
        'Ergo Proxy',
        'The Fall',
        'Coherence'
    ]

    for t in titles:
        cur.execute("""
            SELECT r.id, t.canonical_title, r.platform, r.target_url, r.direct_url
            FROM releases r
            JOIN titles t ON r.title_id = t.id
            WHERE t.canonical_title LIKE ?
        """, (f'%{t}%',))
        releases = cur.fetchall()
        print(f"\n=== Checking {t} ({len(releases)} releases) ===")
        verified = False
        for r_id, canon, plat, tgt, dir_u in releases:
            u = dir_u or tgt
            if not u or not ('http' in u):
                continue
            try:
                res = await resolver.resolve_auto(u)
                resolved_u = res.get('direct_url', u)
                v = await verifier.verify(resolved_u)
                if v.get('valid'):
                    fn = resolved_u.split('?')[0].split('/')[-1]
                    fmt = v.get('format', 'MKV')
                    print(f"  [+] VERIFIED: {canon} -> {fn} ({fmt}) on {plat}")
                    cur.execute("UPDATE releases SET direct_url = ? WHERE id = ?", (resolved_u, r_id))
                    verified = True
                    break
            except Exception as e:
                pass
        if not verified:
            print(f"  [-] No verified stream yet for {t}")

    conn.commit()
    conn.close()

if __name__ == '__main__':
    asyncio.run(test_and_preverify())
