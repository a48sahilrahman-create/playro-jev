import asyncio
import re
import time
from patchright.async_api import async_playwright

async def solve_cloudflare_and_extract(url: str, max_timeout: float = 20.0) -> dict:
    """
    Automated Cloudflare Turnstile & Intermediary Gateway Solver (< 20s adaptive wait).
    Monitors background network requests, clicks Cloudflare challenges if present,
    and extracts direct CDN streaming links (Cloudflare R2, Workers, Pixeldrain, OMDrive).
    """
    t0 = time.perf_counter()
    direct_found = None
    extracted_html = ""

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720}
            )
            page = await context.new_page()

            # Intercept video CDN streams from network responses
            async def on_response(resp):
                nonlocal direct_found
                u = resp.url
                if any(k in u for k in ["dl.olamdrive.com/dl", "r2.cloudflarestorage.com", "pixeldrain.dev", "workers.dev", "googleusercontent.com"]):
                    direct_found = u

            page.on("response", on_response)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass

            deadline = time.time() + max_timeout
            while time.time() < deadline:
                if direct_found:
                    break

                try:
                    title = await page.title()
                except Exception:
                    title = ""

                # Handle Cloudflare Challenge / Turnstile
                if "Just a moment" in title or "Cloudflare" in title:
                    for frame in page.frames:
                        if "challenges.cloudflare.com" in frame.url:
                            try:
                                cb = frame.locator('input[type="checkbox"], span.mark, .ctp-checkbox-label, #challenge-stage')
                                if await cb.count() > 0:
                                    await cb.first.click(timeout=1500)
                            except Exception:
                                pass
                else:
                    # Cloudflare challenge bypassed! Check for countdown / hold buttons
                    try:
                        hold_btn = page.locator(".cc-hold-target, button:has-text('Hold'), a:has-text('Download'), a:has-text('Continue')").first
                        if await hold_btn.is_visible(timeout=1000):
                            box = await hold_btn.bounding_box()
                            if box:
                                await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                                await page.mouse.down()
                                await asyncio.sleep(3.2)
                                await page.mouse.up()
                    except Exception:
                        pass

                    try:
                        content = await page.content()
                        extracted_html = content

                        # Check for Cloudflare R2
                        r2_m = re.search(r'https?://[a-zA-Z0-9.-]+\.r2\.cloudflarestorage\.com/[^\s\'"<>\\]+', content)
                        if r2_m:
                            direct_found = r2_m.group(0).replace("&amp;", "&")
                            break

                        # Check for Pixeldrain
                        pd_m = re.search(r'https?://pixeldrain\.(?:com|dev)/(?:u|api/file)/([a-zA-Z0-9]+)', content)
                        if pd_m:
                            direct_found = f"https://pixeldrain.dev/api/file/{pd_m.group(1)}"
                            break

                        # Check for OMDrive
                        dl_m = re.search(r'https?://dl\.olamdrive\.com/dl\?t=[^\s\'"<>\\]+', content)
                        if dl_m:
                            direct_found = dl_m.group(0)
                            break
                    except Exception:
                        pass

                await asyncio.sleep(0.8)

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
        "host": "Cloudflare Bypass HTML",
        "html": extracted_html,
        "elapsed_ms": round(elapsed, 2)
    }

if __name__ == '__main__':
    async def run():
        u = "https://links.olamovies.mov/34993"
        print(f"Bypassing Cloudflare on {u}...")
        res = await solve_cloudflare_and_extract(u, max_timeout=20.0)
        print("Outcome:", res.get("status"), "| Host:", res.get("host"), "| Elapsed:", res.get("elapsed_ms"), "ms")
        if res.get("direct_url"):
            print("Direct Stream URL:", res.get("direct_url")[:80])

    asyncio.run(run())
