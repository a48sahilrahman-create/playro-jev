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
    print("R1 Status:", r1.status_code, "Final URL:", r1.url)
    soup1 = BeautifulSoup(r1.text, "html.parser")
    form1 = soup1.find("form")
    print("Form 1:", form1.get("action") if form1 else "NO FORM")
    if form1:
        inputs1 = {inp.get("name"): inp.get("value", "") for inp in form1.find_all("input") if inp.get("name")}
        print("Inputs 1:", inputs1)
        action1 = form1.get("action") or str(r1.url)
        if action1.startswith("/"):
            action1 = f"{r1.url.scheme}://{r1.url.netloc}{action1}"
        r2 = client.post(action1, data=inputs1, headers={"Referer": str(r1.url)})
        print("R2 Status:", r2.status_code, "Final URL:", r2.url)
        print("R2 text preview:", r2.text[:500])
        soup2 = BeautifulSoup(r2.text, "html.parser")
        form2 = soup2.find("form")
        print("Form 2:", form2.get("action") if form2 else "NO FORM")
        if form2:
            inputs2 = {inp.get("name"): inp.get("value", "") for inp in form2.find_all("input") if inp.get("name")}
            print("Inputs 2:", inputs2)
