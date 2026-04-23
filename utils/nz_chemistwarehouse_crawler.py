"""Chemist Warehouse NZ (chemistwarehouse.co.nz) Playwright 크롤러 — 이중 가격 DOM 분리.

핵심 방어:
  일반 정가(regular_price)와 프로모션가를 DOM 클래스로 엄격히 분리.
  regular_price → raw_price (기본값 / FOB 역산 입력)
  promo_price → 별도 메타 컬럼 저장 (프로모션 분석용)

환경변수 PLAYWRIGHT_LIVE=1 시 실제 브라우저 실행.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote

from utils.nz_parser import ParsedDrug, _safe_decimal, parse_drug_text

BASE_URL = "https://www.chemistwarehouse.co.nz"
SEARCH_URL = f"{BASE_URL}/search?searchtext="
HEALTH_CATEGORY_URL = f"{BASE_URL}/buy/health"
RATE_LIMIT_DELAY = 3.0  # Chemist Warehouse 부하 고려

LIVE = os.environ.get("PLAYWRIGHT_LIVE", "0") == "1"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-NZ,en;q=0.9",
}

# DOM 셀렉터 — Chemist Warehouse NZ 실제 구조에 맞게 우선순위 정렬
# regular_price 셀렉터: 일반 정가
_REGULAR_PRICE_SELECTORS = [
    ".Price",
    "[class*='Price--sale']",
    ".product-price .Price",
    "[data-testid='product-price']",
    ".product__price",
    "span.Price",
    "[class*='price']:not([class*='was']):not([class*='old'])",
]

# promo_price 셀렉터: 프로모션가
_PROMO_PRICE_SELECTORS = [
    ".Price--saving",
    ".Price--was",
    "[class*='WasPrice']",
    "[class*='was-price']",
    "[class*='old-price']",
    ".product-price .Price--discounted",
]


@dataclass
class ChemistWarehouseRaw:
    product_name: str
    regular_price_nzd: str
    promo_price_nzd: str | None
    url: str
    raw_text: str


def _extract_price(el_text: str) -> str:
    """NZD 금액에서 숫자만 추출 (예: '$12.99' → '12.99')."""
    return re.sub(r"[^\d.]", "", el_text.replace(",", ""))


def _parse_playwright_html(html: str) -> list[ChemistWarehouseRaw]:
    from bs4 import BeautifulSoup  # type: ignore[import]

    soup = BeautifulSoup(html, "html.parser")
    items: list[ChemistWarehouseRaw] = []

    product_cards = soup.select(
        ".product-tile, "
        ".product-grid-item, "
        ".search-result-item, "
        "article[class*='product'], "
        "[class*='ProductTile'], "
        "[class*='product-item']"
    )

    for card in product_cards:
        name_el = card.select_one(
            ".product-title, "
            "[class*='ProductTitle'], "
            "[class*='product-name'], "
            "h2, h3, "
            "[data-testid='product-name']"
        )
        if not name_el:
            continue
        name = name_el.get_text(strip=True)
        if not name:
            continue

        regular_price_str = ""
        for sel in _REGULAR_PRICE_SELECTORS:
            el = card.select_one(sel)
            if el:
                raw = el.get_text(strip=True)
                clean = _extract_price(raw)
                if clean and "." in clean:
                    regular_price_str = clean
                    break
        if not regular_price_str:
            continue

        promo_price_str: str | None = None
        for sel in _PROMO_PRICE_SELECTORS:
            el = card.select_one(sel)
            if el:
                raw = el.get_text(strip=True)
                clean = _extract_price(raw)
                if clean and clean != regular_price_str and "." in clean:
                    promo_price_str = clean
                    break

        link_el = card.select_one("a[href]")
        product_url = (
            f"{BASE_URL}{link_el['href']}"
            if link_el and str(link_el["href"]).startswith("/")
            else (str(link_el["href"]) if link_el else "")
        )

        raw_text = f"{name} NZD {regular_price_str}"
        items.append(
            ChemistWarehouseRaw(
                product_name=name,
                regular_price_nzd=regular_price_str,
                promo_price_nzd=promo_price_str,
                url=product_url,
                raw_text=raw_text,
            )
        )
    return items


async def _fetch_with_playwright(url: str) -> str:
    from playwright.async_api import async_playwright  # type: ignore[import]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not LIVE)
        page = await browser.new_page(extra_http_headers=_HEADERS)
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(2000)

        # 동적 콘텐츠 로드를 위해 스크롤
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(500)

        html = await page.content()
        await browser.close()
        return html


async def _fetch_html_safe(url: str) -> str:
    if not LIVE:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url, headers=_HEADERS, follow_redirects=True)
                resp.raise_for_status()
                return resp.text
        except Exception:
            pass

    return await _fetch_with_playwright(url)


async def crawl_chemistwarehouse(
    inn_name: str,
    max_pages: int = 3,
) -> list[ParsedDrug | None]:
    raw_items: list[ChemistWarehouseRaw] = []

    for page in range(1, max_pages + 1):
        url = f"{SEARCH_URL}{quote(inn_name)}&page={page}"
        html = await _fetch_html_safe(url)
        page_items = _parse_playwright_html(html)
        if not page_items:
            break
        raw_items.extend(page_items)
        await asyncio.sleep(RATE_LIMIT_DELAY)

    if not raw_items:
        # 검색 실패 시 Health 카테고리 페이지에서 키워드 필터링
        html = await _fetch_html_safe(HEALTH_CATEGORY_URL)
        cat_items = _parse_playwright_html(html)
        keyword = inn_name.lower()
        raw_items = [
            i for i in cat_items if keyword in i.product_name.lower()
        ]

    parsed: list[ParsedDrug | None] = []
    for raw in raw_items:
        promo_dec = _safe_decimal(raw.promo_price_nzd)
        drug = await parse_drug_text(
            raw_text=raw.raw_text,
            source_site="chemistwarehouse",
            source_url=raw.url,
            promo_price_nzd=promo_dec,
        )
        parsed.append(drug)

    return parsed
