import re
import time
from curl_cffi import requests
from bs4 import BeautifulSoup

def test_driveseed():
    s = requests.Session(impersonate='chrome120')
    url = 'https://cloud.unblockedgames.world/?sid=aDBQeDhZNVBsY1I5cVlWUzJJM1pvc3lPMUw2ZjY1MzZjQ0s2MnFuM3BQS3B1R2w1ZjhIY1NXKzhXbEVsMjVsbg=='

    t0 = time.perf_counter()
    print(f"[*] Fetching Step 1: {url}")
    r1 = s.get(url, timeout=5.0)
    soup1 = BeautifulSoup(r1.text, 'lxml')
    f1 = soup1.find('form')
    if not f1:
        print("[-] No form in step 1")
        return

    action1 = f1.get('action') or url
    data1 = {inp.get('name'): inp.get('value', '') for inp in f1.find_all('input') if inp.get('name')}
    print(f"[*] Posting Step 1 -> {action1}")

    r2 = s.post(action1, data=data1, headers={'Referer': url}, timeout=5.0)
    soup2 = BeautifulSoup(r2.text, 'lxml')
    f2 = soup2.find('form')
    if not f2:
        print("[-] No form in step 2")
        return

    action2 = f2.get('action') or r2.url
    data2 = {inp.get('name'): inp.get('value', '') for inp in f2.find_all('input') if inp.get('name')}
    print(f"[*] Posting Step 2 -> {action2}")

    r3 = s.post(action2, data=data2, headers={'Referer': r2.url}, timeout=5.0)

    # Extract s_343 cookie
    cookie_match = re.search(r"s_343\(['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]", r3.text)
    if not cookie_match:
        print("[-] Could not extract s_343 cookie from step 3")
        return

    c_name = cookie_match.group(1)
    c_val = cookie_match.group(2)
    print(f"[+] Found session cookie: {c_name} = {c_val[:30]}...")

    s.cookies.set(c_name, c_val, domain="cloud.unblockedgames.world")

    go_url = f"https://cloud.unblockedgames.world/?go={c_name}"
    print(f"[*] Fetching final download page: {go_url}")
    r4 = s.get(go_url, headers={'Referer': r3.url}, timeout=5.0)
    print(f"[+] Step 4 Status: {r4.status_code}, Final URL: {r4.url}")

    soup4 = BeautifulSoup(r4.text, 'lxml')
    for a in soup4.find_all('a', href=True):
        href = a['href']
        text = a.get_text(strip=True)
        print(f"  Link: {href} -> {text}")

    # Check for direct downloads
    directs = re.findall(r'https?://(?:video-downloads\.googleusercontent\.com|video-seed\.dev|pub-[a-zA-Z0-9]+\.r2\.dev|pixeldrain\.(?:com|dev)/api/file)/[^\s\'"<>]+', r4.text)
    print(f"[+] Direct URLs found: {directs}")
    print(f"[+] Total Bypass Time: {round((time.perf_counter() - t0)*1000, 2)} ms")

if __name__ == '__main__':
    test_driveseed()
