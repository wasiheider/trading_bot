import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright

URL = "https://tradingbot-production-1e5a.up.railway.app/dashboard"

async def snap():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1600, "height": 1200})

        print(f"Loading {URL} ...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)

        # Wait for COT grid and analytics card to appear
        try:
            await page.wait_for_function(
                "document.getElementById('col4-analytics-body') !== null && "
                "document.getElementById('cot-grid').querySelectorAll('.cc').length > 5",
                timeout=40000
            )
            print("Page ready.")
        except Exception as e:
            print(f"Wait: {e}")

        await asyncio.sleep(2)

        # Full-page screenshot
        await page.screenshot(path="railway_full.png", full_page=False)

        # Col 4 screenshot — find the analytics card
        analytics_card = await page.query_selector("#col4-analytics-body")
        col4 = await page.query_selector("div.col:last-child")

        if col4:
            box = await col4.bounding_box()
            await page.screenshot(
                path="railway_col4.png",
                clip={"x": box["x"], "y": box["y"], "width": box["width"], "height": min(box["height"], 1100)}
            )
            print(f"Col 4 screenshot saved: railway_col4.png  (box: {box})")

        # Text content of analytics card
        if analytics_card:
            txt = await analytics_card.inner_text()
            print(f"\n--- col4-analytics-body ---\n{txt.strip()}\n---")
        else:
            print("col4-analytics-body: NOT FOUND")

        # COT card still intact?
        cot_cards = await page.query_selector_all(".cc")
        print(f"\nCOT cards: {len(cot_cards)}")
        if cot_cards:
            first = await cot_cards[0].inner_text()
            print(f"First COT card: {first.strip()}")

        await browser.close()

asyncio.run(snap())
