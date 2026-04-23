"""GETS 뉴질랜드 정부 전자입찰 포털 낙찰가 크롤러 — Playwright 기반.

타깃: https://www.gets.govt.nz
필터 조건:
  - Type: Contract Awards (낙찰 완료 건)
  - Agency: PHARMAC / Health New Zealand / Ministry of Health
  - Category: Pharmaceuticals / Medical Supplies (UNSPSC 51000000)

환경변수 PLAYWRIGHT_LIVE=1 시 헤드풀 실행.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

from utils.nz_parser import ParsedDrug, parse_drug_text

BASE_URL = "https://www.gets.govt.nz"
SEARCH_PATH = "/ExternalIndex.htm"
CONTRACT_AWARDS_PATH = "/ExternalIndex.htm?d=contract_awards"
RATE_LIMIT_DELAY = 2.0

LIVE = os.environ.get("PLAYWRIGHT_LIVE", "0") == "1"

# 타깃 기관명 키워드
_TARGET_AGENCIES = [
    "pharmac",
    "health new zealand",
    "te whatu ora",
    "ministry of health",
    "healthnz",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-NZ,en;q=0.9",
}


@dataclass
class GetsAward:
    agency: str
    reference_number: str
    description: str
    supplier: str
    value_nzd: str
    date: str
    url: str
    raw_text: str
    parsed_drug: ParsedDrug | None = field(default=None, repr=False)


def _parse_gets_table(html: str, page_url: str) -> list[GetsAward]:
    from bs4 import BeautifulSoup  # type: ignore[import]

    soup = BeautifulSoup(html, "html.parser")
    awards: list[GetsAward] = []

    # GETS 테이블 또는 리스트 파싱
    table = soup.select_one(
        "table.results, table#resultsTable, "
        ".search-results table, "
        "[class*='results'] table, "
        "table"
    )
    if not table:
        # 리스트 형식 fallback
        for item in soup.select(".result-item, .tender-item, [class*='notice']"):
            title_el = item.select_one("h2, h3, .title, [class*='title']")
            desc = title_el.get_text(strip=True) if title_el else ""
            if not desc:
                continue
            link_el = item.select_one("a[href]")
            detail_url = (
                f"{BASE_URL}{link_el['href']}"
                if link_el and str(link_el["href"]).startswith("/")
                else (str(link_el["href"]) if link_el else page_url)
            )
            agency_el = item.select_one(".agency, .organisation, [class*='agency']")
            agency = agency_el.get_text(strip=True) if agency_el else ""
            value_el = item.select_one(".value, .amount, [class*='value']")
            value_str = ""
            if value_el:
                value_str = re.sub(r"[^\d.,]", "", value_el.get_text(strip=True))
            awards.append(
                GetsAward(
                    agency=agency,
                    reference_number="",
                    description=desc,
                    supplier="",
                    value_nzd=value_str,
                    date="",
                    url=detail_url,
                    raw_text=f"{desc} {value_str} NZD",
                )
            )
        return awards

    rows = table.select("tr")[1:]
    for row in rows:
        cells = [td.get_text(strip=True) for td in row.select("td")]
        if len(cells) < 3:
            continue

        link_el = row.select_one("a[href]")
        detail_url = (
            f"{BASE_URL}{link_el['href']}"
            if link_el and str(link_el["href"]).startswith("/")
            else page_url
        )

        agency     = cells[0] if len(cells) > 0 else ""
        ref_num    = cells[1] if len(cells) > 1 else ""
        description = cells[2] if len(cells) > 2 else ""
        supplier   = cells[3] if len(cells) > 3 else ""
        value      = cells[4] if len(cells) > 4 else ""
        date       = cells[5] if len(cells) > 5 else ""

        value_clean = re.sub(r"[^\d.,]", "", value).replace(",", "")
        raw_text = f"{description} {value_clean} NZD {supplier}"
        awards.append(
            GetsAward(
                agency=agency,
                reference_number=ref_num,
                description=description,
                supplier=supplier,
                value_nzd=value_clean,
                date=date,
                url=detail_url,
                raw_text=raw_text,
            )
        )
    return awards


def _filter_by_keyword(awards: list[GetsAward], keyword: str) -> list[GetsAward]:
    kw = keyword.lower()
    return [
        a for a in awards
        if kw in a.description.lower() or kw in a.raw_text.lower()
    ]


def _is_target_agency(award: GetsAward) -> bool:
    agency_lower = award.agency.lower()
    return any(t in agency_lower for t in _TARGET_AGENCIES)


async def _fetch_with_playwright(keyword: str) -> str:
    from playwright.async_api import async_playwright  # type: ignore[import]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not LIVE)
        page = await browser.new_page(extra_http_headers=_HEADERS)
        await page.goto(f"{BASE_URL}{CONTRACT_AWARDS_PATH}", wait_until="networkidle", timeout=30_000)

        # 검색어 입력 시도
        try:
            search_input = await page.query_selector(
                "input[name*='search'], input[type='search'], input[id*='search'], "
                "input[placeholder*='search' i], input[name='q']"
            )
            if search_input:
                await search_input.fill(keyword)
                btn = await page.query_selector(
                    "button[type='submit'], input[type='submit'], button:has-text('Search')"
                )
                if btn:
                    await btn.click()
                    await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        # PHARMAC/Health NZ 기관 필터 시도
        try:
            org_sel = await page.query_selector(
                "select[name*='agency'], select[name*='organisation'], "
                "select[id*='agency'], select[id*='org']"
            )
            if org_sel:
                await org_sel.select_option(label="PHARMAC")
        except Exception:
            pass

        await page.wait_for_timeout(2000)
        html = await page.content()
        await browser.close()
        return html


async def _fetch_html_fallback(keyword: str) -> str:
    import httpx

    params = {
        "keyword": keyword,
        "statusID": "4",          # Contract Awards
        "categories": "51000000",  # UNSPSC 의약품
    }
    url = f"{BASE_URL}{SEARCH_PATH}?{urlencode(params)}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=_HEADERS, follow_redirects=True)
        resp.raise_for_status()
        return resp.text


async def crawl_gets(
    inn_name: str,
    max_pages: int = 3,
) -> list[GetsAward]:
    all_awards: list[GetsAward] = []

    try:
        html = await _fetch_with_playwright(inn_name)
    except Exception:
        try:
            html = await _fetch_html_fallback(inn_name)
        except Exception:
            return []

    awards = _parse_gets_table(html, f"{BASE_URL}{CONTRACT_AWARDS_PATH}")
    # 1순위: 키워드 + 대상 기관
    filtered = [
        a for a in _filter_by_keyword(awards, inn_name)
        if _is_target_agency(a)
    ]
    # 2순위: 키워드만
    if not filtered:
        filtered = _filter_by_keyword(awards, inn_name)

    all_awards.extend(filtered)

    for award in all_awards:
        combined_text = f"{award.description} {award.value_nzd} NZD {award.supplier}"
        drug = await parse_drug_text(
            raw_text=combined_text,
            source_site="gets",
            source_url=award.url,
        )
        if drug:
            drug.extra["agency"]           = award.agency
            drug.extra["date"]             = award.date
            drug.extra["reference_number"] = award.reference_number
        award.parsed_drug = drug
        await asyncio.sleep(RATE_LIMIT_DELAY)

    return all_awards


async def crawl_gets_to_parsed(inn_name: str) -> list[ParsedDrug | None]:
    awards = await crawl_gets(inn_name)
    return [a.parsed_drug for a in awards]
