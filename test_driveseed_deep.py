import asyncio
import re
from curl_cffi import requests as stealth_requests
from bs4 import BeautifulSoup

url = "https://cloud.unblockedgames.world/?sid=a3Y4azk3STZ5RVphb1c0d0pkeDllaWVjc3NTd1dyeHJZSlNRUkszSnJFSGphampKSXNSZWwrcG5LYTJEYkE2UkZIZjJJc3VDQ1hhdW9jVTJmTTFzU29PMlFKZ0MzTmhvSC9NWEh6a0Z2T2MzWlhheUgxdzBURHNmME8xOFF0RkttbEp5aFg4VFFmMjVNWC9LSThQcW9qaUpJMS9BVVh2bGxmR2w3SURnbkFVMVk1emxkSDhuVnIzbjNZY0syOGNmc3BxT2JDWGZMZjlxbnd4OFU2TDFZMVNhcW96a1ZIZUQxa2xHeGdxUXQ5TUx5dmVBa3FlZmhXd2pZMHRjSkhzRA=="

s = stealth_requests.Session(impersonate="chrome120")
r1 = s.get(url)
f1 = BeautifulSoup(r1.text, "lxml").find("form")
action1 = f1.get("action") or url
data1 = {inp.get("name"): inp.get("value", "") for inp in f1.find_all("input") if inp.get("name")}
r2 = s.post(action1, data=data1, headers={"Referer": url})
f2 = BeautifulSoup(r2.text, "lxml").find("form")
action2 = f2.get("action") or r2.url
data2 = {inp.get("name"): inp.get("value", "") for inp in f2.find_all("input") if inp.get("name")}
r3 = s.post(action2, data=data2, headers={"Referer": r2.url})

c_match = re.search(r"s_343\(['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]", r3.text)
if c_match:
    tok = c_match.group(1)
    c_val = c_match.group(2)
    s.cookies.set(tok, c_val, domain="cloud.unblockedgames.world")
    s.cookies.set(tok, c_val)
    go_url = "https://cloud.unblockedgames.world/?go=" + tok
    r4 = s.get(go_url, headers={"Referer": r3.url})

    soup4 = BeautifulSoup(r4.text, "lxml")
    refresh = soup4.find("meta", attrs={"http-equiv": re.compile(r"refresh", re.I)})
    if refresh and "url=" in refresh.get("content", ""):
        dest = refresh["content"].split("url=")[1].strip()
        print("Navigating to dest:", dest)
        r5 = s.get(dest, headers={"Referer": r4.url})
        print("R5 URL:", r5.url)
        print("R5 text:\n", r5.text)

        loc = re.search(r'replace\([\'"]([^\'"]+)[\'"]\)', r5.text).group(1)
        final_url = "https://driveseed.org" + loc
        r6 = s.get(final_url, headers={"Referer": r5.url})
        print("R6 URL:", r6.url)
        print("R6 text snippet:\n", r6.text[:1500])

        soup6 = BeautifulSoup(r6.text, "lxml")
        instant_links = [a["href"] for a in soup6.find_all("a", href=True) if "video-gen" in a["href"] or "video-seed" in a["href"]]
        if instant_links:
            print("Found instant download link:", instant_links[0])
            r7 = s.get(instant_links[0], headers={"Referer": r6.url})
            print("R7 URL:", r7.url)
            print("R7 status:", r7.status_code)
            print("R7 text snippet:\n", r7.text[:1000])
