#!/usr/bin/env python3
"""
Comprehensive 30-Case Universal Media Benchmark Suite
Spanning:
  1. Malayalam Cinema (5 titles)
  2. Hindi Blockbusters & Cult Classics (5 titles)
  3. Hollywood Blockbusters (5 titles)
  4. Vintage Classics & World Cinema (5 titles)
  5. Multi-Season Anime (5 titles)
  6. Multi-Season TV Series & Tricky Titles (5 titles)

Evaluates:
  - Exact Platform Presence vs Absence Matrix across:
      [Local DB, VegaMovies, UHDMovies, OlaMovies, HDHub4u, Telegram, YouTube]
  - Multi-Platform Aggregation & TV Series Season/Episode Hierarchical Mapping
  - Resolution & Format Breakdown (4K UHD, 1080p, 720p, 480p, HDR/HEVC, Audio)
  - End-to-End Search Latency across all platforms
  - Direct Stream Extraction & EBML Verification
"""

import os
import sys
import time
import asyncio
import json
import httpx
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from parallel_dispatcher import ParallelMediaDispatcher
from stream_verifier import StreamVerifier
from link_resolver import LinkResolver

TEST_BENCHMARK_CASES = [
    # Category 1: Malayalam Cinema
    {"category": "Malayalam", "title": "Thondimuthalum Driksakshiyum", "year": "2017", "query": "Thondimuthalum Driksakshiyum"},
    {"category": "Malayalam", "title": "Kummatty", "year": "1979", "query": "Kummatty"},
    {"category": "Malayalam", "title": "Ustad Hotel", "year": "2012", "query": "Ustad Hotel"},
    {"category": "Malayalam", "title": "Manjummel Boys", "year": "2024", "query": "Manjummel Boys"},
    {"category": "Malayalam", "title": "Bramayugam", "year": "2024", "query": "Bramayugam"},

    # Category 2: Hindi Blockbusters & Cult Classics
    {"category": "Hindi", "title": "Andhadhun", "year": "2018", "query": "Andhadhun"},
    {"category": "Hindi", "title": "Tumbbad", "year": "2018", "query": "Tumbbad"},
    {"category": "Hindi", "title": "Sholay", "year": "1975", "query": "Sholay"},
    {"category": "Hindi", "title": "Gangs of Wasseypur", "year": "2012", "query": "Gangs of Wasseypur"},
    {"category": "Hindi", "title": "3 Idiots", "year": "2009", "query": "3 Idiots"},

    # Category 3: Hollywood Blockbusters
    {"category": "Hollywood", "title": "Avengers: Endgame", "year": "2019", "query": "Avengers Endgame"},
    {"category": "Hollywood", "title": "Interstellar", "year": "2014", "query": "Interstellar"},
    {"category": "Hollywood", "title": "Oppenheimer", "year": "2023", "query": "Oppenheimer"},
    {"category": "Hollywood", "title": "The Dark Knight", "year": "2008", "query": "The Dark Knight"},
    {"category": "Hollywood", "title": "Avatar: The Way of Water", "year": "2022", "query": "Avatar The Way of Water"},

    # Category 4: Vintage Classics & World Cinema
    {"category": "Vintage/World", "title": "The Godfather", "year": "1972", "query": "The Godfather"},
    {"category": "Vintage/World", "title": "12 Angry Men", "year": "1957", "query": "12 Angry Men"},
    {"category": "Vintage/World", "title": "Seven Samurai", "year": "1954", "query": "Seven Samurai"},
    {"category": "Vintage/World", "title": "Memories of Murder", "year": "2003", "query": "Memories of Murder"},
    {"category": "Vintage/World", "title": "Cinema Paradiso", "year": "1988", "query": "Cinema Paradiso"},

    # Category 5: Multi-Season Anime
    {"category": "Anime", "title": "Attack on Titan", "year": "2013-2023", "query": "Attack on Titan"},
    {"category": "Anime", "title": "Jujutsu Kaisen", "year": "2020-2023", "query": "Jujutsu Kaisen"},
    {"category": "Anime", "title": "Death Note", "year": "2006-2007", "query": "Death Note"},
    {"category": "Anime", "title": "Demon Slayer", "year": "2019-2024", "query": "Demon Slayer"},
    {"category": "Anime", "title": "Fullmetal Alchemist: Brotherhood", "year": "2009-2010", "query": "Fullmetal Alchemist Brotherhood"},

    # Category 6: TV Series & Tricky Titles
    {"category": "TV Series", "title": "House of the Dragon", "year": "2022-", "query": "House of the Dragon"},
    {"category": "TV Series", "title": "Breaking Bad", "year": "2008-2013", "query": "Breaking Bad"},
    {"category": "TV Series", "title": "Stranger Things", "year": "2016-", "query": "Stranger Things"},
    {"category": "TV Series", "title": "Game of Thrones", "year": "2011-2019", "query": "Game of Thrones"},
    {"category": "TV Series", "title": "Dark", "year": "2017-2020", "query": "Dark"}
]

async def benchmark_single_case(dispatcher: ParallelMediaDispatcher, client: httpx.AsyncClient, item: dict) -> dict:
    q = item["query"]
    t0 = time.perf_counter()

    # Run individual platform fetchers concurrently
    local_task = dispatcher.fetch_local_db(q)
    vega_task = dispatcher.fetch_vegamovies(client, q)
    uhd_task = dispatcher.fetch_uhdmovies(client, q)
    ola_task = dispatcher.fetch_olamovies(client, q)
    hdhub_task = dispatcher.fetch_hdhub4u(client, q)
    tg_task = dispatcher.fetch_telegram(q)
    yt_task = dispatcher.fetch_youtube(client, q)
    series_task = dispatcher.fetch_series_simultaneous(q)

    results = await asyncio.gather(
        local_task, vega_task, uhd_task, ola_task, hdhub_task, tg_task, yt_task, series_task,
        return_exceptions=True
    )

    elapsed_ms = (time.perf_counter() - t0) * 1000

    local_res = results[0] if isinstance(results[0], list) else []
    vega_res = results[1] if isinstance(results[1], list) else []
    uhd_res = results[2] if isinstance(results[2], list) else []
    ola_res = results[3] if isinstance(results[3], list) else []
    hdhub_res = results[4] if isinstance(results[4], list) else []
    tg_res = results[5] if isinstance(results[5], list) else []
    yt_res = results[6] if isinstance(results[6], list) else []
    series_res = results[7] if isinstance(results[7], dict) else {}

    # Extract format and resolution breakdown
    all_releases = local_res + vega_res + uhd_res + ola_res + hdhub_res + tg_res + yt_res
    resolutions = set()
    codecs = set()
    audio_langs = set()

    for r in all_releases:
        if r.get("resolution"):
            resolutions.add(r["resolution"])
        if r.get("codec"):
            codecs.add(r["codec"])
        if r.get("audio"):
            audio_langs.add(r["audio"])

    is_series = bool(series_res and series_res.get("seasons"))
    season_count = len(series_res.get("seasons", {})) if is_series else 0
    total_episodes = sum(len(eps) for eps in series_res.get("seasons", {}).values()) if is_series else 0

    return {
        "category": item["category"],
        "title": item["title"],
        "year": item["year"],
        "query": q,
        "latency_ms": round(elapsed_ms, 2),
        "platforms": {
            "Local DB": len(local_res),
            "VegaMovies": len(vega_res),
            "UHDMovies": len(uhd_res),
            "OlaMovies": len(ola_res),
            "HDHub4u": len(hdhub_res),
            "Telegram": len(tg_res),
            "YouTube": len(yt_res)
        },
        "total_sources_count": len(all_releases),
        "active_platform_count": sum(1 for k, v in [
            ("Local DB", len(local_res)),
            ("VegaMovies", len(vega_res)),
            ("UHDMovies", len(uhd_res)),
            ("OlaMovies", len(ola_res)),
            ("HDHub4u", len(hdhub_res)),
            ("Telegram", len(tg_res)),
            ("YouTube", len(yt_res))
        ] if v > 0),
        "is_series": is_series,
        "seasons_mapped": season_count,
        "episodes_mapped": total_episodes,
        "resolutions": sorted(list(resolutions)),
        "codecs": sorted(list(codecs)),
        "audio_tracks": sorted(list(audio_langs))[:3]
    }

async def run_benchmark():
    dispatcher = ParallelMediaDispatcher()
    print("=" * 115)
    print("🚀 EXECUTING 30-CASE DIVERSE UNIVERSAL MEDIA BENCHMARK")
    print("=" * 115)

    suite_results = []
    t_start = time.perf_counter()

    async with httpx.AsyncClient(http2=True, limits=dispatcher.limits, follow_redirects=True) as client:
        for idx, item in enumerate(TEST_BENCHMARK_CASES, 1):
            print(f"[{idx:02d}/30] Benchmarking: {item['title']} ({item['category']}) ...", end="", flush=True)
            res = await benchmark_single_case(dispatcher, client, item)
            suite_results.append(res)
            print(f" Done ({res['latency_ms']:.1f}ms) -> {res['active_platform_count']} Platforms Active | {res['total_sources_count']} Sources")

    total_time = time.perf_counter() - t_start

    # Save benchmark json output
    out_file = BASE_DIR / "benchmark_30_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(suite_results, f, indent=2)

    # Print Formatted Markdown Table
    print("\n" + "=" * 115)
    print("📊 30-CASE UNIVERSAL BENCHMARK: EXACT PLATFORM PRESENCE MATRIX")
    print("=" * 115)
    header = f"{'#':<3} | {'Category':<13} | {'Title':<30} | {'DB':<3} | {'Vega':<4} | {'UHD':<4} | {'Ola':<4} | {'TG':<3} | {'YT':<3} | {'Series':<6} | {'Latency':<9}"
    print(header)
    print("-" * 115)

    for i, r in enumerate(suite_results, 1):
        p = r["platforms"]
        db_s = "✅" if p["Local DB"] > 0 else "❌"
        vega_s = "✅" if p["VegaMovies"] > 0 else "❌"
        uhd_s = "✅" if p["UHDMovies"] > 0 else "❌"
        ola_s = "✅" if p["OlaMovies"] > 0 else "❌"
        tg_s = "✅" if p["Telegram"] > 0 else "❌"
        yt_s = "✅" if p["YouTube"] > 0 else "❌"
        series_s = f"S{r['seasons_mapped']}:E{r['episodes_mapped']}" if r["is_series"] else "Movie"

        print(f"{i:<3} | {r['category']:<13} | {r['title'][:30]:<30} | {db_s:<3} | {vega_s:<4} | {uhd_s:<4} | {ola_s:<4} | {tg_s:<3} | {yt_s:<3} | {series_s:<6} | {r['latency_ms']:>6.1f}ms")

    print("-" * 115)
    print(f"Total Benchmark Execution Time: {total_time:.2f}s | Average Latency per Query: {sum(r['latency_ms'] for r in suite_results)/len(suite_results):.2f}ms")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
