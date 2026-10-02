import asyncio
import json
from patchright.async_api import async_playwright

async def run_login():
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

        print("[*] Current URL:", page.url)
        print("[*] Clicking 'Continue with Google'...")
        await page.locator("button:has-text('Continue with Google')").click()

        # Wait for navigation to accounts.google.com
        for _ in range(15):
            await asyncio.sleep(1)
            if "google.com" in page.url:
                break

        print(f"[+] Current URL: {page.url[:90]}")
        print(f"[+] Page Title: {await page.title()}")

        if "google.com" in page.url:
            # Step 1: Email
            email_input = page.locator("input[type='email'], #identifierId").first
            if await email_input.is_visible(timeout=5000):
                print("[*] Entering email: juvgeminipro@gmail.com...")
                await email_input.fill("juvgeminipro@gmail.com")
                await asyncio.sleep(1)
                await page.locator("#identifierNext").click()
                await asyncio.sleep(6)
                print(f"[*] Post-email URL: {page.url[:90]}")

            # Step 2: Password
            pass_input = page.locator("input[type='password'], input[name='Passwd'], input[name='password']").first
            if await pass_input.is_visible(timeout=6000):
                print("[*] Entering password...")
                await pass_input.fill("9061506630s")
                await asyncio.sleep(1)
                await page.locator("#passwordNext").click()
                print("[*] Password submitted. Awaiting OAuth redirect...")
                await asyncio.sleep(12)
                print(f"[+] Final Redirect URL: {page.url[:90]}")
                print(f"[+] Final Title: {await page.title()}")
            else:
                body_txt = await page.inner_text("body")
                print(f"[!] Password field not encountered. Screen text summary:\n{body_txt[:400]}")

        # Capture and export all cookies
        cookies = await context.cookies()
        print(f"\n[+] Total cookies captured: {len(cookies)}")
        auth_cookies = [c for c in cookies if any(k in c.get('domain', '') for k in ['olamovies', 'paybase'])]
        print(f"[+] OlaMovies / PayBase cookies: {len(auth_cookies)}")
        for c in auth_cookies:
            print(f"   {c.get('name')} ({c.get('domain')}) = {c.get('value')[:30]}...")

        with open("olamovies_session_cookies.json", "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        print("[+] Session state exported to olamovies_session_cookies.json")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_login())
