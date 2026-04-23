"""PHARMAC 뉴질랜드 공식 약가 스케줄 XML 파서.

PHARMAC (schedule.pharmac.govt.nz) 월별 XML 스케줄에서
보조금가(subsidy)·제조업체 공시가(ex-manufacturer)·병원 국가협상가(hospital price)를
INN 기반으로 추출한다.

환경변수:
  PHARMAC_SCHEDULE_URL  — XML URL 덮어쓰기 (기본: 자동 탐색)
  PHARMAC_AUD_NZD       — AUD→NZD 환율 (미사용, NZD 자체 사용)
  PHARMAC_FETCH         — "0" 이면 API 호출 건너뜀 (테스트용)
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import quote
import xml.etree.ElementTree as ET

import httpx

# ---------------------------------------------------------------------------
# 상수 및 설정
# ---------------------------------------------------------------------------

# PHARMAC 공식 스케줄 XML URL 후보 (순서대로 시도)
_SCHEDULE_URLS: Final[list[str]] = [
    "https://schedule.pharmac.govt.nz/Schedule.xml",
    "https://www.pharmac.govt.nz/assets/schedule.xml",
    "https://schedule.pharmac.govt.nz/ScheduleOnlineFull.xml",
]

PHARMAC_METHODOLOGY_LABEL_KO: Final[str] = "(PHARMAC, 방법론적 추산)"
_DEFAULT_NZD_KRW: Final[float] = 830.0   # 폴백: 1 NZD ≈ 830 KRW (2025.04 기준)
_API_SLEEP_SEC: float = float(os.environ.get("PHARMAC_API_SLEEP_SEC", "2"))

_api_lock = threading.Lock()
_last_api_call_ts: float = 0.0

# XML 캐시 (1일 TTL)
_xml_cache: str | None = None
_xml_cache_ts: float = 0.0
_XML_CACHE_TTL: float = 86400.0

# INN 동의어 매핑 (PHARMAC 표기 vs WHO INN)
_PHARMAC_INN_SYNONYMS: dict[str, list[str]] = {
    "paracetamol":      ["acetaminophen"],
    "acetaminophen":    ["paracetamol"],
    "salbutamol":       ["albuterol"],
    "albuterol":        ["salbutamol"],
    "adrenaline":       ["epinephrine"],
    "epinephrine":      ["adrenaline"],
    "frusemide":        ["furosemide"],
    "furosemide":       ["frusemide"],
    "fluticasone":      ["fluticasone propionate", "fluticasone furoate"],
    "hydroxyurea":      ["hydroxycarbamide"],
    "hydroxycarbamide": ["hydroxyurea"],
    "cilostazol":       ["cilostazol"],
    "rosuvastatin":     ["rosuvastatin calcium"],
    "amlodipine":       ["amlodipine besylate"],
    "omeprazole":       ["omeprazole magnesium"],
    "metformin":        ["metformin hydrochloride"],
    "sertraline":       ["sertraline hydrochloride"],
}


# ---------------------------------------------------------------------------
# 결과 dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PharmacPricingResult:
    """PHARMAC 스케줄에서 추출한 참고 가격 (방법론적 추산)."""

    product_id: str
    search_terms_tried: tuple[str, ...] = ()
    search_hit: bool = False
    listing_url: str = ""
    schedule_drug_name: str = ""
    pack_description: str = ""
    subsidy_nzd: float | None = None           # 보조금가 (환자 부담 NZD)
    manufacturer_price_nzd: float | None = None  # 제조업체 공시가
    hospital_price_nzd: float | None = None     # 병원 국가협상가
    methodology_label_ko: str = PHARMAC_METHODOLOGY_LABEL_KO
    fetch_error: str = ""
    # 추가 메타
    pharmac_item_code: str | None = None
    pharmac_brand_name: str | None = None
    pharmac_pack_size: Any = None
    pharmac_subsidy_type: str | None = None     # "Full" / "Partial" / "OFT"
    pharmac_funded: bool = False
    pharmac_total_brands: int = 0
    pharmac_brands: tuple[dict[str, Any], ...] = ()
    # 부가: HTA 비고
    pharmac_note: str = ""

    # ── PBS backward-compat aliases ─────────────────────────────────────────
    # analyzer가 pbs_res.dpmq_aud / dpmq_sgd_hint 를 직접 참조하는 경우 대비.
    # NZ 컨텍스트에서 dpmq_aud → subsidy_nzd, dpmq_sgd_hint → subsidy_nzd (NZD 그대로).
    @property
    def dpmq_aud(self) -> float | None:
        """PBS compat: PHARMAC 보조금가 (NZD). 분석기에서 참고가 존재 여부 확인용."""
        return self.subsidy_nzd

    @property
    def dpmq_sgd_hint(self) -> float | None:
        """PBS compat: NZD 보조금가 그대로 반환 (분석기 표시용)."""
        return self.subsidy_nzd

    @property
    def aud_to_sgd_rate(self) -> float | None:
        """PBS compat: NZD 자체 사용이므로 1.0 반환."""
        return 1.0 if self.subsidy_nzd is not None else None

    def to_prompt_block(self) -> str:
        lines: list[str] = [
            "### PHARMAC 참고 가격 (뉴질랜드 공식 스케줄, 방법론적 추산)",
            f"- 라벨: {self.methodology_label_ko}",
            "- 주의: 정부 보조금·협상가이며 수출가 직접 벤치마크가 아님. HTA·국가협상 참고용.",
        ]
        if self.fetch_error:
            lines.append(f"- 수집 상태: {self.fetch_error}")
        if self.listing_url:
            lines.append(f"- PHARMAC 페이지: {self.listing_url}")
        if self.pharmac_item_code:
            lines.append(f"- 품목 코드: {self.pharmac_item_code}")
        if self.schedule_drug_name:
            lines.append(f"- 스케줄 표기: {self.schedule_drug_name}")
        if self.pack_description:
            lines.append(f"- 제형·규격: {self.pack_description}")
        if self.subsidy_nzd is not None:
            lines.append(f"- 보조금가 (NZD): ${self.subsidy_nzd:.2f}")
        if self.manufacturer_price_nzd is not None:
            lines.append(f"- 제조업체 공시가 (NZD): ${self.manufacturer_price_nzd:.2f}")
        if self.hospital_price_nzd is not None:
            lines.append(f"- 병원 국가협상가 (NZD): ${self.hospital_price_nzd:.2f}")
        if self.pharmac_subsidy_type:
            lines.append(f"- 보조금 유형: {self.pharmac_subsidy_type}")
        if self.pharmac_brand_name:
            lines.append(f"- 브랜드명: {self.pharmac_brand_name}")
        if self.pharmac_total_brands > 1:
            lines.append(f"- 등재 브랜드 수: {self.pharmac_total_brands}")
        if self.search_terms_tried:
            lines.append(f"- 시도 검색어: {', '.join(self.search_terms_tried)}")
        return "\n".join(lines)

    def to_flat_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "pharmac_listing_url":           self.listing_url or None,
            "pharmac_schedule_drug_name":    self.schedule_drug_name or None,
            "pharmac_pack_description":      self.pack_description or None,
            "pharmac_subsidy_nzd":           self.subsidy_nzd,
            "pharmac_manufacturer_price_nzd": self.manufacturer_price_nzd,
            "pharmac_hospital_price_nzd":    self.hospital_price_nzd,
            "pharmac_subsidy_type":          self.pharmac_subsidy_type,
            "pharmac_methodology_label_ko":  self.methodology_label_ko,
            "pharmac_search_terms_tried":    ", ".join(self.search_terms_tried) if self.search_terms_tried else None,
            "pharmac_search_hit":            self.search_hit,
            "pharmac_fetch_error":           self.fetch_error or None,
            "pharmac_item_code":             self.pharmac_item_code,
            "pharmac_brand_name":            self.pharmac_brand_name,
            "pharmac_pack_size":             self.pharmac_pack_size,
            "pharmac_funded":                self.pharmac_funded,
            "pharmac_total_brands":          self.pharmac_total_brands,
            "pharmac_note":                  self.pharmac_note or None,
        }
        # ── PBS backward-compat 키 (analyzer/report_generator 호환) ─────────
        d.update({
            "pbs_listing_url":          self.listing_url or None,
            "pbs_schedule_drug_name":   self.schedule_drug_name or None,
            "pbs_pack_description":     self.pack_description or None,
            "pbs_dpmq_aud":             self.subsidy_nzd,          # NZD 보조금가
            "pbs_dpmq_label_en":        "PHARMAC subsidy price (NZD)",
            "pbs_aud_to_sgd_rate":      1.0 if self.subsidy_nzd is not None else None,
            "pbs_dpmq_sgd_hint":        self.subsidy_nzd,          # NZD 그대로
            "pbs_methodology_label_ko": self.methodology_label_ko,
            "pbs_search_terms_tried":   ", ".join(self.search_terms_tried) if self.search_terms_tried else None,
            "pbs_search_hit":           self.search_hit,
            "pbs_fetch_error":          self.fetch_error or None,
            "pbs_item_code":            self.pharmac_item_code,
            "pbs_determined_price":     self.manufacturer_price_nzd,
            "pbs_pack_size":            self.pharmac_pack_size,
            "pbs_benefit_type":         self.pharmac_subsidy_type,
            "pbs_brand_name":           self.pharmac_brand_name,
            "pbs_innovator":            None,
            "pbs_restriction":          False,
            "pbs_total_brands":         self.pharmac_total_brands,
        })
        return d


# ---------------------------------------------------------------------------
# XML 로드 및 파싱
# ---------------------------------------------------------------------------

def _api_sleep() -> None:
    if _API_SLEEP_SEC > 0:
        time.sleep(_API_SLEEP_SEC)


def _fetch_xml_raw() -> str | None:
    """PHARMAC 스케줄 XML 원문 반환. 실패 시 None."""
    global _xml_cache, _xml_cache_ts
    now = time.monotonic()
    if _xml_cache and (now - _xml_cache_ts) < _XML_CACHE_TTL:
        return _xml_cache

    env_url = os.environ.get("PHARMAC_SCHEDULE_URL", "").strip()
    urls = [env_url] + _SCHEDULE_URLS if env_url else _SCHEDULE_URLS

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; NZPharmaBot/1.0)",
        "Accept": "application/xml, text/xml, */*",
    }

    for url in urls:
        try:
            with _api_lock:
                elapsed = time.monotonic() - _last_api_call_ts
                wait = _API_SLEEP_SEC - elapsed
                if wait > 0:
                    time.sleep(wait)
            r = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
            if r.status_code == 200 and r.content:
                text = r.text
                _xml_cache = text
                _xml_cache_ts = time.monotonic()
                return text
        except Exception:
            continue

    return None


def _expand_synonyms(term: str) -> list[str]:
    t = term.strip().lower()
    extras = _PHARMAC_INN_SYNONYMS.get(t, [])
    seen: set[str] = set()
    out: list[str] = []
    for s in [t] + extras:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _build_needles(inn: str) -> list[str]:
    raw = (inn or "").strip().lower()
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"[+/]", raw) if p.strip()]
    needles: list[str] = []
    for p in parts:
        needles.extend(_expand_synonyms(p))
    if raw not in needles:
        needles.append(raw)
    seen: set[str] = set()
    out: list[str] = []
    for n in needles:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _safe_float(val: Any) -> float | None:
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_xml_for_inn(xml_text: str, needles: list[str]) -> list[dict[str, Any]]:
    """XML 파싱 → INN/브랜드명 매칭 후 가격 행 추출.

    PHARMAC XML 구조 (일반적):
      <Schedule>
        <Pharmaceutical>
          <Name>...</Name>
          <Chemical>...</Chemical>
          <Presentation>...</Presentation>
          <SubsidyPrice>...</SubsidyPrice>
          <ManufacturerPrice>...</ManufacturerPrice>
          <HospitalPrice>...</HospitalPrice>
        </Pharmaceutical>
      </Schedule>
    (실제 태그명은 버전별 상이 — 다중 패턴 지원)
    """
    if not needles:
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # 태그명 패턴 — PHARMAC XML 버전별 대응
    name_tags = {"name", "chemical", "genericname", "inn", "drugname", "chemicalname"}
    price_tags_subsidy = {"subsidyprice", "subsidy", "patientcharge", "pharmaccost"}
    price_tags_mfr = {"manufacturerprice", "exmanufacturerprice", "mfr", "manufacturercharge"}
    price_tags_hosp = {"hospitalprice", "hospitalnegotiatedprice", "hospitalcost"}
    brand_tags = {"brandname", "brand", "productname", "tradename"}
    pack_tags = {"presentation", "pack", "packsize", "packdescription", "strength"}
    code_tags = {"code", "itemcode", "pharmacccode", "id"}

    results: list[dict[str, Any]] = []

    # 모든 엔트리 반복 (다양한 부모 태그 지원)
    for entry in root.iter():
        # 이름 텍스트 수집
        entry_texts: list[str] = []
        for child in entry:
            tag = child.tag.lower().replace("-", "").replace("_", "")
            if tag in name_tags and child.text:
                entry_texts.append(child.text.strip().lower())

        if not entry_texts:
            continue

        # needle 매칭
        combined = " ".join(entry_texts)
        if not any(needle in combined for needle in needles):
            continue

        # 가격·메타 추출
        row: dict[str, Any] = {"_entry_texts": entry_texts}
        for child in entry:
            tag = child.tag.lower().replace("-", "").replace("_", "")
            val = child.text.strip() if child.text else None

            if tag in price_tags_subsidy and val:
                row.setdefault("subsidy_nzd", _safe_float(val))
            elif tag in price_tags_mfr and val:
                row.setdefault("manufacturer_price_nzd", _safe_float(val))
            elif tag in price_tags_hosp and val:
                row.setdefault("hospital_price_nzd", _safe_float(val))
            elif tag in brand_tags and val:
                row.setdefault("brand_name", val)
            elif tag in pack_tags and val:
                row.setdefault("pack_description", val)
            elif tag in code_tags and val:
                row.setdefault("item_code", val)
            elif tag in {"subsidytype", "fundingtype", "type"} and val:
                row.setdefault("subsidy_type", val)
            elif tag in {"funded", "isfunded"} and val:
                row.setdefault("funded", val.lower() in ("true", "yes", "1", "y"))
            elif tag in {"note", "restriction", "comment"} and val:
                row.setdefault("note", val)

        # 이름 정보 추가
        row["_matched_name"] = entry_texts[0] if entry_texts else ""
        results.append(row)

    return results


def _select_best_entry(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """subsidy_nzd가 있는 행 우선, 없으면 manufacturer_price_nzd 우선."""
    with_subsidy = [r for r in rows if r.get("subsidy_nzd") is not None]
    if with_subsidy:
        return with_subsidy[0]
    with_mfr = [r for r in rows if r.get("manufacturer_price_nzd") is not None]
    if with_mfr:
        return with_mfr[0]
    return rows[0]


# ---------------------------------------------------------------------------
# 메인 공개 함수
# ---------------------------------------------------------------------------

def fetch_pharmac_pricing_sync(meta: dict[str, str]) -> PharmacPricingResult:
    """PHARMAC XML 스케줄에서 INN 매칭 후 가격 추출. 동기."""
    pid = str(meta.get("product_id", "") or "")

    if os.environ.get("PHARMAC_FETCH", "").strip().lower() in ("0", "false", "skip", "off"):
        return PharmacPricingResult(
            product_id=pid,
            fetch_error="PHARMAC_FETCH 비활성(테스트/오프라인)",
        )

    inn = str(meta.get("inn", "") or "").strip()
    trade = str(meta.get("trade_name", "") or "").strip()
    needles = _build_needles(inn)
    if trade and trade.lower() not in needles:
        needles.append(trade.lower())

    terms_tried = list(needles)

    if not needles:
        return PharmacPricingResult(
            product_id=pid,
            fetch_error="검색어 없음(INN/trade_name 미입력)",
        )

    xml_text = _fetch_xml_raw()
    if not xml_text:
        # XML 로드 실패 → 폴백: PHARMAC 검색 페이지 URL만 반환
        listing_url = f"https://schedule.pharmac.govt.nz/ScheduleOnlineFull.xml"
        return PharmacPricingResult(
            product_id=pid,
            search_terms_tried=tuple(terms_tried),
            listing_url=listing_url,
            fetch_error="PHARMAC XML 로드 실패 — 스케줄 페이지 직접 확인 필요",
        )

    rows = _parse_xml_for_inn(xml_text, needles)

    if not rows:
        return PharmacPricingResult(
            product_id=pid,
            search_terms_tried=tuple(terms_tried),
            listing_url=f"https://schedule.pharmac.govt.nz/",
            fetch_error="PHARMAC 스케줄 미등재 또는 XML 구조 불일치",
        )

    best = _select_best_entry(rows)

    # 브랜드 목록
    brand_set: dict[str, dict[str, Any]] = {}
    for row in rows:
        bn = row.get("brand_name") or ""
        if bn and bn not in brand_set:
            brand_set[bn] = {
                "brand_name": bn,
                "subsidy_nzd":          row.get("subsidy_nzd"),
                "manufacturer_price_nzd": row.get("manufacturer_price_nzd"),
                "item_code":            row.get("item_code"),
            }
    pharmac_brands = tuple(brand_set.values())

    subsidy      = best.get("subsidy_nzd")
    mfr_price    = best.get("manufacturer_price_nzd")
    hosp_price   = best.get("hospital_price_nzd")
    item_code    = best.get("item_code")
    brand_name   = best.get("brand_name")
    pack_desc    = best.get("pack_description", "")
    subsidy_type = best.get("subsidy_type")
    funded       = bool(best.get("funded", subsidy is not None))
    note         = best.get("note", "")
    drug_name    = best.get("_matched_name", inn)

    listing_url = (
        f"https://schedule.pharmac.govt.nz/ScheduleOnlineFull.xml"
    )

    return PharmacPricingResult(
        product_id=pid,
        search_terms_tried=tuple(terms_tried),
        search_hit=True,
        listing_url=listing_url,
        schedule_drug_name=drug_name,
        pack_description=pack_desc,
        subsidy_nzd=subsidy,
        manufacturer_price_nzd=mfr_price,
        hospital_price_nzd=hosp_price,
        methodology_label_ko=PHARMAC_METHODOLOGY_LABEL_KO,
        fetch_error="" if (subsidy is not None or mfr_price is not None) else "가격 필드 없음",
        pharmac_item_code=item_code,
        pharmac_brand_name=brand_name,
        pharmac_pack_size=pack_desc or None,
        pharmac_subsidy_type=subsidy_type,
        pharmac_funded=funded,
        pharmac_total_brands=len(brand_set),
        pharmac_brands=pharmac_brands,
        pharmac_note=note,
    )


async def fetch_pharmac_pricing(meta: dict[str, str]) -> PharmacPricingResult:
    """비동기 래퍼 (네트워크는 스레드에서 실행)."""
    return await asyncio.to_thread(fetch_pharmac_pricing_sync, meta)


# ---------------------------------------------------------------------------
# 하위 호환 alias (pbs_pricing → pharmac_pricing 전환 브릿지)
# ---------------------------------------------------------------------------

fetch_pbs_pricing_sync = fetch_pharmac_pricing_sync
fetch_pbs_pricing = fetch_pharmac_pricing
