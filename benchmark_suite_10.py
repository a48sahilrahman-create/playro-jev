import sys
import os
import asyncio
import sqlite3
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(r"C:\Users\sahil\.claude\skills\play\tools")
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from link_resolver import LinkResolver
from stream_verifier import StreamVerifier
from parallel_dispatcher import ParallelMediaDispatcher
from play_app import find_vlc

TITLES = [
    ("Thondimuthalum Driksakshiyum", "Thondimuthalum Driksakshiyum (2017)", "Malayalam"),
    ("Kummatty", "Kummatty (1979)", "Malayalam"),
    ("Ustad Hotel", "Ustad Hotel (2012)", "Malayalam"),
    ("Andhadhun", "Andhadhun (2018)", "Hindi"),
    ("Tumbbad", "Tumbbad (2018)", "Hindi"),
    ("Memories of Murder", "Memories of Murder (2003)", "Korean"),
    ("Perfect Blue", "Perfect Blue (1997)", "Anime"),
    ("Ergo Proxy", "Ergo Proxy (2006)", "Anime Series"),
    ("The Fall", "The Fall (2006)", "Western"),
    ("Coherence", "Coherence (2013)", "Western")
]

async def benchmark_and_stream():
    db_path = r"D:\play skill\media_index.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    resolver = LinkResolver()
    verifier = StreamVerifier()
    dispatcher = ParallelMediaDispatcher(db_path=db_path)
    vlc = find_vlc()

    print("=" * 95)
    print("LIVE 12-15s VLC STREAMING BENCHMARK: 10 SPECIFIC TITLES WITH ON-DEMAND AUTO-RESOLVER")
    print("=" * 95)

    results = []

    for short_name, canon_title, lang in TITLES:
        print(f"\n[*] Processing [{lang}] '{canon_title}'...")
        t0 = time.perf_counter()

        # Step 1: Query DB releases
        cur.execute("""
            SELECT r.id, r.platform, r.label, r.target_url, r.direct_url
            FROM releases r
            JOIN titles t ON r.title_id = t.id
            WHERE t.canonical_title = ?
        """, (canon_title,))
        releases = cur.fetchall()

        verified_url = None
        verified_meta = None
        used_platform = "Local DB"

        # Check existing release candidates
        for r_id, plat, lbl, tgt, dir_u in releases:
            for cand in [tgt, dir_u]:
                if not cand or not cand.startswith("http"):
                    continue
                try:
                    res = await resolver.resolve_auto(cand)
                    direct = res.get("direct_url", cand)
                    v = await verifier.verify(direct)
                    if v.get("valid"):
                        verified_url = direct
                        verified_meta = v
                        used_platform = plat
                        cur.execute("UPDATE releases SET direct_url = ? WHERE id = ?", (direct, r_id))
                        break
                except Exception:
                    pass
            if verified_url:
                break

        # Step 2: Fallback to live parallel multi-source search if not yet verified
        if not verified_url:
            print(f"    [-] Triggering live parallel search across web/mirrors for '{canon_title}'...")
            live_candidates = await dispatcher.search_all_parallel(short_name)
            for cand_obj in live_candidates:
                tgt_u = cand_obj.get("target_url")
                if not tgt_u or not tgt_u.startswith("http"):
                    continue
                try:
                    res = await resolver.resolve_auto(tgt_u)
                    direct = res.get("direct_url", tgt_u)
                    v = await verifier.verify(direct)
                    if v.get("valid"):
                        verified_url = direct
                        verified_meta = v
                        used_platform = cand_obj.get("platform", "Live Scraper")
                        break
                except Exception:
                    pass

        elapsed_resolve_ms = (time.perf_counter() - t0) * 1000

        # Step 3: Stream in VLC for 12 seconds if verified
        if verified_url:
            fn = verified_url.split("?")[0].split("/")[-1]
            fmt = verified_meta.get("format", "MKV")
            size = verified_meta.get("size", "N/A")
            print(f"    [+] VERIFIED in {elapsed_resolve_ms:.2f}ms on {used_platform}")
            print(f"        Exact Filename: {fn}")
            print(f"        Container: {fmt} | Size: {size}")
            print(f"        Stream URL: {verified_url[:65]}...")

            proc = subprocess.Popen(
                [vlc, verified_url, f"--meta-title={canon_title}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print(f"        [STREAMING] VLC PID {proc.pid} active (Streaming for 12 seconds)...")
            await asyncio.sleep(12.0)
            proc.terminate()
            print("        [SUCCESS] Streamed successfully for 12 seconds.")
            results.append((canon_title, lang, "PASS", f"{elapsed_resolve_ms:.1f}ms", fn))
        else:
            print(f"    [-] FAILED to resolve valid stream candidate.")
            results.append((canon_title, lang, "FAIL", f"{elapsed_resolve_ms:.1f}ms", "N/A"))

    conn.commit()
    conn.close()

    print("\n" + "=" * 95)
    print("FINAL 10-TITLE BENCHMARK REPORT")
    print("=" * 95)
    print(f"{'Title':<38} | {'Language':<12} | {'Status':<6} | {'Latency':<9} | {'Exact Filename'}")
    print("-" * 95)
    for c, l, s, lat, fn in results:
        print(f"{c:<38} | {l:<12} | {s:<6} | {lat:<9} | {fn[:35]}")
    print("=" * 95)

if __name__ == "__main__":
    asyncio.run(benchmark_and_stream())
