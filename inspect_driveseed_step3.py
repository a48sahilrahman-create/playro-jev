import sys
sys.path.insert(0, r'C:\Users\sahil\.claude\skills\play\tools')
import httpx
from bs4 import BeautifulSoup
import re

url = "https://cloud.unblockedgames.world/?sid=aDBQeDhZNVBsY1I5cVlWUzJJM1pvdXc0TTc5dzN0YVZFWFVXWHVtU0NhL0pSYVkrQ051NjVTaWlpYm05OXkwaw=="

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

with httpx.Client(headers=headers, follow_redirects=True, timeout=10.0) as client:
    # 1. First Form
    r1 = client.get(url)
    soup1 = BeautifulSoup(r1.text, "html.parser")
    f1 = soup1.find("form")
    data1 = {inp.get("name"): inp.get("value", "") for inp in f1.find_all("input") if inp.get("name")}
    action1 = f1.get("action") or str(r1.url)

    # 2. Second Form
    r2 = client.post(action1, data=data1, headers={"Referer": str(r1.url)})
    soup2 = BeautifulSoup(r2.text, "html.parser")
    f2 = soup2.find("form")
    data2 = {inp.get("name"): inp.get("value", "") for inp in f2.find_all("input") if inp.get("name")}
    action2 = f2.get("action") or str(r2.url)
    print("Action 2:", action2)
    print("Data 2:", data2)

    # 3. Third Form
    r3 = client.post(action2, data=data2, headers={"Referer": str(r2.url)})
    print("R3 Status:", r3.status_code, "Final URL:", r3.url)
    print("R3 text snippet:", r3.text[:1000])

    # Look for cookies or javascript redirects in r3
    cookie_match = re.search(r"s_343\(['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]", r3.text)
    print("Cookie match in r3:", cookie_match.groups() if cookie_match else "None")

    # Look for any form, links, scripts in r3
    soup3 = BeautifulSoup(r3.text, "html.parser")
    f3 = soup3.find("form")
    print("Form 3 in r3:", f3.get("action") if f3 else "None")
    if f3:
        data3 = {inp.get("name"): inp.get("value", "") for inp in f3.find_all("input") if inp.get("name")}
        print("Data 3:", data3)

    for a in soup3.find_all("a", href=True):
        print("Link in r3:", a["href"], a.get_text(strip=True))

    for s in soup3.find_all("script"):
        if s.string and ("location" in s.string or "url" in s.string or "cookie" in s.string or "go=" in s.string):
            print("Script in r3:", s.string.strip()[:300])
