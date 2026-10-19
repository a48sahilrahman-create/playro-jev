import urllib.request
import urllib.parse
import json
import time

base = "http://localhost:7860"

# 1. Test Root
r_root = urllib.request.urlopen(f"{base}/")
print(f"[1] Root /: {r_root.status} | HTML length: {len(r_root.read())} bytes")

# 2. Test Catalog
r_cat = urllib.request.urlopen(f"{base}/api/catalog/trending")
cat_data = json.loads(r_cat.read().decode('utf-8'))
print(f"[2] Catalog Trending count: {len(cat_data.get('results', []))} items")

# 3. Test Embed Sources
r_embed = urllib.request.urlopen(f"{base}/api/embed/sources?id=tt0137523&type=movie")
embed_data = json.loads(r_embed.read().decode('utf-8'))
print(f"[3] Embed Sources status: {r_embed.status} | Preferred: {embed_data.get('preferred')} | Providers: {list(embed_data.get('sources', {}).keys())}")

# 4. Test HLS Master Manifest with local test clip
sample_mp4 = "D:/playro_jev/static/test_clips/clip1_bunny.mp4"
req_master = urllib.request.Request(f"{base}/api/hls/master.m3u8?url={urllib.parse.quote(sample_mp4, safe='')}")
r_master = urllib.request.urlopen(req_master)
master_text = r_master.read().decode("utf-8")
print(f"[4] HLS Master status: {r_master.status} | Content-Type: {r_master.headers.get('content-type')}")
lines = master_text.strip().split("\n")
print(f"    Master playlist lines: {len(lines)} | Target media URL: {lines[-1]}")

# 5. Test HLS Media Playlist & Segment 0 Slicing
media_url = lines[-1]
r_media = urllib.request.urlopen(media_url)
media_text = r_media.read().decode("utf-8")
media_lines = [l for l in media_text.strip().split("\n") if l.endswith(".ts")]
print(f"[5] HLS Media playlist segments count: {len(media_lines)}")
if media_lines:
    seg_url = media_lines[0]
    t0 = time.perf_counter()
    r_seg = urllib.request.urlopen(seg_url)
    seg_bytes = r_seg.read()
    elapsed = round((time.perf_counter() - t0) * 1000, 2)
    sync_byte_hex = hex(seg_bytes[0]) if len(seg_bytes) > 0 else "None"
    print(f"    HLS Segment 0 sliced: {len(seg_bytes)} bytes in {elapsed}ms | Sync Byte: {sync_byte_hex} (0x47 = valid TS)")

# 6. Test Resolve URL with Quota Exceeded Link (Chernobyl from Image #2)
chernobyl_url = "https://aws.amazon-in.workers.dev/d67075f0f3536fae26615ae262e31ea4cdb::42c238b1f50684f8ee75a7c5c04bde24/1398010311/Chernobyl"
encoded_url = urllib.parse.quote(chernobyl_url, safe="")
r_resolve = urllib.request.urlopen(f"{base}/api/resolve-url?url={encoded_url}")
resolve_data = json.loads(r_resolve.read().decode('utf-8'))
print(f"[6] Resolve Chernobyl Link from Image #2:")
print(f"    valid: {resolve_data.get('valid')}")
print(f"    format: {resolve_data.get('format')}")
print(f"    direct_url: {resolve_data.get('direct_url')[:60]}...")
