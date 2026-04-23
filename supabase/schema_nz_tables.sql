-- =============================================================================
-- NZ 전용 보조 테이블 (Supabase SQL Editor에서 한 번 실행)
-- team_schema.md의 products/sources 등 공통 테이블은 이미 생성된 상태
-- =============================================================================

-- 1. 품목별 분석 컨텍스트 (PHARMAC + Medsafe + GETS 캐시)
create table if not exists nz_product_context (
  id                    uuid primary key default gen_random_uuid(),
  product_id            text not null unique,
  -- 공통 6 컬럼 (team_schema.md 기준)
  market_segment        text default 'NZ',
  fob_estimated_usd     numeric(14,4),
  confidence            numeric(5,2),
  crawled_at            timestamptz,
  -- Medsafe 관련
  medsafe_matches       jsonb default '[]'::jsonb,
  medsafe_registered    boolean default false,
  medsafe_consent_no    text,
  prescription_only     boolean default true,
  -- PHARMAC 관련
  pharmac_funded        boolean default false,
  pharmac_subsidy_nzd   numeric(14,4),
  pharmac_item_code     text,
  pharmac_subsidy_type  text,
  -- GETS 조달 이력
  gets_procurement      jsonb default '[]'::jsonb,
  gets_latest_value_nzd numeric(14,4),
  -- 기타
  competitor_count      int default 0,
  pdf_snippets          jsonb default '[]'::jsonb,
  brochure_snippets     jsonb default '[]'::jsonb,
  regulatory_summary    text default '',
  built_at              timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

-- 2. PHARMAC 의약품 급여 스케줄 (schedule.pharmac.govt.nz XML)
create table if not exists nz_pharmac_schedule (
  id                    bigserial primary key,
  item_code             text unique,
  inn_name              text not null,
  brand_name            text,
  pack_description      text,
  strength              text,
  dosage_form           text,
  subsidy_nzd           numeric(14,4),
  manufacturer_price_nzd numeric(14,4),
  hospital_price_nzd    numeric(14,4),
  subsidy_type          text,   -- 'S' = Subsidised, 'P' = Partly, 'H' = Hospital
  funded                boolean default false,
  atc_code              text,
  section               text,
  schedule_date         date,
  raw_payload           jsonb,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

-- 3. GETS 조달 공고 / 낙찰 정보
create table if not exists nz_gets_tenders (
  id                    bigserial primary key,
  reference_number      text unique,
  description           text not null,
  agency                text,                 -- Health NZ / PHARMAC / MoH 등
  supplier              text,
  value_nzd             numeric(18,2),
  contract_start        date,
  contract_end          date,
  award_date            date,
  category              text,
  inn_name              text,                 -- 파싱된 INN
  raw_payload           jsonb,
  created_at            timestamptz not null default now()
);

-- 4. 세계 인구 데이터 (World Bank)
create table if not exists nz_world_population (
  id                    bigserial primary key,
  country_name          text not null,
  country_code          text not null,        -- 'NZL' 등 ISO 3166-1 alpha-3
  year                  int not null,
  population            bigint,
  created_at            timestamptz not null default now(),
  unique(country_code, year)
);

-- 5. 보건 지출 데이터 (UN SYB / WHO GHED)
create table if not exists nz_health_expenditure (
  id                    bigserial primary key,
  country_or_area       text not null,        -- 'New Zealand'
  year                  int not null,
  series                text not null,        -- 예: 'Current health expenditure (CHE) as % of GDP'
  value                 numeric(20,6),
  footnotes             text,
  source                text,
  created_at            timestamptz not null default now()
);

-- 6. Medsafe 허가 품목 (medsafe.govt.nz)
create table if not exists nz_medsafe_consents (
  id                    bigserial primary key,
  consent_number        text unique,
  product_name          text not null,
  inn_name              text,
  sponsor               text,
  dosage_form           text,
  strength              text,
  approval_date         date,
  expiry_date           date,
  prescription_status   text,  -- 'Prescription Medicine' / 'Pharmacy Medicine' / 'General Sale'
  atc_code              text,
  raw_payload           jsonb,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

-- 7. NZ 소매 약국 크롤링 결과 (Chemist Warehouse NZ / Life Pharmacy / Net Pharmacy / Bargain Chemist)
create table if not exists nz_retail_prices (
  id                    uuid primary key default gen_random_uuid(),
  -- 공통 6 컬럼 (team_schema.md 기준)
  product_id            text,
  market_segment        text default 'NZ',
  fob_estimated_usd     numeric(14,4),
  confidence            numeric(5,2),
  crawled_at            timestamptz not null default now(),
  -- 소매가 필드
  inn_name              text,
  product_name          text not null,
  price_nzd             numeric(14,4),
  price_per_unit_nzd    numeric(14,4),
  promo_price_nzd       numeric(14,4),
  savings_pct           numeric(6,2),
  pack_size             text,
  strength              text,
  dosage_form           text,
  source_site           text not null,  -- 'chemistwarehouse_nz' | 'lifepharmacy' | 'netpharmacy' | 'bargainchemist'
  source_url            text,
  raw_payload           jsonb
);

-- 8. PDF 문서 메타데이터 (Supabase Storage)
create table if not exists nz_documents (
  id                    uuid primary key default gen_random_uuid(),
  filename              text not null unique,
  storage_path          text not null,
  bucket                text not null default 'nz-documents',
  category              text check (category in
    ('regulation','brochure','paper','report','market','strategy')),
  product_id            text,
  label                 text,
  file_size_bytes       bigint,
  created_at            timestamptz not null default now()
);

-- 9. WHO GHED 보건 지출 상세
create table if not exists nz_ghed_expenditure (
  id                    bigserial primary key,
  country               text not null,
  country_code          text,
  year                  int not null,
  indicator_code        text not null,
  indicator_name        text,
  value                 numeric(20,6),
  created_at            timestamptz not null default now(),
  unique(country_code, year, indicator_code)
);

-- =============================================================================
-- 인덱스
-- =============================================================================
create index if not exists idx_nz_product_context_pid       on nz_product_context(product_id);
create index if not exists idx_nz_product_context_funded    on nz_product_context(pharmac_funded);
create index if not exists idx_nz_pharmac_schedule_inn      on nz_pharmac_schedule(inn_name);
create index if not exists idx_nz_pharmac_schedule_code     on nz_pharmac_schedule(item_code);
create index if not exists idx_nz_pharmac_schedule_atc      on nz_pharmac_schedule(atc_code);
create index if not exists idx_nz_gets_tenders_inn          on nz_gets_tenders(inn_name);
create index if not exists idx_nz_gets_tenders_agency       on nz_gets_tenders(agency);
create index if not exists idx_nz_gets_tenders_award        on nz_gets_tenders(award_date);
create index if not exists idx_nz_world_pop_code_year       on nz_world_population(country_code, year);
create index if not exists idx_nz_health_exp_country        on nz_health_expenditure(country_or_area, year);
create index if not exists idx_nz_medsafe_consents_inn      on nz_medsafe_consents(inn_name);
create index if not exists idx_nz_medsafe_consents_num      on nz_medsafe_consents(consent_number);
create index if not exists idx_nz_retail_prices_inn         on nz_retail_prices(inn_name);
create index if not exists idx_nz_retail_prices_site        on nz_retail_prices(source_site);
create index if not exists idx_nz_retail_prices_pid         on nz_retail_prices(product_id);
create index if not exists idx_nz_retail_prices_crawled     on nz_retail_prices(crawled_at);
create index if not exists idx_nz_ghed_code_year            on nz_ghed_expenditure(country_code, year);
create index if not exists idx_nz_documents_category        on nz_documents(category);
create index if not exists idx_nz_documents_product         on nz_documents(product_id);

-- =============================================================================
-- Row Level Security (RLS) — 기본 읽기 허용, 쓰기는 service_role
-- =============================================================================
alter table nz_product_context    enable row level security;
alter table nz_pharmac_schedule   enable row level security;
alter table nz_gets_tenders       enable row level security;
alter table nz_world_population   enable row level security;
alter table nz_health_expenditure enable row level security;
alter table nz_medsafe_consents   enable row level security;
alter table nz_retail_prices      enable row level security;
alter table nz_documents          enable row level security;
alter table nz_ghed_expenditure   enable row level security;

-- anon / authenticated: SELECT 허용
create policy "nz_product_context_select"    on nz_product_context    for select using (true);
create policy "nz_pharmac_schedule_select"   on nz_pharmac_schedule   for select using (true);
create policy "nz_gets_tenders_select"       on nz_gets_tenders       for select using (true);
create policy "nz_world_population_select"   on nz_world_population   for select using (true);
create policy "nz_health_expenditure_select" on nz_health_expenditure for select using (true);
create policy "nz_medsafe_consents_select"   on nz_medsafe_consents   for select using (true);
create policy "nz_retail_prices_select"      on nz_retail_prices      for select using (true);
create policy "nz_documents_select"          on nz_documents          for select using (true);
create policy "nz_ghed_expenditure_select"   on nz_ghed_expenditure   for select using (true);
