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
    r1 = client.get(url)
    soup1 = BeautifulSoup(r1.text, "html.parser")
    f1 = soup1.find("form")
    data1 = {inp.get("name"): inp.get("value", "") for inp in f1.find_all("input") if inp.get("name")}
    action1 = f1.get("action") or str(r1.url)

    r2 = client.post(action1, data=data1, headers={"Referer": str(r1.url)})
    soup2 = BeautifulSoup(r2.text, "html.parser")
    f2 = soup2.find("form")
    data2 = {inp.get("name"): inp.get("value", "") for inp in f2.find_all("input") if inp.get("name")}
    action2 = f2.get("action") or str(r2.url)

    r3 = client.post(action2, data=data2, headers={"Referer": str(r2.url)})
    cookie_match = re.search(r"s_343\(['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]", r3.text)
    if cookie_match:
        c_name, c_val = cookie_match.group(1), cookie_match.group(2)
        client.cookies.set(c_name, c_val, domain="cloud.unblockedgames.world")
        go_url = f"https://cloud.unblockedgames.world/?go={c_name}"
        r4 = client.get(go_url, headers={"Referer": str(r3.url)})
        print("R4 Status:", r4.status_code, "Final URL:", r4.url)
        print("R4 text snippet:", r4.text[:1500])

        soup4 = BeautifulSoup(r4.text, "html.parser")
        for a in soup4.find_all("a", href=True):
            print("Link in r4:", a["href"], a.get_text(strip=True))

        for form in soup4.find_all("form"):
            print("Form in r4:", form.get("action"))
            for inp in form.find_all("input"):
                print("  Input:", inp.get("name"), "=", inp.get("value"))
