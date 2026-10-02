import asyncio
import httpx
from bs4 import BeautifulSoup
import re
import urllib.parse

url = 'https://en.thenaukriadda.in/?sid=THpveVRFRjJpV3pwVUxzNUZOQk5KN2ZKdDNxWFRHZnpaZjdqZDYyUzliWFcwRkJKK2dGcENBR1lXYzVaTXZZWWVqNDNnd2FndGVBN2lCR09ZYWdzSk9UaS9WanJnTTFIeDZzTVNwUkJHNFM0TUpUSGRERnlKWEpqcWNOVmNOdHhWbzFiTVNRclExTVBSOVNCZjBTZEk5NWdSU2dzbCtxQkpDSGIybWt3M2hCREJWYkJNY3lZZU11RDJFOTNxQXBkcjlsRlFIbWJIL0FnalR5ZzVwSE1OalAxSkZmN05PeU8xUUwrUmRDRDlQalpleDJXVmxLZC9EZHNqQkpUZzJJZg=='

async def trace():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    async with httpx.AsyncClient(follow_redirects=False, timeout=8.0) as client:
        r1 = await client.get(url, headers=headers)
        print('Step 1 status:', r1.status_code, 'url:', r1.url)
        soup1 = BeautifulSoup(r1.text, 'lxml')
        form1 = soup1.find('form')
        if form1:
            act1 = form1.get('action') or str(r1.url)
            act1 = urllib.parse.urljoin(str(r1.url), act1)
            inputs1 = {inp.get('name'): inp.get('value', '') for inp in form1.find_all('input') if inp.get('name')}
            print('Form 1 action:', act1, 'inputs:', list(inputs1.keys()))
            r2 = await client.post(act1, data=inputs1, headers={'Referer': str(r1.url), **headers})
            print('Step 2 status:', r2.status_code, 'url:', r2.url)
            soup2 = BeautifulSoup(r2.text, 'lxml')
            form2 = soup2.find('form')
            if form2:
                act2 = form2.get('action') or str(r2.url)
                act2 = urllib.parse.urljoin(str(r2.url), act2)
                inputs2 = {inp.get('name'): inp.get('value', '') for inp in form2.find_all('input') if inp.get('name')}
                print('Form 2 action:', act2, 'inputs:', list(inputs2.keys()))
                r3 = await client.post(act2, data=inputs2, headers={'Referer': str(r2.url), **headers})
                print('Step 3 status:', r3.status_code, 'url:', r3.url)
                s3 = BeautifulSoup(r3.text, 'lxml')
                forms3 = s3.find_all('form')
                print('Step 3 forms count:', len(forms3))
                for idx, f in enumerate(forms3):
                    inps = {inp.get('name'): inp.get('value', '') for inp in f.find_all('input') if inp.get('name')}
                    print(f'Form {idx} action: {f.get("action")} inputs: {inps}')
                links3 = [(a.get_text(strip=True), a['href']) for a in s3.find_all('a', href=True) if any(k in a['href'] for k in ['go=', 'unblocked', 'sid=', 'driveseed', 'token'])]
                print('Step 3 special links:', links3)
                scripts3 = [s.get_text() for s in s3.find_all('script') if any(k in s.get_text() for k in ['s_343', 'unblocked', 'go=', 'countdown', 'location'])]
                print('Step 3 matching scripts count:', len(scripts3))
                sc_match = re.search(r'sc\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']', scripts3[0])
                if sc_match:
                    c_name, c_val = sc_match.group(1), sc_match.group(2)
                    print(f'Found sc cookie: {c_name} -> {c_val[:40]}...')
                    client.cookies.set(c_name, c_val)
                    lp_url = f"https://en.thenaukriadda.in/?lp_go={c_name}"
                    r4 = await client.get(lp_url, headers={'Referer': str(r3.url), **headers})
                    print('Step 4 status:', r4.status_code, 'url:', r4.url)
                    if r4.status_code in [301, 302, 303, 307, 308]:
                        print('Step 4 redirect:', r4.headers.get('location'))
                    else:
                        print('Step 4 HTML preview:', r4.text[:500])
                        s4 = BeautifulSoup(r4.text, 'lxml')
                        print('Step 4 forms:', [f.get('action') for f in s4.find_all('form')])
                        print('Step 4 links:', [(a.get_text(strip=True), a['href']) for a in s4.find_all('a', href=True) if any(k in a['href'] for k in ['go=', 'unblocked', 'sid=', 'driveseed', 'token', 'hub', 'drive', 'download'])])
                        scripts4 = [s.get_text() for s in s4.find_all('script') if any(k in s.get_text() for k in ['s_343', 'unblocked', 'go=', 'countdown', 'location', 'replace', 'sc('])]
                        print('Step 4 scripts count:', len(scripts4))
                        loc_match = re.search(r'window\.location\.replace\(["\']([^"\']+)["\']\)', scripts4[0])
                        if loc_match:
                            driveseed_url = loc_match.group(1).replace('\\/', '/')
                            print('Redirecting to DriveSeed URL:', driveseed_url)
                            r5 = await client.get(driveseed_url, headers={'Referer': str(r4.url), **headers})
                            print('Step 5 status:', r5.status_code, 'url:', r5.url)
                            print('Step 5 HTML preview:', r5.text[:1000])
                            s5 = BeautifulSoup(r5.text, 'lxml')
                            scripts5 = [s.get_text() for s in s5.find_all('script')]
                            for i, sc in enumerate(scripts5):
                                print(f'Step 5 script {i}: {sc[:300]}')
                            buttons = [(a.get_text(strip=True), a['href']) for a in s5.find_all('a', href=True)]
                            print('Discovered buttons on DriveSeed landing:')
                            for txt, h in buttons:
                                print(f'  [BUTTON] {txt} -> {h}')

if __name__ == '__main__':
    asyncio.run(trace())
