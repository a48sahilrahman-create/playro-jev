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
            const scripts = Array.from(document.querySelectorAll('script[src*="_next/static/chunks"]')).map(s => s.src);
            const results = [];
            for (const s of scripts) {
                try {
                    const r = await fetch(s);
                    const txt = await r.text();
                    const apis = txt.match(/\\/api\\/[^\\s"';<>]+/g) || [];
                    const urls = txt.match(/https?:\\/\\/[^\\s"';<>]+/g) || [];
                    results.push({ src: s, apis: apis.slice(0, 10), urls: urls.slice(0, 10), len: txt.length });
                } catch(e) {}
            }
            return results;
        }"""

        res = await page.evaluate(js_code)
        for r in res:
            print("SRC:", r["src"])
            print("  APIs:", r["apis"])
            print("  URLs:", r["urls"])

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
