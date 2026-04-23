"""뉴질랜드 크롤러 통합 오케스트레이터.

실행 모드:
  all       — 모든 소스 순차 실행 (기본)
  retail    — 소매 약국 6개 체인만
  pharmac   — PHARMAC 월별 스케줄 XML만
  gets      — GETS 공공조달만
  medsafe   — Medsafe 품목허가만

예시:
  python scripts/run_all_crawlers.py                    # 전체
  python scripts/run_all_crawlers.py --mode retail      # 소매만
  python scripts/run_all_crawlers.py --mode medsafe --inns cilostazol rosuvastatin

Render Cron Job 예시:
  0 23 * * *   python scripts/run_all_crawlers.py --mode retail
  0 1 * * 1    python scripts/run_all_crawlers.py --mode pharmac
  0 23 * * 1   python scripts/run_all_crawlers.py --mode gets
  0 2 * * 1    python scripts/run_all_crawlers.py --mode medsafe
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.db import get_client  # noqa: E402

# 8개 KUP 제품의 INN (frontend/static/app.js INN_MAP 기반)
DEFAULT_INNS = [
    "cilostazol",
    "hydroxyurea",
    "gadobutrol",
    "fluticasone",
    "salmeterol",
    "omega-3",
    "rosuvastatin",
    "atorvastatin",
    "mosapride",
]

# 소매 크롤러 (모듈명, async 함수명, source_site 키)
RETAIL_CRAWLERS = [
    ("utils.nz_chemistwarehouse_crawler", "crawl_chemistwarehouse",   "chemistwarehouse"),
    ("utils.nz_lifepharmacy_crawler",     "crawl_lifepharmacy_multi", "lifepharmacy"),
    ("utils.nz_netpharmacy_crawler",      "crawl_netpharmacy",        "netpharmacy"),
    ("utils.nz_bargainchemist_crawler",   "crawl_bargainchemist",     "bargainchemist"),
    ("utils.nz_online_pharmacy_crawler",  "crawl_nzonlinepharmacy_multi", "nzonlinepharmacy"),
    ("utils.nz_pharmacydirect_crawler",   "crawl_pharmacydirect_multi",   "pharmacydirect"),
]


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{ts}] {msg}", flush=True)


def _insert_retail_rows(source_site: str, inn: str, drugs: list) -> int:
    """소매 크롤러 결과를 nz_retail_prices 에 insert."""
    sb = get_client()
    inserted = 0
    now = datetime.now(timezone.utc).isoformat()
    for drug in drugs or []:
        if drug is None:
            continue
        try:
            row = {
                "inn_name":           inn,
                "product_name":       getattr(drug, "brand_name", None) or getattr(drug, "inn_name", "") or "",
                "price_nzd":          getattr(drug, "total_price_nzd", None),
                "price_per_unit_nzd": getattr(drug, "price_per_unit_nzd", None),
                "fob_estimated_usd":  getattr(drug, "price_per_unit_usd", None),
                "confidence":         getattr(drug, "confidence", 0.5),
                "source_site":        source_site,
                "source_url":         getattr(drug, "source_url", "") or "",
                "market_segment":     "NZ",
                "strength":           getattr(drug, "strength_mg", None),
                "dosage_form":        getattr(drug, "dosage_form", None),
                "pack_size":          getattr(drug, "pack_size", None),
                "crawled_at":         now,
                "raw_payload":        getattr(drug, "extra", None),
            }
            sb.table("nz_retail_prices").insert(row).execute()
            inserted += 1
        except Exception as exc:
            _log(f"  ! retail insert 실패 [{source_site}/{inn}]: {str(exc)[:120]}")
    return inserted


async def _run_retail_one(mod_name: str, fn_name: str, source_site: str, inns: list[str]) -> dict:
    """단일 소매 크롤러 실행 후 DB 적재."""
    try:
        mod = importlib.import_module(mod_name)
        fn = getattr(mod, fn_name)
    except Exception as exc:
        return {"ok": False, "source": source_site, "error": f"import 실패: {exc}"}

    total = 0
    per_inn: dict[str, int] = {}
    for inn in inns:
        try:
            result = await fn(inn) if fn_name in {"crawl_chemistwarehouse", "crawl_netpharmacy", "crawl_bargainchemist"} else await fn([inn])
            # crawl_*_multi 는 {inn: [drugs]}, 단건은 [drugs]
            drugs = result.get(inn, []) if isinstance(result, dict) else (result or [])
            inserted = _insert_retail_rows(source_site, inn, drugs)
            per_inn[inn] = inserted
            total += inserted
        except Exception as exc:
            _log(f"  ! {source_site}/{inn} 실패: {str(exc)[:150]}")
            per_inn[inn] = 0
    return {"ok": True, "source": source_site, "total_inserted": total, "per_inn": per_inn}


async def run_retail(inns: list[str]) -> list[dict]:
    _log(f"▶ 소매 크롤러 시작 ({len(RETAIL_CRAWLERS)}개 사이트 × {len(inns)}개 INN)")
    results = []
    for mod_name, fn_name, site in RETAIL_CRAWLERS:
        _log(f"  • {site} ...")
        res = await _run_retail_one(mod_name, fn_name, site, inns)
        _log(f"    → {res}")
        results.append(res)
    return results


def run_pharmac(inns: list[str]) -> dict:
    """PHARMAC XML 스케줄 fetch 후 nz_pharmac_schedule 적재."""
    _log(f"▶ PHARMAC 스케줄 fetch ({len(inns)}개 INN)")
    try:
        from utils.pharmac_pricing import fetch_pharmac_pricing_sync
    except Exception as exc:
        return {"ok": False, "error": f"pharmac_pricing import 실패: {exc}"}

    sb = get_client()
    now = datetime.now(timezone.utc).isoformat()
    total = 0
    for inn in inns:
        try:
            result = fetch_pharmac_pricing_sync({"inn_name": inn})
            row = {
                "inn_name":              inn,
                "subsidy_nzd":           getattr(result, "subsidy_nzd", None),
                "manufacturer_price_nzd": getattr(result, "manufacturer_price_nzd", None),
                "hospital_price_nzd":    getattr(result, "hospital_price_nzd", None),
                "funded":                getattr(result, "funded", False),
                "item_code":             getattr(result, "item_code", None),
                "atc_code":              getattr(result, "atc_code", None),
                "section":               getattr(result, "section", None),
                "updated_at":            now,
            }
            row = {k: v for k, v in row.items() if v is not None}
            if row.get("inn_name"):
                sb.table("nz_pharmac_schedule").upsert(row, on_conflict="inn_name,item_code").execute()
                total += 1
        except Exception as exc:
            _log(f"  ! PHARMAC/{inn} 실패: {str(exc)[:150]}")
    _log(f"  → PHARMAC upsert 완료: {total}건")
    return {"ok": True, "upserted": total}


async def run_gets(inns: list[str]) -> dict:
    """GETS 입찰 낙찰 데이터 수집."""
    _log(f"▶ GETS 공공조달 크롤링 ({len(inns)}개 INN)")
    try:
        from utils.nz_gets_crawler import crawl_gets
    except Exception as exc:
        return {"ok": False, "error": f"gets import 실패: {exc}"}

    sb = get_client()
    now = datetime.now(timezone.utc).isoformat()
    total = 0
    for inn in inns:
        try:
            awards = await crawl_gets(inn)
            for aw in awards or []:
                try:
                    row = {
                        "inn_name":         inn,
                        "reference_number": getattr(aw, "reference_number", None),
                        "title":            getattr(aw, "title", None),
                        "agency":           getattr(aw, "agency", None),
                        "supplier":         getattr(aw, "supplier", None),
                        "value_nzd":        getattr(aw, "value_nzd", None),
                        "contract_start":   getattr(aw, "contract_start", None),
                        "contract_end":     getattr(aw, "contract_end", None),
                        "source_url":       getattr(aw, "source_url", None),
                        "crawled_at":       now,
                    }
                    row = {k: v for k, v in row.items() if v is not None}
                    sb.table("nz_gets_tenders").upsert(
                        row, on_conflict="reference_number"
                    ).execute()
                    total += 1
                except Exception as exc:
                    _log(f"  ! GETS insert 실패: {str(exc)[:120]}")
        except Exception as exc:
            _log(f"  ! GETS/{inn} 실패: {str(exc)[:150]}")
    _log(f"  → GETS upsert 완료: {total}건")
    return {"ok": True, "upserted": total}


def run_medsafe(inns: list[str]) -> dict:
    """Medsafe 품목허가 수집."""
    _log(f"▶ Medsafe 품목허가 크롤링 ({len(inns)}개 INN)")
    try:
        from utils.nz_medsafe_crawler import run as medsafe_run
        result = medsafe_run(inns, dry_run=False)
        _log(f"  → Medsafe 결과: {result}")
        return result
    except Exception as exc:
        _log(f"  ! Medsafe 실패: {traceback.format_exc()[:400]}")
        return {"ok": False, "error": str(exc)[:200]}


async def run_all(inns: list[str]) -> dict:
    summary = {}
    summary["retail"]  = await run_retail(inns)
    summary["pharmac"] = run_pharmac(inns)
    summary["gets"]    = await run_gets(inns)
    summary["medsafe"] = run_medsafe(inns)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="NZ 크롤러 통합 실행기")
    parser.add_argument("--mode", choices=["all", "retail", "pharmac", "gets", "medsafe"], default="all")
    parser.add_argument("--inns", nargs="*", default=DEFAULT_INNS,
                        help="대상 INN 목록 (기본: 8개 KUP 제품)")
    args = parser.parse_args()

    _log(f"=== run_all_crawlers 시작 (mode={args.mode}, INN={len(args.inns)}개) ===")
    try:
        if args.mode == "all":
            result = asyncio.run(run_all(args.inns))
        elif args.mode == "retail":
            result = asyncio.run(run_retail(args.inns))
        elif args.mode == "pharmac":
            result = run_pharmac(args.inns)
        elif args.mode == "gets":
            result = asyncio.run(run_gets(args.inns))
        elif args.mode == "medsafe":
            result = run_medsafe(args.inns)
        _log(f"=== 완료 ===\n{result}")
        return 0
    except Exception as exc:
        _log(f"!!! 치명적 오류: {traceback.format_exc()[:800]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
