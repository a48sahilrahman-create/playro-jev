import re
import time
from curl_cffi import requests
from bs4 import BeautifulSoup

def inspect_step4():
    s = requests.Session(impersonate='chrome120')
    url = 'https://cloud.unblockedgames.world/?sid=aDBQeDhZNVBsY1I5cVlWUzJJM1pvc3lPMUw2ZjY1MzZjQ0s2MnFuM3BQS3B1R2w1ZjhIY1NXKzhXbEVsMjVsbg=='

    r1 = s.get(url, timeout=5.0)
    soup1 = BeautifulSoup(r1.text, 'lxml')
    f1 = soup1.find('form')
    action1 = f1.get('action') or url
    data1 = {inp.get('name'): inp.get('value', '') for inp in f1.find_all('input') if inp.get('name')}

    r2 = s.post(action1, data=data1, headers={'Referer': url}, timeout=5.0)
    soup2 = BeautifulSoup(r2.text, 'lxml')
    f2 = soup2.find('form')
    action2 = f2.get('action') or r2.url
    data2 = {inp.get('name'): inp.get('value', '') for inp in f2.find_all('input') if inp.get('name')}

    r3 = s.post(action2, data=data2, headers={'Referer': r2.url}, timeout=5.0)

    cookie_match = re.search(r"s_343\(['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]", r3.text)
    c_name = cookie_match.group(1)
    c_val = cookie_match.group(2)

    s.cookies.set(c_name, c_val, domain="cloud.unblockedgames.world")
    go_url = f"https://cloud.unblockedgames.world/?go={c_name}"
    r4 = s.get(go_url, headers={'Referer': r3.url}, timeout=5.0)

    print("Step 4 Raw HTML snippet:\n", r4.text[:2500])
    soup4 = BeautifulSoup(r4.text, 'lxml')
    print("\n--- ALL A TAGS ---")
    for a in soup4.find_all('a'):
        print(a)
    print("\n--- ALL FORMS ---")
    for f in soup4.find_all('form'):
        print(f)
    print("\n--- ALL SCRIPTS ---")
    for s_tag in soup4.find_all('script'):
        txt = s_tag.string or s_tag.get_text()
        if txt and 'window' in txt or 'location' in txt or 'http' in txt:
            print("Script:", txt.strip())

if __name__ == '__main__':
    inspect_step4()
