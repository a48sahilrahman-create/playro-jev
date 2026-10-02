import asyncio
import re
import time
from patchright.async_api import async_playwright

async def solve_cloudflare_url(url: str, max_timeout: float = 20.0) -> dict:
    t0 = time.perf_counter()
    direct_found = None
    extracted_html = ""

    try:
        async with async_playwright() as p:
            # Launch stealth headless browser
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage"
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720}
            )
            page = await context.new_page()

            # Intercept background network responses for direct CDN video streams
            async def on_response(resp):
                nonlocal direct_found
                u = resp.url
                if any(k in u for k in ["dl.olamdrive.com/dl", "r2.cloudflarestorage", "pixeldrain.dev", "workers.dev", "googleusercontent.com"]):
                    direct_found = u

            page.on("response", on_response)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=12000)
            except Exception:
                pass

            # Loop up to max_timeout seconds checking for challenge resolution or button appearance
            deadline = time.time() + max_timeout
            while time.time() < deadline:
                if direct_found:
                    break

                title = await page.title()
                content = await page.content()

                # If Cloudflare "Just a moment..." challenge is active, attempt Turnstile click
                if "Just a moment" in title or "challenges.cloudflare.com" in content:
                    for frame in page.frames:
                        try:
                            # Try finding turnstile checkbox inside Cloudflare iframe
                            cb = frame.locator('input[type="checkbox"], .ctp-checkbox-label, #challenge-stage')
                            if await cb.count() > 0:
                                box = await cb.first.bounding_box()
                                if box:
                                    await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        except Exception:
                            pass
                else:
                    # Cloudflare passed! Look for download / hold buttons
                    hold_btn = page.locator(".cc-hold-target, button:has-text('Hold'), a:has-text('Download'), a:has-text('Continue')").first
                    if await hold_btn.is_visible(timeout=1000):
                        try:
                            box = await hold_btn.bounding_box()
                            if box:
                                await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                                await page.mouse.down()
                                await asyncio.sleep(3.2)
                                await page.mouse.up()
                        except Exception:
                            pass

                    # Extract HTML content
                    extracted_html = content
                    # Check for direct video links in page content
                    r2_m = re.search(r'https?://[a-zA-Z0-9.-]+\.r2\.cloudflarestorage\.com/[^\s\'"<>\\]+', content)
                    if r2_m:
                        direct_found = r2_m.group(0)
                        break

                    pd_m = re.search(r'https?://pixeldrain\.(?:com|dev)/(?:u|api/file)/([a-zA-Z0-9]+)', content)
                    if pd_m:
                        direct_found = f"https://pixeldrain.dev/api/file/{pd_m.group(1)}"
                        break

                    dl_m = re.search(r'https?://dl\.olamdrive\.com/dl\?t=[^\s\'"<>\\]+', content)
                    if dl_m:
                        direct_found = dl_m.group(0)
                        break

                await asyncio.sleep(0.5)

            await browser.close()

    except Exception as e:
        return {"status": "ERROR", "error": str(e), "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}

    elapsed = (time.perf_counter() - t0) * 1000
    if direct_found:
        return {
            "status": "SUCCESS",
            "host": "Cloudflare Stealth Bypass",
            "direct_url": direct_found,
            "elapsed_ms": round(elapsed, 2)
        }

    return {
        "status": "SUCCESS" if extracted_html else "FAILED",
        "host": "Cloudflare Challenge Solved",
        "html": extracted_html,
        "elapsed_ms": round(elapsed, 2)
    }

async def main():
    test_url = "https://links.olamovies.mov/34993"
    print(f"Solving Cloudflare Challenge on: {test_url}...")
    res = await solve_cloudflare_url(test_url, max_timeout=20.0)
    print("Solver Result:", res.get("status"), "| Host:", res.get("host"), "| Elapsed:", res.get("elapsed_ms"), "ms")
    if res.get("direct_url"):
        print("Direct URL found:", res.get("direct_url")[:80])

if __name__ == "__main__":
    asyncio.run(main())
