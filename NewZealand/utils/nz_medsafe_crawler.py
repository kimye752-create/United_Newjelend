"""Medsafe NZ (medsafe.govt.nz) 의약품 허가 정보 크롤러.

수집 전략:
  Medsafe DbSearch Product/Application Search 페이지를 INN별로 호출하여
  허가 품목 목록(product_id, product_name, sponsor, 처방 분류 등)을 수집한다.

  검색 흐름:
    1. GET  https://www.medsafe.govt.nz/DbSearch/            ← 세션 쿠키 취득
    2. POST https://www.medsafe.govt.nz/DbSearch/Default.asp ← 검색 실행
       Body: optSearch=Product&txtIngredient={inn_name}&...

  결과 테이블 컬럼:
    Product | Active ingredients | Sponsor | Status | Approval date | Notification date
    (각 Product 셀은 ProductDetail.asp?ID=XXXX 링크를 포함 → ID를 consent_number로 사용)

결과는 nz_medsafe_consents 테이블에 upsert된다.

출처: Medsafe NZ — 뉴질랜드 의약품 및 의료기기 안전청
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup  # type: ignore[import]

from utils.antibot import detect as antibot_detect, pick_ua, AntiBotType
from utils.backoff_retry import with_retry

logger = logging.getLogger(__name__)

BASE_URL    = "https://www.medsafe.govt.nz"
DBSEARCH_URL = f"{BASE_URL}/DbSearch/"
SEARCH_URL   = f"{BASE_URL}/DbSearch/Default.asp"
DETAIL_URL   = f"{BASE_URL}/DbSearch/ProductDetail.asp"
RATE_LIMIT_DELAY = 4.0   # Medsafe 정부 ASP 서버 — 넉넉한 간격 필요


def _make_headers(referer: str = DBSEARCH_URL) -> dict[str, str]:
    return {
        "User-Agent": pick_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-NZ,en;q=0.9",
        "Referer": referer,
        "Origin": BASE_URL,
        "Content-Type": "application/x-www-form-urlencoded",
    }


@dataclass
class MedsafeConsent:
    """Medsafe DbSearch 허가 항목 원시 데이터."""
    consent_number:      str           # ProductDetail?ID 값
    product_name:        str
    inn_name:            str | None    # Active ingredients 컬럼
    sponsor:             str | None
    dosage_form:         str | None
    strength:            str | None
    prescription_status: str | None    # 제품명 괄호 내 분류 e.g. 'Prescription'
    approval_date:       str | None
    registration_status: str | None    # 'Consent given' / 'Approval lapsed' 등
    source_url:          str
    raw_payload:         dict = field(default_factory=dict)


# ── 제품명 파싱 헬퍼 ──────────────────────────────────────────────────────────

_RX_CLASS_RE     = re.compile(r"\(([^)]+)\)\s*$")
_STRENGTH_RE     = re.compile(
    r"(?:,\s*)(\d[\d./\s]*(?:mg|mcg|g|mL|IU|%|mmol)(?:[/\s]\d+[\d./\s]*(?:mg|mcg|g|mL|IU|%))?)",
    re.IGNORECASE,
)
_DOSAGE_FORM_RE  = re.compile(
    r"\b(tablet|capsule|solution|injection|cream|ointment|gel|spray|drops?|"
    r"syrup|suspension|powder|film coated tablet|modified release tablet|"
    r"effervescent tablet|chewable tablet|soft gelatin capsule)\b",
    re.IGNORECASE,
)


def _parse_product_name(name: str) -> tuple[str | None, str | None, str | None]:
    """제품명 문자열 → (dosage_form, strength, prescription_status) 추출."""
    # 괄호 내 처방 분류 (예: "(Prescription)")
    rx_match = _RX_CLASS_RE.search(name)
    rx_class = rx_match.group(1).strip() if rx_match else None
    clean = name[: rx_match.start()].strip() if rx_match else name

    # 함량/규격
    s_match = _STRENGTH_RE.search(clean)
    strength = s_match.group(1).strip() if s_match else None

    # 제형
    d_match = _DOSAGE_FORM_RE.search(clean)
    dosage_form = d_match.group(0).strip() if d_match else None

    return dosage_form, strength, rx_class


# ── HTML 파싱 ──────────────────────────────────────────────────────────────────

def _parse_search_results(
    html: str,
    inn_query: str,
) -> list[MedsafeConsent]:
    """Medsafe DbSearch POST 결과 HTML → MedsafeConsent 목록.

    결과 테이블은 페이지의 두 번째 <table> (index 1).
    컬럼: Product | Active ingredients | Sponsor | Status | Approval date | Notification date
    """
    soup = BeautifulSoup(html, "html.parser")

    # 빠른 실패: "Nothing was found" 체크
    if "Nothing was found matching your search criteria" in soup.get_text():
        logger.info("Medsafe: INN=%s — 검색 결과 없음", inn_query)
        return []

    tables = soup.find_all("table")
    # 결과 테이블은 form 테이블(index 0) 다음에 오는 테이블
    result_table = None
    for t in tables[1:]:
        rows = t.find_all("tr")
        if not rows:
            continue
        header_cells = rows[0].find_all(["th", "td"])
        header_text = " ".join(c.get_text(strip=True) for c in header_cells)
        if "Product" in header_text and ("Sponsor" in header_text or "Active" in header_text):
            result_table = t
            break

    if result_table is None:
        logger.warning("Medsafe: INN=%s — 결과 테이블을 찾을 수 없음", inn_query)
        return []

    results: list[MedsafeConsent] = []
    rows = result_table.find_all("tr")

    for row in rows[1:]:   # 헤더 건너뜀
        cells = row.find_all(["th", "td"])
        if len(cells) < 3:
            continue

        # ── Product 이름 + ID 링크 ───────────────────────────────────────
        name_cell = cells[0]
        product_name = name_cell.get_text(strip=True)
        if not product_name:
            continue

        link_el = name_cell.find("a", href=True)
        product_id = ""
        if link_el:
            href = str(link_el["href"])
            id_match = re.search(r"ID=(\d+)", href, re.IGNORECASE)
            if id_match:
                product_id = id_match.group(1)

        source_url = f"{DETAIL_URL}?ID={product_id}" if product_id else SEARCH_URL

        # ── 나머지 컬럼 ─────────────────────────────────────────────────
        active_ing   = cells[1].get_text(strip=True) if len(cells) > 1 else None
        sponsor      = cells[2].get_text(strip=True) if len(cells) > 2 else None
        reg_status   = cells[3].get_text(strip=True) if len(cells) > 3 else None
        approval_dt  = cells[4].get_text(strip=True) if len(cells) > 4 else None

        # ── 제품명에서 제형/함량/처방분류 추출 ──────────────────────────
        dosage_form, strength, rx_class = _parse_product_name(product_name)

        # inn_name: 쿼리 INN + Active ingredients 컬럼 합성
        inn_name = active_ing or inn_query

        results.append(MedsafeConsent(
            consent_number=product_id or product_name[:80],
            product_name=product_name,
            inn_name=inn_name,
            sponsor=sponsor or None,
            dosage_form=dosage_form,
            strength=strength,
            prescription_status=rx_class,
            approval_date=approval_dt or None,
            registration_status=reg_status or None,
            source_url=source_url,
            raw_payload={
                "inn_query": inn_query,
                "active_ingredients": active_ing,
                "registration_status": reg_status,
            },
        ))

    logger.info("Medsafe: INN=%s → %d건 파싱", inn_query, len(results))
    return results


# ── 비동기 HTTP 요청 ───────────────────────────────────────────────────────────

async def _fetch_html(client: httpx.AsyncClient, inn_name: str) -> str:
    """Medsafe DbSearch POST → 결과 HTML 반환.

    1. GET DbSearch/ 로 세션 쿠키 취득
    2. POST DbSearch/Default.asp 에 검색 폼 전송
    """
    # Step 1: 세션 초기화
    async def _warmup() -> None:
        await client.get(DBSEARCH_URL, headers={
            "User-Agent": pick_ua(),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-NZ,en;q=0.9",
        })

    async def _do() -> str:
        await _warmup()
        await asyncio.sleep(2.0)   # ASP 서버 세션 안정화 대기

        form_data = {
            "optSearch":       "Product",
            "txtIngredient":   inn_name,
            "txtTradeName":    "",
            "txtSponsor":      "",
            "cboClassification": "",
            "cboProductType":  "",
            "txtFromDate":     "",
            "txtToDate":       "",
            "cboType":         "",
            "cboStatus":       "",
            "cmdSearch":       "Submit",
        }

        resp = await client.post(
            SEARCH_URL,
            data=form_data,
            headers=_make_headers(),
        )
        ab = antibot_detect(resp.status_code, resp.text[:2000], dict(resp.headers))
        if ab != AntiBotType.NONE:
            logger.warning("Medsafe anti-bot 탐지: %s (INN=%s)", ab.value, inn_name)
        resp.raise_for_status()
        return resp.text

    return await with_retry(_do)


# ── 공개 크롤러 함수 ───────────────────────────────────────────────────────────

async def crawl_medsafe(inn_name: str) -> list[MedsafeConsent]:
    """단일 INN명으로 Medsafe DbSearch 검색 → MedsafeConsent 목록 반환."""
    try:
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            html = await _fetch_html(client, inn_name)
    except Exception as exc:
        logger.error("Medsafe 크롤링 실패 (INN=%s): %s", inn_name, exc)
        return []

    return _parse_search_results(html, inn_name)


async def crawl_medsafe_multi(inn_names: list[str]) -> dict[str, list[MedsafeConsent]]:
    """여러 INN명에 대해 순차 크롤링 (정부 사이트 rate limit 준수)."""
    results: dict[str, list[MedsafeConsent]] = {}
    for inn in inn_names:
        results[inn] = await crawl_medsafe(inn)
        await asyncio.sleep(RATE_LIMIT_DELAY)
    return results


# ── Supabase 저장 ──────────────────────────────────────────────────────────────

def _consent_to_db_row(c: MedsafeConsent) -> dict[str, Any]:
    """MedsafeConsent → nz_medsafe_consents 테이블 행."""
    return {
        "consent_number":      c.consent_number,
        "product_name":        c.product_name,
        "inn_name":            c.inn_name,
        "sponsor":             c.sponsor,
        "dosage_form":         c.dosage_form,
        "strength":            c.strength,
        "prescription_status": c.prescription_status,
        "approval_date":       c.approval_date,
        "raw_payload":         {
            **c.raw_payload,
            "registration_status": c.registration_status,
            "source_url": c.source_url,
        },
        "updated_at":          datetime.utcnow().isoformat(),
    }


def upsert_medsafe_consents(
    consents: list[MedsafeConsent],
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """nz_medsafe_consents 테이블에 upsert. dry_run=True면 DB 쓰기 생략."""
    if not consents:
        return {"inserted": 0, "skipped": 0}

    rows = [_consent_to_db_row(c) for c in consents if c.consent_number]
    if not rows:
        return {"inserted": 0, "skipped": len(consents)}

    if dry_run:
        logger.info("[dry-run] Medsafe upsert 건너뜀 (%d행)", len(rows))
        for row in rows[:3]:
            logger.info("  샘플: %s", {k: v for k, v in row.items() if k != "raw_payload"})
        return {"inserted": 0, "skipped": len(rows)}

    from utils.db import get_client
    sb = get_client()
    sb.table("nz_medsafe_consents").upsert(
        rows, on_conflict="consent_number"
    ).execute()

    return {"inserted": len(rows), "skipped": 0}


# ── 통합 실행 진입점 ───────────────────────────────────────────────────────────

def run(inn_names: list[str], *, dry_run: bool = False) -> dict[str, Any]:
    """inn_names 목록에 대해 Medsafe 크롤링 + DB 저장.

    Args:
        inn_names: 검색할 INN 성분명 목록
        dry_run:   True면 DB 쓰기 생략 (테스트용)

    Returns:
        {"ok": bool, "inserted": int, "skipped": int, "total_found": int, "errors": list}
    """
    all_consents: list[MedsafeConsent] = []
    errors: list[str] = []

    async def _run_all() -> None:
        results = await crawl_medsafe_multi(inn_names)
        for inn, consents in results.items():
            all_consents.extend(consents)

    import asyncio as _asyncio
    _asyncio.run(_run_all())

    counts = upsert_medsafe_consents(all_consents, dry_run=dry_run)
    return {
        "ok": True,
        "inserted": counts["inserted"],
        "skipped": counts["skipped"],
        "total_found": len(all_consents),
        "errors": errors,
    }
