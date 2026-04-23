"""Pharmacy Direct NZ (pharmacydirect.co.nz) 소매 약가 크롤러.

수집 전략:
  GET https://www.pharmacydirect.co.nz/search?q={inn_name}
  정적 HTML → BeautifulSoup 파싱 → nz_parser.py 전달

Pharmacy Direct는 NZ 온라인 전용 약국으로 처방전 필요 의약품 포함
전문의약품 소매가 데이터를 제공.
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

BASE_URL   = "https://www.pharmacydirect.co.nz"
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
class PharmacyDirectRaw:
    product_name:    str
    price_nzd:       str
    sale_price_nzd:  str | None
    strength:        str | None
    url:             str
    raw_text:        str


def _parse_search_results(html: str, base_url: str) -> list[PharmacyDirectRaw]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[PharmacyDirectRaw] = []

    for card in soup.select(
        ".product-item, .product, .product-card, "
        "article, [class*='product-listing'], "
        "[class*='ProductCard'], li.item, [class*='product-tile']"
    ):
        # ── 제품명 ──────────────────────────────────────────────────────
        name_el = card.select_one(
            ".product-name, .product-title, h2, h3, h4, "
            ".name, [class*='name'], [class*='title']"
        )
        if not name_el:
            continue
        name = name_el.get_text(strip=True)
        if not name:
            continue

        # ── 가격 ────────────────────────────────────────────────────────
        price_el = card.select_one(
            ".price:not(.price-old):not(.was-price):not(.price-was), "
            ".product-price, .current-price, "
            "[class*='price-now'], [class*='current'], "
            "[class*='sale-price'], [data-price]"
        )
        if not price_el:
            continue
        price_raw = price_el.get_text(strip=True)
        price_clean = re.sub(r"[^\d.]", "", price_raw.replace(",", ""))
        if not price_clean:
            continue

        # ── 할인 전 가격 ─────────────────────────────────────────────
        sale_el = card.select_one(
            ".price-old, .was-price, .price-was, "
            "[class*='original'], [class*='was'], [class*='regular']"
        )
        sale_str: str | None = None
        if sale_el:
            s = re.sub(r"[^\d.]", "", sale_el.get_text(strip=True).replace(",", ""))
            if s:
                sale_str = s

        # ── 함량/규격 ────────────────────────────────────────────────
        strength_el = card.select_one(
            ".strength, .dosage, [class*='strength'], "
            "[class*='dosage'], .subtitle"
        )
        strength = strength_el.get_text(strip=True) if strength_el else None

        # ── URL ──────────────────────────────────────────────────────
        link_el = card.select_one("a[href]")
        product_url = (
            f"{base_url}{link_el['href']}"
            if link_el and str(link_el["href"]).startswith("/")
            else (str(link_el["href"]) if link_el else "")
        )

        raw_text = f"{name} NZD {price_raw}"
        if strength:
            raw_text += f" {strength}"

        results.append(PharmacyDirectRaw(
            product_name=name,
            price_nzd=price_clean,
            sale_price_nzd=sale_str,
            strength=strength,
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
                "Pharmacy Direct anti-bot: %s", ab.value
            )
        resp.raise_for_status()
        return resp.text

    return await with_retry(_do)


async def crawl_pharmacydirect(
    inn_name: str,
    max_pages: int = 3,
) -> list[ParsedDrug | None]:
    results: list[PharmacyDirectRaw] = []

    async with httpx.AsyncClient(timeout=25.0) as client:
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
            source_site="pharmacydirect",
            source_url=raw.url,
        )
        if drug:
            if raw.sale_price_nzd:
                drug.extra["sale_price_nzd"] = raw.sale_price_nzd
            if raw.strength:
                drug.extra["strength_hint"] = raw.strength
        parsed.append(drug)

    return parsed


async def crawl_pharmacydirect_multi(
    inn_names: list[str],
) -> dict[str, list[ParsedDrug | None]]:
    results: dict[str, list[ParsedDrug | None]] = {}
    for inn in inn_names:
        results[inn] = await crawl_pharmacydirect(inn)
        await asyncio.sleep(RATE_LIMIT_DELAY)
    return results
