"""분석 대시보드 서버: SSE 실시간 로그 + 분석/보고서 API."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frontend.dashboard_sites import DASHBOARD_SITES

STATIC = Path(__file__).resolve().parent / "static"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

_state: dict[str, Any] = {
    "events": [],
    "lock": None,
}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _state["lock"] = asyncio.Lock()
    yield


app = FastAPI(title="NZ Analysis Dashboard", version="3.0.0", lifespan=_lifespan)

import os as _os
_cors_origins = _os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _emit(event: dict[str, Any]) -> None:
    payload = {**event, "ts": time.time()}
    lock = _state["lock"]
    if lock is None:
        return
    async with lock:
        _state["events"].append(payload)
        if len(_state["events"]) > 500:
            _state["events"] = _state["events"][-400:]


# ── API 키 런타임 설정 ────────────────────────────────────────────────────────

class ApiKeysBody(BaseModel):
    perplexity_api_key: str = ""
    anthropic_api_key:  str = ""


@app.post("/api/settings/keys")
async def set_api_keys(body: ApiKeysBody) -> JSONResponse:
    """프론트엔드에서 API 키를 런타임에 설정 (프로세스 환경변수 갱신)."""
    import os
    updated: list[str] = []
    if body.perplexity_api_key.strip():
        os.environ["PERPLEXITY_API_KEY"] = body.perplexity_api_key.strip()
        updated.append("PERPLEXITY_API_KEY")
    if body.anthropic_api_key.strip():
        os.environ["ANTHROPIC_API_KEY"] = body.anthropic_api_key.strip()
        updated.append("ANTHROPIC_API_KEY")
    return JSONResponse({"ok": True, "updated": updated})


@app.get("/api/settings/keys/status")
async def get_keys_status() -> JSONResponse:
    """현재 API 키 설정 여부 확인 (값은 노출하지 않음)."""
    import os
    return JSONResponse({
        "perplexity": bool(os.environ.get("PERPLEXITY_API_KEY", "").strip()),
        "anthropic":  bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
    })


# ── 분석 ──────────────────────────────────────────────────────────────────────

_analysis_cache: dict[str, Any] = {"result": None, "running": False}


class AnalyzeBody(BaseModel):
    use_perplexity: bool = True
    force_refresh: bool = False


@app.post("/api/analyze")
async def trigger_analyze(body: AnalyzeBody | None = None) -> JSONResponse:
    """8품목 수출 적합성 분석 실행 (Claude API + Perplexity 보조)."""
    req = body if body is not None else AnalyzeBody()
    if _analysis_cache["running"]:
        raise HTTPException(status_code=409, detail="분석이 이미 실행 중입니다.")
    if _analysis_cache["result"] and not req.force_refresh:
        return JSONResponse({"ok": True, "message": "캐시된 분석 결과 사용. force_refresh=true로 재실행."})

    async def _run() -> None:
        _analysis_cache["running"] = True
        try:
            from analysis.sg_export_analyzer import analyze_all
            from analysis.perplexity_references import fetch_all_references

            results = await analyze_all(use_perplexity=req.use_perplexity)
            pids = [r["product_id"] for r in results]
            refs = await fetch_all_references(pids)
            for r in results:
                r["references"] = refs.get(r["product_id"], [])
            _analysis_cache["result"] = results
        finally:
            _analysis_cache["running"] = False

    asyncio.create_task(_run())
    return JSONResponse({"ok": True, "message": "분석을 백그라운드에서 시작했습니다."})


@app.get("/api/analyze/result")
async def analyze_result() -> JSONResponse:
    if _analysis_cache["running"]:
        return JSONResponse({"status": "running"}, status_code=202)
    if not _analysis_cache["result"]:
        raise HTTPException(status_code=404, detail="분석 결과 없음. POST /api/analyze 먼저 실행")
    return JSONResponse({
        "status": "done",
        "count": len(_analysis_cache["result"]),
        "results": _analysis_cache["result"],
    })


@app.get("/api/analyze/status")
async def analyze_status() -> dict[str, Any]:
    return {
        "running": _analysis_cache["running"],
        "has_result": _analysis_cache["result"] is not None,
        "product_count": len(_analysis_cache["result"]) if _analysis_cache["result"] else 0,
    }


# ── 시장 신호 · 뉴스 (Perplexity) ─────────────────────────────────────────────

_news_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_NEWS_TTL = 1800  # 30분 캐시


def _parse_perplexity_news_items(raw_text: str) -> list[dict[str, str]]:
    """Perplexity 텍스트 응답에서 뉴스 배열(JSON) 파싱."""
    import re

    text = (raw_text or "").strip()
    if not text:
        return []

    candidates: list[str] = [text]
    m = re.search(r"\[\s*\{.*\}\s*\]", text, flags=re.S)
    if m:
        candidates.append(m.group(0))

    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except Exception:
            continue
        if not isinstance(parsed, list):
            continue
        items: list[dict[str, str]] = []
        for row in parsed[:6]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title", "") or "").strip()
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "source": str(row.get("source", "") or "").strip(),
                    "date": str(row.get("date", "") or "").strip(),
                    "link": str(row.get("link", "") or "").strip(),
                }
            )
        if items:
            return items
    return []


@app.get("/api/news")
async def api_news() -> JSONResponse:
    """Perplexity 기반 뉴질랜드 제약 시장 뉴스 (30분 캐시)."""
    import time as _time
    import os
    import httpx

    if _news_cache["data"] and _time.time() - _news_cache["ts"] < _NEWS_TTL:
        return JSONResponse(_news_cache["data"])

    px_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not px_key:
        return JSONResponse({"ok": False, "error": "PERPLEXITY_API_KEY 미설정", "items": []})

    try:
        from datetime import date as _date
        today_str = _date.today().strftime("%Y-%m-%d")
        payload = {
            "model": "sonar-pro",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a New Zealand (NZ) pharmaceutical market news analyst. "
                        "Search the web for RECENT (2024–2025) news specifically about "
                        "New Zealand (NZ/Aotearoa) pharmaceutical and healthcare policy. "
                        "Do NOT include news about Singapore, Australia, or any other country. "
                        "Return ONLY a JSON array with up to 6 items. "
                        "Every 'title' value MUST be written in Korean (한국어). "
                        "Translate any English title into natural Korean."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Today is {today_str}. "
                        "Search for the most recent New Zealand pharmaceutical and healthcare news. "
                        "Focus on: PHARMAC (Pharmaceutical Management Agency) funding decisions, "
                        "Medsafe drug approvals/recalls, Health New Zealand (Te Whatu Ora) procurement, "
                        "GETS pharmaceutical tenders, NZ drug pricing updates, NZ biosimilar policy, "
                        "NZ medicine shortages or supply chain issues. "
                        "ALL news must be specifically about New Zealand — not Singapore, not Australia. "
                        "Return a strict JSON array (no other text). "
                        "Each item: {\"title\": \"Korean title\", \"source\": \"publisher name\", "
                        "\"date\": \"YYYY-MM-DD or approximate\", \"link\": \"URL or empty string\"}. "
                        "Translate ALL titles to Korean. Do not use English titles."
                    ),
                },
            ],
            "max_tokens": 1000,
            "temperature": 0.1,
        }
        headers = {
            "Authorization": f"Bearer {px_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            raw = resp.json()

        content = str(
            raw.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        items = _parse_perplexity_news_items(content)
        if not items:
            return JSONResponse({"ok": False, "error": "Perplexity 응답 파싱 실패", "items": []})

        data = {"ok": True, "items": items}
        _news_cache["data"] = data
        _news_cache["ts"]   = _time.time()
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:120], "items": []})


# ── 거시지표 ──────────────────────────────────────────────────────────────────

@app.get("/api/macro")
async def api_macro() -> JSONResponse:
    from utils.nz_macro import get_nz_macro
    return JSONResponse(get_nz_macro())

_PREVIEW_STATS_STATIC = {
    "gdp":           {"value": "US$ 48,800",  "source": "World Bank"},
    "population":    {"value": "5,127,000명", "source": "Stats NZ"},
    "pharma_market": {"value": "$1.26B",      "source": "Statista"},
    "import_dep":    {"value": "~90%",        "source": "KOTRA / ITA"},
}


@app.get("/api/preview/stats")
async def preview_stats() -> JSONResponse:
    """New Zealand preview stats for the teammate-matched dashboard layout."""
    try:
        from utils.db import get_client
        sb = get_client()
        rows = sb.table("nz_market_macro").select("*").order("id").limit(1).execute().data or []
        if not rows:
            return JSONResponse(_PREVIEW_STATS_STATIC)

        row = rows[0]
        result = {k: dict(v) for k, v in _PREVIEW_STATS_STATIC.items()}
        if row.get("gdp_per_capita_usd"):
            result["gdp"] = {
                "value": f"US$ {row['gdp_per_capita_usd']:,}",
                "source": row.get("gdp_source") or "World Bank",
            }
        if row.get("population"):
            result["population"] = {
                "value": str(row["population"]),
                "source": row.get("population_source") or "Stats NZ",
            }
        if row.get("pharma_market_usd"):
            result["pharma_market"] = {
                "value": str(row["pharma_market_usd"]),
                "source": row.get("pharma_source") or "Statista",
            }
        if row.get("import_dependency"):
            result["import_dep"] = {
                "value": str(row["import_dependency"]),
                "source": row.get("import_source") or "KOTRA / ITA",
            }
        return JSONResponse(result)
    except Exception:
        return JSONResponse(_PREVIEW_STATS_STATIC)


# ── 환율 (yfinance SGD/KRW) ───────────────────────────────────────────────────

_exchange_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_EXCHANGE_TTL_SEC = 0.0


@app.get("/api/exchange")
async def api_exchange() -> JSONResponse:
    """NZD/KRW 실시간 환율 (yfinance). 짧은 캐시로 준실시간 제공."""
    import time as _time

    if _exchange_cache["data"] and _time.time() - _exchange_cache["ts"] < _EXCHANGE_TTL_SEC:
        return JSONResponse(_exchange_cache["data"])

    def _fetch() -> dict[str, Any]:
        import yfinance as yf  # type: ignore[import]
        nzd_krw = float(yf.Ticker("NZDKRW=X").fast_info.last_price)
        usd_krw = float(yf.Ticker("USDKRW=X").fast_info.last_price)
        nzd_usd = float(yf.Ticker("NZDUSD=X").fast_info.last_price)
        nzd_aud = float(yf.Ticker("NZDAUD=X").fast_info.last_price)
        nzd_jpy = float(yf.Ticker("NZDJPY=X").fast_info.last_price)
        return {
            "nzd_krw": round(nzd_krw, 2),
            "usd_krw": round(usd_krw, 2),
            "nzd_usd": round(nzd_usd, 4),
            "nzd_aud": round(nzd_aud, 4),
            "nzd_jpy": round(nzd_jpy, 4),
            # 하위 호환 alias (UI가 sgd_krw를 참조하는 경우 대비)
            "sgd_krw": round(nzd_krw, 2),
            "sgd_usd": round(nzd_usd, 4),
            "sgd_jpy": round(nzd_jpy, 4),
            "sgd_cny": round(nzd_aud, 4),
            "source": "Yahoo Finance",
            "fetched_at": _time.time(),
            "ok": True,
        }

    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _fetch)
        _exchange_cache["data"] = data
        _exchange_cache["ts"]   = _time.time()
        return JSONResponse(data)
    except Exception as exc:
        fallback: dict[str, Any] = {
            "nzd_krw": 830.0,
            "usd_krw": 1393.0,
            "nzd_usd": 0.5950,
            "nzd_aud": 0.9150,
            "nzd_jpy": 87.5,
            "sgd_krw": 830.0,    # compat alias
            "sgd_usd": 0.5950,   # compat alias
            "sgd_jpy": 87.5,
            "sgd_cny": 0.9150,
            "source": "폴백 (Yahoo Finance 연결 실패)",
            "fetched_at": _time.time(),
            "ok": False,
            "error": str(exc),
        }
        return JSONResponse(fallback)


# ── 단일 품목 파이프라인 (분석 + 논문 + PDF) ──────────────────────────────────

_pipeline_tasks: dict[str, dict[str, Any]] = {}


async def _run_pipeline_for_product(product_key: str) -> None:
    task = _pipeline_tasks[product_key]
    try:
        # 0. DB 조회 (Supabase)
        task.update({"step": "db_load", "step_label": "Supabase 데이터 로드 중…"})
        await _emit({"phase": "pipeline", "message": f"{product_key} — DB 조회 중", "level": "info"})

        from utils.db import fetch_kup_products
        kup_rows = await asyncio.to_thread(fetch_kup_products, "NZ")
        db_row = next((r for r in kup_rows if r.get("product_id") == product_key), None)

        if db_row is None:
            await _emit({"phase": "pipeline", "message": f"DB에서 품목 미발견: {product_key}", "level": "warn"})

        # 1. Claude 분석
        task.update({"step": "analyze", "step_label": "Claude 분석 중…"})
        await _emit({"phase": "pipeline", "message": f"{product_key} — 분석 시작", "level": "info"})

        from analysis.sg_export_analyzer import analyze_product
        result = await analyze_product(product_key, db_row)
        task["result"] = result
        verdict = result.get("verdict") or "미분석"
        await _emit({"phase": "pipeline", "message": f"분석 완료 — {verdict}", "level": "success"})

        # 2. Perplexity 논문
        task.update({"step": "refs", "step_label": "논문 검색 중…"})
        from analysis.perplexity_references import fetch_references
        refs = await fetch_references(product_key)
        task["refs"] = refs
        if refs:
            await _emit({"phase": "pipeline", "message": f"논문 {len(refs)}건 검색 완료", "level": "success"})

        # 3. DOCX 보고서 생성
        task.update({"step": "report", "step_label": "보고서 생성 중…"})
        await _emit({"phase": "pipeline", "message": "DOCX 보고서 생성 중…", "level": "info"})

        from datetime import datetime, timezone as _tz
        from report_generator_docx import render_p1_docx

        _ts = datetime.now(_tz.utc).strftime("%Y%m%d_%H%M%S")
        _reports_dir = ROOT / "reports"
        _reports_dir.mkdir(parents=True, exist_ok=True)

        # P1 데이터 빌드
        _trade  = db_row.get("trade_name", product_key) if db_row else product_key
        _inn    = db_row.get("inn_name", "") if db_row else ""
        _hs     = db_row.get("hs_code", "3004.90") if db_row else "3004.90"
        _p1_data = {
            "trade_name":  _trade,
            "inn_name":    _inn,
            "hs_code":     _hs,
            "country_ko":  "뉴질랜드",
            "report_date": datetime.now(_tz.utc).strftime("%Y-%m-%d"),
            "macro_rows":  [],
            "macro_summary": result.get("basis_market_medical", ""),
            "regulatory": {
                "registration": result.get("basis_regulatory", "—"),
                "channel":      result.get("regulatory_id", "—"),
                "tariff":       result.get("basis_trade", "관세 0%, GST 15%"),
            },
            "ref_prices":        [{"label": "PHARMAC 참고가", "value": result.get("price_positioning_pbs", "—")}],
            "ref_price_summary": "",
            "risks": {
                "review_period": result.get("risks_conditions", "—"),
                "competition":   result.get("key_risk", "—"),
                "formulary":     "PHARMAC 급여 등재 심사 필요",
            },
            "refs":       refs,
            "db_sources": [
                "Medsafe Therapeutic Products Register",
                "PHARMAC Schedule (schedule.pharmac.govt.nz)",
                "GETS 조달 공고 (gets.govt.nz)",
                "Perplexity 실시간 규제·시장 정보 (2024~2026)",
            ],
        }

        _docx_name = f"nz_p1_{product_key}_{_ts}.docx"
        _docx_path = _reports_dir / _docx_name
        await asyncio.to_thread(render_p1_docx, _p1_data, _docx_path)
        task["p1_data"] = _p1_data   # Final 보고서용 저장

        task["pdf"] = _docx_name
        task.update({"status": "done", "step": "done", "step_label": "완료"})
        await _emit({"phase": "pipeline", "message": "파이프라인 완료", "level": "success"})

    except Exception as exc:
        task.update({"status": "error", "step": "error", "step_label": str(exc)})
        await _emit({"phase": "pipeline", "message": f"오류: {exc}", "level": "error"})


# ── 신약(커스텀) 파이프라인 ────────────────────────────────────────────────────
# 주의: 리터럴 경로("/api/pipeline/custom/...")는 반드시 {product_key} 라우트보다 먼저 선언

_custom_task: dict[str, Any] = {}


class CustomDrugBody(BaseModel):
    trade_name: str
    inn: str
    dosage_form: str = ""


async def _run_custom_pipeline(trade_name: str, inn: str, dosage_form: str) -> None:
    global _custom_task
    try:
        # Step 1: Claude 분석
        _custom_task.update({"step": "analyze", "step_label": "Claude 분석 중…"})
        from analysis.sg_export_analyzer import analyze_custom_product
        result = await analyze_custom_product(trade_name, inn, dosage_form)
        _custom_task["result"] = result

        # Step 2: Perplexity 논문
        _custom_task.update({"step": "refs", "step_label": "논문 검색 중…"})
        from analysis.perplexity_references import fetch_references_for_custom
        refs = await fetch_references_for_custom(trade_name, inn)
        _custom_task["refs"] = refs

        # Step 3: DOCX 보고서
        _custom_task.update({"step": "report", "step_label": "보고서 생성 중…"})
        from datetime import datetime, timezone as _tz2
        from report_generator_docx import render_p1_docx as _render_p1_custom

        _ts2 = datetime.now(_tz2.utc).strftime("%Y%m%d_%H%M%S")
        _reports_dir2 = ROOT / "reports"
        _reports_dir2.mkdir(parents=True, exist_ok=True)

        _p1_custom = {
            "trade_name":  trade_name,
            "inn_name":    inn,
            "hs_code":     "3004.90",
            "country_ko":  "뉴질랜드",
            "report_date": datetime.now(_tz2.utc).strftime("%Y-%m-%d"),
            "macro_summary": result.get("basis_market_medical", ""),
            "regulatory": {
                "registration": result.get("basis_regulatory", "—"),
                "channel":      result.get("regulatory_id", "—"),
                "tariff":       result.get("basis_trade", "관세 0%, GST 15%"),
            },
            "ref_prices":  [{"label": "PHARMAC 참고가", "value": result.get("price_positioning_pbs", "—")}],
            "risks": {
                "review_period": result.get("risks_conditions", "—"),
                "competition":   result.get("key_risk", "—"),
                "formulary":     "PHARMAC 급여 등재 심사 필요",
            },
            "refs":       refs,
            "db_sources": ["Medsafe", "PHARMAC Schedule", "GETS", "Perplexity (2024~2026)"],
        }
        _pdf_name2 = f"nz_report_custom_{_ts2}.docx"
        _pdf_path2 = _reports_dir2 / _pdf_name2
        await asyncio.to_thread(_render_p1_custom, _p1_custom, _pdf_path2)

        _custom_task["pdf"] = _pdf_name2
        _custom_task.update({"status": "done", "step": "done", "step_label": "완료"})

    except Exception as exc:
        _custom_task.update({"status": "error", "step": "error", "step_label": str(exc)})


@app.post("/api/pipeline/custom")
async def trigger_custom_pipeline(body: CustomDrugBody) -> JSONResponse:
    global _custom_task
    if _custom_task.get("status") == "running":
        raise HTTPException(status_code=409, detail="신약 분석이 이미 실행 중입니다.")
    _custom_task = {
        "status": "running", "step": "analyze", "step_label": "시작 중…",
        "result": None, "refs": [], "pdf": None,
    }
    asyncio.create_task(_run_custom_pipeline(body.trade_name, body.inn, body.dosage_form))
    return JSONResponse({"ok": True})


@app.get("/api/pipeline/custom/status")
async def custom_pipeline_status() -> JSONResponse:
    if not _custom_task:
        return JSONResponse({"status": "idle"})
    return JSONResponse({
        "status":     _custom_task.get("status", "idle"),
        "step":       _custom_task.get("step", ""),
        "step_label": _custom_task.get("step_label", ""),
        "has_result": _custom_task.get("result") is not None,
        "has_pdf":    bool(_custom_task.get("pdf")),
    })


@app.get("/api/pipeline/custom/result")
async def custom_pipeline_result() -> JSONResponse:
    if not _custom_task:
        raise HTTPException(404, "신약 분석 미실행")
    return JSONResponse({
        "status": _custom_task.get("status"),
        "result": _custom_task.get("result"),
        "refs":   _custom_task.get("refs", []),
        "pdf":    _custom_task.get("pdf"),
    })


# ── 기존 품목 파이프라인 ──────────────────────────────────────────────────────

@app.post("/api/pipeline/{product_key}")
async def trigger_pipeline(product_key: str) -> JSONResponse:
    if _pipeline_tasks.get(product_key, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="이미 실행 중입니다.")
    from datetime import datetime, timezone as _tz_rid
    import uuid as _uuid
    _run_id = f"run_{datetime.now(_tz_rid.utc).strftime('%Y%m%d_%H%M%S')}_{_uuid.uuid4().hex[:6]}"
    _pipeline_tasks[product_key] = {
        "status": "running", "step": "init", "step_label": "시작 중…",
        "result": None, "refs": [], "pdf": None, "run_id": _run_id,
        "p1_data": None,
    }
    asyncio.create_task(_run_pipeline_for_product(product_key))
    return JSONResponse({"ok": True, "message": "파이프라인 시작됨", "run_id": _run_id})


@app.get("/api/pipeline/{product_key}/status")
async def pipeline_status(product_key: str) -> JSONResponse:
    task = _pipeline_tasks.get(product_key)
    if not task:
        return JSONResponse({"status": "idle"})
    return JSONResponse({
        "status":     task["status"],
        "step":       task["step"],
        "step_label": task["step_label"],
        "has_result": task["result"] is not None,
        "has_pdf":    bool(task["pdf"]),
        "ref_count":  len(task.get("refs", [])),
    })


@app.get("/api/pipeline/{product_key}/result")
async def pipeline_result(product_key: str) -> JSONResponse:
    task = _pipeline_tasks.get(product_key)
    if not task:
        raise HTTPException(404, "파이프라인 미실행")
    return JSONResponse({
        "status": task["status"],
        "step":   task["step"],
        "result": task.get("result"),
        "refs":   task.get("refs", []),
        "pdf":    task.get("pdf"),
    })


# ── 보고서 ────────────────────────────────────────────────────────────────────

_report_cache: dict[str, Any] = {"path": None, "running": False}

def _latest_report_pdf() -> Path | None:
    reports_dir = ROOT / "reports"
    if not reports_dir.exists():
        return None
    pdfs = [p for p in reports_dir.glob("nz_report_*.pdf") if p.is_file()]
    if not pdfs:
        pdfs = [p for p in reports_dir.glob("*.pdf") if p.is_file()]
    if not pdfs:
        return None
    return max(pdfs, key=lambda p: p.stat().st_mtime)


def _latest_report_docx() -> Path | None:
    reports_dir = ROOT / "reports"
    if not reports_dir.exists():
        return None
    docs = [p for p in reports_dir.glob("nz_final_*.docx") if p.is_file()]
    if not docs:
        docs = [p for p in reports_dir.glob("*.docx") if p.is_file()]
    if not docs:
        return None
    return max(docs, key=lambda p: p.stat().st_mtime)


class ReportBody(BaseModel):
    run_analysis: bool = False
    use_perplexity: bool = False


@app.post("/api/report")
async def trigger_report(body: ReportBody | None = None) -> JSONResponse:
    req = body if body is not None else ReportBody()
    if _report_cache["running"]:
        raise HTTPException(status_code=409, detail="보고서 생성이 이미 실행 중입니다.")

    async def _run_report() -> None:
        _report_cache["running"] = True
        try:
            import subprocess
            cmd = [
                sys.executable, str(ROOT / "report_generator.py"),
                "--out", str(ROOT / "reports"),
            ]
            if req.run_analysis:
                cmd.append("--run-analysis")
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: subprocess.run(cmd, capture_output=True, text=True)
            )
            reports_dir = ROOT / "reports"
            docs = sorted(reports_dir.glob("nz_final_*.docx"), key=lambda p: p.stat().st_mtime, reverse=True)
            _report_cache["path"] = str(docs[0]) if docs else None
        finally:
            _report_cache["running"] = False

    asyncio.create_task(_run_report())
    return JSONResponse({"ok": True, "message": "보고서 생성을 백그라운드에서 시작했습니다."})


@app.get("/api/report/status")
async def report_status() -> dict[str, Any]:
    reports_dir = ROOT / "reports"
    docs = [p for p in reports_dir.glob("nz_*.docx")] if reports_dir.exists() else []
    latest = _latest_report_docx() or _latest_report_pdf()
    return {
        "running":    _report_cache["running"],
        "latest_pdf": str(latest) if latest else _report_cache["path"],
        "pdf_count":  len(docs),
    }


@app.get("/api/report/download")
async def download_report(name: str | None = None, inline: bool = False) -> Any:
    """DOCX/PDF 반환. name 파라미터로 특정 파일 지정."""
    reports_dir = ROOT / "reports"
    disp = "inline" if inline else "attachment"
    if name:
        target = reports_dir / Path(name).name
        if target.is_file():
            ext = target.suffix.lower()
            mt = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if ext == ".docx" else "application/pdf"
            return FileResponse(str(target), media_type=mt, filename=target.name,
                                content_disposition_type=disp)

    # 최신 파일 (docx 우선)
    latest = _latest_report_docx() or _latest_report_pdf()
    if not latest:
        raise HTTPException(status_code=404, detail="생성된 보고서 없음")
    ext = latest.suffix.lower()
    mt = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if ext == ".docx" else "application/pdf"
    return FileResponse(str(latest), media_type=mt, filename=latest.name,
                        content_disposition_type=disp)


# ── 2공정 가격 전략 PDF ───────────────────────────────────────────────────────

class P2ReportBody(BaseModel):
    product_name:  str        = ""
    verdict:       str        = ""
    seg_label:     str        = ""
    base_price:    float | None = None
    formula_str:   str        = ""
    mode_label:    str        = ""
    scenarios:     list[Any]  = []
    ai_rationale:  list[Any]  = []


@app.post("/api/p2/report")
async def generate_p2_report(body: P2ReportBody) -> JSONResponse:
    """2공정 수출 가격 전략 DOCX 생성."""
    import re
    from datetime import datetime, timezone as _tz_p2
    from report_generator_docx import render_p2_docx_report

    _ts = datetime.now(_tz_p2.utc).strftime("%Y%m%d_%H%M%S")
    _reports_dir = ROOT / "reports"
    _reports_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^\w가-힣]", "_", body.product_name)[:30] or "product"
    docx_name = f"nz_p2_{safe_name}_{_ts}.docx"
    docx_path = _reports_dir / docx_name

    # 기존 scenarios 필드 → docx 포맷 정규화
    def _norm(sc_list: list) -> list:
        out = []
        for sc in (sc_list or []):
            price_raw = sc.get("price_sgd") or sc.get("price") or sc.get("price_nzd") or 0
            out.append({
                "label":       sc.get("name", sc.get("label", "")),
                "price_nzd":   float(price_raw) if price_raw else 0,
                "reason":      sc.get("reason", ""),
                "fob_formula": sc.get("formula", sc.get("fob_formula", "")),
            })
        return out

    p2_data = {
        "trade_name":        body.product_name,
        "inn_name":          "",
        "country_ko":        "뉴질랜드",
        "report_date":       datetime.now(_tz_p2.utc).strftime("%Y-%m-%d"),
        "macro_text":        "",
        "base_price_nzd":    float(body.base_price or 0),
        "price_method":      body.mode_label or "AI 분석 (Claude Haiku)",
        "market_type":       body.seg_label or "공공 / 민간",
        "competitors":       [],
        "scenarios_public":  _norm(body.scenarios),
        "scenarios_private": [],
        "fx_snapshot":       {},
    }

    await asyncio.to_thread(render_p2_docx_report, p2_data, docx_path)
    return JSONResponse({"ok": True, "pdf": docx_name})


# ── 2공정 AI 파이프라인 (PDF → Haiku 가격 추출 → 계산 → Haiku 분석 → PDF) ────────

_p2_ai_task: dict[str, Any] = {}


async def _run_p2_ai_pipeline(report_path: str, market: str) -> None:
    global _p2_ai_task
    try:
        import json
        import os
        import re

        import anthropic

        api_key = (
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY", "")
        ).strip()
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY 미설정 — 환경변수를 확인하세요.")

        # ── Step 1: 보고서 텍스트 추출 (DOCX 우선, PDF 폴백) ─────────────────
        _p2_ai_task.update({"step": "extract", "step_label": "보고서 텍스트 추출 중…"})
        await _emit({"phase": "p2_pipeline", "message": "보고서 텍스트 추출 시작", "level": "info"})

        pdf_text = ""
        try:
            if report_path.endswith(".docx"):
                from docx import Document as _DocxDoc
                _doc = _DocxDoc(report_path)
                pdf_text = "\n".join(p.text for p in _doc.paragraphs if p.text.strip())
            else:
                from pypdf import PdfReader  # type: ignore[import]
                reader = PdfReader(report_path)
                for page in reader.pages:
                    pdf_text += (page.extract_text() or "") + "\n"
        except Exception as exc_pdf:
            await _emit({"phase": "p2_pipeline", "message": f"텍스트 추출 경고: {exc_pdf}", "level": "warn"})

        if not pdf_text.strip():
            raise ValueError("보고서에서 텍스트를 추출할 수 없습니다. 파일이 손상됐거나 빈 문서일 수 있습니다.")

        await _emit({"phase": "p2_pipeline", "message": f"텍스트 {len(pdf_text)}자 추출 완료", "level": "success"})

        # ── Step 2: Claude Haiku — 가격 정보 추출 ──────────────────────────────
        _p2_ai_task.update({"step": "ai_extract", "step_label": "AI 가격 정보 추출 중…"})
        await _emit({"phase": "p2_pipeline", "message": "Claude Haiku — 가격 정보 추출", "level": "info"})

        client = anthropic.Anthropic(api_key=api_key)

        extract_prompt = f"""다음 의약품 수출 분석 보고서에서 가격 관련 정보를 추출하세요.

보고서 내용:
{pdf_text[:7000]}

아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{
  "product_name": "제품명 (없으면 '미상')",
  "ref_price_sgd": 숫자 또는 null,
  "ref_price_currency": "NZD 또는 USD",
  "ref_price_text": "원문 가격 텍스트 (없으면 빈 문자열)",
  "competitor_prices": [{{"name": "경쟁사명", "price_sgd": 숫자}}],
  "market_context": "시장 맥락 요약 (1-2문장)",
  "hs_code": "HS 코드 (없으면 빈 문자열)",
  "verdict": "수출 적합성 판정 (적합/조건부/부적합/미상)"
}}

가격 추출 규칙 (반드시 준수):
- 'PHARMAC 보조금가 NZD X.XX', 'NZD X.XX 수준', '참고 NZD X.XX' 등 NZD 금액이 포함된 모든 표현에서 숫자를 추출하세요.
- 'PHARMAC 방법론적 추산', '수출가 아님' 같은 면책 문구가 있어도 NZD 숫자는 ref_price_sgd에 넣으세요.
- 보고서의 '참고 가격', '가격 포지셔닝', 'PHARMAC 보조금가' 섹션을 특히 확인하세요.
- USD($) 금액만 있다면 ref_price_sgd는 null로, ref_price_currency는 'USD'로, ref_price_text에 원문 그대로 기록하세요."""

        extract_resp = await asyncio.to_thread(
            lambda: client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": extract_prompt}],
            )
        )

        extracted: dict[str, Any] = {}
        try:
            raw_extract = extract_resp.content[0].text
            m_json = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw_extract, re.S)
            if m_json:
                extracted = json.loads(m_json.group(0))
        except Exception:
            extracted = {
                "product_name": "미상",
                "ref_price_sgd": None,
                "ref_price_text": "",
                "market_context": "",
                "verdict": "미상",
            }

        _p2_ai_task["extracted"] = extracted
        await _emit({
            "phase": "p2_pipeline",
            "message": f"가격 추출 완료 — 참조가: NZD {extracted.get('ref_price_sgd', '미확인')}",
            "level": "success",
        })

        # ── Step 3: 실시간 환율 (yfinance) ────────────────────────────────────
        _p2_ai_task.update({"step": "exchange", "step_label": "실시간 환율 조회 중…"})
        await _emit({"phase": "p2_pipeline", "message": "yfinance 환율 조회", "level": "info"})

        exchange_rates: dict[str, Any] = {
            "nzd_krw": 830.0, "usd_krw": 1393.0,
            "nzd_usd": 0.5950, "source": "폴백값 (Yahoo Finance 연결 실패)",
            "sgd_krw": 830.0, "sgd_usd": 0.5950,   # compat alias
        }
        try:
            import yfinance as yf  # type: ignore[import]

            def _fetch_rates() -> dict[str, Any]:
                nzd_krw = round(float(yf.Ticker("NZDKRW=X").fast_info.last_price), 2)
                nzd_usd = round(float(yf.Ticker("NZDUSD=X").fast_info.last_price), 4)
                return {
                    "nzd_krw": nzd_krw,
                    "usd_krw": round(float(yf.Ticker("USDKRW=X").fast_info.last_price), 2),
                    "nzd_usd": nzd_usd,
                    "sgd_krw": nzd_krw,   # compat alias
                    "sgd_usd": nzd_usd,   # compat alias
                    "source": "Yahoo Finance (실시간)",
                }

            exchange_rates = await asyncio.to_thread(_fetch_rates)
        except Exception as exc_fx:
            await _emit({"phase": "p2_pipeline", "message": f"환율 폴백: {exc_fx}", "level": "warn"})

        _p2_ai_task["exchange_rates"] = exchange_rates
        await _emit({
            "phase": "p2_pipeline",
            "message": f"환율 — 1 NZD = {exchange_rates['nzd_krw']} KRW",
            "level": "success",
        })

        # ── Step 4: Claude Haiku — 최종 가격 전략 분석 ──────────────────────────
        _p2_ai_task.update({"step": "ai_analysis", "step_label": "AI 최종 분석 중…"})
        await _emit({"phase": "p2_pipeline", "message": "Claude Haiku — 최종 가격 전략 분석", "level": "info"})

        ref_price    = extracted.get("ref_price_sgd") or 0
        ref_display  = f"NZD {float(ref_price):.2f}" if ref_price else (extracted.get("ref_price_text") or "미확인")
        nzd_krw      = exchange_rates.get("nzd_krw", exchange_rates.get("sgd_krw", 830.0))
        market_label = "공공 시장 (PHARMAC/GETS 조달 채널)" if market == "public" else "민간 시장 (약국·체인 채널)"
        verdict_src  = extracted.get("verdict", "미상")
        competitor_json = json.dumps(extracted.get("competitor_prices", []), ensure_ascii=False)

        async def _claude_analyze_market(mkt: str) -> dict[str, Any]:
            """Run Claude Haiku analysis for a single market segment."""
            m_label = "공공 시장 (PHARMAC/GETS 조달 채널)" if mkt == "public" else "민간 시장 (약국·체인 채널)"
            if mkt == "public":
                options_hint = (
                    "공공 시장이라면 PHARMAC 급여 할인율(%), GETS 조달 수수료(%), Medsafe 허가비 분담(NZD), "
                    "해상 운임 비율(%) 등 공공 채널 특유의 역산 요소를 포함하세요."
                )
            else:
                options_hint = (
                    "민간 시장이라면 약국 마진율(%), 병원 채널 마진율(%), NZ GST 15% 공제(%), "
                    "해상 운임 비율(%), 도매상 마진(%) 등 민간 채널 특유의 역산 요소를 포함하세요."
                )

            prompt = f"""뉴질랜드 수출 가격 전략을 수립해주세요.

## 추출된 보고서 정보
- 제품명: {extracted.get('product_name', '미상')}
- 수출 적합성 판정: {verdict_src}
- 참조가: {ref_display}
- 참조가 원문: {extracted.get('ref_price_text', '없음')}
- HS 코드: {extracted.get('hs_code', '미상')}
- 시장: {m_label}
- 현재 환율: 1 NZD = {nzd_krw:.2f} KRW (실시간 Yahoo Finance)
- 경쟁사 가격: {competitor_json}
- 시장 맥락: {extracted.get('market_context', '정보 없음')}

## 요청
1. 뉴질랜드 제약 시장의 특성, 판정 결과, 시장 구분을 종합해 최종 수출 권고가를 산정하세요.
   (PHARMAC 급여가·GETS 조달가·현지 소매가를 참조 벤치마크로 사용하세요)
2. 시나리오는 저가 진입·기준·프리미엄 3개로 구분하세요. 각 시나리오마다:
   - 가격 근거·포지셔닝 전략·적합 상황을 포함한 한 문단(3-4문장)으로 reason을 작성하세요.
   - 구체적인 계산식을 formula 필드에 작성하세요 (예: NZD 9.87 × 0.85 = NZD 8.39).
   - 해당 시나리오에 맞는 NZ 역산 옵션을 options 배열로 제시하세요. {options_hint}
3. rationale은 3-4문장으로 시장 근거·판정 근거·리스크를 포함해 서술하세요.

아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{
  "final_price_sgd": 숫자,
  "rationale": "산정 이유 3-4문장",
  "scenarios": [
    {{"name": "저가 진입", "price_sgd": 숫자, "reason": "저마진 포지셔닝 정의·근거·적합 상황", "formula": "계산식", "options": [{{"name": "옵션명", "type": "pct_deduct", "value": 숫자, "rationale": "이유"}}]}},
    {{"name": "기준", "price_sgd": 숫자, "reason": "중간 포지셔닝 정의·근거·적합 상황", "formula": "계산식", "options": [...]}},
    {{"name": "프리미엄", "price_sgd": 숫자, "reason": "고마진 포지셔닝 정의·근거·적합 상황", "formula": "계산식", "options": [...]}}
  ]
}}

참조가가 미확인이라면 PHARMAC 스케줄·GETS 조달가·현지 약국 실세가를 기반으로 합리적인 가격을 추정하세요."""

            resp = await asyncio.to_thread(
                lambda: client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=2500,
                    messages=[{"role": "user", "content": prompt}],
                )
            )

            result: dict[str, Any] = {}
            try:
                raw = resp.content[0].text
                m_json2 = re.search(r"\{.*\}", raw, re.S)
                if m_json2:
                    result = json.loads(m_json2.group(0))
            except Exception:
                pass

            if not result:
                final_est = (ref_price * 0.30) if ref_price else 0
                pub_opts = [
                    {"name": "PHARMAC 할인", "type": "pct_deduct", "value": 20, "rationale": "PHARMAC 급여 협상 할인율"},
                    {"name": "GETS 수수료", "type": "pct_deduct", "value": 5, "rationale": "정부 조달 플랫폼 수수료"},
                    {"name": "해상 운임", "type": "pct_deduct", "value": 8, "rationale": "한국→NZ 해상 운임"},
                ]
                pri_opts = [
                    {"name": "약국 마진", "type": "pct_deduct", "value": 30, "rationale": "NZ 약국 채널 마진"},
                    {"name": "NZ GST 15%", "type": "pct_deduct", "value": 13, "rationale": "GST 15% 역산 공제"},
                    {"name": "해상 운임", "type": "pct_deduct", "value": 8, "rationale": "한국→NZ 해상 운임"},
                ]
                default_opts = pub_opts if mkt == "public" else pri_opts
                result = {
                    "final_price_sgd": round(final_est, 2),
                    "rationale": "AI 응답 파싱 중 오류가 발생했습니다. 기본값 30% 비율로 산정합니다.",
                    "scenarios": [
                        {"name": "저가 진입", "price_sgd": round(final_est * 0.88, 2),
                         "reason": "저마진 포지셔닝 — 시장 진입 초기 가격경쟁력으로 점유율을 선점합니다.",
                         "formula": f"NZD {final_est:.2f} × 0.88 = NZD {round(final_est * 0.88, 2):.2f}",
                         "options": default_opts},
                        {"name": "기준", "price_sgd": round(final_est, 2),
                         "reason": "중간 포지셔닝 — 리스크와 마진의 균형을 유지하는 기본 산정가입니다.",
                         "formula": f"NZD {final_est:.2f} (기준가 그대로)",
                         "options": default_opts},
                        {"name": "프리미엄", "price_sgd": round(final_est * 1.12, 2),
                         "reason": "고마진 포지셔닝 — 브랜드 가치와 차별화 요소로 마진을 극대화합니다.",
                         "formula": f"NZD {final_est:.2f} × 1.12 = NZD {round(final_est * 1.12, 2):.2f}",
                         "options": default_opts},
                    ],
                }
            return result

        # ── 공공·민간 양쪽 분석 동시 실행 ──────────────────────────────────────
        await _emit({"phase": "p2_pipeline", "message": "Claude Haiku — 공공·민간 양쪽 시장 분석 시작", "level": "info"})
        pub_analysis, pri_analysis = await asyncio.gather(
            _claude_analyze_market("public"),
            _claude_analyze_market("private"),
        )
        await _emit({"phase": "p2_pipeline", "message": f"양쪽 분석 완료 — 공공 NZD {pub_analysis.get('final_price_sgd', 0):.2f} / 민간 NZD {pri_analysis.get('final_price_sgd', 0):.2f}", "level": "success"})

        analysis = pub_analysis if market == "public" else pri_analysis
        _p2_ai_task["analysis"] = analysis  # backward compat
        _p2_ai_task["analysis_both"] = {"public": pub_analysis, "private": pri_analysis}
        await _emit({
            "phase": "p2_pipeline",
            "message": f"최종 분석 완료 — NZD {analysis.get('final_price_sgd', 0):.2f}",
            "level": "success",
        })

        # ── Step 5: DOCX 보고서 생성 ─────────────────────────────────────────
        _p2_ai_task.update({"step": "report", "step_label": "보고서 생성 중…"})
        await _emit({"phase": "p2_pipeline", "message": "2공정 DOCX 보고서 생성", "level": "info"})

        from datetime import datetime, timezone as _tz_p2ai
        import re as _re2
        from report_generator_docx import render_p2_docx_report

        _ts_p2 = datetime.now(_tz_p2ai.utc).strftime("%Y%m%d_%H%M%S")
        _reports_dir_p2 = ROOT / "reports"
        _reports_dir_p2.mkdir(parents=True, exist_ok=True)

        _safe = _re2.sub(r"[^\w가-힣]", "_", extracted.get("product_name", "product"))[:30] or "product"
        _pdf_name_p2 = f"nz_p2_{_safe}_{_ts_p2}.docx"
        _pdf_path_p2 = _reports_dir_p2 / _pdf_name_p2

        # 공공/민간 시나리오 정규화
        def _norm_sc(sc_list: list) -> list:
            out = []
            for sc in (sc_list or []):
                price_nzd = sc.get("price_sgd") or sc.get("price") or sc.get("price_nzd") or 0
                out.append({
                    "label":       sc.get("name", sc.get("label", "")),
                    "price_nzd":   float(price_nzd) if price_nzd else 0,
                    "reason":      sc.get("reason", ""),
                    "fob_formula": sc.get("formula", sc.get("fob_formula", "")),
                    "options":     sc.get("options", []),
                })
            return out

        _fx = exchange_rates
        p2_data = {
            "trade_name":        extracted.get("product_name", "미상"),
            "inn_name":          "",
            "country_ko":        "뉴질랜드",
            "report_date":       datetime.now(_tz_p2ai.utc).strftime("%Y-%m-%d"),
            "macro_text":        extracted.get("market_context", ""),
            "base_price_nzd":    float(pub_analysis.get("final_price_sgd", 0) or 0),
            "price_method":      "AI 분석 (Claude Haiku)",
            "market_type":       "공공 / 민간",
            "competitors":       [
                {"name": c.get("name",""), "product":"", "spec":"",
                 "price_text": f"NZD {c.get('price_sgd',0):.2f}"}
                for c in extracted.get("competitor_prices", [])
            ],
            "scenarios_public":  _norm_sc(pub_analysis.get("scenarios", [])),
            "scenarios_private": _norm_sc(pri_analysis.get("scenarios", [])),
            "fx_snapshot":       _fx,
            # AI 판단 근거 저장
            "ai_rationale_pub":  pub_analysis.get("rationale", ""),
            "ai_rationale_pri":  pri_analysis.get("rationale", ""),
        }

        await asyncio.to_thread(render_p2_docx_report, p2_data, _pdf_path_p2)
        _p2_ai_task["p2_data"] = p2_data   # Final 보고서용 저장

        _p2_ai_task["pdf"] = _pdf_name_p2
        _p2_ai_task.update({"status": "done", "step": "done", "step_label": "완료"})
        await _emit({"phase": "p2_pipeline", "message": "P2 파이프라인 완료", "level": "success"})

    except Exception as exc:
        _p2_ai_task.update({"status": "error", "step": "error", "step_label": str(exc)[:300]})
        await _emit({"phase": "p2_pipeline", "message": f"P2 오류: {exc}", "level": "error"})


class UploadBody(BaseModel):
    filename: str
    content_b64: str  # base64 인코딩된 PDF 바이너리


@app.post("/api/p2/upload")
async def upload_p2_pdf(body: UploadBody) -> JSONResponse:
    """P2 파이프라인용 PDF 업로드 (base64 JSON — python-multipart 불필요)."""
    import base64
    import re as _re_up

    fname = body.filename or "upload.pdf"
    if not fname.lower().endswith((".pdf", ".docx")):
        raise HTTPException(400, "PDF(.pdf) 또는 DOCX(.docx) 파일만 업로드 가능합니다.")

    try:
        content = base64.b64decode(body.content_b64)
    except Exception:
        raise HTTPException(400, "base64 디코딩 실패 — 올바른 PDF 파일인지 확인하세요.")

    safe_fname = _re_up.sub(r"[^\w가-힣\-\.]", "_", fname)[:80]
    _reports_dir = ROOT / "reports"
    _reports_dir.mkdir(parents=True, exist_ok=True)
    dest = _reports_dir / f"upload_{safe_fname}"
    dest.write_bytes(content)

    return JSONResponse({"ok": True, "filename": dest.name})


class P2PipelineBody(BaseModel):
    report_filename: str = ""  # reports/ 내 파일명 (비어 있으면 최신 1공정 PDF 사용)
    market: str = "public"     # "public" | "private"


@app.post("/api/p2/pipeline")
async def trigger_p2_pipeline(body: P2PipelineBody) -> JSONResponse:
    """2공정 AI 파이프라인 실행."""
    global _p2_ai_task
    if _p2_ai_task.get("status") == "running":
        raise HTTPException(409, "P2 파이프라인이 이미 실행 중입니다.")

    if body.report_filename:
        report_path = ROOT / "reports" / Path(body.report_filename).name
    else:
        # DOCX(P1) 우선, 없으면 PDF 폴백
        _rdir = ROOT / "reports"
        _p1_docs = sorted(_rdir.glob("nz_p1_*.docx"), key=lambda p: p.stat().st_mtime, reverse=True) if _rdir.exists() else []
        report_path = _p1_docs[0] if _p1_docs else _latest_report_pdf()

    if not report_path or not Path(report_path).is_file():
        raise HTTPException(404, f"보고서 파일을 찾을 수 없습니다: {body.report_filename or '(최신 P1 보고서 없음)'}")

    _p2_ai_task = {
        "status":   "running",
        "step":     "extract",
        "step_label": "시작 중…",
        "extracted": None,
        "exchange_rates": None,
        "analysis": None,
        "analysis_both": None,
        "pdf":      None,
    }
    asyncio.create_task(_run_p2_ai_pipeline(str(report_path), body.market))
    return JSONResponse({"ok": True})


@app.get("/api/p2/pipeline/status")
async def p2_pipeline_status_ai() -> JSONResponse:
    if not _p2_ai_task:
        return JSONResponse({"status": "idle"})
    return JSONResponse({
        "status":     _p2_ai_task.get("status", "idle"),
        "step":       _p2_ai_task.get("step", ""),
        "step_label": _p2_ai_task.get("step_label", ""),
        "has_result": _p2_ai_task.get("analysis") is not None,
        "has_pdf":    bool(_p2_ai_task.get("pdf")),
    })


@app.get("/api/p2/pipeline/result")
async def p2_pipeline_result_ai() -> JSONResponse:
    if not _p2_ai_task:
        raise HTTPException(404, "P2 파이프라인 미실행")
    return JSONResponse({
        "status":         _p2_ai_task.get("status"),
        "extracted":      _p2_ai_task.get("extracted"),
        "exchange_rates": _p2_ai_task.get("exchange_rates"),
        "analysis":       _p2_ai_task.get("analysis"),
        "analysis_both":  _p2_ai_task.get("analysis_both"),
        "pdf":            _p2_ai_task.get("pdf"),
    })


# ── products 조회 ─────────────────────────────────────────────────────────────

@app.get("/api/products")
async def products() -> list[dict[str, Any]]:
    from utils.db import fetch_kup_products
    return fetch_kup_products("NZ")


# ── API 키 상태 (U1) ──────────────────────────────────────────────────────────

@app.get("/api/keys/status")
async def keys_status() -> dict[str, Any]:
    """Claude·Perplexity API 키 설정 여부 반환 (실제 키 값은 노출하지 않음)."""
    import os
    claude_key     = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "")
    return {
        "claude":     bool(claude_key.strip()),
        "perplexity": bool(perplexity_key.strip()),
    }


# ── 데이터 소스 상태 (U5·B1) ──────────────────────────────────────────────────

@app.get("/api/datasource/status")
async def datasource_status() -> JSONResponse:
    """Supabase 연결 상태, KUP 품목 수, HSA 컨텍스트 출처 반환."""
    try:
        from utils.db import get_client, fetch_kup_products
        kup_rows = fetch_kup_products("NZ")
        kup_count = len(kup_rows)

        # PHARMAC·Medsafe 컨텍스트 테이블 점검
        sb = get_client()
        ctx_count = 0
        context_source = "없음"
        try:
            ctx_rows = (
                sb.table("nz_product_context")
                .select("product_id", count="exact")
                .execute()
            )
            ctx_count = ctx_rows.count or 0
            context_source = f"nz_product_context {ctx_count}건" if ctx_count else "products 테이블 폴백"
        except Exception:
            context_source = "조회 실패"

        return JSONResponse({
            "supabase":       "ok",
            "kup_count":      kup_count,
            "context_ok":     ctx_count > 0,
            "context_source": context_source,
            "message":        f"KUP {kup_count}건 로드",
        })
    except Exception as exc:
        return JSONResponse({
            "supabase":       "error",
            "kup_count":      0,
            "context_ok":     False,
            "context_source": "연결 실패",
            "message":        str(exc)[:120],
        })


# ── 상태 / SSE 스트림 ─────────────────────────────────────────────────────────

@app.get("/api/status")
async def status() -> dict[str, Any]:
    lock = _state["lock"]
    assert lock is not None
    async with lock:
        n = len(_state["events"])
    return {"event_count": n}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Render 헬스체크용 경량 엔드포인트."""
    return {"ok": True, "service": "sg-analysis-dashboard"}


@app.get("/api/stream")
async def stream() -> StreamingResponse:
    last = 0

    async def gen() -> Any:
        nonlocal last
        while True:
            await asyncio.sleep(0.12)
            chunk: list[dict[str, Any]] = []
            lock = _state["lock"]
            assert lock is not None
            async with lock:
                while last < len(_state["events"]):
                    chunk.append(_state["events"][last])
                    last += 1
            for ev in chunk:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ── 3공정: 바이어 발굴 파이프라인 ─────────────────────────────────────────────

_buyer_task: dict[str, Any] = {}

_PROD_LABELS: dict[str, str] = {
    "NZ_cilostazol_cr_200":       "Cilostazol CR (Cilostazol 200mg SR)",
    "NZ_ciloduo_cilosta_rosuva":  "Ciloduo (Cilostazol+Rosuvastatin)",
    "NZ_rosumeg_combigel":        "Rosumeg Combigel (Rosuvastatin+Omega-3)",
    "NZ_atmeg_combigel":          "Atmeg Combigel (Atorvastatin+Omega-3)",
    "NZ_gastiin_cr_mosapride":    "Gastiin CR (Mosapride citrate 15mg)",
    "NZ_omethyl_omega3_2g":       "Omethyl Cutielet (Omega-3 ethyl esters 2g)",
    "NZ_sereterol_activair":      "Sereterol Activair (Fluticasone+Salmeterol)",
    "NZ_hydrine_hydroxyurea_500": "Hydrine (Hydroxyurea 500mg)",
    "NZ_gadvoa_gadobutrol_604":   "Gadvoa Inj. (Gadobutrol 604.72mg)",
}


class BuyerRunBody(BaseModel):
    product_key:     str = "NZ_sereterol_activair"
    active_criteria: list[str] | None = None
    target_country:  str = "New Zealand"
    target_region:   str = "Oceania"


async def _run_buyer_pipeline(
    product_key: str,
    active_criteria: list[str] | None = None,
    target_country: str = "New Zealand",
    target_region: str = "Oceania",
) -> None:
    global _buyer_task

    async def _log(msg: str, level: str = "info") -> None:
        await _emit({"phase": "buyer", "message": msg, "level": level})

    try:
        product_label = _PROD_LABELS.get(product_key, product_key)

        # ── Step 1: 1차 수집 (CPHI 크롤링 — 후보 최대 20개) ─────────────
        _buyer_task.update({"step": "crawl", "step_label": "CPHI 크롤링 중…"})
        await _log(f"바이어 발굴 시작 — 품목: {product_label} / 타깃: {target_country} ({target_region})")

        from utils.cphi_crawler import crawl as cphi_crawl
        companies = await cphi_crawl(
            product_key=product_key,
            candidate_pool=20,
            emit=_log,
        )
        _buyer_task["crawl_count"] = len(companies)
        await _log(f"1차 수집 완료 — {len(companies)}개 후보", "success")

        # ── Step 2: 심층조사 (CPHI 전체 텍스트 → Claude Haiku) ───────────
        _buyer_task.update({"step": "enrich", "step_label": "심층조사 중…"})
        await _log("심층조사 시작 (CPHI 페이지 텍스트 → Claude Haiku 파싱)")

        from utils.buyer_enricher import enrich_all
        enriched = await enrich_all(
            companies,
            product_label=product_label,
            target_country=target_country,
            target_region=target_region,
            emit=_log,
        )
        # 전체 후보 풀 저장 — 기준 변경 시 재선택에 사용
        _buyer_task["all_candidates"] = enriched
        await _log(f"심층조사 완료 — {len(enriched)}개", "success")

        # ── Step 3: 상위 10개 선택 ────────────────────────────────────────
        _buyer_task.update({"step": "rank", "step_label": "Top 10 선정 중…"})
        await _log("평가 기준 적용 → Top 10 선정")

        from analysis.buyer_scorer import rank_companies
        ranked = rank_companies(enriched, active_criteria=active_criteria, top_n=10)
        _buyer_task["buyers"] = ranked
        await _log(f"Top {len(ranked)}개 바이어 선정 완료", "success")

        # ── Step 4: DOCX 보고서 생성 ─────────────────────────────────────
        _buyer_task.update({"step": "report", "step_label": "보고서 생성 중…"})
        await _log("바이어 보고서 DOCX 생성 중…")

        from datetime import datetime, timezone as _tz_b
        from report_generator_docx import render_p3_docx_report
        import re as _re_b

        _ts = datetime.now(_tz_b.utc).strftime("%Y%m%d_%H%M%S")
        _reports_dir = ROOT / "reports"
        _reports_dir.mkdir(parents=True, exist_ok=True)

        safe = _re_b.sub(r"[^\w가-힣]", "_", product_key)[:30]
        pdf_name = f"nz_buyers_{safe}_{_ts}.docx"
        pdf_path = _reports_dir / pdf_name

        await asyncio.to_thread(
            render_p3_docx_report, ranked, product_label, pdf_path,
            "뉴질랜드", datetime.now(_tz_b.utc).strftime("%Y-%m-%d"),
        )
        _buyer_task["p3_product_label"] = product_label   # Final 보고서용
        _buyer_task["pdf"] = pdf_name
        _buyer_task.update({"status": "done", "step": "done", "step_label": "완료"})
        await _log("바이어 발굴 파이프라인 완료", "success")

    except Exception as exc:
        _buyer_task.update({"status": "error", "step": "error", "step_label": str(exc)})
        await _emit({"phase": "buyer", "message": f"오류: {exc}", "level": "error"})


@app.post("/api/buyers/run")
async def trigger_buyers(body: BuyerRunBody | None = None) -> JSONResponse:
    global _buyer_task
    req = body if body is not None else BuyerRunBody()
    if _buyer_task.get("status") == "running":
        raise HTTPException(409, "바이어 발굴이 이미 실행 중입니다.")
    _buyer_task = {
        "status": "running", "step": "crawl", "step_label": "시작 중…",
        "crawl_count": 0, "all_candidates": [], "buyers": [], "pdf": None,
    }
    asyncio.create_task(_run_buyer_pipeline(
        req.product_key,
        req.active_criteria,
        req.target_country,
        req.target_region,
    ))
    return JSONResponse({"ok": True})


@app.get("/api/buyers/status")
async def buyer_status() -> JSONResponse:
    if not _buyer_task:
        return JSONResponse({"status": "idle"})
    return JSONResponse({
        "status":          _buyer_task.get("status", "idle"),
        "step":            _buyer_task.get("step", ""),
        "step_label":      _buyer_task.get("step_label", ""),
        "crawl_count":     _buyer_task.get("crawl_count", 0),
        "buyer_count":     len(_buyer_task.get("buyers", [])),
        "candidate_count": len(_buyer_task.get("all_candidates", [])),
        "has_pdf":         bool(_buyer_task.get("pdf")),
    })


@app.get("/api/buyers/result")
async def buyer_result() -> JSONResponse:
    if not _buyer_task:
        raise HTTPException(404, "바이어 발굴 미실행")
    return JSONResponse({
        "status":  _buyer_task.get("status"),
        "buyers":  _buyer_task.get("buyers", []),
        "pdf":     _buyer_task.get("pdf"),
    })


@app.post("/api/buyers/rerank")
async def buyer_rerank(body: dict = None) -> JSONResponse:
    """기준 변경 시 전체 후보 풀(20개)에서 재선택."""
    all_candidates = _buyer_task.get("all_candidates", [])
    if not all_candidates:
        raise HTTPException(404, "후보 풀 없음. 파이프라인을 먼저 실행하세요.")
    criteria = (body or {}).get("criteria")
    from analysis.buyer_scorer import rank_companies
    ranked = rank_companies(all_candidates, active_criteria=criteria, top_n=10)
    _buyer_task["buyers"] = ranked
    return JSONResponse({"buyers": ranked})


@app.get("/api/buyers/report/download")
async def buyer_report_download(name: str | None = None) -> Any:
    reports_dir = ROOT / "reports"
    _DOCX_MT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if name:
        target = reports_dir / Path(name).name
        if target.is_file():
            ext = target.suffix.lower()
            mt = _DOCX_MT if ext == ".docx" else "application/pdf"
            return FileResponse(str(target), media_type=mt, filename=target.name,
                                content_disposition_type="attachment")
    # 최신 buyers 파일 (docx 우선)
    docs = sorted(reports_dir.glob("nz_buyers_*.docx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if docs:
        return FileResponse(str(docs[0]), media_type=_DOCX_MT,
                            filename=docs[0].name, content_disposition_type="attachment")
    pdfs = sorted(reports_dir.glob("sg_buyers_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pdfs:
        raise HTTPException(404, "바이어 보고서 없음")
    return FileResponse(str(pdfs[0]), media_type="application/pdf",
                        filename=pdfs[0].name, content_disposition_type="attachment")


# ── P2 다운로드 엔드포인트 ────────────────────────────────────────────────────

@app.get("/api/p2/download")
async def p2_report_download(name: str | None = None) -> Any:
    reports_dir = ROOT / "reports"
    _DOCX_MT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if name:
        target = reports_dir / Path(name).name
        if target.is_file():
            ext = target.suffix.lower()
            mt = _DOCX_MT if ext == ".docx" else "application/pdf"
            return FileResponse(str(target), media_type=mt, filename=target.name,
                                content_disposition_type="attachment")
    docs = sorted(reports_dir.glob("nz_p2_*.docx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if docs:
        return FileResponse(str(docs[0]), media_type=_DOCX_MT,
                            filename=docs[0].name, content_disposition_type="attachment")
    raise HTTPException(404, "P2 보고서 없음")


# ── 최종 합본 보고서 API ──────────────────────────────────────────────────────

class FinalReportBody(BaseModel):
    product_key: str = ""
    run_id:      str = ""


@app.post("/api/final/report")
async def generate_final_report(body: FinalReportBody | None = None) -> JSONResponse:
    """P1+P2+P3 합본 최종 DOCX 생성 (표지 + P2 + P3 + P1 순서)."""
    from datetime import datetime, timezone as _tz_fin
    import re as _re_fin
    from report_generator_docx import render_final_docx

    req = body if body is not None else FinalReportBody()
    product_key = req.product_key

    # ── P1 데이터 수집 ─────────────────────────────────────────────────────
    p1_task = _pipeline_tasks.get(product_key) or {}
    p1_data = p1_task.get("p1_data") or {}
    if not p1_data:
        # 폴백: 기본 구조
        p1_data = {
            "trade_name":  product_key, "inn_name": "", "country_ko": "뉴질랜드",
            "report_date": datetime.now(_tz_fin.utc).strftime("%Y-%m-%d"),
            "macro_summary": "—", "regulatory": {}, "ref_prices": [], "risks": {},
            "refs": [], "db_sources": [],
        }

    # ── P2 데이터 수집 ─────────────────────────────────────────────────────
    p2_data = _p2_ai_task.get("p2_data") or {}
    if not p2_data:
        p2_data = {
            "trade_name": product_key, "inn_name": "", "country_ko": "뉴질랜드",
            "report_date": datetime.now(_tz_fin.utc).strftime("%Y-%m-%d"),
            "macro_text": "—", "base_price_nzd": 0,
            "scenarios_public": [], "scenarios_private": [],
        }

    # ── P3 데이터 수집 ─────────────────────────────────────────────────────
    p3_buyers = _buyer_task.get("buyers") or []
    p3_data = {
        "trade_name":  _buyer_task.get("p3_product_label") or product_key,
        "country_ko":  "뉴질랜드",
        "report_date": datetime.now(_tz_fin.utc).strftime("%Y-%m-%d"),
        "buyers":      p3_buyers,
    }

    # ── 표지 데이터 ────────────────────────────────────────────────────────
    trade_name = p1_data.get("trade_name") or p2_data.get("trade_name") or product_key
    cover_data = {
        "country_ko":  "뉴질랜드",
        "company":     "한국유나이티드제약(주)",
        "report_date": datetime.now(_tz_fin.utc).strftime("%Y-%m-%d"),
        "subtitle":    "수출가격 전략 - 바이어 후보 리스트 - 시장분석",
    }

    # ── DOCX 생성 ──────────────────────────────────────────────────────────
    _ts_fin = datetime.now(_tz_fin.utc).strftime("%Y%m%d_%H%M%S")
    _safe = _re_fin.sub(r"[^\w가-힣]", "_", trade_name)[:20] or "product"
    _reports_dir = ROOT / "reports"
    _reports_dir.mkdir(parents=True, exist_ok=True)
    docx_name = f"nz_final_{_safe}_{_ts_fin}.docx"
    docx_path = _reports_dir / docx_name

    await asyncio.to_thread(render_final_docx, cover_data, p2_data, p3_data, p1_data, docx_path)

    return JSONResponse({"ok": True, "docx": docx_name})


@app.get("/api/final/download")
async def final_report_download(name: str | None = None) -> Any:
    """최종 합본 DOCX 다운로드."""
    reports_dir = ROOT / "reports"
    _DOCX_MT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if name:
        target = reports_dir / Path(name).name
        if target.is_file():
            return FileResponse(str(target), media_type=_DOCX_MT,
                                filename=target.name, content_disposition_type="attachment")
    docs = sorted(reports_dir.glob("nz_final_*.docx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not docs:
        raise HTTPException(404, "최종 보고서 없음. POST /api/final/report 먼저 실행하세요.")
    return FileResponse(str(docs[0]), media_type=_DOCX_MT,
                        filename=docs[0].name, content_disposition_type="attachment")


# ── 우루과이 거시지표 ──────────────────────────────────────────────────────────

@app.get("/api/uy/macro")
async def api_uy_macro() -> JSONResponse:
    from utils.uy_macro import get_uy_macro
    return JSONResponse(get_uy_macro())


# ── UYU/USD 환율 ──────────────────────────────────────────────────────────────

_uyu_exchange_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_UYU_EXCHANGE_TTL = 300.0


@app.get("/api/exchange/uyu")
async def api_exchange_uyu() -> JSONResponse:
    import time as _time

    if _uyu_exchange_cache["data"] and _time.time() - _uyu_exchange_cache["ts"] < _UYU_EXCHANGE_TTL:
        return JSONResponse(_uyu_exchange_cache["data"])

    def _fetch_uyu() -> dict[str, Any]:
        import yfinance as yf  # type: ignore[import]
        uyu_usd = float(yf.Ticker("UYUUSD=X").fast_info.last_price)
        usd_krw = float(yf.Ticker("USDKRW=X").fast_info.last_price)
        return {
            "uyu_usd": round(uyu_usd, 6),
            "usd_krw": round(usd_krw, 2),
            "uyu_krw": round(uyu_usd * usd_krw, 4),
            "source": "Yahoo Finance",
            "fetched_at": _time.time(),
            "ok": True,
        }

    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _fetch_uyu)
        _uyu_exchange_cache["data"] = data
        _uyu_exchange_cache["ts"] = _time.time()
        return JSONResponse(data)
    except Exception as exc:
        fallback: dict[str, Any] = {
            "uyu_usd": 0.02481,
            "usd_krw": 1393.0,
            "uyu_krw": 34.57,
            "source": "폴백 (Yahoo Finance 연결 실패)",
            "fetched_at": time.time(),
            "ok": False,
            "error": str(exc),
        }
        return JSONResponse(fallback)


# ── 우루과이 크롤링 파이프라인 ────────────────────────────────────────────────────

_uy_crawl_cache: dict[str, Any] = {"result": None, "running": False}


class UyCrawlBody(BaseModel):
    inn_names: list[str] = ["Cilostazol"]
    save_db: bool = True


@app.post("/api/uy/crawl")
async def trigger_uy_crawl(body: UyCrawlBody | None = None) -> JSONResponse:
    req = body if body is not None else UyCrawlBody()
    if _uy_crawl_cache["running"]:
        raise HTTPException(status_code=409, detail="UY 크롤링이 이미 실행 중입니다.")

    async def _run() -> None:
        _uy_crawl_cache["running"] = True
        try:
            from analysis.uy_export_analyzer import analyze_uy_market
            result = await analyze_uy_market(
                inn_names=req.inn_names,
                save_db=req.save_db,
                emit=_emit,
            )
            _uy_crawl_cache["result"] = result
        finally:
            _uy_crawl_cache["running"] = False

    asyncio.create_task(_run())
    return JSONResponse({"ok": True, "message": f"{req.inn_names} UY 크롤링 시작"})


@app.get("/api/uy/crawl/status")
async def uy_crawl_status() -> JSONResponse:
    return JSONResponse({
        "running": _uy_crawl_cache["running"],
        "has_result": _uy_crawl_cache["result"] is not None,
        "result": _uy_crawl_cache["result"],
    })


@app.get("/api/uy/pricing")
async def api_uy_pricing(inn_name: str | None = None, limit: int = 100) -> JSONResponse:
    try:
        from utils.db import get_supabase_client
        sb = get_supabase_client()
        query = sb.table("uy_pricing").select("*").order("crawled_at", desc=True).limit(limit)
        if inn_name:
            query = query.ilike("inn_name", f"%{inn_name}%")
        result = query.execute()
        return JSONResponse({"ok": True, "count": len(result.data), "rows": result.data})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200], "rows": []})


# ── FOB 역산기 ────────────────────────────────────────────────────────────────

class FobBody(BaseModel):
    price_usd: float
    market_segment: str = "private"
    inn_name: str = ""
    import_duty_pct: float | None = None


@app.post("/api/fob/calculate")
async def api_fob_calculate(body: FobBody) -> JSONResponse:
    from analysis.fob_calculator import (
        calc_logic_a, calc_logic_b, fob_result_to_dict, msp_copayment_check
    )
    from decimal import Decimal

    price = Decimal(str(body.price_usd))
    if body.market_segment == "public":
        duty = Decimal(str(body.import_duty_pct / 100)) if body.import_duty_pct else None
        result = calc_logic_a(price, import_duty_rate=duty, inn_name=body.inn_name)
    else:
        result = calc_logic_b(price, inn_name=body.inn_name)

    d = fob_result_to_dict(result)
    d["msp_check"] = msp_copayment_check(result.base.fob_usd)
    return JSONResponse({"ok": True, **d})


# ── 인도네시아 AHP 파트너 매칭 ────────────────────────────────────────────────────

@app.get("/api/ahp/partners")
async def api_ahp_partners() -> JSONResponse:
    from analysis.ahp_matcher import score_all_candidates, ahp_results_to_dicts
    results = score_all_candidates()
    return JSONResponse({"ok": True, "count": len(results), "partners": ahp_results_to_dicts(results)})


# ── 우루과이 시장 뉴스 (Perplexity) ────────────────────────────────────────────

_uy_news_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_UY_NEWS_TTL = 1800


@app.get("/api/uy/news")
async def api_uy_news() -> JSONResponse:
    import time as _time
    import os
    import httpx

    if _uy_news_cache["data"] and _time.time() - _uy_news_cache["ts"] < _UY_NEWS_TTL:
        return JSONResponse(_uy_news_cache["data"])

    px_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not px_key:
        return JSONResponse({"ok": False, "error": "PERPLEXITY_API_KEY 미설정", "items": []})

    try:
        payload = {
            "model": "sonar-pro",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a Uruguay pharmaceutical market analyst. "
                        "Return ONLY a JSON array with up to 6 recent news items. "
                        "All 'title' values MUST be written in Korean (한국어)."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Find the latest Uruguay pharmaceutical market, regulatory news, "
                        "and drug pricing policy (ASSE, MSP, ARCE). "
                        "Return strict JSON array. Each item: title (Korean), source, date, link."
                    ),
                },
            ],
            "max_tokens": 900,
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {px_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            raw = resp.json()

        content = str(raw.get("choices", [{}])[0].get("message", {}).get("content", ""))
        items = _parse_perplexity_news_items(content)
        data = {"ok": bool(items), "items": items}
        _uy_news_cache["data"] = data
        _uy_news_cache["ts"] = _time.time()
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:120], "items": []})


# ══════════════════════════════════════════════════════════════════════════════
# NZ 크롤러 엔드포인트 (Medsafe + 소매 약국 신규 2곳)
# ══════════════════════════════════════════════════════════════════════════════

class MedsafeCrawlBody(BaseModel):
    inn_names: list[str] = [
        "cilostazol", "rosuvastatin", "omega-3", "atorvastatin",
        "mosapride", "gadobutrol", "hydroxyurea", "fluticasone",
    ]
    dry_run: bool = False


@app.post("/api/crawl/medsafe")
async def trigger_medsafe_crawl(body: MedsafeCrawlBody) -> JSONResponse:
    """Medsafe TPR 크롤링 → nz_medsafe_consents 저장."""
    try:
        from utils.nz_medsafe_crawler import run as medsafe_run
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: medsafe_run(body.inn_names, dry_run=body.dry_run),
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=500)


class RetailCrawlBody(BaseModel):
    inn_names: list[str] = [
        "cilostazol", "rosuvastatin", "omega-3", "atorvastatin",
    ]
    sources: list[str] = ["nzonlinepharmacy", "pharmacydirect"]
    dry_run: bool = False


_RETAIL_CRAWLERS = {
    "nzonlinepharmacy": "utils.nz_online_pharmacy_crawler",
    "pharmacydirect":   "utils.nz_pharmacydirect_crawler",
}

_CRAWL_FN_MAP = {
    "nzonlinepharmacy": "crawl_nzonlinepharmacy_multi",
    "pharmacydirect":   "crawl_pharmacydirect_multi",
}


@app.post("/api/crawl/retail")
async def trigger_retail_crawl(body: RetailCrawlBody) -> JSONResponse:
    """신규 소매 약국 (NZ Online Pharmacy / Pharmacy Direct) 크롤링 → nz_retail_prices 저장."""
    import importlib
    from utils.db import get_client
    from datetime import datetime

    results: dict[str, dict] = {}
    for source in body.sources:
        mod_name = _RETAIL_CRAWLERS.get(source)
        fn_name  = _CRAWL_FN_MAP.get(source)
        if not mod_name:
            results[source] = {"ok": False, "error": f"알 수 없는 소스: {source}"}
            continue
        try:
            mod = importlib.import_module(mod_name)
            crawl_fn = getattr(mod, fn_name)
            drug_map = await crawl_fn(body.inn_names)

            if body.dry_run:
                total = sum(len(v) for v in drug_map.values())
                results[source] = {"ok": True, "dry_run": True, "found": total}
                continue

            sb = get_client()
            rows_inserted = 0
            for inn, drugs in drug_map.items():
                for drug in drugs:
                    if drug is None:
                        continue
                    row = {
                        "inn_name":         inn,
                        "product_name":     drug.brand_name or drug.inn_name or "",
                        "price_nzd":        drug.total_price_nzd,
                        "price_per_unit_nzd": drug.price_per_unit_nzd,
                        "fob_estimated_usd": drug.price_per_unit_usd,
                        "confidence":       drug.confidence,
                        "source_site":      source,
                        "source_url":       drug.source_url or "",
                        "market_segment":   "NZ",
                        "strength":         drug.strength_mg,
                        "dosage_form":      drug.dosage_form,
                        "pack_size":        drug.pack_size,
                        "crawled_at":       datetime.utcnow().isoformat(),
                        "raw_payload":      drug.extra,
                    }
                    sb.table("nz_retail_prices").insert(row).execute()
                    rows_inserted += 1

            results[source] = {"ok": True, "inserted": rows_inserted}
        except Exception as exc:
            results[source] = {"ok": False, "error": str(exc)[:200]}

    return JSONResponse({"ok": True, "results": results})


@app.get("/api/medsafe/{inn_name}")
async def get_medsafe_consents(inn_name: str, limit: int = 50) -> JSONResponse:
    """nz_medsafe_consents 테이블에서 INN명으로 허가 정보 조회."""
    try:
        from utils.db import get_client
        sb = get_client()
        result = (
            sb.table("nz_medsafe_consents")
            .select("*")
            .ilike("inn_name", f"%{inn_name}%")
            .order("updated_at", desc=True)
            .limit(limit)
            .execute()
        )
        return JSONResponse({
            "ok": True,
            "inn_name": inn_name,
            "count": len(result.data),
            "rows": result.data,
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200], "rows": []})


@app.get("/")
async def index() -> FileResponse:
    index_path = STATIC / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="index.html 없음")
    return FileResponse(index_path)


@app.get("/frontend3")
async def frontend3() -> FileResponse:
    path = STATIC / "frontend3.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="frontend3.html 없음")
    return FileResponse(path)


# ── NZ DB 데이터 API ─────────────────────────────────────────────────────────
# nz_market_macro / nz_price_strategy / nz_buyers 테이블에서 직접 반환

@app.get("/api/nz/macro")
async def nz_macro_db() -> JSONResponse:
    """Section 1 - 거시 시장 환경 (nz_market_macro 테이블)."""
    try:
        from utils.db import get_client as _db
        sb = _db()
        resp = sb.table("nz_market_macro").select("*").order("id").limit(1).execute()
        if resp.data:
            return JSONResponse({"ok": True, "data": resp.data[0]})
        return JSONResponse({"ok": False, "data": None, "message": "데이터 없음"})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/api/nz/price-strategy")
async def nz_price_strategy_all() -> JSONResponse:
    """Section 2 - 8개 제품 가격 전략 전체 (nz_price_strategy 테이블)."""
    try:
        from utils.db import get_client as _db
        import json as _json
        sb = _db()
        resp = sb.table("nz_price_strategy").select("*").order("sort_order").execute()
        rows = resp.data or []
        # competitors 필드: DB에서 문자열로 오는 경우 파싱
        for row in rows:
            if isinstance(row.get("competitors"), str):
                try:
                    row["competitors"] = _json.loads(row["competitors"])
                except Exception:
                    pass
        return JSONResponse({"ok": True, "data": rows, "count": len(rows)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/api/nz/price-strategy/{product_key}")
async def nz_price_strategy_one(product_key: str) -> JSONResponse:
    """Section 2 - 특정 제품 가격 전략 (nz_price_strategy 테이블)."""
    try:
        from utils.db import get_client as _db
        import json as _json
        sb = _db()
        resp = sb.table("nz_price_strategy").select("*").eq("product_key", product_key).execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail=f"product_key '{product_key}' 없음")
        row = resp.data[0]
        if isinstance(row.get("competitors"), str):
            try:
                row["competitors"] = _json.loads(row["competitors"])
            except Exception:
                pass
        return JSONResponse({"ok": True, "data": row})
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/api/nz/buyers")
async def nz_buyers_all() -> JSONResponse:
    """Section 3 - 3개 바이어 기업 전체 (nz_buyers 테이블)."""
    try:
        from utils.db import get_client as _db
        sb = _db()
        resp = sb.table("nz_buyers").select("*").order("sort_order").execute()
        rows = resp.data or []
        return JSONResponse({"ok": True, "data": rows, "count": len(rows)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/api/nz/buyers/{company_key}")
async def nz_buyers_one(company_key: str) -> JSONResponse:
    """Section 3 - 특정 바이어 기업 상세 (nz_buyers 테이블)."""
    try:
        from utils.db import get_client as _db
        sb = _db()
        resp = sb.table("nz_buyers").select("*").eq("company_key", company_key).execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail=f"company_key '{company_key}' 없음")
        return JSONResponse({"ok": True, "data": resp.data[0]})
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="SG 분석 대시보드")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    if args.open:
        def _open_later() -> None:
            time.sleep(1.0)
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
        threading.Thread(target=_open_later, daemon=True).start()

    print(f"\n  ▶ 대시보드: http://127.0.0.1:{args.port}/\n")
    uvicorn.run(app, host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
