import asyncio
import json
from patchright.async_api import async_playwright

async def complete_consent_flow():
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

        print("[*] Clicking 'Continue with Google'...")
        await page.locator("button:has-text('Continue with Google')").click()

        for _ in range(15):
            await asyncio.sleep(1)
            if "google.com" in page.url:
                break

        print(f"[+] Google Sign-In URL: {page.url[:90]}")

        # Step 1: Email
        email_input = page.locator("input[type='email'], #identifierId").first
        if await email_input.is_visible(timeout=5000):
            print("[*] Entering email: juvgeminipro@gmail.com...")
            await email_input.fill("juvgeminipro@gmail.com")
            await asyncio.sleep(1)
            await page.locator("#identifierNext").click()
            await asyncio.sleep(5)

        # Step 2: Password
        pass_input = page.locator("input[type='password'], input[name='Passwd'], input[name='password']").first
        if await pass_input.is_visible(timeout=6000):
            print("[*] Entering password...")
            await pass_input.fill("9061506630s")
            await asyncio.sleep(1)
            await page.locator("#passwordNext").click()
            print("[*] Password submitted. Checking for consent / continue buttons...")

        # Step 3: Loop checking for Google Consent / Allow / Continue button
        for step in range(20):
            await asyncio.sleep(1)
            current_url = page.url
            title = await page.title()
            print(f"[*] Step {step+1}: URL = {current_url[:75]} | Title = {title}")

            # Check if redirected back to paybase or olamovies
            if "paybase.olamovies.mov" in current_url or "olamovies" in current_url:
                print(f"[+] Successfully redirected to destination: {current_url}")
                break

            # Look for Allow / Continue / Next buttons on Google OAuth consent screen
            consent_btn = page.locator("button:has-text('Continue'), button:has-text('Allow'), button:has-text('I agree'), button:has-text('Confirm'), #submit_approve_access").first
            if await consent_btn.is_visible(timeout=1000):
                print(f"[*] Found OAuth consent button: '{await consent_btn.inner_text()}'. Clicking...")
                await consent_btn.click()
                await asyncio.sleep(3)

        await asyncio.sleep(5)
        print(f"[+] Final URL: {page.url}")
        print(f"[+] Final Title: {await page.title()}")

        # Export cookies
        cookies = await context.cookies()
        auth_cookies = [c for c in cookies if any(k in c.get('domain', '') for k in ['olamovies', 'paybase'])]
        print(f"\n[+] Captured {len(auth_cookies)} OlaMovies/PayBase cookies:")
        for c in auth_cookies:
            print(f"   {c.get('name')} ({c.get('domain')}) = {c.get('value')[:30]}...")

        with open("olamovies_session_cookies.json", "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        print("[+] Session state exported to olamovies_session_cookies.json")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(complete_consent_flow())
