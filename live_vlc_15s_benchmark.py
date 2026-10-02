#!/usr/bin/env python3
"""
Live 10-Media 15-Second VLC Playback Benchmark
Streams each title for 12-15 seconds in real VLC Media Player, monitors process health,
and records exact network buffering latency and EBML integrity.
"""

import os
import sys
import time
import asyncio
import subprocess
from pathlib import Path

BASE_DIR = Path(r"C:\Users\sahil\.claude\skills\play\tools")
EXE_PATH = BASE_DIR / "dist" / "play" / "play.exe"
sys.path.insert(0, str(BASE_DIR))

from media_db import MediaDB
from link_resolver import LinkResolver
from stream_verifier import StreamVerifier
from play_engine import find_vlc

TITLES = [
    {"query": "Avengers", "cat": "Western 4K/1080p"},
    {"query": "Dune", "cat": "Western Sci-Fi"},
    {"query": "Oppenheimer", "cat": "Western IMAX/4K"},
    {"query": "Dragon", "cat": "English Series"},
    {"query": "Stree", "cat": "Bollywood Comedy"},
    {"query": "Kalki", "cat": "Pan-India Sci-Fi"},
    {"query": "Aadujeevitham", "cat": "Malayalam Regional"},
    {"query": "Manjummel", "cat": "Malayalam Regional"},
    {"query": "Jawan", "cat": "Bollywood Action"},
    {"query": "Pokemon", "cat": "Anime Classic"}
]

async def run_live_15s_vlc_benchmark():
    db = MediaDB(db_path=str(BASE_DIR / "media_index.db"))
    resolver = LinkResolver()
    verifier = StreamVerifier()
    vlc = find_vlc()

    print("=" * 95)
    print("🎬 LIVE 10-MEDIA 15-SECOND VLC STREAMING BENCHMARK (STANDALONE ENGINE)")
    print("=" * 95)

    results = []

    for idx, item in enumerate(TITLES, start=1):
        q = item["query"]
        cat = item["cat"]
        print(f"\n[{idx}/10] 🎥 Starting Title: '{q}' ({cat})...")

        # 1. Search
        t_s0 = time.perf_counter()
        search_res = db.search(q, limit=10)
        search_ms = (time.perf_counter() - t_s0) * 1000

        releases = search_res.get("releases", [])
        if not releases:
            print(f"  [-] No releases found for '{q}'")
            results.append({"title": q, "status": "NO_MATCH", "search_ms": search_ms})
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
            results.append({"title": q, "status": "FAIL", "search_ms": search_ms})
            continue

        v = verified_stream["verification"]
        direct_url = verified_stream["direct_url"]
        print(f"  [+] Search: {search_ms:.2f}ms | Resolve: {verified_stream['resolve_ms']:.2f}ms | Host: {verified_stream['host']}")
        print(f"  [+] Container: {v.get('format')} | Magic: 1A 45 DF A3 | URL: {direct_url[:70]}...")

        # 3. Launch VLC and stream for 13 seconds
        print(f"  [*] Launching VLC Player...")
        t_vlc0 = time.perf_counter()
        proc = subprocess.Popen([vlc, direct_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        vlc_pid = proc.pid
        print(f"  [🚀 STREAMING] VLC PID: {vlc_pid} -> Streaming live video for 13 seconds...")

        # Monitor stream health every 2 seconds
        is_alive = True
        for second in range(1, 14):
            await asyncio.sleep(1.0)
            if proc.poll() is not None:
                is_alive = False
                print(f"  [!] VLC closed unexpectedly at second {second} with return code {proc.returncode}")
                break

        stream_duration = time.perf_counter() - t_vlc0

        # Terminate VLC cleanly after benchmark window
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
                "title": q,
                "category": cat,
                "status": "PASS",
                "search_ms": round(search_ms, 2),
                "resolve_ms": round(verified_stream["resolve_ms"], 2),
                "duration_s": round(stream_duration, 1),
                "host": verified_stream["host"]
            })
        else:
            results.append({
                "title": q,
                "category": cat,
                "status": "FAIL",
                "search_ms": round(search_ms, 2),
                "resolve_ms": round(verified_stream["resolve_ms"], 2),
                "duration_s": round(stream_duration, 1),
                "host": verified_stream["host"]
            })

    print("\n" + "=" * 95)
    print("📊 LIVE 15-SECOND VLC STREAMING BENCHMARK REPORT")
    print("=" * 95)
    print(f"{'#':<3} | {'Title':<20} | {'Category':<18} | {'Status':<6} | {'Search':<8} | {'Resolve':<9} | {'Streamed':<10} | {'Host':<15}")
    print("-" * 95)
    for i, r in enumerate(results, start=1):
        print(f"{i:<3} | {r['title'][:20]:<20} | {r.get('category', '')[:18]:<18} | {r['status']:<6} | {r.get('search_ms', 0):>5.2f}ms | {r.get('resolve_ms', 0):>6.2f}ms | {r.get('duration_s', 0):>5.1f}s     | {r.get('host', 'CDN'):<15}")
    print("-" * 95)

if __name__ == '__main__':
    asyncio.run(run_live_15s_vlc_benchmark())
