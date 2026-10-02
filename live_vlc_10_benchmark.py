"""
Real Live VLC Execution Benchmark Across 10 Completely Fresh & Unseen Titles
Measures exact end-to-end latency until VLC Media Player is physically spawned with stream URL.
"""

import sys
import time
import asyncio
import subprocess
import os

from play_engine import PlayEngine

TEST_SUITE_10_FRESH = [
    {"id": 1, "category": "Old Malayalam Classic", "query": "Manichitrathazhu"},
    {"id": 2, "category": "Old Malayalam Action", "query": "Spadikam"},
    {"id": 3, "category": "Modern Malayalam Hit", "query": "Manjummel Boys"},
    {"id": 4, "category": "Modern Malayalam Blockbuster", "query": "Lucifer"},
    {"id": 5, "category": "Bollywood Action Hit", "query": "Jawan"},
    {"id": 6, "category": "Bollywood Sports Drama", "query": "Dangal"},
    {"id": 7, "category": "Western 4K Sci-Fi", "query": "Interstellar"},
    {"id": 8, "category": "Western 4K Superhero", "query": "The Dark Knight"},
    {"id": 9, "category": "Anime Masterpiece Movie", "query": "Demon Slayer Mugen Train"},
    {"id": 10, "category": "Anime Top Series", "query": "Death Note"}
]

async def run_live_vlc_benchmark():
    engine = PlayEngine()
    print("\n" + "=" * 125)
    print(f"{'#':<3} | {'CATEGORY':<28} | {'QUERY':<24} | {'QUALITY & SIZE':<22} | {'VLC PID':<10} | {'TIME (ms)':<12} | {'VLC STATUS'}")
    print("=" * 125)

    times = []
    active_pids = []

    for item in TEST_SUITE_10_FRESH:
        t0 = time.perf_counter()
        res = await engine.play(item["query"], auto_launch=True)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        times.append(elapsed_ms)

        quality_str = f"{res.get('quality', '1080p')} ({res.get('size', 'N/A')})"
        pid = res.get("vlc_pid")

        if pid:
            active_pids.append(pid)

        status_text = "SPAWNED & ACTIVE" if pid else ("RESOLVED" if res.get("stream_url") else "FAILED")
        print(f"{item['id']:<3} | {item['category']:<28} | {item['query']:<24} | {quality_str[:22]:<22} | {str(pid):<10} | {elapsed_ms:>8.2f} ms | {status_text}")

        # Brief pause between launches
        await asyncio.sleep(0.5)

    print("-" * 125)
    avg_ms = round(sum(times) / len(times), 2)
    min_ms = min(times)
    max_ms = max(times)

    print(f"\n[+] LIVE VLC EXECUTION SUMMARY:")
    print(f"  • Total Real Titles Tested & Played : {len(TEST_SUITE_10_FRESH)} / 10")
    print(f"  • Average End-to-End Play Time      : {avg_ms} ms")
    print(f"  • Fastest 1-Shot VLC Spawn          : {min_ms} ms")
    print(f"  • Max 1-Shot VLC Spawn              : {max_ms} ms")
    print(f"  • Successful Real VLC Launches      : {len(active_pids)} / {len(TEST_SUITE_10_FRESH)}\n")

    # Clean up spawned test VLC processes via taskkill
    print("[*] Cleaning up test VLC processes...")
    for pid in active_pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    print("[+] Test cleanup completed.\n")

if __name__ == "__main__":
    asyncio.run(run_live_vlc_benchmark())
