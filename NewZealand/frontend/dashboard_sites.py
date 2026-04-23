"""대시보드에 표시할 뉴질랜드(NZ) 소스 라벨 (한국어)."""

from __future__ import annotations

from typing import Any, TypedDict


class SiteDef(TypedDict):
    id: str
    name: str
    hint: str
    domain: str


DASHBOARD_SITES: tuple[SiteDef, ...] = (
    {
        "id": "gets",
        "name": "GETS · 정부 전자입찰 포털",
        "hint": "PHARMAC·Health NZ 의약품 공급 입찰(ITT) 공고 모니터링 (Playwright)",
        "domain": "gets.govt.nz",
    },
    {
        "id": "chemistwarehouse",
        "name": "Chemist Warehouse NZ · 대형 할인 약국 체인",
        "hint": "처방약·OTC·건강보조식품 소매가·프로모션 최저가 실시간 수집 (Playwright)",
        "domain": "chemistwarehouse.co.nz",
    },
    {
        "id": "lifepharmacy",
        "name": "Life Pharmacy · 프리미엄 약국 체인",
        "hint": "전문의약품·만성질환 관리 의료기기 표준 소매가(Standard Price) 분석 (정적 HTML)",
        "domain": "lifepharmacy.co.nz",
    },
    {
        "id": "netpharmacy",
        "name": "Net Pharmacy · 온라인 약국",
        "hint": "만성질환 치료제·보조제 할인가, 원격 처방 연계 비급여 소매가 (정적 HTML)",
        "domain": "netpharmacy.co.nz",
    },
    {
        "id": "bargainchemist",
        "name": "Bargain Chemist · 저가 약국 체인",
        "hint": "Pharmacist Only 포함 실질 조제 비용·최저가 트렌드 벤치마킹 (정적 HTML)",
        "domain": "bargainchemist.co.nz",
    },
    {
        "id": "nzonlinepharmacy",
        "name": "NZ Online Pharmacy · 현지 온라인 약국",
        "hint": "NZ 현지 운영 온라인 약국 — Medsafe 기준 소매 의약품가 수집 (정적 HTML)",
        "domain": "nz-online-pharmacy.com",
    },
    {
        "id": "pharmacydirect",
        "name": "Pharmacy Direct · 전문의약품 온라인 약국",
        "hint": "처방전 필요 의약품 포함 전문의약품 소매가 데이터 (정적 HTML)",
        "domain": "pharmacydirect.co.nz",
    },
    {
        "id": "medsafe",
        "name": "Medsafe · NZ 의약품 허가 기관",
        "hint": "Therapeutic Products Register(TPR) — 허가 품목·처방 분류·스폰서 정보 (정적 HTML)",
        "domain": "medsafe.govt.nz",
    },
)


def initial_site_states() -> dict[str, dict[str, Any]]:
    return {
        s["id"]: {
            "status": "pending",
            "message": "아직 시작 전이에요",
            "ts": 0.0,
        }
        for s in DASHBOARD_SITES
    }
