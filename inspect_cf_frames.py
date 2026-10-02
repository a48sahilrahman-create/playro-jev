import asyncio
from patchright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto('https://links.olamovies.mov/34993', wait_until='domcontentloaded')
        await asyncio.sleep(4.0)
        print('Title:', await page.title())
        print('Frames count:', len(page.frames))
        for idx, f in enumerate(page.frames):
            print(f'Frame #{idx}: url={f.url}')
            try:
                content = await f.content()
                print(f'  Frame content snippet: {content[:300]}')
            except Exception as e:
                print(f'  Frame error: {e}')
        await browser.close()

if __name__ == '__main__':
    asyncio.run(inspect())
