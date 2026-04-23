"""Save Medsafe metformin search result for inspection."""
import httpx
import asyncio
import sys
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding='utf-8')

BASE = "https://www.medsafe.govt.nz"
SEARCH_URL = f"{BASE}/DbSearch/Default.asp"


async def main():
    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as c:
        await c.get(f"{BASE}/DbSearch/")
        await asyncio.sleep(2)

        data = {
            "optSearch": "Product",
            "txtIngredient": "metformin",
            "txtTradeName": "",
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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Referer": f"{BASE}/DbSearch/",
            "Origin": BASE,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        r = await c.post(SEARCH_URL, data=data, headers=headers)

    # Save HTML
    with open("tmp/medsafe_metformin.html", "w", encoding="utf-8") as f:
        f.write(r.text)
    print("Saved", len(r.text), "bytes")

    # Parse
    soup = BeautifulSoup(r.text, "html.parser")
    tables = soup.find_all("table")
    print(f"Found {len(tables)} tables")
    for i, t in enumerate(tables):
        rows = t.find_all("tr")
        print(f"\n=== Table {i} ({len(rows)} rows) ===")
        for j, row in enumerate(rows):
            cells = row.find_all(["th", "td"])
            texts = [c.get_text(strip=True)[:50] for c in cells]
            print(f"  Row {j}: {texts}")


asyncio.run(main())
