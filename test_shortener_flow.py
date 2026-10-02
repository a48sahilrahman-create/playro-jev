import asyncio
from patchright.async_api import async_playwright

async def test_shortener():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigating to links.olamovies.mov/9583...")
        await page.goto("https://links.olamovies.mov/9583", timeout=30000)
        await asyncio.sleep(6)

        print("Waiting for popup / redirect after click...")
        try:
            async with context.expect_page(timeout=10000) as new_page_info:
                await page.locator("button:has-text('Login to Continue')").click()
            new_page = await new_page_info.value
            print("Opened new page URL:", new_page.url)
            await asyncio.sleep(8)
            print("New page URL after 8s:", new_page.url)
            print("New page title:", await new_page.title())
            content = await new_page.content()
            with open("shortener_page.html", "w", encoding="utf-8") as f:
                f.write(content)
            print("Saved shortener_page.html")
        except Exception as e:
            print("Click / expect_page error:", e)
            print("Main page current URL:", page.url)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_shortener())
