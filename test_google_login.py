import asyncio
import json
import os
from patchright.async_api import async_playwright

async def test_google_login():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()

        print("[*] Navigating to PayBase login...")
        await page.goto("https://paybase.olamovies.mov/login?callback=https%3A%2F%2Fv4.olamovies.mov%2Fsso-login", timeout=30000)
        await asyncio.sleep(4)

        print(f"[*] PayBase Page title: {await page.title()}")
        print("[*] Clicking 'Continue with Google'...")

        try:
            async with context.expect_page(timeout=15000) as new_page_info:
                await page.locator("button:has-text('Continue with Google')").click()
            google_page = await new_page_info.value
            print(f"[+] Google Auth URL opened: {google_page.url[:80]}...")
            await asyncio.sleep(4)
            print(f"[+] Google Auth title: {await google_page.title()}")

            # Fill Email
            email_input = google_page.locator("input[type='email'], #identifierId")
            if await email_input.is_visible(timeout=5000):
                print("[*] Filling email: juvgeminipro@gmail.com...")
                await email_input.fill("juvgeminipro@gmail.com")
                await asyncio.sleep(1)
                await google_page.locator("#identifierNext, button:has-text('Next')").click()
                await asyncio.sleep(5)
                print(f"[*] After Email Next -> URL: {google_page.url[:80]} | Title: {await google_page.title()}")

                # Check if password field is visible
                pass_input = google_page.locator("input[type='password'], input[name='Passwd'], input[name='password']")
                if await pass_input.is_visible(timeout=6000):
                    print("[*] Filling password...")
                    await pass_input.fill("9061506630s")
                    await asyncio.sleep(1)
                    await google_page.locator("#passwordNext, button:has-text('Next')").click()
                    print("[*] Clicked Password Next. Waiting for redirect callback...")
                    await asyncio.sleep(10)
                    print(f"[*] After Password Next -> Google Page: {google_page.url[:80]} | Main Page: {page.url[:80]}")
                else:
                    body_txt = await google_page.inner_text("body")
                    print(f"[!] Password field not found. Google screen text:\n{body_txt[:400]}")
        except Exception as e:
            print(f"[!] Error during Google OAuth flow: {e}")

        # Check cookies
        cookies = await context.cookies()
        print(f"\n[+] Total cookies captured: {len(cookies)}")
        auth_cookies = [c for c in cookies if any(k in c.get('domain', '') for k in ['olamovies', 'paybase'])]
        print(f"[+] OlaMovies / PayBase cookies: {len(auth_cookies)}")
        for c in auth_cookies:
            print(f"   {c.get('name')} ({c.get('domain')}) = {c.get('value')[:30]}...")

        # Save cookies if any
        if auth_cookies:
            with open("olamovies_session_cookies.json", "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2)
            print("[+] Session cookies saved to olamovies_session_cookies.json")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_google_login())
