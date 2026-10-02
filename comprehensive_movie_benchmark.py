import asyncio
import time
from parallel_dispatcher import ParallelMediaDispatcher

async def run_comprehensive_movie_benchmark():
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
        "Kantara",
        "Jujutsu Kaisen 0",
        "Spider-Man: Across the Spider-Verse",
        "John Wick: Chapter 4",
        "1917",
        "Gladiator",
        "Premalu",
        "RRR (2022)",
        "Sholay",
        "KGF Chapter 2",
        "Pulp Fiction"
    ]

    print("=" * 105)
    print(f"{'#':<3} | {'Query':<32} | {'DB':<4} | {'Vega':<5} | {'UHD':<5} | {'Ola':<5} | {'YT':<4} | {'Total':<6} | {'Platforms':<20} | {'Time'}")
    print("=" * 105)

    grand_total_items = 0
    start_all = time.perf_counter()

    for idx, q in enumerate(queries, 1):
        t0 = time.perf_counter()
        plat_counts = {}
        async for batch in d.stream_all_parallel(q):
            for item in batch:
                if isinstance(item, dict) and not item.get("is_series"):
                    p = item.get("platform", "Unknown")
                    plat_counts[p] = plat_counts.get(p, 0) + 1
        elapsed = time.perf_counter() - t0
        total = sum(plat_counts.values())
        grand_total_items += total
        plat_names = ", ".join(sorted(plat_counts.keys()))

        print(f"{idx:<3} | {q:<32} | {plat_counts.get('Local DB', 0):<4} | {plat_counts.get('VegaMovies', 0):<5} | {plat_counts.get('UHDMovies', 0):<5} | {plat_counts.get('OlaMovies', 0):<5} | {plat_counts.get('YouTube', 0):<4} | {total:<6} | {plat_names:<20} | {elapsed:.2f}s")

    total_time = time.perf_counter() - start_all
    print("=" * 105)
    print(f"Summary: {len(queries)} movie test cases evaluated. Total stream targets yielded: {grand_total_items}. Elapsed: {total_time:.2f}s.")

if __name__ == "__main__":
    asyncio.run(run_comprehensive_movie_benchmark())
