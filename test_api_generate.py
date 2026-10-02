import asyncio
from patchright.async_api import async_playwright

async def test_generate():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigating to https://links.olamovies.mov/9583...")
        await page.goto("https://links.olamovies.mov/9583", timeout=30000)
        await asyncio.sleep(6)

        print("Testing /api/generate directly...")
        res = await page.evaluate("""async () => {
            try {
                const r = await fetch('/api/generate', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id: '9583'})
                });
                const data = await r.json();
                return { status: r.status, data };
            } catch(e) {
                return { error: e.toString() };
            }
        }""")
        print("API Response:", res)

        print("Testing /api/get-filename...")
        res2 = await page.evaluate("""async () => {
            try {
                const r = await fetch('/api/get-filename?id=9583');
                const data = await r.json();
                return { status: r.status, data };
            } catch(e) {
                return { error: e.toString() };
            }
        }""")
        print("Get Filename Response:", res2)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_generate())
