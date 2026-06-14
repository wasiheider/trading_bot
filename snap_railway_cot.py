import asyncio, sys, io, pathlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright

URL = "https://tradingbot-production-1e5a.up.railway.app/dashboard"

async def snap():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1600, "height": 1200})

        print(f"Loading {URL} ...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)

        # Wait for COT cards to populate
        try:
            await page.wait_for_function(
                "document.getElementById('cot-grid') && "
                "document.getElementById('cot-grid').querySelectorAll('.cc').length > 5",
                timeout=40000
            )
            print("COT grid populated.")
        except Exception as e:
            print(f"Wait timeout: {e}")

        await asyncio.sleep(2)

        # Screenshot the COT section
        cot = await page.query_selector("#cot-grid")
        if cot:
            box = await cot.bounding_box()
            await page.screenshot(
                path="cot_railway.png",
                clip={"x": box["x"]-12, "y": box["y"]-40, "width": box["width"]+24, "height": box["height"]+50}
            )
            print("Screenshot: cot_railway.png")

        # Grab timestamp + all card raw text
        try:
            updated = await page.inner_text("#cot-updated", timeout=5000)
            print(f"CFTC timestamp: {updated}")
        except:
            print("CFTC timestamp: (not found)")

        cards = await page.query_selector_all(".cc")
        print(f"Cards rendered: {len(cards)}")
        print("=" * 60)
        for c in cards:
            t = await c.inner_text()
            print(t.strip())
            print("-" * 30)

        await browser.close()

asyncio.run(snap())
