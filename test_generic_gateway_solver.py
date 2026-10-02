"""
Test script verifying the generalized, adaptive multi-hop form & cookie gateway solver.
"""
import re
import urllib.parse
from bs4 import BeautifulSoup
from curl_cffi import requests as stealth_requests

def solve_multistep_gateway(start_url: str):
    print(f"[*] Starting solver on: {start_url}")
    s = stealth_requests.Session(impersonate="chrome120")
    current_url = start_url

    r = s.get(current_url, timeout=10.0)
    current_url = str(r.url)

    for step in range(1, 9):
        print(f"\n[STEP {step}] Current URL: {current_url} (status: {r.status_code})")
        html = r.text
        soup = BeautifulSoup(html, "lxml")

        # 1. Direct CDN Check
        g_cdn = re.search(r'https?://video-downloads\.googleusercontent\.com/[^\s\'"<>\\]+', f"{current_url} {html}")
        if g_cdn:
            direct_u = g_cdn.group(0).replace("&amp;", "&").rstrip('\\"')
            print(">>> FOUND GOOGLE CDN DIRECT STREAM:", direct_u)
            return direct_u

        w_cdn = re.search(r'https?://[a-zA-Z0-9.-]+\.workers\.dev/[^\s\'"<>\\]+', html)
        if w_cdn:
            direct_u = w_cdn.group(0).replace("&amp;", "&").rstrip('\\"')
            print(">>> FOUND CLOUDFLARE WORKER STREAM:", direct_u)
            return direct_u

        # 2. Check if this is a File Landing Page (e.g. driveseed.org/file/..., /zfile/, or contains download options)
        is_file_landing = bool("/file/" in current_url or "/download" in current_url or "/view" in current_url or "/zfile/" in html or re.search(r'video-(?:gen|seed|plex)', html))

        if is_file_landing:
            print(f"  [*] Detected File Landing Page on: {current_url}")

            # A. Check Instant Download (VideoGen / VideoSeed / VideoPlex)
            inst_links = [a["href"] for a in soup.find_all("a", href=True) if any(k in a["href"] for k in ["video-gen", "video-seed", "video-plex", "video-downloads"])]
            if inst_links:
                inst_target = urllib.parse.urljoin(current_url, inst_links[0])
                print(f"  [->] Probing Instant Download option: {inst_target}")
                try:
                    r_inst = s.get(inst_target, headers={"Referer": current_url}, timeout=10.0)
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(str(r_inst.url)).query)
                    if "url" in qs and qs["url"]:
                        cand = qs["url"][0].replace("&amp;", "&").rstrip('\\"')
                        if "googleusercontent.com" in cand:
                            print(">>> EXTRACTED GOOGLE CDN FROM QUERY:", cand)
                            return cand
                    g_match = re.search(r'https?://video-downloads\.googleusercontent\.com/[^\s\'"<>\\]+', f"{str(r_inst.url)} {r_inst.text}")
                    if g_match:
                        cand = g_match.group(0).replace("&amp;", "&").rstrip('\\"')
                        print(">>> EXTRACTED GOOGLE CDN FROM RESPONSE:", cand)
                        return cand
                except Exception as e:
                    print(f"  [!] Instant download probe error: {e}")

            # B. Check Resume Cloud (/zfile/...)
            zfile_links = [a["href"] for a in soup.find_all("a", href=True) if "/zfile/" in a["href"]]
            # Also check if we can synthesize zfile from /file/
            if not zfile_links and "/file/" in current_url:
                zfile_links = [current_url.replace("/file/", "/zfile/")]

            if zfile_links:
                z_target = urllib.parse.urljoin(current_url, zfile_links[0])
                print(f"  [->] Probing Resume Cloud option: {z_target}")
                try:
                    r_z = s.get(z_target, headers={"Referer": current_url}, timeout=10.0)
                    w_match = re.search(r'https?://[a-zA-Z0-9.-]+\.workers\.dev/[^\s\'"<>\\]+', r_z.text)
                    if w_match:
                        cand = w_match.group(0).replace("&amp;", "&").rstrip('\\"')
                        print(">>> EXTRACTED WORKER STREAM FROM ZFILE:", cand)
                        return cand
                except Exception as e:
                    print(f"  [!] Zfile probe error: {e}")

            # C. Check for Pixeldrain or Cloudflare R2
            pxl_match = re.search(r'https?://pixeldrain\.(?:com|dev)/(?:u|api/file)/([a-zA-Z0-9]+)', html)
            if pxl_match and pxl_match.group(1) not in ["negn6f", ""]:
                direct_pxl = f"https://pixeldrain.dev/api/file/{pxl_match.group(1)}"
                print(">>> EXTRACTED PIXELDRAIN API STREAM:", direct_pxl)
                return direct_pxl

            r2_match = re.search(r'https?://[a-zA-Z0-9.-]+(?:\.r2\.cloudflarestorage\.com|\.r2\.dev)/[^\s\'"<>\\]+', html)
            if r2_match:
                direct_r2 = r2_match.group(0).replace("&amp;", "&").rstrip('\\"')
                print(">>> EXTRACTED R2 DIRECT STREAM:", direct_r2)
                return direct_r2

            break

        # 3. Check for Dynamic Cookie setters
        cookie_setters = []
        for m in re.finditer(r'(?:sc|s_\d+)\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']', html):
            c_name, c_val = m.group(1), m.group(2)
            cookie_setters.append((c_name, c_val))
            print(f"  [+] Extracted JS Cookie: {c_name} = {c_val[:15]}...")
            s.cookies.set(c_name, c_val)
            domain = urllib.parse.urlparse(current_url).netloc
            s.cookies.set(c_name, c_val, domain=domain)

        # 4. Check for Intermediate Form submissions (e.g. _lp_http, _lp_token, lp-s1-form)
        form = soup.find("form", id="lp-s1-form")
        if not form:
            # Only pick general forms if they have LinkProtector or gateway hidden inputs
            for candidate_form in soup.find_all("form"):
                inps = [inp.get("name", "") for inp in candidate_form.find_all("input") if inp.get("name")]
                if any(k.startswith("_lp_") or k in ["landing", "step", "token"] for k in inps):
                    form = candidate_form
                    break
        if not form and not cookie_setters:
            # If step 1, take first form with inputs
            first_f = soup.find("form")
            if first_f and first_f.find("input"):
                form = first_f

        if form and not cookie_setters:
            action = form.get("action") or current_url
            target_post_url = urllib.parse.urljoin(current_url, action)
            inputs = {inp.get("name"): inp.get("value", "") for inp in form.find_all("input") if inp.get("name")}
            print(f"  [->] Submitting Form to {target_post_url} with {len(inputs)} inputs: {list(inputs.keys())}")
            r = s.post(target_post_url, data=inputs, headers={"Referer": current_url}, timeout=10.0)
            current_url = str(r.url)
            continue

        # 5. Check for Next Redirect (setAttribute href, location.replace, meta refresh)
        next_target = None

        # setAttribute('href', '...')
        href_match = re.search(r'setAttribute\(\s*["\']href["\']\s*,\s*["\']([^"\']+)["\']', html)
        if href_match:
            next_target = href_match.group(1).replace(r'\/', '/')
            print(f"  [+] Found setAttribute redirect: {next_target}")

        # location.replace('...')
        if not next_target:
            loc_match = re.search(r'location\.replace\(\s*["\']([^"\']+)["\']', html)
            if loc_match:
                next_target = loc_match.group(1).replace(r'\/', '/')
                print(f"  [+] Found location.replace redirect: {next_target}")

        # meta refresh
        if not next_target:
            refresh = soup.find("meta", attrs={"http-equiv": re.compile(r"refresh", re.I)})
            if refresh and "url=" in refresh.get("content", "").lower():
                next_target = re.split(r'url=', refresh["content"], flags=re.I)[1].strip()
                print(f"  [+] Found meta refresh redirect: {next_target}")

        if next_target:
            next_url = urllib.parse.urljoin(current_url, next_target)
            print(f"  [->] Navigating to: {next_url}")
            r = s.get(next_url, headers={"Referer": current_url}, timeout=10.0)
            current_url = str(r.url)
            continue

        break

    print("[-] Solver loop ended.")
    return None

if __name__ == "__main__":
    test_url = 'https://en.thenaukriadda.in/?sid=THpveVRFRjJpV3pwVUxzNUZOQk5KN2ZKdDNxWFRHZnpaZjdqZDYyUzliWFcwRkJKK2dGcENBR1lXYzVaTXZZWWVqNDNnd2FndGVBN2lCR09ZYWdzSk9UaS9WanJnTTFIeDZzTVNwUkJHNFM0TUpUSGRERnlKWEpqcWNOVmNOdHhWbzFiTVNRclExTVBSOVNCZjBTZEk5NWdSU2dzbCtxQkpDSGIybWt3M2hCREJWYkJNY3lZZU11RDJFOTNxQXBkcjlsRlFIbWJIL0FnalR5ZzVwSE1OalAxSkZmN05PeU8xUUwrUmRDRDlQalpleDJXVmxLZC9EZHNqQkpUZzJJZg=='
    res = solve_multistep_gateway(test_url)
    print("\nFinal Result:", res)
