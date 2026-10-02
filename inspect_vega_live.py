import httpx
from bs4 import BeautifulSoup

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"}
for url in ["https://1vegamovies.lat/", "https://vegamovies.gallery/"]:
    try:
        r = httpx.get(url, headers=headers, follow_redirects=True, timeout=5.0)
        soup = BeautifulSoup(r.text, "lxml")
        print(f"=== {url} ===")
        print(f"Status: {r.status_code}, Final: {r.url}, Title: {soup.title.string if soup.title else 'None'}")
        links = [a.get("href") for a in soup.find_all("a", href=True) if len(a.get("href")) > 10][:10]
        print(f"Sample links: {links[:5]}")
        forms = [(f.get("action"), [i.get("name") for i in f.find_all("input")]) for f in soup.find_all("form")]
        print(f"Forms: {forms}")
    except Exception as e:
        print(f"Error {url}: {e}")
