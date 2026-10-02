import asyncio
import time
import sys
from pathlib import Path

# Add project root and jev scripts
sys.path.append(r"D:\playro_jev")
sys.path.append(str(Path.home() / ".claude" / "skills" / "jev" / "scripts"))

from jev_stream_resolver import JevStreamResolver

async def main():
    html = '''<div class='custom-wrapper-2026'>
        <h2 class='film-title'>Avatar The Way of Water 2022 2160p 4K UHD HDR Dual Audio</h2>
        <div class='ad-banner'><a href='https://fake-ad-domain.com/click'>DOWNLOAD NOW FREE HD</a></div>
        <div class='download-box-mutated'>
            <a href='https://driveseed.org/drive/avatar4k'>Fast Cloud DriveSeed 4K [18.2 GB]</a>
        </div>
    </div>'''

    t0 = time.perf_counter()
    streams = await JevStreamResolver.extract_media_streams(html, query="Avatar", platform="UHDMovies", timeout=2.5)
    dt = time.perf_counter() - t0
    print(f"Extracted {len(streams)} streams in {dt:.3f}s:")
    for s in streams:
        print(f" - {s['label']} -> {s['target_url']} (Res: {s['resolution']})")

    # Second call should hit LRU cache (< 1ms)
    t1 = time.perf_counter()
    cached_streams = await JevStreamResolver.extract_media_streams(html, query="Avatar", platform="UHDMovies")
    dt_cache = (time.perf_counter() - t1) * 1000
    print(f"Cache hit returned {len(cached_streams)} streams in {dt_cache:.2f}ms")

    # Gateway hop test
    hop_html = '''<div class='step2-box'>
        <p>Please wait 5 seconds before link unlocks...</p>
        <script>var x = atob(atob('YUhSMGNITTZMeTlrY21sMlpYTmxaV1F1YjNKuT19'));</script>
        <a href='https://driveseed.org/drive/real_stream_target'>Direct Download Cloud</a>
    </div>'''
    hop_res = await JevStreamResolver.resolve_gateway_hop(hop_html, current_url="https://techy.org/step2")
    print("Gateway Hop Result:", hop_res)

if __name__ == "__main__":
    asyncio.run(main())
