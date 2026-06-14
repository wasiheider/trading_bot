"""Screenshot the live dashboard COT section and extract all instrument data."""
import asyncio, json, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright

import os, pathlib
DASHBOARD = pathlib.Path(__file__).parent / "dashboard.html"
URL = DASHBOARD.as_uri()   # file:///... — COT section hits CFTC API directly

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Allow file:// pages to make cross-origin XHR to cftc.gov
        ctx = await p.chromium.launch_persistent_context(
            "",
            headless=True,
            viewport={"width": 1600, "height": 1200},
            args=["--disable-web-security", "--allow-file-access-from-files"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        print(f"Loading dashboard from: {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=20000)

        # Wait up to 30s for COT grid to populate
        try:
            await page.wait_for_function(
                "document.getElementById('cot-grid') && "
                "!document.getElementById('cot-grid').innerText.includes('FETCHING') && "
                "document.getElementById('cot-grid').querySelectorAll('.cc').length > 5",
                timeout=35000
            )
            print("COT grid populated.")
        except Exception as e:
            print(f"Wait timed out ({e}) — capturing whatever is rendered.")

        await asyncio.sleep(2)

        # Screenshot the whole page
        await page.screenshot(path="dashboard_full.png", full_page=False)

        # Zoom into COT grid specifically — find its bounding box
        cot_card = await page.query_selector("#cot-grid")
        if cot_card:
            box = await cot_card.bounding_box()
            if box:
                # Expand box to include card title above
                clip = {
                    "x": max(0, box["x"] - 12),
                    "y": max(0, box["y"] - 40),
                    "width": box["width"] + 24,
                    "height": box["height"] + 50,
                }
                await page.screenshot(path="cot_section.png", clip=clip)
                print(f"COT grid box: {box}")

        # Extract text data from every COT card
        cot_data = await page.evaluate("""
        () => {
            const grid = document.getElementById('cot-grid');
            if (!grid) return null;
            const cards = grid.querySelectorAll('.cc');
            return Array.from(cards).map(card => {
                return {
                    sym:    (card.querySelector('.csym')  || {}).innerText || '',
                    pop:    (card.querySelector('.cpop')  || {}).innerText || '',
                    bias:   (card.querySelector('.cbias') || {}).innerText || '',
                    delta:  (card.querySelector('.ctrend')|| {}).innerText || '',
                    conf:   (card.querySelector('.cconf') || {}).innerText || '',
                    lev:    card.innerText.match(/Lev.*|LEV.*/)?.[0] || '',
                    mm:     card.innerText.match(/MM.*/)?.[0] || '',
                    rawText: card.innerText.trim(),
                };
            });
        }
        """)

        # Also grab the update timestamp
        try:
            updated = await page.inner_text("#cot-updated", timeout=5000)
        except:
            updated = "(not loaded)"

        print(f"\nCOT Updated: {updated}")
        print(f"\nTotal cards: {len(cot_data) if cot_data else 0}")
        print("\n" + "="*70)
        if cot_data:
            for d in cot_data:
                print(f"\n{d['sym']}  ({d['pop']})")
                print(f"  Bias:  {d['bias']}")
                print(f"  Delta: {d['delta']}")
                print(f"  Index: {d['conf']}")
                if d['lev']:  print(f"  Lev:   {d['lev']}")
                if d['mm']:   print(f"  MM:    {d['mm']}")

        # Save JSON too
        with open("cot_data.json", "w") as f:
            json.dump({"updated": updated, "cards": cot_data}, f, indent=2)
        print("\n\nJSON saved to cot_data.json")
        print("Screenshots: dashboard_full.png, cot_section.png")

        await ctx.close()

asyncio.run(main())
