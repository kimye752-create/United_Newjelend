-- =============================================================================
-- NZ 시장 데이터 전용 테이블 (거시환경 / 가격전략 / 바이어)
-- Supabase SQL Editor에서 실행
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. NZ 거시 시장 환경 정보 (Section 1)
-- ─────────────────────────────────────────────────────────────────────────────
create table if not exists nz_market_macro (
  id                          serial primary key,
  -- 시장 규모
  market_size_usd_bn          numeric(6,2)   default 1.00,  -- 10억 USD
  market_size_note            text,                           -- 부연 설명
  growth_drivers              text[],                         -- 성장 동인 키워드
  -- 구매 체계 (PHARMAC)
  pharmac_system              text,                           -- 'Monopsony'
  pharmac_description         text,                           -- 설명 텍스트
  pharmac_budget_note         text,                           -- 최근 예산 동향
  -- 유통 구조
  distributor_major           text[],                         -- ['EBOS Group', 'CDC Pharmaceuticals']
  distributor_margin_pct_low  numeric(5,2),                   -- 3%
  distributor_margin_pct_high numeric(5,2),                   -- 10%
  distributor_note            text,
  -- Medsafe 등록 절차
  medsafe_sponsor_required    boolean default true,
  medsafe_standard_fee_nzd    numeric(10,2) default 53251.00,
  medsafe_standard_days       int default 200,
  medsafe_standard_note       text,
  medsafe_verification_days   int default 30,
  medsafe_verification_note   text,
  medsafe_device_note         text,                           -- WAND 관련
  -- FOB 공공 시장 역산 로직
  fob_public_formula          text,                           -- 'target_bid × factor'
  fob_public_factor_low       numeric(5,4) default 0.25,
  fob_public_factor_std       numeric(5,4) default 0.30,
  fob_public_factor_high      numeric(5,4) default 0.40,
  fob_public_note             text,
  -- FOB 민간 시장 역산 로직
  fob_private_formula         text,                           -- 'HET / 1.15 × 0.72 × 0.90 × [0.8]'
  fob_private_gst_pct         numeric(5,4) default 0.15,     -- GST 15%
  fob_private_pharmacy_margin numeric(5,4) default 0.28,     -- 약국 마진 28%
  fob_private_dist_margin     numeric(5,4) default 0.10,     -- 유통 마진 10%
  fob_private_discount        numeric(5,4) default 0.80,     -- 침투 할인 계수
  fob_private_note            text,
  -- 환율
  exchange_rate_usd_nzd       numeric(8,4) default 1.65,     -- 1 USD = 1.65 NZD
  -- 메타
  report_section              text default '1',
  updated_at                  timestamptz not null default now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 11. 제품별 가격 전략 (Section 2 — 8 products)
-- ─────────────────────────────────────────────────────────────────────────────
create table if not exists nz_price_strategy (
  id                          serial primary key,
  -- 제품 식별
  product_key                 text not null unique,           -- 'NZ_rosumeg_combigel'
  product_name_ko             text not null,                  -- '로수메그 콤비겔'
  product_name_en             text not null,                  -- 'Rosumeg Combigel'
  inn_components              text[],                         -- ['Rosuvastatin', 'Omega-3']
  dosage_form                 text,                           -- '연질캡슐(CombiGel)'
  strength                    text,                           -- 'Rosuvastatin 10mg + Omega-3 1000mg'
  unit                        text default '정',              -- '정' / '바이알' / '인할러' / '포'
  -- 시장 구분
  market_type                 text[],                         -- ['공공', '민간']
  primary_market              text,                           -- '공공' | '민간' | '공공 병원'
  -- 기준 단가
  base_price_usd              numeric(10,4),                  -- USD 0.45
  base_price_note             text,                           -- 산정 방식 설명
  -- 거시 시장 텍스트 (보고서 Section 4-0)
  macro_text                  text,
  -- 공공 시장 시나리오
  public_target_usd           numeric(10,4),                  -- 타겟 기준가
  public_low_usd              numeric(10,4),                  -- 저가 진입
  public_low_rationale        text,
  public_low_formula          text,
  public_std_usd              numeric(10,4),                  -- 기준가 유지
  public_std_rationale        text,
  public_std_formula          text,
  public_premium_usd          numeric(10,4),                  -- 프리미엄
  public_premium_rationale    text,
  public_premium_formula      text,
  -- 민간 시장 시나리오
  private_het_usd             numeric(10,4),                  -- 소비자가(HET)
  private_low_usd             numeric(10,4),
  private_low_rationale       text,
  private_low_formula         text,
  private_std_usd             numeric(10,4),
  private_std_rationale       text,
  private_std_formula         text,
  private_premium_usd         numeric(10,4),
  private_premium_rationale   text,
  private_premium_formula     text,
  -- 경쟁사 참고 가격 (jsonb 배열: [{company, product, ingredient, price_usd, market_type}])
  competitors                 jsonb default '[]'::jsonb,
  -- 추천 바이어
  recommended_buyers          text[],                         -- ['Douglas', 'AFT']
  -- 보고서 관련
  report_section              text default '2',
  sort_order                  int default 0,
  updated_at                  timestamptz not null default now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 12. NZ 바이어 기업 정보 (Section 3)
-- ─────────────────────────────────────────────────────────────────────────────
create table if not exists nz_buyers (
  id                          serial primary key,
  -- 기업 식별
  company_key                 text not null unique,           -- 'douglas_pharmaceuticals'
  company_name_en             text not null,                  -- 'Douglas Pharmaceuticals'
  company_name_ko             text,                           -- '더글러스 파마슈티컬스'
  -- 기업 개요
  overview                    text,                           -- 기업 개요 전문
  founded_year                int,                            -- 1967
  employee_count_approx       text,                           -- '약 500명'
  company_size_note           text,                           -- '뉴질랜드 최대 로컬 제약사'
  -- 연락처
  address                     text,                           -- 'Lincoln, Auckland, NZ'
  phone                       text,                           -- '+64 9 588 1343'
  email                       text,                           -- 'info@douglas.co.nz'
  website                     text,                           -- 'www.douglas.co.nz'
  -- 파이프라인 / 취급 제품군
  pipeline_categories         text[],                         -- ['505(b)(2)', 'Softgels', 'CNS']
  registered_products_note    text,
  -- 추천 이유 ①~⑤
  reason_1                    text,
  reason_2                    text,
  reason_3                    text,
  reason_4                    text,
  reason_5                    text,
  -- 추천 제품 (당사 파이프라인 연결)
  recommended_products        text[],                         -- ['NZ_rosumeg_combigel', 'NZ_atmeg_combigel']
  -- 상장 여부
  listed_exchange             text,                           -- 'NZX,ASX' | null
  -- 정보 출처
  sources                     text[],
  -- 보고서 관련
  report_section              text default '3',
  sort_order                  int default 0,
  updated_at                  timestamptz not null default now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 인덱스
-- ─────────────────────────────────────────────────────────────────────────────
create index if not exists idx_nz_price_strategy_key    on nz_price_strategy(product_key);
create index if not exists idx_nz_price_strategy_market on nz_price_strategy(primary_market);
create index if not exists idx_nz_buyers_key            on nz_buyers(company_key);

-- ─────────────────────────────────────────────────────────────────────────────
-- Row Level Security
-- ─────────────────────────────────────────────────────────────────────────────
alter table nz_market_macro   enable row level security;
alter table nz_price_strategy enable row level security;
alter table nz_buyers         enable row level security;

create policy "nz_market_macro_select"   on nz_market_macro   for select using (true);
create policy "nz_price_strategy_select" on nz_price_strategy for select using (true);
create policy "nz_buyers_select"         on nz_buyers         for select using (true);
