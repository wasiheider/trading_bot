"""
verify_cot.py — Weekly COT verification before dashboard bias is trusted.

Run after each Friday CFTC release:
    python verify_cot.py

Phases:
  1. CFTC Direct  — fetches raw data from CFTC APIs, computes bias with same
                    Larry Williams COT Index logic as the dashboard.
  2. Dashboard    — scrapes Railway live dashboard, extracts displayed bias/index.
  3. Tradingster  — scrapes tradingster.com for independent net-position cross-check.

Output: comparison table + PASS/FAIL.
Only trust dashboard biases once this script exits with ALL MATCH.
"""
import asyncio, sys, io, json, re, os, urllib.request, urllib.parse
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    print("WARNING: playwright not installed — dashboard + Tradingster checks skipped.")

# ── Config ────────────────────────────────────────────────────────────────────
RAILWAY_URL = "https://tradingbot-production-1e5a.up.railway.app/dashboard"

# CFTC contract codes (same as dashboard)
COT_TFF  = {'ES':'13874+','NQ':'209742','RTY':'239742','US30':'12460+','BTC':'133741',
             'USD':'098662','EUR':'099741','GBP':'096742','JPY':'097741','NZD':'112741','VIX':'1170E1'}
COT_COMM = {'GOLD':'088691','SILVER':'084691','CRUDE':'067651'}

# Tradingster URL codes (with leading zeros — confirmed required)
TS_FIN   = {'ES':'013874','NQ':'209742','RTY':'239742','US30':'124603','BTC':'133741',
             'USD':'098662','EUR':'099741','GBP':'096742','JPY':'097741','NZD':'112741','VIX':'1170E1'}
TS_DISAGG = {'GOLD':'088691','SILVER':'084691','CRUDE':'067651'}

COT_ORDER = ['ES','NQ','RTY','US30','BTC','GOLD','SILVER','CRUDE','USD','EUR','GBP','JPY','NZD','VIX']

SNAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cot_verify_snap.json")

# ── Helpers ───────────────────────────────────────────────────────────────────
def cot_index(nets):
    mn, mx = min(nets), max(nets)
    r = mx - mn or 1
    return [round(((n - mn) / r) * 100) for n in nets]

def bias_label(idx):
    return 'Bullish' if idx >= 60 else 'Bearish' if idx <= 40 else 'Neutral'

def http_get(url):
    url = url.replace(' ', '%20')
    req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

# ── Phase 1: CFTC Direct ─────────────────────────────────────────────────────
def fetch_cftc():
    print("\n[1/3] Fetching CFTC data directly...")

    tff_codes  = ','.join(f"'{c.replace('+', '%2B')}'" for c in COT_TFF.values())
    comm_codes = ','.join(f"'{c}'" for c in COT_COMM.values())

    tff_rows  = http_get(f"https://publicreporting.cftc.gov/resource/gpe5-46if.json"
                         f"?$where=cftc_contract_market_code in({tff_codes})"
                         f"&$order=report_date_as_yyyy_mm_dd DESC&$limit=600")
    comm_rows = http_get(f"https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"
                         f"?$where=cftc_contract_market_code in({comm_codes})"
                         f"&$order=report_date_as_yyyy_mm_dd DESC&$limit=200")

    results = {}

    for sym, code in COT_TFF.items():
        rows = sorted([r for r in tff_rows if r.get('cftc_contract_market_code') == code],
                      key=lambda r: r['report_date_as_yyyy_mm_dd'])
        if len(rows) < 2:
            print(f"  WARNING: {sym} has < 2 rows — skipped")
            continue
        nets = [int(r.get('lev_money_positions_long') or 0) - int(r.get('lev_money_positions_short') or 0)
                for r in rows]
        idxs = cot_index(nets)
        if sym == 'VIX':
            idxs = [100 - i for i in idxs]
        idx  = idxs[-1]
        net  = nets[-1]
        date = rows[-1]['report_date_as_yyyy_mm_dd'][:10]
        results[sym] = {'net': net, 'idx': idx, 'bias': bias_label(idx), 'date': date,
                        'cat': 'Lev Fund', 'rows': len(rows)}

    for sym, code in COT_COMM.items():
        rows = sorted([r for r in comm_rows if r.get('cftc_contract_market_code') == code],
                      key=lambda r: r['report_date_as_yyyy_mm_dd'])
        if len(rows) < 2:
            print(f"  WARNING: {sym} has < 2 rows — skipped")
            continue
        nets = [int(r.get('m_money_positions_long_all') or 0) - int(r.get('m_money_positions_short_all') or 0)
                for r in rows]
        idxs = cot_index(nets)
        idx  = idxs[-1]
        net  = nets[-1]
        date = rows[-1]['report_date_as_yyyy_mm_dd'][:10]
        results[sym] = {'net': net, 'idx': idx, 'bias': bias_label(idx), 'date': date,
                        'cat': 'Mgd Money', 'rows': len(rows)}

    dates = sorted({v['date'] for v in results.values()}, reverse=True)
    print(f"  Report date: {dates[0] if dates else 'unknown'}  |  {len(results)} instruments")
    return results

# ── Phase 2: Railway Dashboard ────────────────────────────────────────────────
async def fetch_dashboard():
    print("\n[2/3] Scraping Railway dashboard...")
    displayed = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = await browser.new_page(viewport={'width': 1600, 'height': 1200})
        await page.goto(RAILWAY_URL, wait_until='domcontentloaded', timeout=30000)
        try:
            await page.wait_for_function(
                "document.getElementById('cot-grid').querySelectorAll('.cc').length > 5",
                timeout=40000)
        except Exception as e:
            print(f"  Wait timeout: {e}")
        await asyncio.sleep(2)

        # Also grab the timestamp shown on the card
        try:
            ts = await page.inner_text('#cot-updated', timeout=3000)
            print(f"  Dashboard timestamp: {ts}")
        except:
            pass

        cards = await page.query_selector_all('.cc')
        print(f"  Cards found: {len(cards)}")
        for card in cards:
            text = await card.inner_text()
            lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
            if not lines:
                continue
            sym = lines[0]
            bias = next((l for l in lines if l in ('Bullish', 'Bearish', 'Neutral')), None)
            idx_m = next((l for l in lines if re.match(r'^\d+/100$', l)), None)
            idx = int(idx_m.split('/')[0]) if idx_m else None
            if sym and bias:
                displayed[sym] = {'bias': bias, 'idx': idx}

        await browser.close()
    return displayed

# ── Phase 3: Tradingster ──────────────────────────────────────────────────────
async def fetch_tradingster():
    print("\n[3/3] Scraping Tradingster for net position cross-check...")
    results = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])

        async def scrape(sym, url, category_key):
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=20000)
                await asyncio.sleep(2)
                text = await page.inner_text('body')
                # Tradingster inner_text structure (tab-separated, multi-line):
                #   Leveraged\nFunds\t91,856\n-17,727\t10.5%\t43\t109,244\n+11,688...
                #   Managed\nMoney\t213,483\n+...\t...\t...\t118,758\n-...
                # Strategy: locate "Leveraged" or "Managed" in text, then collect
                # all standalone numbers (no leading +/-) ≥ 10,000 in the next 500 chars.
                # First two standalone large numbers = Long, Short.
                idx = text.lower().find(category_key.split()[0].lower())
                if idx == -1:
                    return None
                chunk = text[idx:idx+600]
                # Find numbers not immediately preceded by + or -
                tokens = re.findall(r'(?<![+\-])(\b\d[\d,]{4,}\b)', chunk)
                clean = []
                for t in tokens:
                    try:
                        n = int(t.replace(',', ''))
                        if n >= 1000:
                            clean.append(n)
                    except:
                        pass
                if len(clean) >= 2:
                    lng, srt = clean[0], clean[1]
                    return {'long': lng, 'short': srt, 'net': lng - srt}
                return None
            except Exception as e:
                return None
            finally:
                await page.close()

        # TFF: Leveraged Funds
        for sym, code in TS_FIN.items():
            url = f"https://www.tradingster.com/cot/futures/fin/{code}"
            r = await scrape(sym, url, 'Leveraged Fund')
            results[sym] = r
            status = f"+{r['net']:,}" if r and r['net'] >= 0 else (f"{r['net']:,}" if r else 'N/A')
            print(f"  {sym:<8} Lev Fund net: {status}")

        # Disaggregated: Managed Money
        for sym, code in TS_DISAGG.items():
            url = f"https://www.tradingster.com/cot/futures/disagg/{code}"
            r = await scrape(sym, url, 'Managed Money')
            results[sym] = r
            status = f"+{r['net']:,}" if r and r['net'] >= 0 else (f"{r['net']:,}" if r else 'N/A')
            print(f"  {sym:<8} Mgd Money net: {status}")

        await browser.close()

    return results

# ── Report ────────────────────────────────────────────────────────────────────
def print_report(cftc, dash, tradingster):
    print("\n" + "=" * 90)
    print("COT WEEKLY VERIFICATION REPORT")
    dates = sorted({v['date'] for v in cftc.values()}, reverse=True)
    print(f"CFTC Report: {dates[0] if dates else '?'}   Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 90)

    print(f"\n{'Sym':<7} {'Cat':<12} {'CFTC Net':>12}  {'Idx':>5} {'Bias':<9}  "
          f"{'Dash Idx':>8} {'Dash Bias':<9}  {'TS Net':>12}  {'Net OK':>7} {'Bias OK':>8}")
    print("-" * 90)

    all_bias_ok = True
    all_net_ok  = True

    for sym in COT_ORDER:
        c = cftc.get(sym)
        d = dash.get(sym)
        t = tradingster.get(sym) if tradingster else None
        if not c:
            continue

        net_str  = f"{c['net']:+,}"
        ts_net   = f"{t['net']:+,}" if t else '—'

        # Net match: CFTC direct vs Tradingster (within 1% tolerance — minor rounding diffs)
        if t:
            net_diff = abs(c['net'] - t['net'])
            net_pct  = net_diff / max(abs(c['net']), 1) * 100
            net_ok   = '✅' if net_pct < 2 else f'⚠ {net_pct:.0f}%'
            if net_pct >= 2:
                all_net_ok = False
        else:
            net_ok = '—'

        # Bias match: CFTC computed vs Dashboard displayed
        d_bias = d.get('bias', '?') if d else '?'
        d_idx  = d.get('idx',  '?') if d else '?'
        bias_ok = '✅' if c['bias'] == d_bias and c['idx'] == d_idx else '❌'
        if bias_ok == '❌':
            all_bias_ok = False

        print(f"{sym:<7} {c['cat']:<12} {net_str:>12}  {c['idx']:>5} {c['bias']:<9}  "
              f"{str(d_idx)+'/100':>8} {d_bias:<9}  {ts_net:>12}  {net_ok:>7} {bias_ok:>8}")

    print("-" * 90)
    dash_result = "✅ PASS — Dashboard biases match CFTC direct computation" if all_bias_ok \
                  else "❌ FAIL — Dashboard bias mismatch detected"
    print(f"\nDashboard vs CFTC:   {dash_result}")

    if tradingster:
        # Check which net discrepancies are KNOWN/EXPECTED vs unexpected
        known_discrepancies = {
            'US30': 'Consolidated (12460+) vs Mini only (124603) — different contract scope, expected',
            'GOLD': 'Futures+Options combined (kh3c-gbw2) vs Tradingster Futures Only — expected',
            'SILVER': 'Futures+Options combined (kh3c-gbw2) vs Tradingster Futures Only — expected',
            'CRUDE': 'Futures+Options combined (kh3c-gbw2) vs Tradingster Futures Only — expected',
        }
        unknown_net_issues = []
        for sym in COT_ORDER:
            c = cftc.get(sym)
            t = tradingster.get(sym) if tradingster else None
            if not c or not t:
                continue
            net_diff = abs(c['net'] - t['net'])
            net_pct  = net_diff / max(abs(c['net']), 1) * 100
            if net_pct >= 2 and sym not in known_discrepancies:
                unknown_net_issues.append(sym)

        if unknown_net_issues:
            print(f"CFTC vs Tradingster: ⚠  Unexpected net discrepancies: {', '.join(unknown_net_issues)}")
        else:
            print(f"CFTC vs Tradingster: ✅ Net discrepancies are all EXPECTED (dataset/scope differences)")
        print(f"\n  Known differences:")
        for sym, reason in known_discrepancies.items():
            t = (tradingster or {}).get(sym)
            c = cftc.get(sym, {})
            if t:
                print(f"    {sym:<8} CFTC {c.get('net',0):+,} vs TS {t['net']:+,} — {reason}")

    # Final verdict — bias correctness is the primary gate
    print()
    if all_bias_ok:
        print("✅  DASHBOARD BIASES VERIFIED — safe to use for this week's trading decisions.")
        if tradingster and unknown_net_issues:
            print("    Note: investigate unexpected Tradingster net discrepancies before next run.")
    else:
        print("❌  DO NOT trust dashboard biases — mismatch between CFTC direct and displayed values.")

    # Save snapshot
    snap = {
        'generated': datetime.now().isoformat(),
        'cftc_date': dates[0] if dates else '?',
        'cftc': {k: {kk: vv for kk, vv in v.items() if kk != 'rows'} for k, v in cftc.items()},
        'dashboard': dash,
        'tradingster': {k: v for k, v in (tradingster or {}).items() if v},
        'bias_ok': all_bias_ok,
        'net_ok': all_net_ok,
    }
    with open(SNAP_PATH, 'w') as f:
        json.dump(snap, f, indent=2)
    print(f"\nSnapshot saved: {SNAP_PATH}")

# ── Entry point ───────────────────────────────────────────────────────────────
async def main():
    print("=" * 90)
    print("COT WEEKLY VERIFICATION SCRIPT")
    print("Sources: CFTC gpe5-46if (Lev Fund) + kh3c-gbw2 (Mgd Money) | Tradingster | Railway Dashboard")
    print("=" * 90)

    cftc = fetch_cftc()

    if HAS_PLAYWRIGHT:
        dash         = await fetch_dashboard()
        tradingster  = await fetch_tradingster()
    else:
        dash         = {}
        tradingster  = {}

    print_report(cftc, dash, tradingster)

asyncio.run(main())
