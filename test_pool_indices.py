import sys
sys.path.insert(0, r'C:\Users\sahil\.claude\skills\play\tools')
import asyncio
from play_app import PlayApp

app = PlayApp()
async def test():
    accumulated = []
    async for batch in app.dispatcher.stream_all_parallel('Obsession'):
        accumulated.extend(batch)

    r2160 = [a for a in accumulated if '2160' in str(a.get('resolution')).upper() and not a.get('is_youtube')]
    print('Total 2160P candidates:', len(r2160))
    for idx, r in enumerate(r2160, 1):
        print(f"#{idx}: {r.get('platform')} | size={r.get('size')} | url={r.get('target_url')[:60]}")

    # Notice where VegaMovies is:
    vega_indices = [i for i, r in enumerate(r2160) if r.get('platform') == 'VegaMovies']
    print("VegaMovies indices in 2160p pool:", vega_indices)

if __name__ == '__main__':
    asyncio.run(test())
