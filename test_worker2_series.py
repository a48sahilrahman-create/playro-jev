import asyncio
from parallel_dispatcher import ParallelMediaDispatcher

async def test_series():
    disp = ParallelMediaDispatcher()
    queries = [
        'Attack on Titan',
        'Death Note',
        'Jujutsu Kaisen',
        'Breaking Bad',
        'Hajime no Ippo',
        'Jujutsu Kaisen 0',
        'Inception'
    ]
    for q in queries:
        print('='*70)
        print('QUERY:', q)
        print('='*70)
        s_data = await disp.fetch_series_simultaneous(q)
        if s_data and s_data.get('is_series'):
            print('-> TV SERIES DETECTED:', s_data.get('show_title'))
            seasons = s_data.get('seasons', {})
            print('Total Seasons Found:', len(seasons))
            for s_name, eps in seasons.items():
                print(f'  [{s_name}] -> {len(eps)} items')
                for ep_name, quals in list(eps.items())[:3]:
                    q_list = [f"{x['resolution']} {x['codec']} ({x['platform']})" for x in quals]
                    print(f'     {ep_name}: {q_list}')
        else:
            print('-> MOVIE / STANDALONE DETECTED')
            all_batches = []
            async for batch in disp.stream_all_parallel(q):
                all_batches.extend(batch)
            print(f'Total releases yielded in parallel stream: {len(all_batches)}')
            for b in all_batches[:4]:
                print(f"   [{b.get('platform')}] {b.get('title')} -- {b.get('resolution')} -- {b.get('size')} -- {b.get('source_origin')}")

if __name__ == '__main__':
    asyncio.run(test_series())
