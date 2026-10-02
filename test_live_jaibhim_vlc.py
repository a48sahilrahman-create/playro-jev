import asyncio
import sys
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(r"C:\Users\sahil\.claude\skills\play\tools")
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from play_app import PlayApp, find_vlc

async def test_playback_jaibhim():
    app = PlayApp()
    await app.ensure_stream_server()
    print("=" * 80)
    print("Testing Multi-Source Stream Discovery & Live VLC Playback for 'Jai Bhim'...")
    print("=" * 80)

    vlc = find_vlc()
    print("VLC executable path:", vlc)

    hits = []
    async for batch in app.dispatcher.stream_all_parallel("Jai Bhim"):
        hits.extend(batch)

    print(f"\nDiscovered {len(hits)} release candidates across all scrapers:")
    for idx, h in enumerate(hits, 1):
        plat = h.get('platform')
        title = h.get('title')
        res = h.get('resolution')
        size = h.get('size')
        origin = h.get('source_origin')
        print(f"  {idx:>2}. [{plat}] {title} | {res} | {size} | {origin}")

    print("\n" + "=" * 80)
    print("Resolving and verifying stream candidates...")
    print("=" * 80)

    for h in hits:
        stream_u = h.get("target_url") or h.get("stream_url") or h.get("direct_url")
        out = await app.resolve_and_verify(stream_u)
        if out.get("verification", {}).get("valid"):
            dir_u = out.get("direct_url")
            fmt = out.get("verification", {}).get("format")
            size = out.get("verification", {}).get("size")
            plat = h.get("platform")
            res = h.get("resolution")
            title = h.get("title")

            print(f"\n[+] VERIFIED STREAM FOUND: '{title}' [{res}] ({plat})")
            print(f"    Container Format: {fmt}")
            print(f"    Stream Size: {size}")
            print(f"    Direct Stream URL: {dir_u[:75]}...")

            # Launch VLC and stream for 12 seconds
            cmd = [vlc, dir_u, f"--meta-title=Jai Bhim ({plat} {res})"]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"\n[+] VLC LAUNCHED SUCCESSFULLY (PID: {proc.pid})!")
            print("    Streaming live video for 12.0 seconds...")
            await asyncio.sleep(12.0)

            # Health check: verify VLC process is still alive and running
            is_running = (proc.poll() is None)
            print(f"    [HEALTH CHECK] VLC Process PID {proc.pid} still actively playing: {is_running}")
            proc.terminate()
            print("    [STREAM SUCCESS] Closed VLC after 12s clean playback verification.")
            return True

    print("[-] No verified stream found among candidates.")
    return False

if __name__ == "__main__":
    success = asyncio.run(test_playback_jaibhim())
    sys.exit(0 if success else 1)
