import httpx, re, base64
from bs4 import BeautifulSoup

url = 'https://vcloud.fit/isruubp9lrrs3cu'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

with httpx.Client(headers=headers, follow_redirects=True, timeout=10.0) as client:
    r1 = client.get(url)
    html1 = r1.text
    atob_m = re.search(r"atob\(atob\((['\"][a-zA-Z0-9+/=]+['\"])\)\)", html1)
    if atob_m:
        raw_tok = atob_m.group(1).strip('\'"')
        b1 = base64.b64decode(raw_tok).decode('utf-8')
        step2_url = base64.b64decode(b1).decode('utf-8')
        print('Decoded Step 2 URL:', step2_url)
        r2 = client.get(step2_url, headers={'Referer': url, **headers})
        html2 = r2.text
        print('Step 2 HTML length:', len(html2))
        soup2 = BeautifulSoup(html2, 'html.parser')
        for a in soup2.find_all('a', href=True):
            print('  Link in Step 2:', a.get_text(strip=True), '-->', a['href'])

        # Look for direct video, R2, Pixeldrain, Cloudflare in html2
        r2_matches = re.findall(r'https?://[a-zA-Z0-9.-]+\.r2\.cloudflarestorage\.com/[^\s\'"<>\\]+', html2)
        print('R2 matches in Step 2:', r2_matches)

        # Check download buttons or javascript in step 2
        for s in soup2.find_all('script'):
            if s.string and ('location' in s.string or 'token' in s.string or 'http' in s.string or 'download' in s.string):
                print('Script in step 2:', s.string[:300])
