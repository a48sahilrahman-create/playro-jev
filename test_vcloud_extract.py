import asyncio
import httpx
import re
import base64
from bs4 import BeautifulSoup

async def test_vcloud_token():
    url = "https://nexdrive.fit/genxfm784776386647/"
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True) as client:
        r1 = await client.get(url)
        v_m = re.search(r'https?://vcloud\.[a-z]+/([a-zA-Z0-9_-]+)', r1.text)
        if v_m:
            v_url = v_m.group(0)
            print("VCloud URL:", v_url)
            r2 = await client.get(v_url)

            # Find the atob(atob(...)) line
            match = re.search(r"atob\(atob\(['\"]([a-zA-Z0-9+/=]+)['\"]\)\)", r2.text)
            if match:
                tok = match.group(1)
                print("Raw base64 token:", tok[:40])
                dec1 = base64.b64decode(tok).decode('utf-8', errors='ignore')
                dec2 = base64.b64decode(dec1).decode('utf-8', errors='ignore')
                print("Decoded URL:", dec2)

                # Fetch step 2
                r3 = await client.get(dec2)
                print("Step 2 response length:", len(r3.text))
                for line in r3.text.splitlines():
                    if ".mkv" in line or ".mp4" in line or "r2." in line:
                        print("  Found stream line:", line.strip()[:100])

asyncio.run(test_vcloud_token())
