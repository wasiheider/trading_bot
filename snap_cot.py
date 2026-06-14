import asyncio, sys, io, pathlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright

async def snap():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            "", headless=True,
            viewport={"width": 1600, "height": 1200},
            args=["--disable-web-security", "--allow-file-access-from-files"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        url = pathlib.Path("dashboard.html").resolve().as_uri()
        await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.wait_for_function(
                "document.getElementById('cot-grid') && "
                "document.getElementById('cot-grid').querySelectorAll('.cc').length > 5",
                timeout=35000
            )
        except Exception as e:
            print(f"Wait: {e}")
        await asyncio.sleep(2)

        cot = await page.query_selector("#cot-grid")
        box = await cot.bounding_box()
        await page.screenshot(
            path="cot_section_v2.png",
            clip={"x": box["x"]-12, "y": box["y"]-40, "width": box["width"]+24, "height": box["height"]+50}
        )
        print("Screenshot saved: cot_section_v2.png")

        cards = await page.query_selector_all(".cc")
        for c in cards[:4]:
            t = await c.inner_text()
            print(t.strip())
            print("---")
        await ctx.close()

asyncio.run(snap())
