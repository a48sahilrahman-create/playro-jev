import asyncio
from patchright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigating...")
        await page.goto("https://links.olamovies.mov/9583", timeout=30000)
        await asyncio.sleep(6)

        js_code = """async () => {
            const r = await fetch('https://links.olamovies.mov/_next/static/chunks/3j8bh_15u8ya_.js');
            const txt = await r.text();
            return txt;
        }"""

        chunk_code = await page.evaluate(js_code)
        with open("chunk_app.js", "w", encoding="utf-8") as f:
            f.write(chunk_code)
        print("Saved chunk_app.js, size:", len(chunk_code))

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
