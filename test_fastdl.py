import sys, asyncio, httpx, re
sys.path.append(r'C:\Users\sahil\.claude\skills\play\tools')
from stream_verifier import StreamVerifier

async def test_fastdl():
    verifier = StreamVerifier()
    fastdl_url = 'https://fastdl.zip/embed.php?download=eZsauToGax85v1ABStJvMeL0V'
    async with httpx.AsyncClient(headers={'User-Agent': 'Mozilla/5.0'}, follow_redirects=True) as client:
        r = await client.get(fastdl_url)
        print('FastDL status:', r.status_code)
        m = re.search(r'https?://video-downloads\.googleusercontent\.com/[^\s\'"]+', r.text)
        if m:
            g_cdn = m.group(0).replace('&amp;', '&')
            print('Extracted Google CDN:', g_cdn[:70])
            v = await verifier.verify(g_cdn)
            print('Verified Google CDN:', v)

if __name__ == '__main__':
    asyncio.run(test_fastdl())
