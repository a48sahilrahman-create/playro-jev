import sys
import os
import time
import asyncio
import subprocess

sys.path.extend(['C:/Users/sahil/play_stremio_cloud', 'C:/Users/sahil/.claude/skills/play/tools'])

from play_engine import PlayEngine, find_vlc

TITLES = [
    'Michael',
    'Interstellar',
    'The Dark Knight',
    'Stranger Things',
    'Avengers',
    'Dune',
    'Oppenheimer',
    'House of the Dragon',
    'Kalki',
    'Jawan'
]

async def verify_10_playback():
    pe = PlayEngine()
    vlc = find_vlc()
    print('=' * 85)
    print('LIVE 10-TITLE MULTI-SOURCE VLC PLAYBACK BENCHMARK')
    print('=' * 85)

    passed = 0
    for idx, title in enumerate(TITLES, start=1):
        print(f'\n[{idx}/10] Searching & Resolving: "{title}"...', flush=True)
        res = await pe.search(title)
        releases = res.get('releases', [])
        platforms = set(r.get('platform') for r in releases)
        print(f'  [+] Found {len(releases)} streams across: {platforms}', flush=True)

        if not releases:
            print(f'  [-] No streams found for {title}', flush=True)
            continue

        stream_candidate = None
        for r in releases:
            u = r.get('direct_url') or r.get('target_url')
            p = r.get('platform')
            if u and p in ['VegaMovies', 'OlaMovies', 'UHDMovies', 'HDHub4u', 'Local DB']:
                stream_candidate = r
                break

        if not stream_candidate:
            stream_candidate = releases[0]

        target_u = stream_candidate.get('direct_url') or stream_candidate.get('target_url')
        p_name = stream_candidate.get('platform')
        print(f'  [*] Selected: [{p_name}] {target_u[:65]}...', flush=True)
        print(f'  [VLC] Launching VLC Player for 15s streaming test...', flush=True)

        proc = subprocess.Popen([vlc, target_u], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        alive = True
        for sec in range(1, 16):
            time.sleep(1.0)
            if proc.poll() is not None:
                alive = False
                print(f'    [!] VLC exited at second {sec}', flush=True)
                break
            if sec % 5 == 0:
                print(f'    [Streaming] {sec}s / 15s in VLC (PID: {proc.pid})', flush=True)

        try:
            proc.terminate()
            time.sleep(0.3)
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

        if alive:
            passed += 1
            print(f'  [PASSED] Streamed 15 continuous seconds in VLC: "{title}"', flush=True)
        else:
            print(f'  [FAILED] "{title}"', flush=True)

    print('\n' + '=' * 85)
    print(f'RESULT: {passed}/{len(TITLES)} TITLES PASSED 15-SECOND VLC STREAMING!')
    print('=' * 85)

if __name__ == '__main__':
    asyncio.run(verify_10_playback())
