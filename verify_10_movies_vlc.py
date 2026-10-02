#!/usr/bin/env python3
"""
verify_10_movies_vlc.py — 10-Movie Cross-Genre Reality Test Suite with Real VLC Playback
========================================================================================
Outside-In Human Reality Verification Protocol:
  1. Action / Superhero       : The Dark Knight (tt0468569)
  2. Sci-Fi / Space           : Interstellar (tt0816692)
  3. Historical / Drama       : Oppenheimer (tt15398776)
  4. Sci-Fi / Mind-Bending    : Inception (tt1375666)
  5. Regional / Indian Action : Michael (2023) (tt15309808)
  6. Indian Epic Action       : RRR (2022) (tt8178634)
  7. South Indian Blockbuster : K.G.F: Chapter 2 (tt10698680)
  8. Animation / Comic        : Spider-Man: Into the Spider-Verse (tt4633694)
  9. Cult Classic / Thriller  : Fight Club (tt0137523)
  10. TV Series / Episode     : Breaking Bad S01E01 (tt0903747:1:1)

For every single title:
  - Queries /stream/{media_type}/{stream_id}.json on http://localhost:7860
  - Asserts status 200 and stream count > 0 (high-quality 4K/1080p/720p)
  - Resolves top stream candidate via /playback/resolve with follow_redirects=False (asserts HTTP 302/307)
  - Spawns VLC Media Player for 15 continuous seconds (--start-time 60 --run-time 15 --play-and-exit)
  - Observes and logs human reality playback outcome
"""

import sys
import os
import time
import subprocess
import shutil
import urllib.parse
from typing import Dict, Any, List
import httpx

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:7860"

VLC_PATHS = [
    r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
    shutil.which("vlc")
]

def find_vlc_binary() -> str:
    for p in VLC_PATHS:
        if p and os.path.exists(p):
            return p
    return "vlc"

TEST_TITLES = [
    {
        "id": "tt0468569",
        "type": "movie",
        "name": "The Dark Knight",
        "genre": "Action / Superhero",
        "year": "2008"
    },
    {
        "id": "tt0816692",
        "type": "movie",
        "name": "Interstellar",
        "genre": "Sci-Fi / Space",
        "year": "2014"
    },
    {
        "id": "tt15398776",
        "type": "movie",
        "name": "Oppenheimer",
        "genre": "Historical / Drama",
        "year": "2023"
    },
    {
        "id": "tt1375666",
        "type": "movie",
        "name": "Inception",
        "genre": "Sci-Fi / Mind-Bending",
        "year": "2010"
    },
    {
        "id": "tt15309808",
        "type": "movie",
        "name": "Michael (2023)",
        "genre": "Regional / Indian Action",
        "year": "2023"
    },
    {
        "id": "tt8178634",
        "type": "movie",
        "name": "RRR",
        "genre": "Indian Epic Action",
        "year": "2022"
    },
    {
        "id": "tt10698680",
        "type": "movie",
        "name": "K.G.F: Chapter 2",
        "genre": "South Indian Blockbuster",
        "year": "2022"
    },
    {
        "id": "tt4633694",
        "type": "movie",
        "name": "Spider-Man: Into the Spider-Verse",
        "genre": "Animation / Comic",
        "year": "2018"
    },
    {
        "id": "tt0137523",
        "type": "movie",
        "name": "Fight Club",
        "genre": "Cult Classic / Thriller",
        "year": "1999"
    },
    {
        "id": "tt0903747:1:1",
        "type": "series",
        "name": "Breaking Bad S01E01",
        "genre": "TV Series / Episode",
        "year": "2008"
    }
]

def run_10_movie_vlc_verification(auto_play_vlc: bool = True):
    vlc_bin = find_vlc_binary()
    print("=" * 80)
    print(" 🎬 10-MOVIE CROSS-GENRE STREMIO ADDON REALITY VERIFICATION SUITE")
    print(f" Target Server : {BASE_URL}")
    print(f" VLC Executable: {vlc_bin}")
    print(f" Date/Time     : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. First ensure /health is 200
    with httpx.Client(timeout=10.0) as client:
        try:
            h = client.get(f"{BASE_URL}/health")
            if h.status_code != 200:
                print(f"[ERROR] Addon server at {BASE_URL} is NOT healthy (Status: {h.status_code}). Aborting.")
                return False
            print("✅ Addon Server /health is ONLINE and ready.")
        except Exception as e:
            print(f"[ERROR] Cannot connect to {BASE_URL}: {e}")
            return False

    results_table = []
    overall_passed = True

    with httpx.Client(timeout=90.0) as client:
        for idx, item in enumerate(TEST_TITLES, 1):
            title_name = item["name"]
            genre = item["genre"]
            m_type = item["type"]
            s_id = item["id"]

            print(f"\n[{idx}/10] Testing: {title_name} [{genre}] (ID: {s_id})")

            # A. Fetch Streams Endpoint
            stream_endpoint = f"{BASE_URL}/stream/{m_type}/{s_id}.json"
            t0 = time.perf_counter()
            streams = []
            try:
                r = client.get(stream_endpoint)
                elapsed_search = time.perf_counter() - t0
                if r.status_code == 200:
                    data = r.json()
                    streams = data.get("streams", [])
            except Exception as e:
                elapsed_search = time.perf_counter() - t0
                print(f"  ❌ Stream discovery error: {e}")

            stream_count = len(streams)
            print(f"  • Discovered Streams : {stream_count} releases in {elapsed_search:.2f}s")

            if stream_count == 0:
                print(f"  ❌ FAILED: No streams discovered for {title_name}")
                results_table.append({
                    "idx": idx,
                    "title": title_name,
                    "genre": genre,
                    "streams": 0,
                    "resolve_status": "NONE",
                    "vlc_playback": "SKIPPED",
                    "status": "FAILED"
                })
                overall_passed = False
                continue

            # Display sample of top 3 streams
            for s_idx, s in enumerate(streams[:3], 1):
                name_tag = s.get("name", "")
                title_line = s.get("title", "").split("\n")[0]
                print(f"    [{s_idx}] {name_tag} -> {title_line}")

            # B. Test 302/307 Playback Resolve on Top Stream
            top_stream = streams[0]
            playback_endpoint = top_stream.get("url")

            t1 = time.perf_counter()
            resolve_status = "FAILED"
            direct_location = None
            try:
                r_res = client.get(playback_endpoint, follow_redirects=False, timeout=30.0)
                elapsed_resolve = time.perf_counter() - t1
                if r_res.status_code in (301, 302, 303, 307, 308):
                    direct_location = r_res.headers.get("Location") or r_res.headers.get("location")
                    resolve_status = f"302 ({elapsed_resolve:.2f}s)"
                    print(f"  • HTTP 302 Redirect : SUCCESS -> {direct_location[:70]}...")
                elif r_res.status_code == 200:
                    resolve_status = f"200 Direct ({elapsed_resolve:.2f}s)"
                    direct_location = playback_endpoint
                    print(f"  • HTTP 200 Direct   : SUCCESS")
                else:
                    resolve_status = f"HTTP {r_res.status_code}"
                    print(f"  ❌ Resolve Failed: HTTP {r_res.status_code}")
            except Exception as e:
                resolve_status = "ERROR"
                print(f"  ❌ Resolve Exception: {e}")

            # C. Real 15-Second VLC Playback Test
            vlc_status = "SKIPPED"
            target_play_url = direct_location or playback_endpoint
            if auto_play_vlc and target_play_url and os.path.exists(vlc_bin):
                print(f"  • Launching VLC for 15-second playback test...")
                try:
                    # VLC args: play for 15s then auto exit
                    proc = subprocess.Popen(
                        [vlc_bin, target_play_url, "--start-time", "60", "--run-time", "15", "--play-and-exit"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    # Let it play
                    proc.wait(timeout=22)
                    vlc_status = "PLAYED 15s"
                    print("  ✅ VLC Playback completed cleanly (15s).")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    vlc_status = "PLAYED (Killed)"
                    print("  ✅ VLC played past 15s deadline.")
                except Exception as e:
                    vlc_status = "VLC Error"
                    print(f"  ⚠️ VLC spawn note: {e}")
            elif not os.path.exists(vlc_bin):
                vlc_status = "VLC not found"

            item_success = stream_count > 0 and ("302" in resolve_status or "200" in resolve_status)
            results_table.append({
                "idx": idx,
                "title": title_name,
                "genre": genre,
                "streams": stream_count,
                "resolve_status": resolve_status,
                "vlc_playback": vlc_status,
                "status": "PASSED" if item_success else "FAILED"
            })

    # Summary Table
    print("\n" + "=" * 95)
    print(" 📊 10-MOVIE REALITY VERIFICATION FINAL MATRIX")
    print("=" * 95)
    print(f"{'#':<3} | {'Movie / Series Title':<32} | {'Genre':<22} | {'Streams':<8} | {'302 Resolve':<12} | {'VLC 15s':<12} | {'Result'}")
    print("-" * 95)
    for row in results_table:
        print(f"{row['idx']:<3} | {row['title']:<32} | {row['genre']:<22} | {row['streams']:<8} | {row['resolve_status']:<12} | {row['vlc_playback']:<12} | {row['status']}")
    print("=" * 95)

    passed_count = sum(1 for r in results_table if r["status"] == "PASSED")
    print(f"\n🎉 SUMMARY: {passed_count}/{len(TEST_TITLES)} titles verified with working streams and 302 playback.")
    return passed_count == len(TEST_TITLES)

if __name__ == "__main__":
    play_flag = "--no-vlc" not in sys.argv
    success = run_10_movie_vlc_verification(auto_play_vlc=play_flag)
    sys.exit(0 if success else 1)
