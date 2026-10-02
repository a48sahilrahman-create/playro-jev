import sys, asyncio, httpx, subprocess, os
sys.path.append(r'C:\Users\sahil\.claude\skills\play\tools')
from link_resolver import LinkResolver
from stream_verifier import StreamVerifier
from play_engine import find_vlc

async def resolve_option_2():
    resolver = LinkResolver()
    verifier = StreamVerifier()
    vlc_path = find_vlc()

    # Target URL for option 2 (2160p HEVC UHDMovies)
    target_urls = [
        "https://cloud.unblockedgames.world/?sid=aDBQeDhZNVBsY1I5cVlWUzJJM1pvc3lPMUw2ZjY1MzZjQ0s2MnFuM3BQS3B1R2w1ZjhIY1NXKzhXbEVsMjVsbg==",
        "https://cloud.unblockedgames.world/?sid=aDBQeDhZNVBsY1I5cVlWUzJJM1pvdXc0TTc5dzN0YVZFWFVXWHVtU0NhL0pSYVkrQ051NjVTaWlpYm05OXkwaw=="
    ]

    for u in target_urls:
        print(f"[*] Resolving: {u}...")
        res = await resolver.resolve_auto(u)
        print("  Resolved result:", res)
        dir_u = res.get("direct_url")
        if dir_u:
            v = await verifier.verify(dir_u)
            print("  Verified:", v)
            if v.get("valid"):
                print(f"[+] Successfully verified stream: {dir_u}")
                proc = subprocess.Popen([vlc_path, dir_u])
                print(f"[+] VLC Process Spawned with PID: {proc.pid}")
                return {
                    "status": "PLAYING",
                    "pid": proc.pid,
                    "stream_url": dir_u,
                    "verification": v
                }

    # If DriveSeed unblockedgames requires live session follow-up
    print("[-] DriveSeed fallback check...")

if __name__ == '__main__':
    asyncio.run(resolve_option_2())
