import asyncio
import time
import httpx
from parallel_dispatcher import ParallelMediaDispatcher, is_strict_match, clean_media_title

async def test_movies():
    d = ParallelMediaDispatcher()
    queries = [
        "Inception",
        "Kummatty (1979)",
        "Interstellar",
        "Avengers: Endgame",
        "Dune: Part Two",
        "Oppenheimer",
        "Manjummel Boys",
        "Spirited Away",
        "The Dark Knight",
        "Kantara"
    ]

    print(f"{'Query':<25} | {'DB':<4} | {'Vega':<5} | {'UHD':<5} | {'Ola':<5} | {'HDHub':<6} | {'YT':<4} | {'Total':<6} | {'Time (s)'}")
    print("-" * 80)

    for q in queries:
        t0 = time.perf_counter()
        results_by_plat = {}
        async for batch in d.stream_all_parallel(q):
            for item in batch:
                if isinstance(item, dict) and not item.get("is_series"):
                    p = item.get("platform", "Unknown")
                    results_by_plat[p] = results_by_plat.get(p, 0) + 1
        elapsed = time.perf_counter() - t0
        total = sum(results_by_plat.values())
        print(f"{q:<25} | {results_by_plat.get('Local DB', 0):<4} | {results_by_plat.get('VegaMovies', 0):<5} | {results_by_plat.get('UHDMovies', 0):<5} | {results_by_plat.get('OlaMovies', 0):<5} | {results_by_plat.get('HDHub4u', 0):<6} | {results_by_plat.get('YouTube', 0):<4} | {total:<6} | {elapsed:.2f}s")

if __name__ == "__main__":
    asyncio.run(test_movies())
