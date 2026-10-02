import asyncio
from patchright.async_api import async_playwright

async def test_solve():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.goto("https://links.olamovies.mov/34993", wait_until="domcontentloaded")
        print("Initial title:", await page.title())

        # Wait up to 20 seconds dynamically
        for tick in range(20):
            await asyncio.sleep(1.0)
            title = await page.title()
            print(f"[{tick+1}s] Title: {title}")

            if "Just a moment" not in title:
                print("Cloudflare Passed!")
                print("Content length:", len(await page.content()))
                break

            # Find Turnstile frame and click checkbox
            for frame in page.frames:
                if "challenges.cloudflare.com" in frame.url:
                    try:
                        btn = frame.locator('input[type="checkbox"], span.mark, .ctp-checkbox-label, #challenge-stage')
                        if await btn.count() > 0:
                            print("Clicking Turnstile button...")
                            await btn.first.click()
                    except Exception as e:
                        print("Click error:", e)

        print("Final URL:", page.url)
        await browser.close()

if __name__ == '__main__':
    asyncio.run(test_solve())
