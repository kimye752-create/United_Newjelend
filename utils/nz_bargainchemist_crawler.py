"""Bargain Chemist NZ (bargainchemist.co.nz) 경량 소매 크롤러.

수집 전략:
  정적 HTML 구조 → httpx + BeautifulSoup (Playwright 불필요)
  제품 상세 페이지: 가격·성분·재고량·할인율(Savings%) 파싱

Bargain Chemist는 저가 지향형 약국 체인으로
Pharmacist Only 포함 실질 조제 비용·최저가 트렌드 벤치마킹에 활용.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup  # type: ignore[import]

from utils.backoff_retry import with_retry
from utils.nz_parser import ParsedDrug, parse_drug_text

BASE_URL = "https://www.bargainchemist.co.nz"
SEARCH_PATH = "/search"
RATE_LIMIT_DELAY = 1.0

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-NZ,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
}


@dataclass
class BargainChemistRaw:
    product_name: str
    price_nzd: str
    savings_pct: float | None
    url: str
    raw_text: str


def _parse_bargainchemist_listing(html: str) -> list[BargainChemistRaw]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[BargainChemistRaw] = []

    for card in soup.select(
        ".product-item, .product-card, .item, "
        "article[class*='product'], [class*='ProductCard'], "
        "[class*='product-tile']"
    ):
        name_el = card.select_one(
            "h2, h3, .product-name, .name, [class*='title'], [class*='name']"
        )
        if not name_el:
            continue
        name = name_el.get_text(strip=True)
        if not name:
            continue

        price_el = card.select_one(
            ".price:not(.price-old):not(.price-was), "
            ".product-price, [class*='price-current'], "
            "[class*='sale-price'], [class*='now-price']"
        )
        if not price_el:
            continue
        price_raw = price_el.get_text(strip=True)
        price_clean = re.sub(r"[^\d.]", "", price_raw.replace(",", ""))
        if not price_clean:
            continue

        # Savings % 추출 (예: "Save 20%", "Saving $5.00")
        savings_el = card.select_one(
            "[class*='saving'], [class*='Save'], [class*='discount'], "
            ".badge-save, [class*='deal']"
        )
        savings_pct: float | None = None
        if savings_el:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", savings_el.get_text())
            if m:
                savings_pct = float(m.group(1))

        link_el = card.select_one("a[href]")
        product_url = (
            f"{BASE_URL}{link_el['href']}"
            if link_el and str(link_el["href"]).startswith("/")
            else (str(link_el["href"]) if link_el else "")
        )

        raw_text = f"{name} NZD {price_raw}"
        items.append(
            BargainChemistRaw(
                product_name=name,
                price_nzd=price_clean,
                savings_pct=savings_pct,
                url=product_url,
                raw_text=raw_text,
            )
        )
    return items


async def _fetch_html(client: httpx.AsyncClient, url: str) -> str:
    async def _do() -> str:
        resp = await client.get(url, headers=_HEADERS, follow_redirects=True)
        resp.raise_for_status()
        return resp.text

    return await with_retry(_do)


async def crawl_bargainchemist(
    inn_name: str,
    max_pages: int = 3,
) -> list[ParsedDrug | None]:
    raw_items: list[BargainChemistRaw] = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for page in range(1, max_pages + 1):
            url = f"{BASE_URL}{SEARCH_PATH}?q={quote(inn_name)}&page={page}"
            html = await _fetch_html(client, url)
            page_items = _parse_bargainchemist_listing(html)
            if not page_items:
                break
            raw_items.extend(page_items)
            await asyncio.sleep(RATE_LIMIT_DELAY)

    parsed: list[ParsedDrug | None] = []
    for raw in raw_items:
        drug = await parse_drug_text(
            raw_text=raw.raw_text,
            source_site="bargainchemist",
            source_url=raw.url,
        )
        if drug and raw.savings_pct is not None:
            drug.extra["savings_pct"] = raw.savings_pct
        parsed.append(drug)
    return parsed
