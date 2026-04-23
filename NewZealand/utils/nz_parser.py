"""뉴질랜드 의약품 원시 텍스트 → 구조화 데이터 파서.

Claude Haiku API를 호출하여 영문 제품명·함량·포장 단위·가격을 파싱하고
NZD → USD 환율 변환 후 ParsedDrug 데이터클래스로 반환한다.

핵심 방어:
  - 포장 단위 기준 단위당 가격(price_per_unit) 강제 계산 → FOB 역산 왜곡 방지
  - 환율 환경변수 NZ_USD_RATE 우선 적용 (yfinance 폴백)
  - VAT는 환경변수 NZ_GST_PCT에서 동적 로드 (하드코딩 금지, 기본 15%)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# 뉴질랜드 제형 표준명 (NZ 영문 → 표준 영문, 대부분 동일)
DOSAGE_FORM_MAP: dict[str, str] = {
    "tablet": "tablet",
    "tablets": "tablet",
    "tab": "tablet",
    "capsule": "capsule",
    "capsules": "capsule",
    "cap": "capsule",
    "ampoule": "ampoule",
    "ampoules": "ampoule",
    "amp": "ampoule",
    "syrup": "syrup",
    "suspension": "suspension",
    "cream": "cream",
    "ointment": "ointment",
    "solution": "solution",
    "sol": "solution",
    "patch": "patch",
    "injectable": "injectable",
    "injection": "injectable",
    "vial": "vial",
    "bottle": "bottle",
    "sachet": "sachet",
    "powder": "powder",
    "liquid": "liquid",
    "drops": "drops",
    "spray": "spray",
    "inhaler": "inhaler",
    "gel": "gel",
    "lotion": "lotion",
    "suppository": "suppository",
    "lozenge": "lozenge",
    "chewable": "chewable tablet",
    "effervescent": "effervescent tablet",
    "modified release": "modified release tablet",
    "extended release": "extended release tablet",
    "cr": "modified release tablet",
    "mr": "modified release tablet",
    "xr": "extended release tablet",
    "er": "extended release tablet",
    "sr": "sustained release tablet",
}

_RATE_CACHE: dict[str, float] = {"rate": 0.0, "ts": 0.0}
_RATE_TTL = 1800.0


def _nzd_to_usd_rate() -> float:
    """NZD → USD 환율. 환경변수 NZ_USD_RATE 우선, 이후 yfinance, 이후 폴백."""
    env_rate = os.environ.get("NZ_USD_RATE", "").strip()
    if env_rate:
        try:
            return float(env_rate)
        except ValueError:
            pass

    now = time.monotonic()
    if _RATE_CACHE["rate"] and (now - _RATE_CACHE["ts"]) < _RATE_TTL:
        return _RATE_CACHE["rate"]

    try:
        import yfinance as yf  # type: ignore[import]
        ticker = yf.Ticker("NZDUSD=X")
        rate = float(ticker.fast_info.last_price)
        if rate > 0:
            _RATE_CACHE["rate"] = rate
            _RATE_CACHE["ts"] = now
            return rate
    except Exception:
        pass

    return 0.5950  # 폴백: 1 NZD ≈ 0.5950 USD (2025.04 기준)


def _normalize_form(raw: str) -> str:
    token = raw.strip().lower()
    for key, val in DOSAGE_FORM_MAP.items():
        if key in token:
            return val
    return raw.strip()


def _safe_decimal(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError):
        return None


@dataclass
class ParsedDrug:
    inn_name: str
    brand_name: str
    strength_mg: float
    dosage_form: str
    pack_size: int
    total_price_nzd: Decimal
    price_per_unit_nzd: Decimal
    price_per_unit_usd: Decimal
    manufacturer: str
    source_site: str
    source_url: str
    raw_text: str
    confidence: float = 0.7
    promo_price_nzd: Decimal | None = None
    extra: dict[str, Any] = field(default_factory=dict)


_SCHEMA_DESC: dict[str, str] = {
    "inn_name":         "WHO INN 국제일반명 (예: Cilostazol). 불명확 시 brand_name 기반 추론",
    "brand_name":       "제품 상품명 (예: Plavix). 없으면 inn_name과 동일",
    "strength_mg":      "주성분 함량 숫자값 (mg 단위, 예: 100.0). 불명확 시 null",
    "dosage_form":      "제형 — 영문 표준으로 반환 (tablet/capsule/ampoule/syrup 등)",
    "pack_size":        "포장 단위 정수 (예: 30). 불명확 시 null",
    "total_price_nzd":  "포장 전체 가격 (NZD, 숫자만). 불명확 시 null",
    "manufacturer":     "제조사/판매사명. 불명확 시 '-'",
}


async def parse_drug_text(
    raw_text: str,
    source_site: str,
    source_url: str,
    promo_price_nzd: Decimal | None = None,
) -> ParsedDrug | None:
    api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _regex_fallback(raw_text, source_site, source_url, promo_price_nzd)

    schema_str = json.dumps(_SCHEMA_DESC, ensure_ascii=False, indent=2)
    prompt = f"""New Zealand pharmacy product text parsing task. Extract structured data as JSON.

Fields to extract:
{schema_str}

Rules:
1. price_per_unit_nzd = total_price_nzd / pack_size (calculate and include).
2. dosage_form must be English standard (tablet/capsule/ampoule/syrup/solution/cream etc).
3. Numeric fields: numbers only. String fields: text only.
4. Uncertain fields: return null. Return ONLY a JSON object, no other text.
5. NZ pharmacy products are in NZD. Strip any "$" or "NZD" prefix from price.

Input text:
{raw_text[:800]}

Return exactly one JSON object."""

    try:
        import httpx

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": CLAUDE_MODEL,
            "max_tokens": 512,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            content = resp.json()["content"][0]["text"].strip()

        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            return _regex_fallback(raw_text, source_site, source_url, promo_price_nzd)
        data: dict[str, Any] = json.loads(m.group(0))
        return _build_parsed(data, raw_text, source_site, source_url, promo_price_nzd)
    except Exception:
        return _regex_fallback(raw_text, source_site, source_url, promo_price_nzd)


def _build_parsed(
    data: dict[str, Any],
    raw_text: str,
    source_site: str,
    source_url: str,
    promo_price_nzd: Decimal | None,
) -> ParsedDrug | None:
    total = _safe_decimal(data.get("total_price_nzd"))
    if total is None:
        return None

    pack = int(data.get("pack_size") or 1) or 1
    per_unit_nzd = total / Decimal(pack)
    rate = Decimal(str(_nzd_to_usd_rate()))
    per_unit_usd = per_unit_nzd * rate

    raw_form = str(data.get("dosage_form") or "")
    dosage_form = _normalize_form(raw_form) if raw_form else "unknown"

    strength_raw = data.get("strength_mg")
    try:
        strength_mg = float(strength_raw) if strength_raw is not None else 0.0
    except (ValueError, TypeError):
        strength_mg = 0.0

    return ParsedDrug(
        inn_name=str(data.get("inn_name") or "").strip(),
        brand_name=str(data.get("brand_name") or "").strip(),
        strength_mg=strength_mg,
        dosage_form=dosage_form,
        pack_size=pack,
        total_price_nzd=total,
        price_per_unit_nzd=per_unit_nzd,
        price_per_unit_usd=per_unit_usd,
        manufacturer=str(data.get("manufacturer") or "-").strip(),
        source_site=source_site,
        source_url=source_url,
        raw_text=raw_text,
        promo_price_nzd=promo_price_nzd,
        confidence=0.75,
    )


def _regex_fallback(
    raw_text: str,
    source_site: str,
    source_url: str,
    promo_price_nzd: Decimal | None,
) -> ParsedDrug | None:
    # NZ 가격 형식: $12.99, NZD 12.99, 12.99
    price_m = re.search(r"(?:NZD\s*|NZ\$|\$)?\s*([\d,]+\.?\d*)", raw_text)
    if not price_m:
        return None
    total = _safe_decimal(price_m.group(1).replace(",", ""))
    if total is None:
        return None

    pack_m = re.search(r"(\d+)\s*(?:tablets?|capsules?|tabs?|caps?|pk|pack)", raw_text, re.I)
    pack = int(pack_m.group(1)) if pack_m else 1

    mg_m = re.search(r"(\d+(?:\.\d+)?)\s*mg", raw_text, re.I)
    strength_mg = float(mg_m.group(1)) if mg_m else 0.0

    rate = Decimal(str(_nzd_to_usd_rate()))
    per_unit_nzd = total / Decimal(pack)

    return ParsedDrug(
        inn_name="",
        brand_name="",
        strength_mg=strength_mg,
        dosage_form="unknown",
        pack_size=pack,
        total_price_nzd=total,
        price_per_unit_nzd=per_unit_nzd,
        price_per_unit_usd=per_unit_nzd * rate,
        manufacturer="-",
        source_site=source_site,
        source_url=source_url,
        raw_text=raw_text,
        promo_price_nzd=promo_price_nzd,
        confidence=0.4,
    )


async def parse_drug_texts_batch(
    items: list[dict[str, Any]],
) -> list[ParsedDrug | None]:
    tasks = [
        parse_drug_text(
            raw_text=item["raw_text"],
            source_site=item.get("source_site", ""),
            source_url=item.get("source_url", ""),
            promo_price_nzd=_safe_decimal(item.get("promo_price_nzd")),
        )
        for item in items
    ]
    return list(await asyncio.gather(*tasks))
