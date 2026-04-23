"""NZ Online Pharmacy (nz-online-pharmacy.com) 소매 약가 크롤러.

수집 전략:
  GET https://www.nz-online-pharmacy.com/search?q={inn_name}
  정적 HTML → BeautifulSoup 파싱 → nz_parser.py 전달

NZ Online Pharmacy는 NZ 현지 운영 온라인 약국으로
규제 준수(Medsafe 기준) 소매 의약품가를 제공.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup  # type: ignore[import]

from utils.antibot import detect as antibot_detect, pick_ua, AntiBotType
from utils.backoff_retry import with_retry
from utils.nz_parser import ParsedDrug, parse_drug_text

BASE_URL   = "https://www.nz-online-pharmacy.com"
SEARCH_URL = f"{BASE_URL}/search"
RATE_LIMIT_DELAY = 1.5


def _make_headers() -> dict[str, str]:
    return {
        "User-Agent": pick_ua(),
        "Accept-Language": "en-NZ,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": BASE_URL,
    }


@dataclass
class NZOnlinePharmacyRaw:
    product_name:    str
    price_nzd:       str
    promo_price_nzd: str | None
    pack_size:       str | None
    url:             str
    raw_text:        str


def _parse_search_results(html: str, base_url: str) -> list[NZOnlinePharmacyRaw]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[NZOnlinePharmacyRaw] = []

    for card in soup.select(
        ".product-item, .product-card, .product, "
        "article.product, [class*='ProductCard'], "
        "[class*='product-tile'], [class*='item-product'], li.product"
    ):
        # ── 제품명 ──────────────────────────────────────────────────────
        name_el = card.select_one(
            ".product-name, .product-title, .name, "
            "h2, h3, h4, [class*='title'], [class*='name']"
        )
        if not name_el:
            continue
        name = name_el.get_text(strip=True)
        if not name:
            continue

        # ── 현재가 ──────────────────────────────────────────────────────
        price_el = card.select_one(
            ".price:not(.price-was):not(.price-old):not(.was-price), "
            ".product-price, .current-price, [class*='price-now'], "
            "[class*='current-price'], [class*='sale-price'], "
            "[data-price], .price-sales"
        )
        if not price_el:
            continue
        price_raw = price_el.get_text(strip=True)
        price_clean = re.sub(r"[^\d.]", "", price_raw.replace(",", ""))
        if not price_clean:
            continue

        # ── 할인 전 가격 ─────────────────────────────────────────────
        promo_el = card.select_one(
            ".price-was, .was-price, .price-old, "
            "[class*='was'], [class*='original-price'], "
            "[class*='regular-price']"
        )
        promo_str: str | None = None
        if promo_el:
            promo_raw = promo_el.get_text(strip=True)
            promo_clean = re.sub(r"[^\d.]", "", promo_raw.replace(",", ""))
            if promo_clean:
                promo_str = promo_clean

        # ── 팩 사이즈 ────────────────────────────────────────────────
        pack_el = card.select_one(
            ".pack-size, .size, [class*='pack'], [class*='quantity']"
        )
        pack_size = pack_el.get_text(strip=True) if pack_el else None

        # ── URL ──────────────────────────────────────────────────────
        link_el = card.select_one("a[href]")
        product_url = (
            f"{base_url}{link_el['href']}"
            if link_el and str(link_el["href"]).startswith("/")
            else (str(link_el["href"]) if link_el else "")
        )

        raw_text = f"{name} NZD {price_raw}"
        if pack_size:
            raw_text += f" {pack_size}"

        results.append(NZOnlinePharmacyRaw(
            product_name=name,
            price_nzd=price_clean,
            promo_price_nzd=promo_str,
            pack_size=pack_size,
            url=product_url,
            raw_text=raw_text,
        ))

    return results


async def _fetch_html(client: httpx.AsyncClient, url: str) -> str:
    async def _do() -> str:
        resp = await client.get(url, headers=_make_headers(), follow_redirects=True)
        ab = antibot_detect(resp.status_code, resp.text[:2000], dict(resp.headers))
        if ab != AntiBotType.NONE:
            import logging
            logging.getLogger(__name__).warning(
                "NZ Online Pharmacy anti-bot: %s", ab.value
            )
        resp.raise_for_status()
        return resp.text

    return await with_retry(_do)


async def crawl_nzonlinepharmacy(
    inn_name: str,
    max_pages: int = 3,
) -> list[ParsedDrug | None]:
    results: list[NZOnlinePharmacyRaw] = []

    async with httpx.AsyncClient(timeout=25.0, http2=True) as client:
        for page in range(1, max_pages + 1):
            url = f"{SEARCH_URL}?q={quote(inn_name)}&page={page}"
            try:
                html = await _fetch_html(client, url)
            except Exception:
                break
            page_results = _parse_search_results(html, BASE_URL)
            if not page_results:
                break
            results.extend(page_results)
            await asyncio.sleep(RATE_LIMIT_DELAY)

    parsed: list[ParsedDrug | None] = []
    for raw in results:
        drug = await parse_drug_text(
            raw_text=raw.raw_text,
            source_site="nzonlinepharmacy",
            source_url=raw.url,
        )
        if drug:
            if raw.promo_price_nzd:
                drug.extra["promo_price_nzd"] = raw.promo_price_nzd
            if raw.pack_size:
                drug.extra["pack_size"] = raw.pack_size
        parsed.append(drug)

    return parsed


async def crawl_nzonlinepharmacy_multi(
    inn_names: list[str],
) -> dict[str, list[ParsedDrug | None]]:
    results: dict[str, list[ParsedDrug | None]] = {}
    for inn in inn_names:
        results[inn] = await crawl_nzonlinepharmacy(inn)
        await asyncio.sleep(RATE_LIMIT_DELAY)
    return results
