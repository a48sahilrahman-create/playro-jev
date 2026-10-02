import asyncio
from play_app import PlayApp

async def test_app_flows():
    app = PlayApp()

    print("=== TESTING PLAY_APP SERIES FLOW: Attack on Titan ===")
    series_data = await app.dispatcher.fetch_series_simultaneous("Attack on Titan")
    if series_data:
        print("Show Title:", series_data["show_title"])
        for s_name, eps in series_data["seasons"].items():
            print(f"[{s_name}] -> {len(eps)} items: {list(eps.keys())[:4]}")
            for ep_name in list(eps.keys())[:2]:
                q_summary = [f"{q['resolution']} {q['codec']} ({q['platform']})" for q in eps[ep_name]]
                print(f"   {ep_name}: {q_summary}")

    print("\n=== TESTING PLAY_APP SERIES FLOW: Death Note ===")
    series_data = await app.dispatcher.fetch_series_simultaneous("Death Note")
    if series_data:
        print("Show Title:", series_data["show_title"])
        for s_name, eps in series_data["seasons"].items():
            print(f"[{s_name}] -> {len(eps)} items: {list(eps.keys())[:4]}")
            for ep_name in list(eps.keys())[:2]:
                q_summary = [f"{q['resolution']} {q['codec']} ({q['platform']})" for q in eps[ep_name]]
                print(f"   {ep_name}: {q_summary}")

    print("\n=== TESTING PLAY_APP SERIES FLOW: Jujutsu Kaisen ===")
    series_data = await app.dispatcher.fetch_series_simultaneous("Jujutsu Kaisen")
    if series_data:
        print("Show Title:", series_data["show_title"])
        for s_name, eps in series_data["seasons"].items():
            print(f"[{s_name}] -> {len(eps)} items: {list(eps.keys())[:4]}")
            for ep_name in list(eps.keys())[:2]:
                q_summary = [f"{q['resolution']} {q['codec']} ({q['platform']})" for q in eps[ep_name]]
                print(f"   {ep_name}: {q_summary}")

    print("\n=== TESTING PLAY_APP SERIES FLOW: Breaking Bad ===")
    series_data = await app.dispatcher.fetch_series_simultaneous("Breaking Bad")
    if series_data:
        print("Show Title:", series_data["show_title"])
        for s_name, eps in series_data["seasons"].items():
            print(f"[{s_name}] -> {len(eps)} items: {list(eps.keys())[:4]}")
            for ep_name in list(eps.keys())[:2]:
                q_summary = [f"{q['resolution']} {q['codec']} ({q['platform']})" for q in eps[ep_name]]
                print(f"   {ep_name}: {q_summary}")

    print("\n=== TESTING PLAY_APP STANDALONE MOVIE FLOW: Hajime no Ippo ===")
    all_hits = []
    async for batch in app.dispatcher.stream_all_parallel("Hajime no Ippo"):
        all_hits.extend(batch)
    print(f"Total yields: {len(all_hits)}")
    for h in all_hits[:4]:
        print(f"   [{h.get('platform')}] {h.get('title')} | {h.get('resolution')} | {h.get('size')} | {h.get('source_origin')}")

    print("\n=== TESTING PLAY_APP STANDALONE MOVIE FLOW: Jujutsu Kaisen 0 ===")
    all_hits = []
    async for batch in app.dispatcher.stream_all_parallel("Jujutsu Kaisen 0"):
        all_hits.extend(batch)
    print(f"Total yields: {len(all_hits)}")
    for h in all_hits[:4]:
        print(f"   [{h.get('platform')}] {h.get('title')} | {h.get('resolution')} | {h.get('size')} | {h.get('source_origin')}")

if __name__ == "__main__":
    asyncio.run(test_app_flows())
