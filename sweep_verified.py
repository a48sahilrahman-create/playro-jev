import sys, asyncio, httpx, sqlite3, os, re
sys.path.append(r'C:\Users\sahil\.claude\skills\play\tools')
from link_resolver import LinkResolver
from stream_verifier import StreamVerifier
from media_db import MediaDB

async def sweep_and_verify_all():
    db = MediaDB()
    resolver = LinkResolver()
    verifier = StreamVerifier()

    db_path = os.path.expanduser(r'~\.claude\skills\play\tools\media_index.db')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, title_id, target_url FROM releases WHERE platform = 'VegaMovies' AND (direct_url IS NULL OR direct_url = '')")
    releases = cur.fetchall()
    print(f"[*] Scanning {len(releases)} candidate release URLs for direct streams...")

    verified_count = 0

    for r_id, t_id, target_url in releases:
        try:
            res = await resolver.resolve_auto(target_url)
            dir_u = res.get('direct_url')
            if dir_u:
                v = await verifier.verify(dir_u)
                if v.get('valid'):
                    verified_count += 1
                    cur.execute("UPDATE releases SET direct_url = ?, size = ?, codec = ? WHERE id = ?", (dir_u, v.get('size', 'Unknown'), v.get('format', 'MKV'), r_id))
                    conn.commit()
                    cur.execute("SELECT canonical_title FROM titles WHERE id = ?", (t_id,))
                    t_title = cur.fetchone()[0]
                    print(f"  [+] #{verified_count}: {t_title[:35]} -> {v.get('format')} [{v.get('size')}]")
        except Exception:
            pass

        if verified_count >= 15:
            break

    conn.close()
    print(f"\n[+] Total Verified Live Streams Saved in DB: {verified_count}")

if __name__ == '__main__':
    asyncio.run(sweep_and_verify_all())
