"""
NZ 시장 데이터 Supabase 적재 스크립트
- Section 1: 거시 시장 환경 (nz_market_macro)
- Section 2: 8개 제품 가격 전략 (nz_price_strategy)
- Section 3: 3개 바이어 기업 (nz_buyers)

실행: /c/Users/user/miniforge3/python seed_nz_market_data.py
"""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path

# Windows CP949 터미널 대응
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

try:
    from supabase import create_client
except ImportError:
    print("supabase-py 미설치. pip install supabase")
    sys.exit(1)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 환경변수 미설정")
    sys.exit(1)

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
print(f"✅ Supabase 연결: {SUPABASE_URL[:40]}...")


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: 거시 시장 환경
# ─────────────────────────────────────────────────────────────────────────────

MACRO_DATA = {
    "market_size_usd_bn": 1.00,
    "market_size_note": "뉴질랜드 제약 시장 규모 약 10억 달러(USD). 인구 고령화에 따른 만성질환(심혈관, 당뇨, 항암) 증가로 복합제 및 개량신약(IMD) 수요 급증.",
    "growth_drivers": ["복합제(IMD)", "고령화", "항암제 예산 확대", "호흡기 치료제 접근성 확대"],
    # PHARMAC
    "pharmac_system": "Monopsony",
    "pharmac_description": "PHARMAC(Pharmaceutical Management Agency)은 한정된 국가 예산 내에서 비용 효용성(Cost-Utility)을 엄격히 평가하여 공공조달 단가를 결정하며, 공공 스케줄(Pharmaceutical Schedule)에 등재될 경우 전국 처방을 독점하게 됨.",
    "pharmac_budget_note": "뉴질랜드 정부 2024~2025년 항암제 및 호흡기 치료제 접근성 확대를 위해 6억 400만 달러 추가 예산 편성. Hydrine(항암) 및 Sereterol Activair(호흡기) 진입 호재.",
    # 유통
    "distributor_major": ["EBOS Group (ProPharma)", "CDC Pharmaceuticals"],
    "distributor_margin_pct_low": 3.0,
    "distributor_margin_pct_high": 10.0,
    "distributor_note": "소수 대형 도매상이 독과점. 역량 있는 현지 스폰서와의 파트너십 필수.",
    # Medsafe
    "medsafe_sponsor_required": True,
    "medsafe_standard_fee_nzd": 53251.00,
    "medsafe_standard_days": 200,
    "medsafe_standard_note": "제네릭·개량신약은 중간 위험(Intermediate-risk) 군으로 분류. 법정 200일이나 RFI 시 1년 이상 소요 가능. CTD 형식 품질/안전성/유효성 데이터 + GMP 인증 필수.",
    "medsafe_verification_days": 30,
    "medsafe_verification_note": "2025년 도입 Verification Pathway: FDA·EMA·TGA 등 7개 인정 국가 중 2곳 이상 허가 시 30 근무일 이내 승인.",
    "medsafe_device_note": "흡입기(DPI) 등 의료기기: 상업 유통 후 30일 이내 WAND(Web Assisted Notification of Devices) 신고 필요. 사전 승인 불필요.",
    # FOB 공공
    "fob_public_formula": "FOB (USD) = 타겟 입찰가(USD) × factor (0.25~0.40, 기본 0.30)",
    "fob_public_factor_low": 0.25,
    "fob_public_factor_std": 0.30,
    "fob_public_factor_high": 0.40,
    "fob_public_note": "PHARMAC 공시가는 유통마진 제외된 공급가 성격. 현지 스폰서 행정·물류비 고려하여 보수적으로 30% 역산 적용.",
    # FOB 민간
    "fob_private_formula": "FOB = HET / (1 + GST 0.15) × (1 - 약국마진 0.28) × (1 - 유통마진 0.10) × [침투할인 0.80]",
    "fob_private_gst_pct": 0.15,
    "fob_private_pharmacy_margin": 0.28,
    "fob_private_dist_margin": 0.10,
    "fob_private_discount": 0.80,
    "fob_private_note": "NZ GST 15% (SG의 12% 아님). 약국 마진 28%, 도매 유통 10%, 무관세(0%). FOB ≈ HET의 약 56% 수준.",
    # 환율
    "exchange_rate_usd_nzd": 1.65,
    "report_section": "1",
}


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: 8개 제품 가격 전략
# ─────────────────────────────────────────────────────────────────────────────

PRODUCTS = [
    {
        "product_key": "NZ_rosumeg_combigel",
        "product_name_ko": "로수메그 콤비겔",
        "product_name_en": "Rosumeg Combigel",
        "inn_components": ["Rosuvastatin", "Omega-3"],
        "dosage_form": "연질캡슐 (CombiGel)",
        "strength": "Rosuvastatin 10mg + Omega-3 1000mg",
        "unit": "정",
        "market_type": ["공공", "민간"],
        "primary_market": "공공/민간",
        "base_price_usd": 0.45,
        "base_price_note": "Rosuvastatin(USD 0.04) + Omega-3(USD 0.08) 단일제 합산가에 복합제 프리미엄 3.5배 적용",
        "macro_text": (
            "심혈관 질환은 뉴질랜드 사망 원인의 핵심으로, PHARMAC은 스타틴 계열의 비용 통제를 강력히 추진하고 있습니다. "
            "현재 Rosuvastatin 단일제는 특별 권한(SA2093) 하에 제한적 공공 지원을 받고 있으며, 오메가-3는 주로 민간 영양제 시장에서 높은 단가로 소비됩니다. "
            "본 제품의 CombiGel 복합 제형은 복약 순응도를 높인다는 임상적 이점을 통해, 공공 기준가를 상회하는 프리미엄 민간 시장 진입 또는 PHARMAC의 신규 복합제 트랙 진입이 모두 가능합니다."
        ),
        # 공공 시장
        "public_target_usd": 0.35,
        "public_low_usd": 0.087,
        "public_low_rationale": "PHARMAC의 단일제 합산가(약 USD 0.11) 대비 80% 수준으로 포지셔닝하여, 기존 단일제 처방을 복합제로 스위칭하도록 강력한 원가 절감 논리 제공.",
        "public_low_formula": "USD 0.35 × 0.25 (로직A 보수적 하단)",
        "public_std_usd": 0.105,
        "public_std_rationale": "공공 입찰 시장의 표준 수익률 모델 적용. 복합제 개발 원가를 보전하면서도 현지 스폰서의 적정 입찰 마진(20%)을 보장하는 균형점.",
        "public_std_formula": "USD 0.35 × 0.30 (로직A 표준)",
        "public_premium_usd": 0.140,
        "public_premium_rationale": "CombiGel 특허 기술의 임상적 우월성(복약 순응도 개선으로 인한 심혈관 사고율 감소)을 HTA(의료기술평가)로 입증하여 고가 등재 성공 시.",
        "public_premium_formula": "USD 0.35 × 0.40 (로직A 상단)",
        # 민간 시장
        "private_het_usd": 0.60,
        "private_low_usd": 0.268,
        "private_low_rationale": "현지 로컬 브랜드의 프리미엄 오메가-3 단가 수준으로 진입하여, 사보험 환자 및 비급여 처방 환자의 가격 저항을 최소화.",
        "private_low_formula": "(USD 0.60 / 1.15(GST)) × (1-0.28(약국)) × (1-0.10(유통)) × 0.8 (침투할인)",
        "private_std_usd": 0.338,
        "private_std_rationale": "타겟 기준가에서 세금과 채널 마진만 정직하게 덜어낸 역산가. 현지 시장에 통상적인 수입 의약품이 자리 잡는 표준 수출 단가.",
        "private_std_formula": "(USD 0.60 / 1.15(GST)) × (1-0.28(약국)) × (1-0.10(유통))",
        "private_premium_usd": 0.380,
        "private_premium_rationale": "약국 내 '프리미엄 개량신약'으로 독자 포지셔닝. 약사 대상 디테일링 프로모션을 강화하여 약국 자체 추천(Switching)을 유도할 수 있는 고마진 구조.",
        "private_premium_formula": "USD 0.60 기반 역산 후 제약사 마진 비율 상향 조정",
        "competitors": [
            {"company": "Sandoz", "product": "Rosuvastatin Sandoz", "ingredient": "Rosuvastatin 10mg", "price_usd": 0.034, "market_type": "공공 조달가"},
            {"company": "Viatris", "product": "Rosuvastatin Viatris", "ingredient": "Rosuvastatin 10mg", "price_usd": 0.120, "market_type": "민간 소매가"},
            {"company": "Good Health", "product": "Omega 3 Fish Oil", "ingredient": "Omega-3 1000mg", "price_usd": 0.075, "market_type": "민간 소매가"},
        ],
        "recommended_buyers": ["douglas_pharmaceuticals", "aft_pharmaceuticals"],
        "sort_order": 1,
    },
    {
        "product_key": "NZ_atmeg_combigel",
        "product_name_ko": "아트메그 콤비겔",
        "product_name_en": "Atmeg Combigel",
        "inn_components": ["Atorvastatin", "Omega-3"],
        "dosage_form": "연질캡슐 (CombiGel)",
        "strength": "Atorvastatin 10mg + Omega-3 1000mg",
        "unit": "정",
        "market_type": ["공공", "민간"],
        "primary_market": "민간 (1차 타겟)",
        "base_price_usd": 0.40,
        "base_price_note": "Atorvastatin(USD 0.006) + Omega-3(USD 0.08) 단일제 합산가 기반 개량신약 마크업 적용",
        "macro_text": (
            "Atorvastatin은 뉴질랜드 내 처방량 최상위권의 고지혈증 약물로, PHARMAC을 통해 완전히 보조금(Fully Subsidised)이 지급되어 단일제 단가가 매우 낮습니다. "
            "단일제 최저가 경쟁이 치열한 공공 시장보다는, Omega-3가 결합된 Atmeg의 특장점을 살려 심혈관 예방 목적의 하이엔드 민간 처방 시장(Out-of-pocket)을 1차 타겟으로 삼는 것이 유리합니다."
        ),
        "public_target_usd": 0.20,
        "public_low_usd": 0.050,
        "public_low_rationale": "로컬 Atorvastatin 단일제의 초저가(USD 0.006) 구조를 고려, PHARMAC 협상 시 최소한의 복합제 프리미엄만 얹어 진입 장벽을 낮춤.",
        "public_low_formula": "USD 0.20 × 0.25",
        "public_std_usd": 0.060,
        "public_std_rationale": "수입 비용(40%) 및 유통 파트너 마진을 정상적으로 보장하여 안정적인 입찰 물량을 확보하는 스탠다드 전략.",
        "public_std_formula": "USD 0.20 × 0.30",
        "public_premium_usd": 0.080,
        "public_premium_rationale": "고위험군 심혈관 환자 대상 필수 병용 투여 약물로 임상적 가치를 인정받아 특수 환자군(Special Authority) 대상 고단가 등재 시.",
        "public_premium_formula": "USD 0.20 × 0.40",
        "private_het_usd": 0.50,
        "private_low_usd": 0.225,
        "private_low_rationale": "기존 고가 오메가-3 소비층을 흡수하기 위해, '스타틴이 포함된 전문 심혈관 케어'라는 가치를 부여하되 가격은 기존 오메가-3와 유사하게 세팅.",
        "private_low_formula": "(USD 0.50 / 1.15) × 0.72 × 0.90 × 0.8",
        "private_std_usd": 0.281,
        "private_std_rationale": "뉴질랜드 부가세(15%) 및 약국(28%), 도매(10%) 정상 마진을 모두 공제한 이상적인 제조사 수출 원가.",
        "private_std_formula": "(USD 0.50 / 1.15) × 0.72 × 0.90",
        "private_premium_usd": 0.320,
        "private_premium_rationale": "민간 심장 전문의(Cardiologist) 처방 전용 브랜드로 론칭하여, 가격 민감도가 낮은 고소득층 사보험 시장 타겟팅.",
        "private_premium_formula": "역산 로직 적용 후 파트너사 프로모션 마진 삭감 및 제약사 이익 극대화",
        "competitors": [
            {"company": "Viatris", "product": "Lorstat", "ingredient": "Atorvastatin 10mg", "price_usd": 0.006, "market_type": "공공 조달가"},
            {"company": "Douglas", "product": "Atorvastatin", "ingredient": "Atorvastatin 20mg", "price_usd": 0.150, "market_type": "민간 소매가 추정"},
            {"company": "Coyne Health", "product": "Purest Omega 3", "ingredient": "Omega-3 1000mg", "price_usd": 0.460, "market_type": "민간 소매 프리미엄"},
        ],
        "recommended_buyers": ["aft_pharmaceuticals", "douglas_pharmaceuticals"],
        "sort_order": 2,
    },
    {
        "product_key": "NZ_ciloduo",
        "product_name_ko": "실로듀오",
        "product_name_en": "Ciloduo",
        "inn_components": ["Cilostazol", "Rosuvastatin"],
        "dosage_form": "정제",
        "strength": "Cilostazol 100mg + Rosuvastatin 20mg",
        "unit": "정",
        "market_type": ["공공 (제한적)", "민간"],
        "primary_market": "민간",
        "base_price_usd": 0.80,
        "base_price_note": "글로벌 Cilostazol 단가 + Rosuvastatin 단가에 특수 질환 복합제 가치 반영",
        "macro_text": (
            "Cilostazol은 뉴질랜드 내에서 폭넓게 급여화되어 있지 않은 성분으로(과거 호주/NZ에서 미분류 및 급여 제한 이력), 특정 말초동맥질환(PAD) 처방에 의존합니다. "
            "반면 Rosuvastatin의 높은 인지도를 활용하여, PAD 및 고지혈증 동반 질환자에게 강력한 혈전 예방과 지질 강하 효과를 동시에 제공하는 니치(Niche) 처방 시장을 개척할 수 있습니다."
        ),
        "public_target_usd": 0.50,
        "public_low_usd": 0.125,
        "public_low_rationale": "미급여 성분(Cilostazol)의 공공 스케줄 최초 등재를 위해, 비용 효용성(ICER) 기준을 충족시키기 위한 파격적인 초도 진입가.",
        "public_low_formula": "USD 0.50 × 0.25",
        "public_std_usd": 0.150,
        "public_std_rationale": "PHARMAC 등재 후 수요가 안정화되었을 때 유통 비용과 수입 부대비용을 커버할 수 있는 지속 가능한 수출가.",
        "public_std_formula": "USD 0.50 × 0.30",
        "public_premium_usd": 0.200,
        "public_premium_rationale": "말초동맥질환의 유일한 복합제 대안으로 인정받아, 대체 약물이 없는 상태에서 독점적 입찰 단가를 확보할 경우.",
        "public_premium_formula": "USD 0.50 × 0.40",
        "private_het_usd": 1.20,
        "private_low_usd": 0.541,
        "private_low_rationale": "비급여 처방 시 환자의 본인 부담금을 경감하기 위해 의도적으로 현지 파트너와 유통 마진을 쉐어하며 가격을 낮춘 시나리오.",
        "private_low_formula": "(USD 1.20 / 1.15) × 0.72 × 0.90 × 0.8",
        "private_std_usd": 0.676,
        "private_std_rationale": "뉴질랜드 부가세 15%와 민간 약국 마진 28%의 허들을 뚫고 역산된 목표가로, 가장 현실적인 민간 병원 납품 기준가.",
        "private_std_formula": "(USD 1.20 / 1.15) × 0.72 × 0.90",
        "private_premium_usd": 0.780,
        "private_premium_rationale": "대체 불가한 복합제의 성격을 활용해 병원 직납(Direct-to-hospital) 채널을 가동, 중간 도매상 마진(10%)을 배제하고 제조사가 흡수한 가격.",
        "private_premium_formula": "역산가에서 유통마진 페널티 제외",
        "competitors": [
            {"company": "Otsuka (글로벌)", "product": "Pletaal (Proxy)", "ingredient": "Cilostazol 100mg", "price_usd": 0.500, "market_type": "글로벌 평균"},
            {"company": "Sandoz", "product": "Rosuvastatin Sandoz", "ingredient": "Rosuvastatin 20mg", "price_usd": 0.054, "market_type": "공공 조달가"},
        ],
        "recommended_buyers": ["aft_pharmaceuticals", "pharmaco_nz"],
        "sort_order": 3,
    },
    {
        "product_key": "NZ_gastiin_cr",
        "product_name_ko": "가스틴 CR",
        "product_name_en": "Gastiin CR",
        "inn_components": ["Mosapride"],
        "dosage_form": "서방형 정제 (BILDAS 기술)",
        "strength": "Mosapride CR",
        "unit": "정",
        "market_type": ["공공", "민간"],
        "primary_market": "민간 (전문의 처방)",
        "base_price_usd": 0.60,
        "base_price_note": "위장관 운동 촉진제 유사군(Domperidone 등) 대비 서방형 프리미엄 적용",
        "macro_text": (
            "Mosapride는 뉴질랜드에서 제한적으로 사용되거나, 오프라벨 및 유사 위장관 운동 촉진제(Prokinetics)의 대체재로 포지셔닝해야 하는 성분입니다. "
            "Gastiin CR은 서방형(Controlled Release) 특허인 BILDAS 기술이 적용되어, 하루 여러 번 복용해야 하는 기존 위장약의 단점을 하루 1회 복용으로 혁신한 개량신약이므로, 이 편의성을 무기로 전문의 대상 마케팅이 주효합니다."
        ),
        "public_target_usd": 0.30,
        "public_low_usd": 0.075,
        "public_low_rationale": "기존 저가 제네릭(Domperidone 등) 대비 1일 약가(Daily Treatment Cost) 관점에서 비용을 맞추어 PHARMAC의 예산 통제 논리를 돌파하는 전략.",
        "public_low_formula": "USD 0.30 × 0.25",
        "public_std_usd": 0.090,
        "public_std_rationale": "서방형 기술의 원가를 반영하면서도 파트너 마진을 고려한 스탠다드.",
        "public_std_formula": "USD 0.30 × 0.30",
        "public_premium_usd": 0.120,
        "public_premium_rationale": "부작용이 적고 1일 1회 복용의 장점을 인정받아 1차 치료제가 아닌 2차 특수 처방용 고가약으로 등재 시.",
        "public_premium_formula": "USD 0.30 × 0.40",
        "private_het_usd": 0.80,
        "private_low_usd": 0.360,
        "private_low_rationale": "소화불량 환자가 쉽게 자비로 구매(Out-of-pocket)할 수 있도록 심리적 저항선인 정당 1달러 이하로 소비자가를 세팅 후 역산.",
        "private_low_formula": "(USD 0.80 / 1.15) × 0.72 × 0.90 × 0.8",
        "private_std_usd": 0.450,
        "private_std_rationale": "수입 VAT 15%, 약국 28%, 유통 10%의 철저한 정석 마진 공제.",
        "private_std_formula": "(USD 0.80 / 1.15) × 0.72 × 0.90",
        "private_premium_usd": 0.550,
        "private_premium_rationale": "고효능/저부작용의 처방 위장약 브랜딩을 통해, 전문의 추천 비급여 처방 시장에서 프리미엄 포지션 구축.",
        "private_premium_formula": "약국 마진을 20%로 협상 조정한 후의 제약사 수익 역산",
        "competitors": [
            {"company": "(글로벌)", "product": "Gasmotin (Proxy)", "ingredient": "Mosapride 5mg", "price_usd": 0.200, "market_type": "글로벌 평균"},
            {"company": "Local Generic", "product": "Domperidone", "ingredient": "Domperidone 10mg", "price_usd": 0.050, "market_type": "공공 조달가 추정"},
        ],
        "recommended_buyers": ["douglas_pharmaceuticals", "aft_pharmaceuticals"],
        "sort_order": 4,
    },
    {
        "product_key": "NZ_omethyl_cutielet",
        "product_name_ko": "오메틸 큐티렛",
        "product_name_en": "Omethyl Cutielet",
        "inn_components": ["Omega-3"],
        "dosage_form": "미니 파우치 (Seamless Pouch)",
        "strength": "Omega-3 2g",
        "unit": "포",
        "market_type": ["민간 (주력)", "공공 (제한적)"],
        "primary_market": "민간",
        "base_price_usd": 1.20,
        "base_price_note": "고용량 오메가-3 캡슐 단가(USD 0.1~0.4) 대비 특수 제형(Seamless) 프리미엄 극대화",
        "macro_text": (
            "오메가-3는 뉴질랜드에서 국민적 영양제이자 심혈관 보조제로 광범위하게 소비되며, 주로 민간 소매(약국, 헬스스토어) 시장에서 높은 마진 구조로 유통됩니다. "
            "Omethyl Cutielet은 고용량(2g)을 목넘김이 쉬운 미니 파우치 제형(Seamless Pouch)으로 구현한 독보적 기술력을 가졌으므로, 캡슐을 삼키기 어려운 노인 및 소아 대상의 하이엔드 프리미엄 니치 시장을 정조준해야 합니다."
        ),
        "public_target_usd": 0.50,
        "public_low_usd": 0.125,
        "public_low_rationale": "연하곤란(삼킴 장애) 환자를 위한 의료용 특수 처방(Special Foods) 카테고리로 진입하기 위한 염가 입찰.",
        "public_low_formula": "USD 0.50 × 0.25",
        "public_std_usd": 0.150,
        "public_std_rationale": "공공 병원의 노인 병동 납품을 위한 스탠다드 제안가.",
        "public_std_formula": "USD 0.50 × 0.30",
        "public_premium_usd": 0.200,
        "public_premium_rationale": "일반 캡슐 대비 조제 및 투약 편의성을 인정받아 병원 조달에서 수의계약 형태의 고단가 방어 시.",
        "public_premium_formula": "USD 0.50 × 0.40",
        "private_het_usd": 1.50,
        "private_low_usd": 0.676,
        "private_low_rationale": "초기 시장 침투를 위해 로컬 프리미엄 캡슐(USD 0.50)과 가격 차이를 최소화하는 전략. 파트너사 프로모션 지원을 위해 수출가 양보.",
        "private_low_formula": "(USD 1.50 / 1.15) × 0.72 × 0.90 × 0.8",
        "private_std_usd": 0.845,
        "private_std_rationale": "정규 부가세 15%, 약국 마진 28%, 도매 마진 10%를 모두 보장하며 프리미엄 HET를 유지할 때의 매력적인 수출가.",
        "private_std_formula": "(USD 1.50 / 1.15) × 0.72 × 0.90",
        "private_premium_usd": 1.000,
        "private_premium_rationale": "약국 체인 내 독점 공급(Exclusive Distribution) 조건으로 유통 단계를 축소(도매 마진 배제)하여 제약사가 이익을 흡수하는 최상단 전략.",
        "private_premium_formula": "(USD 1.50 / 1.15) × 0.72 (유통마진 10% 제외 역산)",
        "competitors": [
            {"company": "Good Health", "product": "Omega 3 Fish Oil", "ingredient": "Omega-3 1000mg", "price_usd": 0.075, "market_type": "저가 벌크형"},
            {"company": "Coyne Health", "product": "Purest Omega 3", "ingredient": "Omega-3 1000mg", "price_usd": 0.460, "market_type": "프리미엄 캡슐"},
        ],
        "recommended_buyers": ["aft_pharmaceuticals", "douglas_pharmaceuticals"],
        "sort_order": 5,
    },
    {
        "product_key": "NZ_sereterol_activair",
        "product_name_ko": "세레테롤 액티베어",
        "product_name_en": "Sereterol Activair",
        "inn_components": ["Fluticasone", "Salmeterol"],
        "dosage_form": "건조분말흡입기 (DPI, Activair)",
        "strength": "Fluticasone 250mcg + Salmeterol 50mcg",
        "unit": "인할러",
        "market_type": ["공공 (절대적 우위)", "민간"],
        "primary_market": "공공",
        "base_price_usd": 15.00,
        "base_price_note": "Fluticasone/Salmeterol 복합제 글로벌 평균 및 PHARMAC 호흡기 약가 기반",
        "macro_text": (
            "뉴질랜드는 천식 및 COPD 유병률이 매우 높은 국가로, 호흡기 치료제(특히 ICS+LABA 복합제)는 국가 보건 예산의 주요 지출 항목입니다. "
            "Seretide(Fluticasone+Salmeterol) 복합제는 이미 널리 처방되고 있으나, 당사의 'Activair DPI(건조분말흡입기)' 특허 기술은 디바이스의 사용 편의성과 약물 전달 효율을 높여 오리지널 및 타 제네릭 대비 차별화된 경쟁력을 갖습니다. "
            "흡입기는 의료기기 등록(WAND)이 병행되어야 합니다. 2024~2025년 호흡기 예산 증액(6억 400만 달러 패키지)의 직접 수혜 품목입니다."
        ),
        "public_target_usd": 12.00,
        "public_low_usd": 3.00,
        "public_low_rationale": "경쟁 입찰(Tender)에서 기존 인도/유럽계 제네릭을 밀어내기 위해, 원가 우위를 바탕으로 마진을 최소화한 파괴적 입찰 수출가.",
        "public_low_formula": "USD 12.00 × 0.25",
        "public_std_usd": 3.60,
        "public_std_rationale": "수입 운송비 및 파트너사의 입찰 진행 마진을 20% 이상 보장하는 스탠다드 B2G(정부조달) 수출가.",
        "public_std_formula": "USD 12.00 × 0.30",
        "public_premium_usd": 4.80,
        "public_premium_rationale": "Activair 디바이스의 우수성(약물 잔류량 최소화 등)을 평가받아, 단순 제네릭이 아닌 차세대 디바이스로 별도 프리미엄 단가 책정 시.",
        "public_premium_formula": "USD 12.00 × 0.40",
        "private_het_usd": 25.00,
        "private_low_usd": 11.26,
        "private_low_rationale": "비급여 천식 환자의 구매 부담을 줄이기 위해 현지 파트너와 프로모션 단가 합의 시의 하단 가격.",
        "private_low_formula": "(USD 25.00 / 1.15) × 0.72 × 0.90 × 0.8",
        "private_std_usd": 14.08,
        "private_std_rationale": "GST 15% 제세금 및 약국 28%, 도매 10%의 민간 유통 마진을 걷어낸 순수 제약사 FOB 마진율 모델.",
        "private_std_formula": "(USD 25.00 / 1.15) × 0.72 × 0.90",
        "private_premium_usd": 17.50,
        "private_premium_rationale": "응급/민간 클리닉에 다이렉트(Direct) 영업망을 갖춘 파트너(Douglas 등)와 직거래하여 유통 마진의 누수를 막은 시나리오.",
        "private_premium_formula": "역산 후 제조사 쉐어 비율 극대화 적용",
        "competitors": [
            {"company": "GSK", "product": "Seretide Accuhaler", "ingredient": "Salmeterol 50 + Fluticasone 250", "price_usd": 18.00, "market_type": "글로벌/공공 추정"},
            {"company": "Generic", "product": "Fluticasone+Salmeterol", "ingredient": "동일 성분", "price_usd": 12.00, "market_type": "제네릭 공공 조달가"},
        ],
        "recommended_buyers": ["pharmaco_nz", "douglas_pharmaceuticals"],
        "sort_order": 6,
    },
    {
        "product_key": "NZ_gadvoa_inj",
        "product_name_ko": "가드보아 주사",
        "product_name_en": "Gadvoa Inj.",
        "inn_components": ["Gadobutrol"],
        "dosage_form": "주사제 (바이알/PFS)",
        "strength": "Gadobutrol 604mg/mL",
        "unit": "바이알",
        "market_type": ["공공 병원 (100%)"],
        "primary_market": "공공 병원",
        "base_price_usd": 35.00,
        "base_price_note": "오리지널(Gadovist) 대비 제네릭 조영제의 글로벌 할인율(30~40%) 적용. HS 3006.30 무관세.",
        "macro_text": (
            "Gadobutrol(가도부트롤)은 MRI 촬영에 필수적인 조영제로, 100% 병원 조달 및 입찰 시장(Hospital Market)에 의존합니다. "
            "일반 약국 유통이 불가능하며, 뉴질랜드 보건부(Health NZ) 및 PHARMAC의 병원 의약품 스케줄(HML)에 등재되어 대형 병원에 직납되어야 합니다. "
            "따라서 민간 약국 마진(28%) 역산 로직은 무의미하며, 병원 납품 구조의 공공 로직이 절대적입니다. HS 3006.30 코드 무관세 혜택."
        ),
        "public_target_usd": 35.00,
        "public_low_usd": 8.75,
        "public_low_rationale": "다국적 제약사(Bayer 등)의 시장 장악력을 깨고 Health NZ 산하 대형 국공립 병원의 Bulk Supply Order(BSO)를 전량 수주하기 위한 초저가 덤핑 전략.",
        "public_low_formula": "USD 35.00 × 0.25",
        "public_std_usd": 10.50,
        "public_std_rationale": "수입 운임 및 콜드체인(온도 유지) 물류비를 감당해야 하는 현지 전문 유통사의 병원 직납 마진(WM)을 보장하는 최적의 공급가.",
        "public_std_formula": "USD 35.00 × 0.30",
        "public_premium_usd": 14.00,
        "public_premium_rationale": "당사 PFS(Pre-filled Syringe) 제형의 의료진 사용 편의성 및 감염 방지 우수성을 기술 평가에서 인정받아 고단가 사수 시.",
        "public_premium_formula": "USD 35.00 × 0.40",
        "private_het_usd": 45.00,
        "private_low_usd": 15.00,
        "private_low_rationale": "사립 영상센터 체인과의 대량 계약(Volume Contract) 시 할인을 제공한 수출가 역산.",
        "private_low_formula": "(사립납품가 USD 45) × 0.33 (약국/유통 마진이 없는 직거래 모델 역산)",
        "private_std_usd": 18.00,
        "private_std_rationale": "도매상(WM 10%)만을 거쳐 사립 병원에 납품될 때의 정규 수출 마진 확보 모델.",
        "private_std_formula": "USD 45.00 × 0.40",
        "private_premium_usd": 22.50,
        "private_premium_rationale": "사립 병원의 특성상 오리지널 선호도가 높으나, 오리지널과 동일한 품질을 보증하며 제조사가 약가의 50%를 취하는 프리미엄 직납 시나리오.",
        "private_premium_formula": "USD 45.00 × 0.50 (이익 극대화)",
        "competitors": [
            {"company": "Bayer (오리지널)", "product": "Gadovist", "ingredient": "Gadobutrol 604mg", "price_usd": 50.00, "market_type": "공공 HML 기준가 추정"},
            {"company": "Generic Competitor", "product": "Gadobutrol Generic", "ingredient": "Gadobutrol 동일 성분", "price_usd": 35.00, "market_type": "제네릭 경쟁가 추정"},
        ],
        "recommended_buyers": ["pharmaco_nz"],
        "sort_order": 7,
    },
    {
        "product_key": "NZ_hydrine",
        "product_name_ko": "하이드린",
        "product_name_en": "Hydrine",
        "inn_components": ["Hydroxyurea"],
        "dosage_form": "캡슐",
        "strength": "Hydroxyurea 500mg",
        "unit": "캡슐",
        "market_type": ["공공 (항암제 특수 채널)"],
        "primary_market": "공공",
        "base_price_usd": 1.50,
        "base_price_note": "고부가가치 항암제 제네릭 글로벌 HTA 기준가 적용",
        "macro_text": (
            "유나이티드제약 브로셔에 명시된 뉴질랜드 최우선 타겟 품목입니다. "
            "Hydroxyurea(하이드록시우레아)는 백혈병, 흑색종 등 중증 항암 치료에 쓰이며, 뉴질랜드 PHARMAC의 항암제(Oncology Agents) 예산 증액 혜택을 직접적으로 받을 수 있는 전략 자산입니다. "
            "생명 직결 약물이므로 민간보다는 공공 입찰을 통한 국가 주도 항암 스케줄 등재가 유일무이한 성공 경로입니다."
        ),
        "public_target_usd": 1.50,
        "public_low_usd": 0.375,
        "public_low_rationale": "뉴질랜드의 고질적인 항암제 예산 부족 문제를 해결하는 '비용 절감형 1차 솔루션'으로 포지셔닝하여 PHARMAC의 심사를 초고속으로 통과하기 위한 단가.",
        "public_low_formula": "USD 1.50 × 0.25",
        "public_std_usd": 0.450,
        "public_std_rationale": "위해성 관리(Pharmacovigilance) 및 항암제 특수 물류를 전담할 현지 파트너(Pharmaco 등)의 마진을 충족시키는 안정적 수출가.",
        "public_std_formula": "USD 1.50 × 0.30",
        "public_premium_usd": 0.600,
        "public_premium_rationale": "제네릭 경쟁사가 부재하거나 숏티지(공급 부족)가 발생한 틈을 타, 안정적 공급(Supply security)을 무기로 PHARMAC과 우위 협상 체결 시.",
        "public_premium_formula": "USD 1.50 × 0.40",
        "private_het_usd": 3.00,
        "private_low_usd": 1.350,
        "private_low_rationale": "공공 등재 지연 시, 민간 사보험 가입 환자를 대상으로 제한적 영업을 전개할 때의 할인 침투 가격.",
        "private_low_formula": "(USD 3.00 / 1.15) × 0.72 × 0.90 × 0.8",
        "private_std_usd": 1.690,
        "private_std_rationale": "GST(15%)와 특수 항암 약국 취급 마진(28%) 및 도매 마진(10%)을 제한 역산 수익 모델.",
        "private_std_formula": "(USD 3.00 / 1.15) × 0.72 × 0.90",
        "private_premium_usd": 2.110,
        "private_premium_rationale": "오리지널(Hydrea) 대체 처방 불가 판정 또는 특수 전문의 지정 약물로 선정되어 가격 저항이 0에 수렴할 경우 제약사 이익 극대화.",
        "private_premium_formula": "유통 채널 축소 및 고마진 역산 로직",
        "competitors": [
            {"company": "BMS (오리지널)", "product": "Hydrea", "ingredient": "Hydroxyurea 500mg", "price_usd": 2.50, "market_type": "글로벌 오리지널가 추정"},
            {"company": "Generic", "product": "Hydroxyurea", "ingredient": "Hydroxyurea 500mg", "price_usd": 1.50, "market_type": "공공 조달가 추정"},
        ],
        "recommended_buyers": ["pharmaco_nz", "aft_pharmaceuticals"],
        "sort_order": 8,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: 3개 바이어 기업
# ─────────────────────────────────────────────────────────────────────────────

BUYERS = [
    {
        "company_key": "douglas_pharmaceuticals",
        "company_name_en": "Douglas Pharmaceuticals",
        "company_name_ko": "더글러스 파마슈티컬스",
        "overview": (
            "1967년 설립된 뉴질랜드 최대의 가족 소유(Family-owned) 제약사로, 500명 이상의 직원을 고용하고 "
            "전 세계 50여 개국에 수출하는 기업입니다. 자체 R&D뿐만 아니라 해외 기업의 '브랜드 대표(Brand Representation)' 및 "
            "인라이선싱을 통해 오세아니아 전역에 완제의약품을 공급하는 강력한 밸류체인을 보유하고 있습니다."
        ),
        "founded_year": 1967,
        "employee_count_approx": "약 500명+",
        "company_size_note": "뉴질랜드 최대 로컬 제약사. 전 세계 50여 개국 수출.",
        "address": "Lincoln, Auckland, New Zealand",
        "phone": "+64 9 588 1343",
        "email": "info@douglas.co.nz",
        "website": "www.douglas.co.nz",
        "pipeline_categories": ["505(b)(2) Complex Generics", "Softgels", "CNS", "피부과", "항암", "중추신경계 질환 전문약"],
        "registered_products_note": "글로벌 규제(FDA/EMA 준수) 승인 제네릭 다수, 자체 개발 40여 종 커머셜 라이선스 보유.",
        "reason_1": "파이프라인 적합도 (최상): 505(b)(2) 개량신약과 연질캡슐(Softgels)을 전략적 파이프라인으로 삼고 있어, 당사의 CombiGel(연질캡슐 복합제) 기술과 완벽한 시너지를 냅니다.",
        "reason_2": "강력한 규제 허들 돌파력: 자체 Regulatory Affairs 팀이 Medsafe(NZ) 및 TGA(호주) 등록을 전담하여 외국 기업의 진입 장벽을 완전히 해소합니다.",
        "reason_3": "공공/민간 하이브리드 영업망: 병원 직납, 약국 체인 등 전국적인 세일즈 포스를 갖추고 있어 PHARMAC 스케줄과 민간 시장을 동시 타겟팅할 수 있습니다.",
        "reason_4": "로컬 스폰서 자격: 외투 기업의 Medsafe 신약 신청(NMA)을 위한 법적 대리인 자격을 완벽히 충족합니다.",
        "reason_5": "호주 동시 진출 기회: 멜버른에 거점을 두고 있어 뉴질랜드 등록 후 호주 시장(TGA) 교차 진출을 원스톱으로 진행할 수 있습니다.",
        "recommended_products": ["NZ_rosumeg_combigel", "NZ_atmeg_combigel", "NZ_ciloduo", "NZ_sereterol_activair"],
        "listed_exchange": None,
        "sources": ["Douglas 공식 홈페이지", "New Zealand Healthtech Report 2024"],
        "sort_order": 1,
    },
    {
        "company_key": "aft_pharmaceuticals",
        "company_name_en": "AFT Pharmaceuticals",
        "company_name_ko": "AFT 파마슈티컬스",
        "overview": (
            "뉴질랜드 오클랜드에 본사를 둔 글로벌 진출 지향형 제약사(NZX, ASX 상장)로, "
            "자체 개발한 복합 진통제 Maxigesic의 글로벌 라이선싱 성공으로 자금력과 유통망이 매우 탄탄합니다. "
            "독자 개발 외에도 아태지역에 특화된 해외 혁신 의약품을 적극적으로 인라이선싱(In-licensing)하고 있습니다."
        ),
        "founded_year": 1997,
        "employee_count_approx": "상장사 (최근 강력한 실적 호조)",
        "company_size_note": "NZX·ASX 상장. 시가총액 및 매출 기준 최상위권.",
        "address": "Takapuna, Auckland, New Zealand",
        "phone": "+64 9 488 0232",
        "email": "customer.service@aftpharm.com",
        "website": "www.aftpharm.com",
        "pipeline_categories": ["복합진통제(Maxigesic)", "알레르기", "위장", "심혈관", "특수 제형 영양제(Lipo-Sachets)", "OTC", "병원용 특수약"],
        "registered_products_note": "Maxigesic 계열(정제, IV 등), Allersoothe, Opti-Soothe 등 전문/일반 100여 종.",
        "reason_1": "파이프라인 적합도 (혁신 제형 특화): 리포좀 파우치(Liposachet) 등 혁신 전달 기술(Delivery system)에 관심이 높아 당사의 Omethyl Cutielet(Seamless Pouch) 도입에 최적의 바이어입니다.",
        "reason_2": "복합제 마케팅의 달인: 이부프로펜+파라세타몰 복합제(Maxigesic)로 세계 시장을 뚫은 경험이 있어, 두 가지 이상의 성분이 섞인 당사 복합제(Ciloduo, Rosumeg)의 임상적 가치를 PHARMAC에 가장 잘 소명할 수 있습니다.",
        "reason_3": "막강한 자금력: 최근 주식 시장을 통한 대규모 자본 조달로 해외 포트폴리오 매입 능력이 우수합니다.",
        "reason_4": "다양한 적응증 포트폴리오: 처방약(Rx)과 일반약(OTC) 라인업을 고루 갖추어 유나이티드제약의 모든 파이프라인 소화가 가능합니다.",
        "reason_5": "아시아 허브 역할: 뉴질랜드를 넘어 싱가포르 등 동남아 거점까지 유통할 수 있는 네트워크를 보유하고 있습니다.",
        "recommended_products": ["NZ_omethyl_cutielet", "NZ_ciloduo", "NZ_rosumeg_combigel", "NZ_atmeg_combigel"],
        "listed_exchange": "NZX,ASX",
        "sources": ["AFT Pharmaceuticals 연례 보고서", "NZX 공시 자료"],
        "sort_order": 2,
    },
    {
        "company_key": "pharmaco_nz",
        "company_name_en": "Pharmaco (NZ) Ltd",
        "company_name_ko": "파마코 (뉴질랜드)",
        "overview": (
            "뉴질랜드 기반의 의약품 전문 수입, 마케팅 및 유통 대행 스폰서 기업으로 50여 년간 운영되어 왔습니다. "
            "전 세계 20여 개 핵심 제약사 및 의료기기 업체의 뉴질랜드 법인 역할을 완벽히 대행하며 "
            "규제부터 영업까지 턴키(Turn-key) 솔루션을 제공합니다."
        ),
        "founded_year": 1965,
        "employee_count_approx": "약 50~100명",
        "company_size_note": "고효율 B2B 의료 유통 전문 기업. ISO 9001 인증.",
        "address": "Mt Wellington, Auckland, New Zealand",
        "phone": "+64 9 527 1800",
        "email": "info@pharmaco.co.nz",
        "website": "www.pharmaco.co.nz",
        "pipeline_categories": ["항암제", "조영제", "호흡기계", "구급의약품", "특수 의료기기 수입"],
        "registered_products_note": "유럽/미국 파트너사들의 전문의약품 및 진단 기기 독점 유통.",
        "reason_1": "파이프라인 적합도 (전문의약품 직납): 조영제, 항암제, 호흡기 의료기기를 전문적으로 취급하는 부서가 있어, 1순위 타겟인 Hydrine(항암)과 Gadvoa Inj.(조영제)의 HML(병원 스케줄) 등재 및 직납에 가장 강력한 파트너입니다.",
        "reason_2": "디바이스 취급 역량: 약물뿐만 아니라 의료기기 유통 역량도 갖추고 있어, WAND 등록이 필수적인 호흡기 디바이스(Sereterol Activair) 유통의 최적임자입니다.",
        "reason_3": "리스크 없는 스폰서 전담: 제조사가 아닌 수입 대행에 특화되어 있어, 자사 제품(자체 생산품)과의 카니발라이제이션(Cannibalization) 우려 없이 온전히 유나이티드제약의 대리인으로만 움직입니다.",
        "reason_4": "강력한 규제 대응력: 약사와 RA 스페셜리스트로 구성된 내부 팀이 Medsafe 신약 허가 신청을 완벽히 대행합니다.",
        "reason_5": "물류 및 품질(ISO 9001): 엄격한 온도 관리가 필요한 조영제 등 고난도 의약품의 창고 및 유통 관리에 ISO 9001 인증을 받은 역량을 제공합니다.",
        "recommended_products": ["NZ_hydrine", "NZ_gadvoa_inj", "NZ_sereterol_activair"],
        "listed_exchange": None,
        "sources": ["Pharmaco 공식 홈페이지", "Medical Supply Directory"],
        "sort_order": 3,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# 적재 실행
# ─────────────────────────────────────────────────────────────────────────────

def upsert_macro():
    print("\n▶ [Section 1] nz_market_macro 적재 중...")
    resp = sb.table("nz_market_macro").upsert(MACRO_DATA, on_conflict="id").execute()
    print(f"  ✅ 완료: {len(resp.data) if resp.data else 0} 행")


def upsert_price_strategy():
    print("\n▶ [Section 2] nz_price_strategy 적재 중 (8개 제품)...")
    for p in PRODUCTS:
        row = {**p, "competitors": json.dumps(p["competitors"], ensure_ascii=False)}
        resp = sb.table("nz_price_strategy").upsert(row, on_conflict="product_key").execute()
        status = "✅" if resp.data else "⚠️"
        print(f"  {status} {p['product_key']}")


def upsert_buyers():
    print("\n▶ [Section 3] nz_buyers 적재 중 (3개 바이어)...")
    for b in BUYERS:
        resp = sb.table("nz_buyers").upsert(b, on_conflict="company_key").execute()
        status = "✅" if resp.data else "⚠️"
        print(f"  {status} {b['company_key']}")


if __name__ == "__main__":
    upsert_macro()
    upsert_price_strategy()
    upsert_buyers()
    print("\n🎉 모든 데이터 적재 완료!")
