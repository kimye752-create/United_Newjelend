"""Test Medsafe search with various terms."""
import httpx
import asyncio
import sys
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding='utf-8')

BASE = "https://www.medsafe.govt.nz"
SEARCH_URL = f"{BASE}/DbSearch/Default.asp"
DELAY = 5.0  # seconds between requests


async def search(ingredient: str = "", trade: str = "") -> None:
    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as c:
        # Warm up session
        await c.get(f"{BASE}/DbSearch/")
        await asyncio.sleep(2)

        data = {
            "optSearch": "Product",
            "txtIngredient": ingredient,
            "txtTradeName": trade,
            "txtSponsor": "",
            "cboClassification": "",
            "cboProductType": "",
            "txtFromDate": "",
            "txtToDate": "",
            "cboType": "",
            "cboStatus": "",
            "cmdSearch": "Submit",
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": f"{BASE}/DbSearch/",
            "Origin": BASE,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml",
        }
        r = await c.post(SEARCH_URL, data=data, headers=headers)
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text()

        label = f"ing={ingredient!r} trade={trade!r}"
        if "Nothing was found" in text:
            print(f"{label} -> NOTHING FOUND ({len(r.text)} bytes)")
            return

        # Find result table
        tables = soup.find_all("table")
        for t in tables:
            rows = t.find_all("tr")
            if len(rows) < 3:
                continue
            header_text = " ".join(
                c.get_text(strip=True)
                for c in (rows[0].find_all(["th", "td"]) if rows else [])
            )
            if any(kw in header_text for kw in ("Product", "Active", "Sponsor")):
                print(f"{label} -> {len(rows)-1} result rows")
                for row in rows[:6]:
                    cells = row.find_all(["th", "td"])
                    print("  ", [c.get_text(strip=True)[:40] for c in cells])
                return

        print(f"{label} -> {len(r.text)} bytes, no obvious result table")
        # save for inspection
        with open("tmp_medsafe_debug.html", "w", encoding="utf-8") as f:
            f.write(r.text)
        print("  Saved to tmp_medsafe_debug.html")


async def main():
    tests = [
        ("metformin", ""),
        ("amlodipine", ""),
        ("atorvastatin", ""),
        ("cilostazol", ""),
    ]
    for ing, trade in tests:
        await search(ing, trade)
        await asyncio.sleep(DELAY)


asyncio.run(main())
