"""뉴질랜드 거시지표 — Supabase nz_health_expenditure / nz_world_population 기반.

Supabase에 데이터가 없으면 Stats NZ / IMF 2024 기준 정적 값으로 폴백.
"""
from __future__ import annotations

from typing import Any

# 폴백용 정적 값 (Supabase 이관 전 또는 조회 실패 시)
# 출처: IMF WEO 2024, Stats NZ 2024, MoH NZ 2023, NZPMA 2024
_STATIC_MACRO: list[dict] = [
    {"label": "1인당 GDP",    "value": "USD $48,781",  "sub": "2024  ·  IMF WEO / Stats NZ"},
    {"label": "인구",         "value": "524만 명",      "sub": "2024  ·  Stats NZ"},
    {"label": "의약품 시장",   "value": "NZD $24억",    "sub": "2023  ·  MoH NZ  ·  보건 지출 포함"},
    {"label": "실질 성장률",   "value": "0.3%",         "sub": "2024  ·  Stats NZ"},
]

_cache: list[dict] | None = None


def get_nz_macro() -> list[dict[str, Any]]:
    """Supabase에서 뉴질랜드 거시지표 조회. 실패 시 정적 폴백."""
    global _cache
    if _cache is not None:
        return _cache

    try:
        from utils.db import get_client
        sb = get_client()
        pop_row = (
            sb.table("nz_world_population")
            .select("population,year")
            .eq("country_code", "NZL")
            .order("year", desc=True)
            .limit(1)
            .execute()
            .data
        )
        exp_row = (
            sb.table("nz_health_expenditure")
            .select("value,year,series")
            .eq("country_or_area", "New Zealand")
            .ilike("series", "%per capita%")
            .order("year", desc=True)
            .limit(1)
            .execute()
            .data
        )

        result = list(_STATIC_MACRO)  # 기본값 복사
        if pop_row:
            p = pop_row[0]
            pop_val = p["population"]
            if isinstance(pop_val, (int, float)):
                result[1] = {
                    "label": "인구",
                    "value": f"{pop_val:,}명",
                    "sub": f"{p['year']}  ·  World Bank",
                }
        if exp_row:
            e = exp_row[0]
            result[0] = {
                "label": "보건 지출/인구",
                "value": f"USD ${e['value']:,.0f}",
                "sub": f"{e['year']}  ·  UN SYB / MoH NZ",
            }

        _cache = result
        return result
    except Exception:
        return _STATIC_MACRO


# 하위 호환 — server.py에서 `from utils.nz_macro import NZ_MACRO` 사용
NZ_MACRO = _STATIC_MACRO
