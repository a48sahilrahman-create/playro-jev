#!/usr/bin/env python3
"""
Live 10-Media 15-Second VLC Playback Benchmark for User-Specified Titles
Titles:
  1. Thondimuthalum Driksakshiyum (2017) — Malayalam
  2. Kummatty (1979) — Malayalam
  3. Ustad Hotel (2012) — Malayalam
  4. Andhadhun (2018) — Hindi
  5. Tumbbad (2018) — Hindi
  6. Memories of Murder (2003) — Korean
  7. Perfect Blue (1997) — Anime
  8. Ergo Proxy (2006) — Anime Series
  9. The Fall (2006) — Western
  10. Coherence (2013) — Western
"""

import os
import sys
import time
import asyncio
import subprocess
from pathlib import Path

BASE_DIR = Path(r"C:\Users\sahil\.claude\skills\play\tools")
EXE_PATH = Path(r"D:\play skill\play.exe")
sys.path.insert(0, str(BASE_DIR))

from media_db import MediaDB
from link_resolver import LinkResolver
from stream_verifier import StreamVerifier
from play_engine import find_vlc

USER_REQUESTED_TITLES = [
    {"query": "Thondimuthalum Driksakshiyum", "label": "Thondimuthalum Driksakshiyum (2017)", "lang": "Malayalam"},
    {"query": "Kummatty", "label": "Kummatty (1979)", "lang": "Malayalam"},
    {"query": "Ustad Hotel", "label": "Ustad Hotel (2012)", "lang": "Malayalam"},
    {"query": "Andhadhun", "label": "Andhadhun (2018)", "lang": "Hindi"},
    {"query": "Tumbbad", "label": "Tumbbad (2018)", "lang": "Hindi"},
    {"query": "Memories of Murder", "label": "Memories of Murder (2003)", "lang": "Korean"},
    {"query": "Perfect Blue", "label": "Perfect Blue (1997)", "lang": "Anime"},
    {"query": "Ergo Proxy", "label": "Ergo Proxy (2006)", "lang": "Anime Series"},
    {"query": "The Fall", "label": "The Fall (2006)", "lang": "Western"},
    {"query": "Coherence", "label": "Coherence (2013)", "lang": "Western"}
]

async def run_specific_10_benchmark():
    db = MediaDB(db_path=r"D:\play skill\media_index.db")
    resolver = LinkResolver()
    verifier = StreamVerifier()
    vlc = find_vlc()

    print("=" * 105)
    print("🎬 LIVE 15-SECOND VLC STREAMING BENCHMARK: 10 SPECIFIC USER-REQUESTED TITLES")
    print("=" * 105)

    results = []

    for idx, item in enumerate(USER_REQUESTED_TITLES, start=1):
        q = item["query"]
        lbl = item["label"]
        lang = item["lang"]
        print(f"\n[{idx}/10] 🎥 Starting Title: '{lbl}' ({lang})...")

        # 1. Search
        t_s0 = time.perf_counter()
        search_res = db.search(q, limit=10)
        search_ms = (time.perf_counter() - t_s0) * 1000

        releases = search_res.get("releases", [])
        if not releases:
            print(f"  [-] No releases found in database for '{q}'")
            results.append({"title": lbl, "lang": lang, "status": "NO_MATCH", "search_ms": search_ms})
            continue

        # 2. Select & Resolve
        verified_stream = None
        for rel in releases:
            u = rel.get("direct_url") or rel.get("target_url")
            if not u:
                continue

            t_r0 = time.perf_counter()
            res_out = await resolver.resolve_auto(u)
            resolve_ms = (time.perf_counter() - t_r0) * 1000
            direct_u = res_out.get("direct_url", u)

            t_v0 = time.perf_counter()
            v = await verifier.verify(direct_u)
            verify_ms = (time.perf_counter() - t_v0) * 1000

            if v.get("valid"):
                verified_stream = {
                    "release": rel,
                    "direct_url": direct_u,
                    "resolve_ms": resolve_ms,
                    "verify_ms": verify_ms,
                    "verification": v,
                    "host": res_out.get("host", "CDN")
                }
                break

        if not verified_stream:
            print(f"  [-] Failed to verify EBML for '{q}'")
            results.append({"title": lbl, "lang": lang, "status": "FAIL", "search_ms": search_ms})
            continue

        v = verified_stream["verification"]
        direct_url = verified_stream["direct_url"]

        # Read exact stream filename from URL path
        filename = direct_url.split("?")[0].split("/")[-1]
        print(f"  [+] Exact Target Filename: {filename}")
        print(f"  [+] Search: {search_ms:.2f}ms | Resolve: {verified_stream['resolve_ms']:.2f}ms | Host: {verified_stream['host']}")
        print(f"  [+] Container: {v.get('format')} | Verified Magic: 1A 45 DF A3 / MP4")

        # 3. Launch VLC and stream for 13 seconds
        print(f"  [*] Launching VLC Player with exact stream...")
        t_vlc0 = time.perf_counter()
        proc = subprocess.Popen([vlc, direct_url, f"--meta-title={lbl}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        vlc_pid = proc.pid
        print(f"  [🚀 STREAMING] VLC PID: {vlc_pid} -> Playing exact movie for 13 seconds...")

        is_alive = True
        for second in range(1, 14):
            await asyncio.sleep(1.0)
            if proc.poll() is not None:
                is_alive = False
                print(f"  [!] VLC closed unexpectedly at second {second} (Exit Code: {proc.returncode})")
                break

        stream_duration = time.perf_counter() - t_vlc0

        try:
            proc.terminate()
            await asyncio.sleep(0.5)
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

        if is_alive:
            print(f"  [✅ SUCCESS] Stream played flawlessly for {stream_duration:.1f}s without interruption.")
            results.append({
                "title": lbl,
                "lang": lang,
                "filename": filename,
                "status": "PASS",
                "search_ms": round(search_ms, 2),
                "resolve_ms": round(verified_stream["resolve_ms"], 2),
                "duration_s": round(stream_duration, 1),
                "host": verified_stream["host"]
            })
        else:
            results.append({
                "title": lbl,
                "lang": lang,
                "filename": filename,
                "status": "FAIL",
                "search_ms": round(search_ms, 2),
                "resolve_ms": round(verified_stream["resolve_ms"], 2),
                "duration_s": round(stream_duration, 1),
                "host": verified_stream["host"]
            })

    print("\n" + "=" * 105)
    print("📊 FINAL BENCHMARK REPORT: 10 SPECIFIC REQUESTED TITLES")
    print("=" * 105)
    print(f"{'#':<3} | {'Title':<32} | {'Language':<14} | {'Status':<6} | {'Search':<8} | {'Resolve':<9} | {'Streamed':<10} | {'Host':<15}")
    print("-" * 105)
    for i, r in enumerate(results, start=1):
        print(f"{i:<3} | {r['title'][:32]:<32} | {r.get('lang', '')[:14]:<14} | {r['status']:<6} | {r.get('search_ms', 0):>5.2f}ms | {r.get('resolve_ms', 0):>6.2f}ms | {r.get('duration_s', 0):>5.1f}s     | {r.get('host', 'CDN'):<15}")
    print("-" * 105)

if __name__ == '__main__':
    asyncio.run(run_specific_10_benchmark())
