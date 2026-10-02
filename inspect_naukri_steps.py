from curl_cffi import requests as stealth_requests
import re
from bs4 import BeautifulSoup

url = 'https://en.thenaukriadda.in/?sid=THpveVRFRjJpV3pwVUxzNUZOQk5KN2ZKdDNxWFRHZnpaZjdqZDYyUzliWFcwRkJKK2dGcENBR1lXYzVaTXZZWWVqNDNnd2FndGVBN2lCR09ZYWdzSk9UaS9WanJnTTFIeDZzTVNwUkJHNFM0TUpUSGRERnlKWEpqcWNOVmNOdHhWbzFiTVNRclExTVBSOVNCZjBTZEk5NWdSU2dzbCtxQkpDSGIybWt3M2hCREJWYkJNY3lZZU11RDJFOTNxQXBkcjlsRlFIbWJIL0FnalR5ZzVwSE1OalAxSkZmN05PeU8xUUwrUmRDRDlQalpleDJXVmxLZC9EZHNqQkpUZzJJZg=='
s = stealth_requests.Session(impersonate='chrome120')

print("[STEP 1] GET start URL:", url)
r1 = s.get(url, timeout=10.0)
print("Step 1 Status:", r1.status_code, "Final URL:", r1.url)
soup1 = BeautifulSoup(r1.text, 'lxml')
f1 = soup1.find('form')
act1 = f1.get('action') or str(r1.url)
d1 = {inp.get('name'): inp.get('value', '') for inp in f1.find_all('input') if inp.get('name')}

print("[STEP 2] POST to:", act1, "keys:", list(d1.keys()))
r2 = s.post(act1, data=d1, headers={'Referer': str(r1.url)}, timeout=10.0)
print("Step 2 Status:", r2.status_code, "Final URL:", r2.url)
soup2 = BeautifulSoup(r2.text, 'lxml')
f2 = soup2.find('form', id='lp-s1-form') or soup2.find('form')
act2 = f2.get('action') or str(r2.url)
d2 = {inp.get('name'): inp.get('value', '') for inp in f2.find_all('input') if inp.get('name')}

print("[STEP 3] POST to:", act2, "keys:", list(d2.keys()))
r3 = s.post(act2, data=d2, headers={'Referer': str(r2.url)}, timeout=10.0)
print("Step 3 Status:", r3.status_code, "Final URL:", r3.url)

sc_match = re.search(r'sc\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']', r3.text)
go_match = re.search(r'setAttribute\(\s*["\']href["\']\s*,\s*["\']([^"\']+)["\']', r3.text)

print("sc_match:", sc_match.groups() if sc_match else None)
print("go_match:", go_match.group(1) if go_match else None)

if sc_match and go_match:
    c_name, c_val = sc_match.group(1), sc_match.group(2)
    s.cookies.set(c_name, c_val, domain="en.thenaukriadda.in")
    s.cookies.set(c_name, c_val)
    go_url = go_match.group(1).replace(r'\/', '/')
    print("\n[STEP 4] GET:", go_url)
    r4 = s.get(go_url, headers={'Referer': str(r3.url)}, timeout=10.0)
    print("Step 4 Status:", r4.status_code, "Final URL:", r4.url)

    replace_match = re.search(r'location\.replace\(\s*["\']([^"\']+)["\']', r4.text)
    if replace_match:
        step5_url = replace_match.group(1).replace(r'\/', '/')
        print("\n[STEP 5] GET DriveSeed URL:", step5_url)
        r5 = s.get(step5_url, headers={'Referer': str(r4.url)}, timeout=10.0)
        print("Step 5 Status:", r5.status_code, "Final URL:", r5.url)
        soup5 = BeautifulSoup(r5.text, 'lxml')
        print("Step 5 Title:", soup5.title.string if soup5.title else 'None')
        print("Step 5 HTML len:", len(r5.text))
        print("Step 5 Head snippet:", r5.text[:500])
        loc_ds = re.search(r'location\.replace\(\s*["\']([^"\']+)["\']', r5.text)
        if loc_ds:
            step6_url = f"https://driveseed.org{loc_ds.group(1)}"
            print("\n[STEP 6] GET DriveSeed File:", step6_url)
            r6 = s.get(step6_url, headers={'Referer': str(r5.url)}, timeout=10.0)
            print("Step 6 Status:", r6.status_code, "Final URL:", r6.url)
            soup6 = BeautifulSoup(r6.text, 'lxml')
            print("Step 6 Title:", soup6.title.string if soup6.title else 'None')
            print("Step 6 HTML len:", len(r6.text))
            zfile_url = "https://driveseed.org/zfile/jzRgLlPGz1vlbh6vCA0l"
            print("\n[STEP 7] GET Resume Cloud (zfile):", zfile_url)
            r7 = s.get(zfile_url, headers={'Referer': step6_url}, timeout=10.0)
            print("Step 7 Status:", r7.status_code, "Final URL:", r7.url)
            print("Step 7 snippet:", r7.text[:400])
            w_match = re.search(r'https?://[a-zA-Z0-9.-]+\.workers\.dev/[^\s\'"<>\\]+', r7.text)
            if w_match:
                print("  FOUND CLOUDFLARE WORKER DIRECT STREAM:", w_match.group(0))


