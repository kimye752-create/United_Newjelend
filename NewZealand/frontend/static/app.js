/**
 * UPharma Export AI — 싱가포르 대시보드 스크립트
 * ═══════════════════════════════════════════════════════════════
 *
 * 기능 목록:
 *   §1  상수 & 전역 상태
 *   §2  탭 전환          goTab(id, el)
 *   §3  환율 로드        loadExchange()  → GET /api/exchange
 *   §4  To-Do 리스트     initTodo / toggleTodo / markTodoDone / addTodoItem
 *   §5  보고서 탭        renderReportTab / _addReportEntry
 *   §6  API 키 배지      loadKeyStatus() → GET /api/keys/status
 *   §7  진행 단계        setProgress / resetProgress
 *   §8  파이프라인       runPipeline / pollPipeline
 *   §9  신약 분석        runCustomPipeline / _pollCustomPipeline
 *   §10 결과 렌더링      renderResult
 *   §11 초기화
 *
 * 수정 이력 (원본 대비):
 *   B1  /api/sites 제거 → /api/datasource/status
 *   B2  크롤링 step → DB 조회 step (prog-db_load)
 *   B3  refreshOutlier → /api/analyze/result
 *   B4  논문 카드: refs 0건이면 숨김
 *   U1  API 키 상태 배지
 *   U2  진입 경로(entry_pathway) 표시
 *   U3  신뢰도(confidence_note) 표시
 *   U4  PDF 카드 3가지 상태
 *   U6  재분석 버튼
 *   N1  탭 전환 (AU 프론트 기반)
 *   N2  환율 카드 (yfinance NZD/KRW)
 *   N3  To-Do 리스트 (localStorage)
 *   N4  보고서 탭 자동 등록
 * ═══════════════════════════════════════════════════════════════
 */

'use strict';

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §1. 상수 & 전역 상태
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/** product_id → INN 표시명 */
const INN_MAP = {
  NZ_cilostazol_cr_200:         'Cilostazol 200mg SR',
  NZ_hydrine_hydroxyurea_500:  'Hydroxyurea 500mg',
  NZ_gadvoa_gadobutrol_604:    'Gadobutrol 604mg',
  NZ_sereterol_activair:       'Fluticasone / Salmeterol',
  NZ_omethyl_omega3_2g:        'Omega-3 EE 2g',
  NZ_rosumeg_combigel:         'Rosuvastatin + Omega-3',
  NZ_atmeg_combigel:           'Atorvastatin + Omega-3',
  NZ_ciloduo_cilosta_rosuva:   'Cilostazol + Rosuvastatin',
  NZ_gastiin_cr_mosapride:     'Mosapride CR',
};

/**
 * B2: 서버 step 이름 → 프론트 progress 단계 ID 매핑
 * 서버 step: init → db_load → analyze → refs → report → done
 */
const STEP_ORDER = ['db_load', 'analyze', 'refs', 'report'];

let _pollTimer  = null;   // 파이프라인 폴링 타이머
let _currentKey = null;   // 현재 선택된 product_key

// P2 3열 시나리오용 원본 데이터 (시장별)
let _p2ScenarioRawByMarket = {
  public:  { agg: 0, avg: 0, cons: 0, sgd_usd: 0, sgd_krw: 0 },
  private: { agg: 0, avg: 0, cons: 0, sgd_usd: 0, sgd_krw: 0 },
};
let _p2ScenarioRaw = _p2ScenarioRawByMarket['public'];

// P2 컬럼별 커스텀 옵션 데이터 (시장별)
let _p2ColDataByMarket = {
  public:  { agg: { opts: [] }, avg: { opts: [] }, cons: { opts: [] } },
  private: { agg: { opts: [] }, avg: { opts: [] }, cons: { opts: [] } },
};
let _p2ColData = _p2ColDataByMarket['public'];

// 시장별 AI 결과 캐시
let _p2PublicResult  = null;
let _p2PrivateResult = null;

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §2. 탭 전환 (N1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * 탭 전환: 모든 .page / .tab 비활성 후 대상만 활성화.
 * @param {string} id  — 대상 페이지 element ID
 * @param {Element} el — 클릭된 탭 element
 */
function goTab(id, el) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('on'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
  const page = document.getElementById(id);
  if (page) page.classList.add('on');
  if (el)   el.classList.add('on');
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §2-b. 공정 섹션 토글
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

const _processOpen = { p1: true, p2: true, p3: true };

function toggleProcess(id) {
  _processOpen[id] = !_processOpen[id];
  const body  = document.getElementById('pb-' + id);
  const arrow = document.getElementById('pa-' + id);
  if (body)  body.classList.toggle('hidden', !_processOpen[id]);
  if (arrow) arrow.classList.toggle('closed', !_processOpen[id]);
}

/* 신약 직접 분석 폼 토글 */
function toggleCustomForm() {
  const wrap = document.getElementById('custom-form-wrap');
  const btn  = document.getElementById('btn-custom-toggle');
  if (!wrap) return;
  const open = wrap.style.display === 'none';
  wrap.style.display = open ? '' : 'none';
  if (btn) btn.textContent = (open ? '▾' : '▸') + ' 신약 직접 분석';
}

/* 1공정 간단 로딩 표시 */
function _showP1Loading() {
  const el = document.getElementById('p1-loading-state');
  if (el) el.style.display = 'flex';
}
function _hideP1Loading() {
  const el = document.getElementById('p1-loading-state');
  if (el) el.style.display = 'none';
}
function _showCustomLoading() {
  const el = document.getElementById('custom-loading-state');
  if (el) el.style.display = 'flex';
}
function _hideCustomLoading() {
  const el = document.getElementById('custom-loading-state');
  if (el) el.style.display = 'none';
}
function _showP2Loading() {
  const el = document.getElementById('p2-loading-state');
  if (el) el.style.display = 'flex';
}
function _hideP2Loading() {
  const el = document.getElementById('p2-loading-state');
  if (el) el.style.display = 'none';
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §2-c. 거시 지표 로드 — GET /api/macro
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

async function loadMacro() {
  try {
    const res  = await fetch('/api/macro');
    const data = await res.json();

    _setMacro('macro-gdp',        data.gdp_per_capita      || 'US$ 48,800', 'macro-gdp-src',    (data.gdp_source      || 'World Bank').replace(/^\d{4}\s*·\s*/, ''));
    _setMacro('macro-pop',        data.population          || '5.13M명',  'macro-pop-src',    (data.pop_source      || 'Stats NZ').replace(/^\d{4}\s*·\s*/, ''));
    _setMacro('macro-pharma',     data.pharma_market       || '$1.26B', 'macro-pharma-src', (data.pharma_source   || 'Statista').replace(/^\d{4}\s*·\s*/, ''));
    _setMacro('macro-growth',     data.real_growth         || '~90%',     'macro-growth-src', (data.growth_source   || 'KOTRA / ITA').replace(/^\d{4}\s*·\s*/, ''));
  } catch (_) {
    // 폴백: 정적 데이터 표시
    _setMacro('macro-gdp',    'US$ 48,800', 'macro-gdp-src',    'World Bank');
    _setMacro('macro-pop',    '5.13M명',  'macro-pop-src',    'Stats NZ');
    _setMacro('macro-pharma', '$1.26B', 'macro-pharma-src', 'Statista');
    _setMacro('macro-growth', '~90%',     'macro-growth-src', 'KOTRA / ITA');
  }
}

function _setMacro(valId, val, srcId, src) {
  const ve = document.getElementById(valId);
  const se = document.getElementById(srcId);
  if (ve) ve.textContent = val;
  if (se) se.textContent = src;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §3. 환율 로드 (N2) — GET /api/exchange
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

async function loadExchange() {
  const btn = document.getElementById('btn-exchange-refresh');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 조회 중…'; }

  try {
    const res  = await fetch('/api/exchange');
    const data = await res.json();

    // P2 환율 자동 채움용 전역 저장
    window._exchangeRates = data;
    if (typeof _p2FillExchangeRate === 'function') {
      _p2FillExchangeRate();
      if (typeof _renderP2Manual === 'function') _renderP2Manual();
    }

    // 메인 숫자 (KRW/NZD)
    const rateEl = document.getElementById('exchange-main-rate');
    if (rateEl) {
      const fmt = data.sgd_krw.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      rateEl.innerHTML = `${fmt}<span style="font-size:14px;margin-left:4px;color:var(--muted);font-weight:700;">원</span>`;
    }

    // 서브 그리드 (USD/KRW + NZD 연관 환율)
    const subEl = document.getElementById('exchange-sub');
    if (subEl) {
      const fmtUsd = data.usd_krw.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      const fmtSgdUsd = Number(data.sgd_usd).toFixed(4);
      const fmtSgdJpy = Number(data.sgd_jpy || data.nzd_jpy || 0).toFixed(4);
      const fmtSgdCny = Number(data.sgd_cny || data.nzd_aud || 0).toFixed(4);
      subEl.innerHTML = `
        <div class="irow" style="margin:0">
          <div style="font-size:10.5px;color:var(--muted);margin-bottom:3px;">USD / KRW</div>
          <div style="font-size:15px;font-weight:900;color:var(--navy);">${fmtUsd}원</div>
        </div>
        <div class="irow" style="margin:0">
          <div style="font-size:10.5px;color:var(--muted);margin-bottom:3px;">NZD / USD</div>
          <div style="font-size:15px;font-weight:900;color:var(--navy);">${fmtSgdUsd}</div>
        </div>
        <div class="irow" style="margin:0">
          <div style="font-size:10.5px;color:var(--muted);margin-bottom:3px;">NZD / JPY</div>
          <div style="font-size:15px;font-weight:900;color:var(--navy);">${fmtSgdJpy}</div>
        </div>
        <div class="irow" style="margin:0">
          <div style="font-size:10.5px;color:var(--muted);margin-bottom:3px;">NZD / AUD</div>
          <div style="font-size:15px;font-weight:900;color:var(--navy);">${fmtSgdCny}</div>
        </div>
      `;
    }

    // 출처 + 조회 시각
    const srcEl = document.getElementById('exchange-source');
    if (srcEl) {
      const now = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
      const fallbackNote = data.ok ? '' : ' · 폴백값';
      srcEl.textContent = `조회: ${now}${fallbackNote}`;
    }
  } catch (e) {
    const srcEl = document.getElementById('exchange-source');
    if (srcEl) srcEl.textContent = '환율 조회 실패 — 잠시 후 다시 시도해 주세요';
    console.warn('환율 로드 실패:', e);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↺ 환율 새로고침'; }
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §4. To-Do 리스트 (N3)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

const TODO_FIXED_IDS = ['p1', 'rep', 'p2', 'p3'];
const TODO_LS_KEY    = 'nz_upharma_todos_v1';
let _lastTodoAddAt   = 0;

/** localStorage에서 todo 상태 읽기 */
function _loadTodoState() {
  try   { return JSON.parse(localStorage.getItem(TODO_LS_KEY) || '{}'); }
  catch { return {}; }
}

/** localStorage에 todo 상태 쓰기 */
function _saveTodoState(state) {
  localStorage.setItem(TODO_LS_KEY, JSON.stringify(state));
}

/** 페이지 로드 시 localStorage 상태 복원 */
function initTodo() {
  const state = _loadTodoState();

  // 고정 항목 상태 복원
  for (const id of TODO_FIXED_IDS) {
    const item = document.getElementById('todo-' + id);
    if (!item) continue;
    item.classList.toggle('done', !!state['fixed_' + id]);
  }

  // 커스텀 항목 렌더
  _renderCustomTodos(state);
}

/**
 * 고정 항목 수동 토글 (클릭 시 호출).
 * @param {string} id  'p1' | 'rep' | 'p2' | 'p3'
 */
function toggleTodo(id) {
  const state       = _loadTodoState();
  const key         = 'fixed_' + id;
  state[key]        = !state[key];
  _saveTodoState(state);

  const item = document.getElementById('todo-' + id);
  if (item) item.classList.toggle('done', state[key]);
}

/**
 * 자동 체크: 파이프라인·보고서 완료 시 호출 (N3).
 * @param {'p1'|'rep'} id
 */
function markTodoDone(id) {
  const state       = _loadTodoState();
  state['fixed_' + id] = true;
  _saveTodoState(state);

  const item = document.getElementById('todo-' + id);
  if (item) item.classList.add('done');
}

/** 사용자가 직접 항목 추가 */
function addTodoItem(evt) {
  if (evt) {
    if (evt.isComposing || evt.repeat) return;
    evt.preventDefault();
  }

  const now = Date.now();
  if (now - _lastTodoAddAt < 250) return;
  _lastTodoAddAt = now;

  const input = document.getElementById('todo-input');
  const text  = input ? input.value.trim() : '';
  if (!text) return;

  const state   = _loadTodoState();
  const customs = state.customs || [];
  customs.push({ id: now + Math.floor(Math.random() * 1000), text, done: false });
  state.customs = customs;
  _saveTodoState(state);
  _renderCustomTodos(state);
  if (input) input.value = '';
}

/** 커스텀 항목 토글 */
function toggleCustomTodo(id) {
  const state   = _loadTodoState();
  const customs = state.customs || [];
  const item    = customs.find(c => c.id === id);
  if (item) item.done = !item.done;
  state.customs = customs;
  _saveTodoState(state);
  _renderCustomTodos(state);
}

/** 커스텀 항목 삭제 */
function deleteCustomTodo(id) {
  const state   = _loadTodoState();
  state.customs = (state.customs || []).filter(c => c.id !== id);
  _saveTodoState(state);
  _renderCustomTodos(state);
}

/** 커스텀 항목 목록 DOM 갱신 */
function _renderCustomTodos(state) {
  const container = document.getElementById('todo-custom-list');
  if (!container) return;
  container.classList.add('todo-list');

  const customs = state.customs || [];
  if (!customs.length) { container.innerHTML = ''; return; }

  container.innerHTML = customs.map(c => `
    <div class="todo-item${c.done ? ' done' : ''}" onclick="toggleCustomTodo(${c.id})">
      <div class="todo-check"></div>
      <span class="todo-label">${_escHtml(c.text)}</span>
      <button
        onclick="event.stopPropagation();deleteCustomTodo(${c.id})"
        style="background:none;color:var(--muted);font-size:16px;cursor:pointer;
               border:none;outline:none;padding:0 4px;line-height:1;flex-shrink:0;"
        title="삭제"
      >×</button>
    </div>
  `).join('');
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §5. 보고서 탭 관리 (N4)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

const REPORTS_LS_KEY = 'nz_upharma_reports_v1';

function _loadReports() {
  try   { return JSON.parse(localStorage.getItem(REPORTS_LS_KEY) || '[]'); }
  catch { return []; }
}

/**
 * 보고서 탭에 항목 추가.
 * @param {object|null} result  분석 결과
 * @param {string|null} pdfName PDF 파일명
 * @param {string} reportType   'p1' | 'p2' | 'p3'
 */
function _addReportEntry(result, pdfName, reportType) {
  const reports = _loadReports();
  const rType = reportType || 'p1';
  const productName = result ? (result.trade_name || result.product_id || '알 수 없음') : '알 수 없음';
  const titleMap = { p1: `시장조사 보고서 - ${productName}`, p2: `수출가격 전략 - ${productName}`, p3: `바이어 발굴 보고서 - ${productName}`, final: `최종 보고서 - ${productName}` };
  const entry   = {
    id:        Date.now(),
    product:   productName,
    report_type: rType,
    report_title: titleMap[rType] || `보고서 - ${productName}`,
    inn:       rType === 'p1' ? (result ? (INN_MAP[result.product_id] || result.inn || '') : '') : '',
    verdict:   rType === 'p1' ? (result ? (result.verdict || '—') : '—') : '—',
    price_hint: rType === 'p1' ? (result ? String(result.price_positioning_pbs || '').trim() : '') : '',
    pbs_sgd_hint: rType === 'p1' ? (result ? (result.pbs_dpmq_sgd_hint ?? null) : null) : null,
    basis_trade: rType === 'p1' ? (result ? String(result.basis_trade || '').trim() : '') : '',
    risks_conditions: rType === 'p1' ? (result ? String(result.risks_conditions || '').trim() : '') : '',
    timestamp: new Date().toLocaleString('ko-KR', {
      month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    }),
    hasPdf: !!pdfName,
    pdf_name: pdfName || '',
  };

  reports.unshift(entry);
  localStorage.setItem(REPORTS_LS_KEY, JSON.stringify(reports.slice(0, 30)));
  renderReportTab();
  _syncP2ReportsOptions();
  _syncP3ReportOptions();
}

function clearAllReports() {
  localStorage.setItem(REPORTS_LS_KEY, JSON.stringify([]));
  renderReportTab();
  _syncP2ReportsOptions();
}

function deleteReportEntry(id) {
  const reports = _loadReports().filter(r => r.id !== id);
  localStorage.setItem(REPORTS_LS_KEY, JSON.stringify(reports));
  renderReportTab();
  _syncP2ReportsOptions();
}

/** 보고서 탭 DOM 갱신 */
function renderReportTab() {
  const container = document.getElementById('report-tab-list');
  if (!container) return;

  const reports = _loadReports();
  if (!reports.length) {
    container.innerHTML = `
      <div class="rep-empty">
        아직 생성된 보고서가 없습니다.<br>
        만들어진 보고서는 자동으로 등록됩니다.
      </div>`;
    return;
  }

  container.innerHTML = reports.map(r => {
    const vc = r.verdict === '적합'   ? 'green'
             : r.verdict === '부적합' ? 'red'
             : r.verdict !== '—'      ? 'orange'
             :                          'gray';
    const innSpan = r.inn
      ? ` <span style="font-weight:400;color:var(--muted);font-size:12px;">· ${_escHtml(r.inn)}</span>`
      : '';
    const downloadUrl = r.report_type === 'p3'
      ? `/api/buyers/report/download${r.pdf_name ? `?name=${encodeURIComponent(r.pdf_name)}` : ''}`
      : r.report_type === 'final'
      ? '/api/report/combined'
      : `/api/report/download${r.pdf_name ? `?name=${encodeURIComponent(r.pdf_name)}` : ''}`;
    const typeBadge = r.report_type === 'p2'
      ? ' <span style="font-size:10px;color:var(--orange);font-weight:600;">[가격]</span>'
      : r.report_type === 'p3'
      ? ' <span style="font-size:10px;color:var(--navy);font-weight:600;">[바이어]</span>'
      : r.report_type === 'final'
      ? ' <span style="font-size:10px;color:#5a3ea8;font-weight:600;">[최종]</span>'
      : '';
    const dlBtn = r.hasPdf
      ? `<a class="btn-download"
            href="${downloadUrl}"
            target="_blank"
            style="padding:7px 14px;font-size:12px;flex-shrink:0;">📄 PDF</a>`
      : '';
    const delBtn = `<button class="btn-report-del" onclick="deleteReportEntry(${r.id})" title="보고서 삭제">×</button>`;

    return `
      <div class="rep-item">
        <div class="rep-item-info">
          <div class="rep-item-product">${_escHtml(r.report_title || r.product)}${typeBadge}${innSpan}</div>
          <div class="rep-item-meta">${_escHtml(r.timestamp)}</div>
        </div>
        <div class="rep-item-verdict">
          <span class="bdg ${vc}">${_escHtml(r.verdict)}</span>
        </div>
        ${dlBtn}
        ${delBtn}
      </div>`;
  }).join('');
  _syncP2ReportsOptions();
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §6. 수출 가격 전략 (P2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

let _p2Ready = false;
let _p2Tab = 'ai';
let _p2ManualSeg = 'public';
let _p2AiSeg = 'public';
let _p2SelectedReportId = '';
let _p2AiSelectedReportId = '';
let _p2UploadedReportFilename = '';
let _p2AiPollTimer = null;
let _p2Manual = _makeP2Defaults();
let _p2LastScenarios = null;
let _p2ManualCalculated = false;

function _makeP2Defaults() {
  return {
    public: [
      { key: 'base_price', label: '기준 입찰가', value: 0, type: 'abs_input', unit: 'NZD', step: 0.5, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '경쟁사 입찰가 또는 목표 기준가', rationale: '공공 채널은 입찰 경쟁이 강해 기준가 설정이 핵심입니다.' },
      { key: 'exchange', label: '환율 (USD→NZD)', value: 1.0, type: 'abs_input', unit: 'rate', step: 0.0001, min: 0.0001, max: 99, enabled: true, fixed: false, expanded: false, hint: 'USD 입력 시 적용, NZD면 1.0 유지', rationale: '실시간 환율을 반영해 환차 리스크를 줄입니다.' },
      { key: 'pub_ratio', label: '공공 수출가 산출 비율', value: 30, type: 'pct_mult', unit: '%', step: 1, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '기준가 대비 최종 반영 비율', rationale: '입찰·유통·파트너 마진을 반영한 목표 비율입니다.' },
    ],
    private: [
      { key: 'base_het', label: '민간 기준가 (HET/HNA)', value: 0, type: 'abs_input', unit: 'NZD', step: 0.5, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '소매/입고 기준 가격', rationale: '민간 시장은 소매 가격 구조 역산이 중요합니다.' },
      { key: 'exchange', label: '환율 (USD→NZD)', value: 1.0, type: 'abs_input', unit: 'rate', step: 0.0001, min: 0.0001, max: 99, enabled: true, fixed: false, expanded: false, hint: 'USD 입력 시 적용', rationale: '실시간 환율 반영으로 가격 정합성을 유지합니다.' },
      { key: 'gst', label: 'GST 공제 (÷1.09)', value: 9, type: 'gst_fixed', unit: '%', step: 0, min: 9, max: 9, enabled: true, fixed: true, expanded: false, hint: '싱가포르 GST 9% 고정', rationale: '민간 소비자 가격에서 세금을 분리합니다.' },
      { key: 'retail', label: '소매 마진율', value: 40, type: 'pct_deduct', unit: '%', step: 1, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '체인/약국 마진 차감', rationale: '채널별 마진 차이를 반영합니다.' },
      { key: 'partner', label: '파트너사 마진', value: 20, type: 'pct_deduct', unit: '%', step: 1, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '현지 파트너 수수료', rationale: '현지 영업·등록 비용을 포함합니다.' },
      { key: 'distribution', label: '유통 마진', value: 15, type: 'pct_deduct', unit: '%', step: 1, min: 0, max: 99999, enabled: true, fixed: false, expanded: false, hint: '물류/도매 비용', rationale: '유통 구조별 고정비를 반영합니다.' },
    ],
  };
}

function initP2Strategy() {
  if (!document.getElementById('p2-wrap')) return;
  _p2Ready = true;

  const aiSelect = document.getElementById('p2-ai-report-select');
  if (aiSelect) {
    aiSelect.addEventListener('change', (e) => {
      _p2AiSelectedReportId = e.target.value || '';
    });
  }

  _syncP2ReportsOptions();
  _p2FillExchangeRate();
}

function switchP2Tab(tab) {
  _p2Tab = tab === 'manual' ? 'manual' : 'ai';
  const aiBtn = document.getElementById('p2-tab-ai');
  const manualBtn = document.getElementById('p2-tab-manual');
  const aiTab = document.getElementById('p2-ai-tab');
  const manualTab = document.getElementById('p2-manual-tab');
  if (aiBtn && manualBtn) {
    aiBtn.classList.toggle('on', _p2Tab === 'ai');
    manualBtn.classList.toggle('on', _p2Tab === 'manual');
  }
  if (aiTab && manualTab) {
    aiTab.style.display = _p2Tab === 'ai' ? '' : 'none';
    manualTab.style.display = _p2Tab === 'manual' ? '' : 'none';
  }
  if (_p2Tab === 'ai') _showP2AiError('');
}

function setP2AiSeg(seg) {
  _p2AiSeg = seg === 'private' ? 'private' : 'public';
  _p2ColData = _p2ColDataByMarket[_p2AiSeg];
  _p2ScenarioRaw = _p2ScenarioRawByMarket[_p2AiSeg];
  document.getElementById('p2-ai-seg-public')?.classList.toggle('on', _p2AiSeg === 'public');
  document.getElementById('p2-ai-seg-private')?.classList.toggle('on', _p2AiSeg === 'private');
  const desc = document.getElementById('p2-ai-seg-desc');
  if (desc) {
    desc.textContent = _p2AiSeg === 'public'
      ? '공공 시장: ALPS 조달청 채널 · 27개 공공기관 통합구매 기준'
      : '민간 시장: 병원·약국·체인 채널 중심 유통 구조 기준';
  }
  const cached = _p2AiSeg === 'public' ? _p2PublicResult : _p2PrivateResult;
  if (cached) _applyP2ResultToCards(cached, _p2AiSeg);
}

async function handleP2FileSelect(inputEl) {
  const file = inputEl?.files?.[0];
  const statusEl = document.getElementById('p2-upload-status');
  const textEl = document.getElementById('p2-upload-text');
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    if (statusEl) {
      statusEl.style.display = 'block';
      statusEl.textContent = 'PDF 파일만 업로드 가능합니다.';
    }
    return;
  }

  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.textContent = '업로드 중…';
  }
  if (textEl) textEl.textContent = file.name;

  try {
    const arr = await file.arrayBuffer();
    const bytes = new Uint8Array(arr);
    let binary = '';
    for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
    const contentB64 = btoa(binary);

    const res = await fetch('/api/p2/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, content_b64: contentB64 }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.filename) throw new Error(data.detail || `HTTP ${res.status}`);

    _p2UploadedReportFilename = data.filename;
    _p2AiSelectedReportId = '';
    const aiSelect = document.getElementById('p2-ai-report-select');
    if (aiSelect) aiSelect.value = '';
    if (statusEl) statusEl.textContent = `업로드 완료: ${data.filename}`;
  } catch (err) {
    if (statusEl) statusEl.textContent = `업로드 실패: ${err.message}`;
  }
}

/* 수출 가격 전략 진행 단계 — 시장조사와 동일한 스타일 */
const P2_STEP_ORDER = ['extract', 'ai_extract', 'ai_analysis', 'report'];

function _setP2Progress(currentStep, status) {
  const row = document.getElementById('p2-progress-row');
  if (row) row.classList.add('visible');
  const idx = P2_STEP_ORDER.indexOf(currentStep);

  for (let i = 0; i < P2_STEP_ORDER.length; i++) {
    const el = document.getElementById('p2prog-' + P2_STEP_ORDER[i]);
    if (!el) continue;
    const dot = el.querySelector('.prog-dot');
    if (status === 'error' && i === idx) {
      el.className = 'prog-step error'; dot.textContent = '✕';
    } else if (i < idx || (i === idx && status === 'done')) {
      el.className = 'prog-step done'; dot.textContent = '✓';
    } else if (i === idx) {
      el.className = 'prog-step active'; dot.textContent = i + 1;
    } else {
      el.className = 'prog-step'; dot.textContent = i + 1;
    }
  }
}

function _resetP2Progress() {
  const row = document.getElementById('p2-progress-row');
  if (row) row.classList.remove('visible');
  for (let i = 0; i < P2_STEP_ORDER.length; i++) {
    const el = document.getElementById('p2prog-' + P2_STEP_ORDER[i]);
    if (!el) continue;
    el.className = 'prog-step';
    el.querySelector('.prog-dot').textContent = i + 1;
  }
}

function _showP2AiError(msg) {
  const el = document.getElementById('p2-ai-error-msg');
  if (!el) return;
  if (msg) { el.style.display = ''; el.textContent = msg; }
  else { el.style.display = 'none'; el.textContent = ''; }
}

function _resetP2AiResultView() {
  const resultSection = document.getElementById('p2-ai-result-section');
  if (resultSection) resultSection.style.display = 'none';
  const dlState = document.getElementById('p2-report-dl-state');
  if (dlState) dlState.innerHTML = '';
  _showP2AiError('');
}

function _resetP2ManualResultView() {
  _p2ManualCalculated = false;
  _p2LastScenarios = null;
  const card = document.getElementById('p2-manual-result-card');
  if (card) card.style.display = 'none';
}

function runP2ManualCalculation() {
  const icon = document.getElementById('p2-manual-calc-icon');
  if (icon) icon.textContent = '⏳';
  _p2ManualCalculated = true;
  _renderP2Manual();
  if (icon) icon.textContent = '▶';
}

async function runP2AiPipeline() {
  const runBtn = document.getElementById('btn-p2-ai-run');
  const runIcon = document.getElementById('p2-ai-run-icon');
  const selectedReport = _loadReports().find((r) => String(r.id) === String(_p2AiSelectedReportId));
  const reportFilename = _p2UploadedReportFilename || (selectedReport ? (selectedReport.pdf_name || '') : '');

  if (!reportFilename) {
    _showP2AiError('실행 전 PDF가 있는 보고서를 선택하거나 PDF를 직접 업로드해 주세요.');
    return;
  }

  _resetP2AiResultView();
  _resetP2Progress();
  _showP2Loading();
  _p2PublicResult  = null;
  _p2PrivateResult = null;

  if (runBtn) runBtn.disabled = true;
  if (runIcon) runIcon.textContent = '⏳';

  try {
    const loadingLabel = document.getElementById('p2-loading-label');

    if (loadingLabel) loadingLabel.textContent = '공공 시장 분석 중…';
    const pubResult = await _runP2MarketPipeline(reportFilename, 'public');
    _p2PublicResult = pubResult;

    if (loadingLabel) loadingLabel.textContent = '민간 시장 분석 중…';
    const privResult = await _runP2MarketPipeline(reportFilename, 'private');
    _p2PrivateResult = privResult;

    _hideP2Loading();
    _resetP2Progress();

    // 두 시장 카드 데이터 초기화 (현재 선택된 시장부터)
    _applyP2ResultToCards(pubResult, 'public');
    _applyP2ResultToCards(privResult, 'private');

    // 현재 선택된 시장을 화면에 적용
    _p2ColData = _p2ColDataByMarket[_p2AiSeg];
    _p2ScenarioRaw = _p2ScenarioRawByMarket[_p2AiSeg];
    _applyP2ResultToCards(_p2AiSeg === 'public' ? pubResult : privResult, _p2AiSeg);

    // 보고서 탭 등록 (공공 시장 결과 기준)
    const extracted = pubResult?.extracted || {};
    if (pubResult?.pdf) {
      _addReportEntry(
        { trade_name: extracted.product_name || '수출가격 전략', inn: null, verdict: '—' },
        pubResult.pdf, 'p2'
      );
    }
  } catch (err) {
    _setP2Progress('extract', 'error');
    _showP2AiError(`실행 실패: ${err.message}`);
    _hideP2Loading();
  } finally {
    if (runBtn) runBtn.disabled = false;
    if (runIcon) runIcon.textContent = '▶';
  }
}

async function _runP2MarketPipeline(reportFilename, market) {
  const res = await fetch('/api/p2/pipeline', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ report_filename: reportFilename, market }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      try {
        const sr = await fetch('/api/p2/pipeline/status');
        const sd = await sr.json();
        if (sd.status === 'idle') return;
        if (sd.status === 'done') {
          clearInterval(timer);
          const rr = await fetch('/api/p2/pipeline/result');
          resolve(await rr.json());
        } else if (sd.status === 'error') {
          clearInterval(timer);
          reject(new Error(sd.step_label || '파이프라인 실패'));
        }
      } catch (_e) {}
    }, 1800);
  });
}

/* P2 3열 카드: 역산 섹션 토글 */
/* P2 단일 편집 모달 */
let _p2EditCol = null;

function openP2EditModal(col) {
  _p2EditCol = col;
  const labels = { agg: '저가 진입', avg: '기준가', cons: '프리미엄' };
  const marketLabel = _p2AiSeg === 'public' ? '[공공 시장]' : '[민간 시장]';
  const titleEl = document.getElementById('p2em-title');
  if (titleEl) titleEl.textContent = `${labels[col] || col} — 역산 · 옵션 편집  ${marketLabel}`;

  // 보고서 NZD 참조 표시
  const sgdVal  = parseFloat(document.getElementById('p2ci-base-' + col)?.value || 0);
  const sgdUsd  = _p2ScenarioRaw.sgd_usd || 0;
  const refSgdEl = document.getElementById('p2em-ref-sgd');
  const refUsdEl = document.getElementById('p2em-ref-usd');
  if (refSgdEl) refSgdEl.textContent = sgdVal > 0 ? sgdVal.toFixed(2) : '—';
  if (refUsdEl) refUsdEl.textContent = (sgdVal > 0 && sgdUsd > 0) ? (sgdVal / sgdUsd).toFixed(2) : '—';

  // 기준가 USD로 초기화
  const initUsd = (sgdVal > 0 && sgdUsd > 0) ? (sgdVal / sgdUsd).toFixed(2) : sgdVal.toFixed(2);
  document.getElementById('p2em-base').value = initUsd;

  // opts가 없으면 시장별 기본값으로 초기화 (fallback)
  _p2ColData = _p2ColDataByMarket[_p2AiSeg];
  if (!_p2ColData[col] || !_p2ColData[col].opts.length) {
    const feeDefault     = { agg: 3.0, avg: 5.0, cons: 10.0 }[col] ?? 5.0;
    const freightDefault = { agg: 0.85, avg: 1.0, cons: 1.20 }[col] ?? 1.0;
    const isPublic = _p2AiSeg === 'public';
    _p2ColData[col] = isPublic
      ? { opts: [
          { id: 'fee',          name: '에이전트 수수료',    type: 'pct_deduct', value: feeDefault },
          { id: 'freight',      name: '운임 배수',          type: 'multiply',   value: freightDefault },
          { id: 'procurement',  name: '조달청 입찰 수수료', type: 'pct_deduct', value: 3.0 },
          { id: 'gpo_discount', name: 'GPO 물량 할인율',   type: 'pct_deduct', value: 2.0 },
        ]}
      : { opts: [
          { id: 'fee',               name: '에이전트 수수료',    type: 'pct_deduct', value: feeDefault },
          { id: 'freight',           name: '운임 배수',          type: 'multiply',   value: freightDefault },
          { id: 'pharmacy_margin',   name: '병원·약국 유통 마진', type: 'pct_deduct', value: 15.0 },
          { id: 'distributor_markup',name: '유통사 마크업',      type: 'pct_add',    value: 8.0 },
        ]};
  }

  _renderP2ModalOpts();
  document.getElementById('p2-edit-overlay').style.display = '';
  document.body.style.overflow = 'hidden';
}

function closeP2EditModal(e) {
  if (e && e.target !== document.getElementById('p2-edit-overlay')) return;
  document.getElementById('p2-edit-overlay').style.display = 'none';
  document.body.style.overflow = '';
  _p2EditCol = null;
}

function recalcP2ColModal() {
  if (!_p2EditCol) return;
  const usdVal = parseFloat(document.getElementById('p2em-base').value || 0);
  const sgdUsd = _p2ScenarioRaw.sgd_usd || 0;
  const baseEl = document.getElementById('p2ci-base-' + _p2EditCol);
  if (baseEl) baseEl.value = (sgdUsd > 0 ? usdVal * sgdUsd : usdVal).toFixed(4);
  recalcP2Col(_p2EditCol);
  _updateP2ModalResult();
}

function _updateP2ModalResult() {
  const col = _p2EditCol;
  if (!col) return;
  const base   = parseFloat(document.getElementById('p2ci-base-' + col)?.value || 0);
  const sgdUsd = _p2ScenarioRaw.sgd_usd || 0;
  const sgdKrw = _p2ScenarioRaw.sgd_krw || 0;
  let priceSgd = base;
  const opts = _p2ColData[col]?.opts || [];
  for (const opt of opts) {
    const v = opt.value ?? 0;
    if      (opt.type === 'pct_add')    priceSgd *= (1 + v / 100);
    else if (opt.type === 'pct_deduct') priceSgd *= (1 - v / 100);
    else if (opt.type === 'multiply')   priceSgd *= v;
    else if (opt.type === 'divide')     { if (v !== 0) priceSgd /= v; }
    else if (opt.type === 'abs_add')    priceSgd += v;
    else if (opt.type === 'abs_deduct') priceSgd -= v;
    else if (opt.type === 'usd_add')    priceSgd += sgdUsd > 0 ? v / sgdUsd : 0;
    else if (opt.type === 'usd_deduct') priceSgd -= sgdUsd > 0 ? v / sgdUsd : 0;
  }
  priceSgd = Math.max(0, priceSgd);
  const usdVal = sgdUsd > 0 ? priceSgd / sgdUsd : 0;
  const krw    = sgdKrw > 0 ? Math.round(priceSgd * sgdKrw).toLocaleString('ko-KR') : '—';
  const resultEl = document.getElementById('p2em-result');
  if (resultEl) resultEl.textContent = usdVal > 0
    ? `${usdVal.toFixed(2)} USD · ${priceSgd.toFixed(2)} NZD`
    : '—';
}

const _P2_TYPE_LABEL = {
  pct_add: '% 가산', pct_deduct: '% 차감',
  multiply: '× 배수', divide: '÷ 나누기',
  abs_add: 'NZD 가산', abs_deduct: 'NZD 차감',
  usd_add: 'USD 가산', usd_deduct: 'USD 차감',
};

function _renderP2ModalOpts() {
  const col = _p2EditCol;
  if (!col) return;
  const container = document.getElementById('p2em-opts');
  if (!container) return;
  const opts = (_p2ColData[col] || { opts: [] }).opts;

  container.innerHTML = opts.map(opt => `
    <div class="p2c-opt-row">
      <span class="p2c-opt-name">${_escHtml(opt.name)}</span>
      <span class="p2c-opt-type-label">${_P2_TYPE_LABEL[opt.type] || opt.type}</span>
      <input class="p2c-opt-val" type="number" value="${opt.value}" step="0.1" min="0"
        onchange="updateP2ColOption('${col}','${_escHtml(opt.id)}',this.value)">
      <button class="p2c-opt-del" onclick="removeP2ColOption('${col}','${_escHtml(opt.id)}')">×</button>
    </div>`).join('');

  _updateP2ModalResult();
}

function confirmP2ModalOption() {
  const col = _p2EditCol;
  if (!col) return;
  const nameEl = document.getElementById('p2em-newname');
  const name = (nameEl?.value || '').trim();
  const type = document.getElementById('p2em-newtype')?.value || 'pct_deduct';
  const val  = parseFloat(document.getElementById('p2em-newval')?.value || '0');
  if (!name || Number.isNaN(val)) return;
  _p2ColData[col] = _p2ColData[col] || { opts: [] };
  _p2ColData[col].opts.push({ id: 'o' + Date.now(), name, type, value: val });
  // 입력창 초기화 (폼은 항상 표시 유지)
  if (nameEl) nameEl.value = '';
  const valEl = document.getElementById('p2em-newval');
  if (valEl) valEl.value = '';
  _renderP2ModalOpts();
  recalcP2Col(col);
}

/* P2 3열 카드: 기준가 + opts 배열 기반 재계산 (USD 환산 수정: × sgd_usd) */
function recalcP2Col(col) {
  const base   = parseFloat(document.getElementById('p2ci-base-' + col)?.value || 0);
  const sgdUsd = _p2ScenarioRaw.sgd_usd || 0;
  const sgdKrw = _p2ScenarioRaw.sgd_krw || 0;

  let priceSgd = base;

  const opts = _p2ColData[col]?.opts || [];
  for (const opt of opts) {
    const v = opt.value ?? 0;
    if      (opt.type === 'pct_add')    priceSgd *= (1 + v / 100);
    else if (opt.type === 'pct_deduct') priceSgd *= (1 - v / 100);
    else if (opt.type === 'multiply')   priceSgd *= v;
    else if (opt.type === 'divide')     { if (v !== 0) priceSgd /= v; }
    else if (opt.type === 'abs_add')    priceSgd += v;
    else if (opt.type === 'abs_deduct') priceSgd -= v;
    else if (opt.type === 'usd_add')    priceSgd += sgdUsd > 0 ? v / sgdUsd : 0;
    else if (opt.type === 'usd_deduct') priceSgd -= sgdUsd > 0 ? v / sgdUsd : 0;
  }
  priceSgd = Math.max(0, priceSgd);

  const usdVal = sgdUsd > 0 ? priceSgd / sgdUsd : 0;
  const krw    = sgdKrw > 0 ? Math.round(priceSgd * sgdKrw).toLocaleString('ko-KR') : '—';

  const priceEl = document.getElementById('p2c-price-' + col);
  const subEl   = document.getElementById('p2c-sub-' + col);
  if (priceEl) priceEl.textContent = usdVal > 0 ? usdVal.toFixed(2) : '—';
  if (subEl)   subEl.textContent   = `${priceSgd.toFixed(2)} NZD`;
}

/* renderP2ColOptions — 모달 전용 _renderP2ModalOpts로 통합됨 (호환용 stub) */
function renderP2ColOptions(col) {
  if (_p2EditCol === col) _renderP2ModalOpts();
  recalcP2Col(col);
}

/* 옵션 삭제 */
function removeP2ColOption(col, optId) {
  if (!_p2ColData[col]) return;
  _p2ColData[col].opts = _p2ColData[col].opts.filter(o => o.id !== optId);
  if (_p2EditCol === col) _renderP2ModalOpts();
  recalcP2Col(col);
}

/* 옵션 값 수정 */
function updateP2ColOption(col, optId, newVal) {
  if (!_p2ColData[col]) return;
  const opt = _p2ColData[col].opts.find(o => o.id === optId);
  if (opt) {
    opt.value = parseFloat(newVal) ?? 0;
    recalcP2Col(col);
    if (_p2EditCol === col) _updateP2ModalResult();
  }
}

function _renderP2AiResult(data) {
  const extracted = data?.extracted || {};
  const analysis = data?.analysis || {};
  const rates = data?.exchange_rates || {};
  const pubMarket = analysis.public_market || analysis;
  const scenarios = Array.isArray(pubMarket.scenarios) ? pubMarket.scenarios : [];
  const resultSection = document.getElementById('p2-ai-result-section');
  if (resultSection) resultSection.style.display = '';

  // 제품명
  _setText('p2r-product-name', extracted.product_name || '미상');

  // 판정 배지 (시장조사 스타일)
  const verdictEl = document.getElementById('p2r-verdict-badge');
  if (verdictEl) {
    const v = extracted.verdict || '미상';
    const vc = v === '적합' ? 'v-ok' : v === '부적합' ? 'v-err' : v !== '미상' ? 'v-warn' : 'v-none';
    verdictEl.className = `verdict-badge ${vc}`;
    verdictEl.textContent = v;
  }

  // 참조 정보
  _setText('p2r-ref-price-text',
    extracted.ref_price_text || (extracted.ref_price_sgd != null ? `NZD ${Number(extracted.ref_price_sgd).toFixed(2)}` : '추출값 없음'));
  const krwRate = rates.sgd_krw;
  const usdRate = rates.sgd_usd;
  let rateText = '환율 정보 없음';
  if (krwRate) {
    rateText = `1 NZD = ${Number(krwRate).toFixed(2)} KRW`;
    if (usdRate) rateText += ` / ${Number(usdRate).toFixed(4)} USD`;
  }
  _setText('p2r-exchange', rateText);

  // 최종 권고가
  _setText('p2r-final-price', `NZD ${Number(pubMarket.final_price_sgd || 0).toFixed(2)}`);

  // 시나리오
  const scenEl = document.getElementById('p2r-scenarios');
  if (scenEl) {
    if (scenarios.length) {
      scenEl.innerHTML = scenarios.map((s, idx) => {
        const cls = idx === 0 ? 'agg' : idx === 1 ? 'avg' : 'cons';
        return `
          <div class="p2-scenario p2-scenario--${cls}">
            <div class="p2-scenario-top">
              <span class="p2-scenario-name">${_escHtml(String(s.name || `시나리오 ${idx + 1}`))}</span>
              <span class="p2-scenario-price">NZD ${Number(s.price_sgd || 0).toFixed(2)}</span>
            </div>
          </div>`;
      }).join('');
    } else {
      scenEl.innerHTML = '<div class="p2-note">시나리오 데이터가 없습니다.</div>';
    }
  }

  // 산정 이유
  _setText('p2r-rationale', analysis.rationale || '산정 이유 없음');

  // P2 보고서 탭 자동 등록
  if (data?.pdf) {
    _addReportEntry(
      { trade_name: extracted.product_name || '수출가격 전략', inn: null, verdict: '—' },
      data.pdf,
      'p2'
    );
  }

  // ── 3열 시나리오 UI 채우기 (현재 활성 시장 기준) ──────────
  _applyP2ResultToCards(data, 'public');

}

/* 특정 시장의 AI 결과를 3열 카드 UI에 적용 */
function _applyP2ResultToCards(data, market) {
  const extracted  = data?.extracted || {};
  const analysis   = data?.analysis  || {};
  const rates      = data?.exchange_rates || {};
  const marketKey  = market === 'public' ? 'public_market' : 'private_market';
  const marketData = analysis[marketKey] || analysis;
  const scenarios  = Array.isArray(marketData.scenarios) ? marketData.scenarios : [];

  const sgdUsd = rates.sgd_usd ? Number(rates.sgd_usd) : 0;
  const sgdKrw = rates.sgd_krw ? Number(rates.sgd_krw) : 0;

  // 결과 섹션 표시
  const resultSection = document.getElementById('p2-ai-result-section');
  if (resultSection) resultSection.style.display = '';

  // 시장별 저장소 참조
  const rawData  = _p2ScenarioRawByMarket[market];
  const colStore = _p2ColDataByMarket[market];
  rawData.sgd_usd = sgdUsd;
  rawData.sgd_krw = sgdKrw;

  const isPublic = market === 'public';
  const cols = ['agg', 'avg', 'cons'];

  scenarios.forEach((s, i) => {
    const col = cols[i];
    if (!col) return;
    const priceSgd = Number(s.price_sgd || 0);
    rawData[col] = priceSgd;

    const baseInput = document.getElementById('p2ci-base-' + col);
    if (baseInput) baseInput.value = priceSgd.toFixed(2);

    // 시장별 기본 옵션 (처음 실행 시에만 초기화)
    const feeDefault     = { agg: 3.0,  avg: 5.0,  cons: 10.0 }[col] ?? 5.0;
    const freightDefault = { agg: 0.85, avg: 1.0,  cons: 1.20 }[col] ?? 1.0;
    colStore[col] = isPublic
      ? { opts: [
          { id: 'fee',         name: '에이전트 수수료',   type: 'pct_deduct', value: feeDefault },
          { id: 'freight',     name: '운임 배수',         type: 'multiply',   value: freightDefault },
          { id: 'procurement', name: '조달청 입찰 수수료', type: 'pct_deduct', value: 3.0 },
          { id: 'gpo_discount',name: 'GPO 물량 할인율',   type: 'pct_deduct', value: 2.0 },
        ]}
      : { opts: [
          { id: 'fee',              name: '에이전트 수수료',    type: 'pct_deduct', value: feeDefault },
          { id: 'freight',          name: '운임 배수',          type: 'multiply',   value: freightDefault },
          { id: 'pharmacy_margin',  name: '병원·약국 유통 마진', type: 'pct_deduct', value: 15.0 },
          { id: 'distributor_markup', name: '유통사 마크업',    type: 'pct_add',    value: 8.0 },
        ]};
  });

  // 이 시장이 현재 표시 중인 시장이면 _p2ColData / _p2ScenarioRaw 참조 동기화
  if (market === _p2AiSeg) {
    _p2ColData = colStore;
    _p2ScenarioRaw = rawData;
    // 숨김 입력값을 실제 카드 가격으로 반영
    cols.forEach(col => recalcP2Col(col));
  }

  // 경쟁가 분포
  if (market === _p2AiSeg && scenarios.length >= 3) {
    const prices = scenarios.map(s => Number(s.price_sgd || 0)).sort((a, b) => a - b);
    _setText('p2-dist-p25', `${prices[0].toFixed(2)} NZD`);
    _setText('p2-dist-med', `${prices[1].toFixed(2)} NZD`);
    _setText('p2-dist-p75', `${prices[2].toFixed(2)} NZD`);
  }
}

function _p2FillExchangeRate() {
  const rates = window._exchangeRates;
  if (!rates) return;
  const sgdUsd = Number(rates.sgd_usd);
  if (!sgdUsd || sgdUsd <= 0) return;
  const usdToSgd = Number((1 / sgdUsd).toFixed(4));
  ['public', 'private'].forEach((seg) => {
    const opt = _p2Manual[seg].find((x) => x.key === 'exchange');
    if (opt) opt.value = usdToSgd;
  });
}

function _p2FillBaseFromReport() {
  const report = _getP2SelectedReport();
  if (!report) return;
  // 1순위: 저장된 숫자형 NZD 값 (pbs_dpmq_sgd_hint)
  const numHint = report.pbs_sgd_hint;
  const hint = (numHint != null && !Number.isNaN(Number(numHint)) && Number(numHint) > 0)
    ? Number(numHint)
    : _extractSgdHint(report.price_hint || '');
  if (!Number.isNaN(hint) && hint > 0) {
    const pub = _p2Manual.public.find((x) => x.key === 'base_price');
    const pri = _p2Manual.private.find((x) => x.key === 'base_het');
    if (pub) pub.value = hint;
    if (pri) pri.value = hint;
  }
}

function _syncP2ReportsOptions() {
  if (!_p2Ready) return;
  const allReports = _loadReports();
  const p1Reports  = allReports.filter((r) => r.report_type === 'p1');
  const optionHtml = ['<option value="">저장된 분석 보고서를 선택하세요.</option>']
    .concat(p1Reports.map((r) => {
      const name = r.product || r.report_title || '보고서';
      return `<option value="${r.id}">시장조사 보고서 · ${_escHtml(name)} · ${_escHtml(r.timestamp || '')}</option>`;
    }))
    .join('');

  const aiSelect = document.getElementById('p2-ai-report-select');
  if (aiSelect) {
    const curr = _p2AiSelectedReportId;
    aiSelect.innerHTML = optionHtml;
    _p2AiSelectedReportId = p1Reports.some((r) => String(r.id) === String(curr)) ? curr : '';
    aiSelect.value = _p2AiSelectedReportId;
  }

}

function _getP2SelectedReport() {
  if (!_p2SelectedReportId) return null;
  return _loadReports().find((r) => String(r.id) === String(_p2SelectedReportId)) || null;
}

function _extractSgdHint(text) {
  const src = String(text || '');
  const mRange = src.match(/NZD\s*([0-9]+(?:\.[0-9]+)?)\s*[~\-–]\s*([0-9]+(?:\.[0-9]+)?)/i);
  if (mRange) return (Number(mRange[1]) + Number(mRange[2])) / 2;
  const mSingle = src.match(/NZD\s*([0-9]+(?:\.[0-9]+)?)/i);
  if (mSingle) return Number(mSingle[1]);
  // PBS 미등재 폴백: Haiku가 "$X.XX" 또는 "USD X.XX" 반환 시 근사값으로 사용
  const mUsd = src.match(/(?:\$|USD\s+)([0-9]+(?:\.[0-9]+)?)/i);
  if (mUsd) return Number(mUsd[1]);
  return NaN;
}

function _calcP2Manual() {
  const seg = _p2ManualSeg;
  const options = _p2Manual[seg].filter((x) => x.enabled);
  if (seg === 'public') {
    const base = Number(options.find((x) => x.key === 'base_price')?.value || 0);
    const ex = Number(options.find((x) => x.key === 'exchange')?.value || 1);
    const ratio = Number(options.find((x) => x.key === 'pub_ratio')?.value || 30);
    let price = base * ex * (ratio / 100);
    const parts = [`NZD ${base.toFixed(2)}`, `× ${ex.toFixed(4)}`, `× ${ratio}%`];
    options.forEach((opt) => {
      if (opt.type === 'pct_add_custom') {
        price *= (1 + Number(opt.value) / 100);
        parts.push(`× (1+${Number(opt.value).toFixed(1)}%)`);
      } else if (opt.type === 'abs_add_custom') {
        price += Number(opt.value);
        parts.push(`+ NZD ${Number(opt.value).toFixed(2)}`);
      }
    });
    return { kup: Math.max(price, 0), formulaStr: `${parts.join('  ')}  =  KUP  NZD ${Math.max(price, 0).toFixed(2)}` };
  }

  let price = 0;
  const parts = [];
  options.forEach((opt) => {
    if (opt.key === 'base_het') {
      price = Number(opt.value);
      parts.push(`NZD ${price.toFixed(2)}`);
    } else if (opt.key === 'exchange' && Number(opt.value) !== 1) {
      price *= Number(opt.value);
      parts.push(`× ${Number(opt.value).toFixed(4)}`);
    } else if (opt.type === 'gst_fixed') {
      price /= 1.09;
      parts.push('÷ 1.09');
    } else if (opt.type === 'pct_deduct') {
      price *= (1 - Number(opt.value) / 100);
      parts.push(`× (1−${Number(opt.value).toFixed(1)}%)`);
    } else if (opt.type === 'pct_add_custom') {
      price *= (1 + Number(opt.value) / 100);
      parts.push(`× (1+${Number(opt.value).toFixed(1)}%)`);
    } else if (opt.type === 'abs_add_custom') {
      price += Number(opt.value);
      parts.push(`+ NZD ${Number(opt.value).toFixed(2)}`);
    }
  });
  return { kup: Math.max(price, 0), formulaStr: `${(parts.join('  ') || 'NZD 0.00')}  =  KUP  NZD ${Math.max(price, 0).toFixed(2)}` };
}

function _renderP2Manual() {
  const wrapEl    = document.getElementById('p2-manual-options');
  const removedEl = document.getElementById('p2-manual-removed');
  if (!wrapEl || !removedEl) return;

  const options = _p2Manual[_p2ManualSeg];
  const active  = options.filter((x) => x.enabled);
  const inactive = options.filter((x) => !x.enabled);
  wrapEl.innerHTML = active.map((opt) => _p2OptionCardHtml(opt)).join('');
  _bindP2OptionEvents(wrapEl, options);

  removedEl.innerHTML = inactive.length
    ? `<span class="p2-removed-label">복원:</span>${inactive.map((opt) => `<button class="p2-add-btn" data-p2-op="add" data-key="${_escHtml(opt.key)}" type="button">+ ${_escHtml(opt.label)}</button>`).join('')}`
    : '';
  removedEl.querySelectorAll('[data-p2-op="add"]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const item = options.find((x) => x.key === btn.getAttribute('data-key'));
      if (item) { item.enabled = true; _renderP2Manual(); }
    });
  });

  _renderP2CustomAddSection();

  const calc = _calcP2Manual();
  const agg  = calc.kup * 0.9;
  const avg  = calc.kup;
  const cons = calc.kup * 1.1;
  const aggReason  = _p2ManualScenarioReason('aggressive',   _p2ManualSeg);
  const avgReason  = _p2ManualScenarioReason('average',      _p2ManualSeg);
  const consReason = _p2ManualScenarioReason('conservative', _p2ManualSeg);
  const aggFormula  = `KUP NZD ${calc.kup.toFixed(2)} × 0.90 = NZD ${agg.toFixed(2)}`;
  const avgFormula  = `KUP NZD ${avg.toFixed(2)} (기준가 그대로)`;
  const consFormula = `KUP NZD ${calc.kup.toFixed(2)} × 1.10 = NZD ${cons.toFixed(2)}`;
  _p2LastScenarios = { mode: 'manual', seg: _p2ManualSeg, base: calc.kup, agg, avg, cons, formulaStr: calc.formulaStr, aggReason, avgReason, consReason, aggFormula, avgFormula, consFormula, rationaleLines: [] };
}

function _p2OptionCardHtml(opt) {
  const isFixed = opt.type === 'gst_fixed';

  // 입력 필드 값 포맷
  const inputVal = opt.unit === 'rate' ? Number(opt.value).toFixed(4)
                 : opt.unit === '%'    ? Number(opt.value).toFixed(0)
                 :                       Number(opt.value).toFixed(2);
  // 단위 표시
  const unitLabel = opt.unit === '%' ? '%' : opt.unit === 'rate' ? '' : 'NZD';

  return `
    <div class="p2-step-card">
      <div class="p2-step-header">
        <button class="p2-step-toggle" data-p2-op="toggle" data-key="${_escHtml(opt.key)}" type="button">
          <span class="p2-step-label-text">${_escHtml(opt.label)}</span>
          <span class="p2-step-arrow">${opt.expanded ? '▾' : '▸'}</span>
        </button>
        <div class="p2-step-controls">
          ${isFixed
            ? `<span class="p2-step-val-display">÷ 1.09 고정</span>`
            : `${unitLabel ? `<span class="p2-step-unit-label" style="font-size:12px;color:var(--muted);margin-right:2px;">${_escHtml(unitLabel)}</span>` : ''}
               <input class="p2-step-input" type="number" data-p2-op="input" data-key="${_escHtml(opt.key)}" value="${inputVal}" step="${opt.step}" min="${opt.min}">`
          }
          ${opt.fixed ? '' : `<button class="p2-del-btn" data-p2-op="del" data-key="${_escHtml(opt.key)}" type="button" title="옵션 제거">×</button>`}
        </div>
      </div>
      ${opt.expanded ? `<div class="p2-step-body"><div class="p2-step-hint">${_escHtml(opt.hint || '')}</div><div class="p2-step-rationale">${_escHtml(opt.rationale || '')}</div></div>` : ''}
    </div>`;
}

function _bindP2OptionEvents(wrap, options) {
  wrap.querySelectorAll('[data-p2-op]').forEach((el) => {
    const op = el.getAttribute('data-p2-op');
    const key = el.getAttribute('data-key');
    const item = options.find((x) => x.key === key);
    if (!item) return;

    if (op === 'toggle') {
      el.addEventListener('click', () => {
        item.expanded = !item.expanded;
        _renderP2Manual();
      });
    } else if (op === 'del') {
      el.addEventListener('click', () => {
        item.enabled = false;
        item.expanded = false;
        _renderP2Manual();
      });
    } else if (op === 'input') {
      el.addEventListener('input', () => {
        const v = parseFloat(el.value);
        if (!Number.isNaN(v)) item.value = Math.max(item.min, v);
        _renderP2Manual();
      });
    }
  });
}

function _renderP2CustomAddSection() {
  const section = document.getElementById('p2-custom-add-section');
  if (!section) return;
  section.innerHTML = `
    <div class="p2-custom-add-row">
      <input class="p2-custom-input" id="p2c-label" type="text" placeholder="옵션명" maxlength="30" style="flex:2">
      <select class="p2-custom-type-select" id="p2c-type">
        <option value="pct_deduct">% 차감</option>
        <option value="pct_add_custom">% 가산</option>
        <option value="abs_add_custom">NZD 가산</option>
      </select>
      <input class="p2-custom-input" id="p2c-val" type="number" placeholder="값" step="0.1" min="0" max="999" style="width:80px;flex:0 0 80px">
      <button class="p2-add-custom-btn" id="p2c-add" type="button">+ 추가</button>
    </div>`;
  document.getElementById('p2c-add')?.addEventListener('click', () => {
    const label = (document.getElementById('p2c-label')?.value || '').trim();
    const type = document.getElementById('p2c-type')?.value || 'pct_deduct';
    const val = parseFloat(document.getElementById('p2c-val')?.value || '0');
    if (!label || Number.isNaN(val) || val < 0) return;
    _p2Manual[_p2ManualSeg].push({
      key: `custom_${Date.now()}`,
      label,
      value: val,
      type,
      unit: type === 'abs_add_custom' ? 'NZD' : '%',
      step: type === 'abs_add_custom' ? 0.1 : 1,
      min: 0,
      max: type === 'abs_add_custom' ? 9999 : 100,
      enabled: true,
      fixed: false,
      expanded: false,
      hint: '사용자 추가 옵션',
      rationale: '',
    });
    _resetP2ManualResultView();
    _renderP2Manual();
  });
}

function _p2ManualScenarioReason(type, seg) {
  if (type === 'aggressive') {
    return seg === 'public'
      ? '저마진 포지셔닝 — 시장 진입 초기, 자사가 손해를 감수하며 가격경쟁력을 앞세워 점유율을 선점합니다.'
      : '저마진 포지셔닝 — 민간 채널 초기 진입 시 자사 손해를 감수해 가격 경쟁력을 확보하고 처방·입고 채널을 빠르게 확대합니다.';
  }
  if (type === 'average') {
    return '중간 포지셔닝 — 현재 입력 옵션을 그대로 반영한 기본 산정가입니다. 리스크와 마진의 균형을 유지하는 표준 전략입니다.';
  }
  return seg === 'public'
    ? '고마진 포지셔닝 — 자사 제품이 시장 내 자리를 잡은 이후, 마진율을 높여 이익 확대를 노리는 전략입니다.'
    : '고마진 포지셔닝 — 제품이 민간 시장에 자리잡은 후 마진율을 높여 이익 확대를 노립니다. 브랜드 포지셔닝이 확립된 단계에 적합합니다.';
}

async function _generateP2Pdf() {
  const btn = document.getElementById('p2-pdf-btn-manual');
  const stateEl = document.getElementById('p2-pdf-state-manual');
  const sc = _p2LastScenarios;
  if (!sc) {
    if (stateEl) stateEl.textContent = '먼저 시나리오를 산정해 주세요.';
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.textContent = '생성 중…';
  }
  if (stateEl) stateEl.textContent = '';

  try {
    const report = _getP2SelectedReport();
    const body = {
      product_name: report ? (report.report_title || report.product || '제품명 미상') : '제품명 미상',
      verdict: report ? (report.verdict || '—') : '—',
      seg_label: sc.seg === 'public' ? '공공 시장' : '민간 시장',
      base_price: sc.base,
      formula_str: sc.formulaStr,
      mode_label: '직접 입력',
      scenarios: [
        { label: '저가 진입', price: sc.agg,  reason: sc.aggReason  || '', formula: sc.aggFormula  || '' },
        { label: '기준가',   price: sc.avg,  reason: sc.avgReason  || '', formula: sc.avgFormula  || '' },
        { label: '프리미엄', price: sc.cons, reason: sc.consReason || '', formula: sc.consFormula || '' },
      ],
      ai_rationale: [],
    };
    const res = await fetch('/api/p2/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.pdf) throw new Error(data.detail || `HTTP ${res.status}`);
    if (stateEl) {
      stateEl.innerHTML = `<a class="btn-download" href="/api/report/download?name=${encodeURIComponent(data.pdf)}" target="_blank" style="font-size:12px;padding:6px 14px;">다운로드</a>`;
    }
  } catch (err) {
    if (stateEl) stateEl.textContent = `생성 실패: ${err.message}`;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'PDF 생성';
    }
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §7. API 키 상태 (U1) — GET /api/keys/status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

async function loadKeyStatus() {
  try {
    const res  = await fetch('/api/keys/status');
    const data = await res.json();
    _applyKeyBadge('key-claude',     data.claude,     'Claude',     'API 키 설정됨',  'API 키 미설정 — 분석 불가');
    _applyKeyBadge('key-perplexity', data.perplexity, 'Perplexity', 'API 키 설정됨',  '미설정 — 논문 검색 생략');
  } catch (_) { /* 조용히 실패 */ }
}

function _applyKeyBadge(id, active, label, okTitle, ngTitle) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = 'key-badge ' + (active ? 'active' : 'inactive');
  el.title     = active ? `${label} ${okTitle}` : `${label} ${ngTitle}`;
  const dot    = el.querySelector('.key-badge-dot');
  if (dot) dot.style.background = active ? 'var(--green)' : 'var(--muted)';
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §7. 진행 단계 표시 (B2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * @param {string} currentStep  STEP_ORDER 내 현재 단계
 * @param {'running'|'done'|'error'} status
 */
function setProgress(currentStep, status) {
  const row = document.getElementById('progress-row');
  if (row) row.classList.add('visible');
  const idx = STEP_ORDER.indexOf(currentStep);

  for (let i = 0; i < STEP_ORDER.length; i++) {
    const el  = document.getElementById('prog-' + STEP_ORDER[i]);
    if (!el) continue;
    const dot = el.querySelector('.prog-dot');

    if (status === 'error' && i === idx) {
      el.className    = 'prog-step error';
      dot.textContent = '✕';
    } else if (i < idx || (i === idx && status === 'done')) {
      el.className    = 'prog-step done';
      dot.textContent = '✓';
    } else if (i === idx) {
      el.className    = 'prog-step active';
      dot.textContent = i + 1;
    } else {
      el.className    = 'prog-step';
      dot.textContent = i + 1;
    }
  }
}

function resetProgress() {
  const row = document.getElementById('progress-row');
  if (row) row.classList.remove('visible');
  for (let i = 0; i < STEP_ORDER.length; i++) {
    const el = document.getElementById('prog-' + STEP_ORDER[i]);
    if (!el) continue;
    el.className = 'prog-step';
    el.querySelector('.prog-dot').textContent = i + 1;
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §8. 파이프라인 실행 & 폴링
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * 선택 품목 파이프라인 실행.
 * U6: 재분석 버튼도 이 함수를 호출.
 */
async function runPipeline() {
  const productKey = document.getElementById('product-select').value;
  _currentKey      = productKey;

  // UI 초기화
  resetProgress();
  _hideP1Note();
  _showP1Loading();
  document.getElementById('result-card').classList.remove('visible');
  document.getElementById('papers-card').classList.remove('visible');
  document.getElementById('report-card').classList.remove('visible');
  document.getElementById('btn-analyze').disabled = true;
  document.getElementById('btn-icon').textContent  = '⏳';

  const reBtn = document.getElementById('btn-reanalyze');
  if (reBtn) reBtn.style.display = 'none';

  // B2: db_load 단계 먼저 활성화
  setProgress('db_load', 'running');

  try {
    const res = await fetch(`/api/pipeline/${encodeURIComponent(productKey)}`, { method: 'POST' });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      console.error('파이프라인 오류:', d.detail || res.status);
      setProgress('db_load', 'error');
      _resetBtn();
      return;
    }
    _pollTimer = setInterval(() => pollPipeline(productKey), 2500);
  } catch (e) {
    console.error('요청 실패:', e);
    setProgress('db_load', 'error');
    _resetBtn();
  }
}

function _resetBtn() {
  document.getElementById('btn-analyze').disabled = false;
  document.getElementById('btn-icon').textContent  = '▶';
  _hideP1Loading();
}

/**
 * GET /api/pipeline/{product_key}/status 를 주기적으로 폴링.
 * 서버 step: init → db_load → analyze → refs → report → done
 */
async function pollPipeline(productKey) {
  try {
    const res = await fetch(`/api/pipeline/${encodeURIComponent(productKey)}/status`);
    const d   = await res.json();

    if (d.status === 'idle') return;

    // B2: 서버 step → 프론트 STEP_ORDER 매핑
    if      (d.step === 'db_load')  { setProgress('db_load',  'running'); }
    else if (d.step === 'analyze')  { setProgress('db_load',  'done'); setProgress('analyze', 'running'); }
    else if (d.step === 'refs')     { setProgress('analyze',  'done'); setProgress('refs',    'running'); }
    else if (d.step === 'report')   {
      setProgress('refs', 'done'); setProgress('report', 'running');
      _showReportLoading();
    }

    if (d.status === 'done') {
      clearInterval(_pollTimer);
      _hideP1Loading();
      for (const s of STEP_ORDER) setProgress(s, 'done');
      const r2   = await fetch(`/api/pipeline/${encodeURIComponent(productKey)}/result`);
      const data = await r2.json();
      renderResult(data.result, data.refs, data.pdf);
      _resetBtn();
    }

    if (d.status === 'error') {
      clearInterval(_pollTimer);
      _hideP1Loading();
      setProgress(STEP_ORDER.includes(d.step) ? d.step : 'analyze', 'error');
      _resetBtn();
    }
  } catch (_) { /* 조용히 재시도 */ }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §9. 신약 분석 파이프라인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

let _customPollTimer = null;
const CUSTOM_STEP_ORDER = ['analyze', 'refs', 'report'];

function _setCustomProgress(step, status) {
  const row = document.getElementById('custom-progress-row');
  if (row) row.classList.add('visible');
  const idMap = { analyze: 'cprog-analyze', refs: 'cprog-refs', report: 'cprog-report' };
  const idx   = CUSTOM_STEP_ORDER.indexOf(step);

  CUSTOM_STEP_ORDER.forEach((s, i) => {
    const el  = document.getElementById(idMap[s]);
    if (!el) return;
    const dot = el.querySelector('.prog-dot');
    if (status === 'error' && i === idx) {
      el.className = 'prog-step error'; dot.textContent = '✕';
    } else if (i < idx || (i === idx && status === 'done')) {
      el.className = 'prog-step done';  dot.textContent = '✓';
    } else if (i === idx) {
      el.className = 'prog-step active'; dot.textContent = i + 1;
    } else {
      el.className = 'prog-step'; dot.textContent = i + 1;
    }
  });
}

function _resetCustomProgress() {
  const row = document.getElementById('custom-progress-row');
  if (row) row.classList.remove('visible');
  CUSTOM_STEP_ORDER.forEach((s, i) => {
    const el = document.getElementById('cprog-' + s);
    if (!el) return;
    el.className = 'prog-step';
    el.querySelector('.prog-dot').textContent = i + 1;
  });
}

function _resetCustomBtn() {
  document.getElementById('btn-custom').disabled = false;
  document.getElementById('custom-icon').textContent = '▶';
  _hideCustomLoading();
}

async function runCustomPipeline() {
  const tradeName = document.getElementById('custom-trade-name').value.trim();
  const inn       = document.getElementById('custom-inn').value.trim();
  const dosage    = document.getElementById('custom-dosage').value.trim();
  if (!tradeName || !inn) { alert('약품명과 성분명을 입력하세요.'); return; }

  _resetCustomProgress();
  _showCustomLoading();
  document.getElementById('result-card').classList.remove('visible');
  document.getElementById('papers-card').classList.remove('visible');
  document.getElementById('report-card').classList.remove('visible');
  document.getElementById('btn-custom').disabled = true;
  document.getElementById('custom-icon').textContent = '⏳';

  if (_customPollTimer) clearInterval(_customPollTimer);
  _setCustomProgress('analyze', 'running');

  try {
    const res = await fetch('/api/pipeline/custom', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ trade_name: tradeName, inn, dosage_form: dosage }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      console.error('신약 분석 오류:', d.detail || res.status);
      _setCustomProgress('analyze', 'error');
      _resetCustomBtn();
      return;
    }
    _customPollTimer = setInterval(_pollCustomPipeline, 2500);
  } catch (e) {
    console.error('요청 실패:', e);
    _setCustomProgress('analyze', 'error');
    _resetCustomBtn();
  }
}

async function _pollCustomPipeline() {
  try {
    const res = await fetch('/api/pipeline/custom/status');
    const d   = await res.json();
    if (d.status === 'idle') return;

    if      (d.step === 'analyze') { _setCustomProgress('analyze', 'running'); }
    else if (d.step === 'refs')    { _setCustomProgress('analyze', 'done'); _setCustomProgress('refs', 'running'); }
    else if (d.step === 'report')  { _setCustomProgress('refs', 'done'); _setCustomProgress('report', 'running'); _showReportLoading(); }

    if (d.status === 'done') {
      clearInterval(_customPollTimer);
      _hideCustomLoading();
      for (const s of CUSTOM_STEP_ORDER) _setCustomProgress(s, 'done');
      const r2   = await fetch('/api/pipeline/custom/result');
      const data = await r2.json();
      renderResult(data.result, data.refs, data.pdf);
      _resetCustomBtn();
    }
    if (d.status === 'error') {
      clearInterval(_customPollTimer);
      _hideCustomLoading();
      _setCustomProgress(d.step || 'analyze', 'error');
      _resetCustomBtn();
    }
  } catch (_) { /* 조용히 재시도 */ }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §10. 결과 렌더링 (U2·U3·U4·U6·B4·N3·N4)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

/**
 * 분석 완료 후 결과·논문·PDF 카드를 화면에 렌더링.
 * @param {object|null} result  분석 결과
 * @param {Array}       refs    Perplexity 논문 목록
 * @param {string|null} pdfName PDF 파일명
 */
function renderResult(result, refs, pdfName) {

  /* ─ 분석 결과 카드 ─ */
  if (result) {
    if (result.error) {
      document.getElementById('verdict-badge').className   = 'verdict-badge v-err';
      document.getElementById('verdict-badge').textContent = '분석 데이터 오류';
      document.getElementById('verdict-name').textContent  = result.trade_name || result.product_id || '';
      document.getElementById('verdict-inn').textContent   = INN_MAP[result.product_id] || result.inn || '';
      _setText('basis-market-medical', String(result.error || '데이터 오류'));
      _setText('basis-regulatory',     '품목 메타/DB 매핑 확인 필요');
      _setText('basis-trade',          '재실행 후 동일하면 서버 로그 점검');
      _setText('basis-pbs-line',       '참고 가격 정보 없음');
      const pathEl = document.getElementById('entry-pathway');
      if (pathEl) {
        pathEl.textContent = '진입 채널 권고 데이터 확인 필요';
        pathEl.style.display = 'block';
        pathEl.classList.add('empty');
      }
      _setText('price-positioning-pbs', '가격 포지셔닝 데이터를 불러오지 못했습니다.');
      _setText('risks-conditions', '분석 데이터 소스 확인 후 재시도해 주세요.');
      _showP1Note('⚠️ 분석 데이터 오류 — 재시도하거나 서버 로그를 확인하세요.', true);
      _showReportError();
      return;
    }

    const verdict = result.verdict;
    const vc      = verdict === '적합'   ? 'v-ok'
                  : verdict === '부적합' ? 'v-err'
                  : verdict             ? 'v-warn'
                  :                       'v-none';
    const err    = result.analysis_error;
    const vLabel = verdict
      || (err === 'no_api_key'    ? 'API 키 미설정'
        : err === 'claude_failed' ? 'Claude 분석 실패'
        :                           '미분석');

    document.getElementById('verdict-badge').className   = `verdict-badge ${vc}`;
    document.getElementById('verdict-badge').textContent = vLabel;
    document.getElementById('verdict-name').textContent  = result.trade_name || result.product_id || '';
    document.getElementById('verdict-inn').textContent   = INN_MAP[result.product_id] || result.inn || '';

    // S2: 신호등
    ['tl-red', 'tl-yellow', 'tl-green'].forEach(id => {
      document.getElementById(id).classList.remove('on');
    });
    if (verdict === '적합')        document.getElementById('tl-green').classList.add('on');
    else if (verdict === '부적합') document.getElementById('tl-red').classList.add('on');
    else if (verdict)              document.getElementById('tl-yellow').classList.add('on');

    // S3: 판정 근거
    const basisFallback = _deriveBasisFromRationale(result.rationale);
    _setText('basis-market-medical', _formatDetailed(result.basis_market_medical || basisFallback.marketMedical));
    _setText('basis-regulatory',     _formatDetailed(result.basis_regulatory     || basisFallback.regulatory));
    _setText('basis-trade',          _formatDetailed(result.basis_trade          || basisFallback.trade));
    _setText('basis-pbs-line',       _pbsLineFromApi(result));

    // S4: 진입 채널
    const pathEl = document.getElementById('entry-pathway');
    if (pathEl) {
      const pathText = String(result.entry_pathway || '').trim();
      pathEl.textContent = pathText || '진입 채널 권고 데이터 확인 필요';
      pathEl.style.display = 'block';
      pathEl.classList.toggle('empty', !pathText);
    }

    const pbsPos = String(result.price_positioning_pbs || '').trim();
    _setText('price-positioning-pbs', _formatDetailed(pbsPos || _pbsLineFromApi(result)));

    const riskText = String(result.risks_conditions || '').trim()
      || (Array.isArray(result.key_factors) ? result.key_factors.join(' / ') : '');
    _setText('risks-conditions', _formatDetailed(riskText));

    // 완료 노트 표시 (result-card는 숨김 DOM이므로 visible 처리 안 함)
    _showP1Note(
      `✅ ${result.trade_name || '제품'} 분석 완료 - 가격 분석을 진행하세요.`,
      false
    );
  }

  /* ─ B4: 논문 카드 ─ */
  const papersCard = document.getElementById('papers-card');
  const papersList = document.getElementById('papers-list');
  papersList.innerHTML = '';

  if (refs && refs.length > 0) {
    for (const ref of refs) {
      const item     = document.createElement('div');
      item.className = 'paper-item';
      const safeUrl  = /^https?:\/\//.test(ref.url || '') ? ref.url : '#';
      item.innerHTML = `
        <span class="paper-arrow">▸</span>
        <div>
          <div>
            <a class="paper-link" href="${safeUrl}" target="_blank" rel="noopener noreferrer"></a>
            <span class="paper-src"></span>
          </div>
          <div class="paper-reason"></div>
        </div>`;
      item.querySelector('.paper-link').textContent   = ref.title || ref.url || '';
      item.querySelector('.paper-src').textContent    = ref.source ? `[${ref.source}]` : '';
      item.querySelector('.paper-reason').textContent = ref.reason || '';
      papersList.appendChild(item);
    }
    papersCard.classList.add('visible');
  } else {
    papersCard.classList.remove('visible');
  }

  /* ─ U4: PDF 보고서 카드 ─ */
  // N4: 보고서 탭에 자동 등록 (PDF 성공 여부 무관)
  _addReportEntry(result, pdfName);
  if (pdfName) {
    _showReportOk(pdfName);
    // N3: 보고서 완료 → Todo 자동 체크
    markTodoDone('rep');
  } else {
    _showReportError();
  }
}

/** U4: PDF 생성 중 */
function _showReportLoading() {
  const preview = document.getElementById('pdf-preview-frame');
  if (preview) preview.setAttribute('src', 'about:blank');
  document.getElementById('report-state-loading').style.display = 'flex';
  document.getElementById('report-state-ok').style.display      = 'none';
  document.getElementById('report-state-error').style.display   = 'none';
  document.getElementById('report-card').classList.add('visible');
}

/** U4: PDF 생성 완료 */
function _showReportOk(pdfName) {
  const dl = document.querySelector('#report-state-ok .btn-download');
  const baseQ = pdfName ? `name=${encodeURIComponent(pdfName)}` : '';
  const downloadUrl = `/api/report/download${baseQ ? `?${baseQ}` : ''}`;
  if (dl) dl.setAttribute('href', downloadUrl);
  // iframe 제거됨 — null-safe 처리
  const preview = document.getElementById('pdf-preview-frame');
  if (preview) {
    const previewUrl = `/api/report/download?${baseQ ? `${baseQ}&` : ''}inline=1`;
    preview.setAttribute('src', previewUrl);
  }
  document.getElementById('report-state-loading').style.display = 'none';
  document.getElementById('report-state-ok').style.display      = 'block';
  document.getElementById('report-state-error').style.display   = 'none';
  document.getElementById('report-card').classList.add('visible');
}

/** U4: PDF 생성 실패 */
function _showReportError() {
  const preview = document.getElementById('pdf-preview-frame');
  if (preview) preview.setAttribute('src', 'about:blank');
  document.getElementById('report-state-loading').style.display = 'none';
  document.getElementById('report-state-ok').style.display      = 'none';
  document.getElementById('report-state-error').style.display   = 'block';
  document.getElementById('report-card').classList.add('visible');
}

/* ─ 유틸 함수 ─ */

function _setText(id, value, fallback = '—') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = String(value || '').trim() || fallback;
}

function _deriveBasisFromRationale(rationale) {
  const text  = String(rationale || '');
  const lines = text.split('\n').map(x => x.trim()).filter(Boolean);
  const out   = { marketMedical: '', regulatory: '', trade: '' };
  for (const line of lines) {
    const low = line.toLowerCase();
    if (!out.marketMedical && (low.includes('시장') || low.includes('의료'))) {
      out.marketMedical = line.replace(/^[\-\d\.\)\s]+/, ''); continue;
    }
    if (!out.regulatory && low.includes('규제')) {
      out.regulatory = line.replace(/^[\-\d\.\)\s]+/, ''); continue;
    }
    if (!out.trade && low.includes('무역')) {
      out.trade = line.replace(/^[\-\d\.\)\s]+/, ''); continue;
    }
  }
  if (!out.marketMedical && lines.length > 0) out.marketMedical = lines[0];
  if (!out.regulatory    && lines.length > 1) out.regulatory    = lines[1];
  if (!out.trade         && lines.length > 2) out.trade         = lines[2];
  return out;
}

function _formatDetailed(text) {
  const src = String(text || '').trim();
  if (!src) return '';
  const lines   = src.split('\n').map(x => x.trim()).filter(Boolean);
  const cleaned = lines.map(l =>
    l.replace(/^[\-\•\*\·]\s+/, '').replace(/^\d+[\.\)]\s+/, '')
  );
  let joined = '';
  for (const part of cleaned) {
    if (!joined) { joined = part; continue; }
    const prev = joined.trimEnd();
    const ends = prev.endsWith('.') || prev.endsWith('!') || prev.endsWith('?')
              || prev.endsWith('다') || prev.endsWith('음') || prev.endsWith('임');
    joined += ends ? ' ' + part : ', ' + part;
  }
  return joined;
}

function _pbsLineFromApi(result) {
  const aud    = result.pbs_dpmq_aud;
  const sgd    = result.pbs_dpmq_sgd_hint;
  const audNum = aud != null && aud !== '' ? Number(aud) : NaN;
  if (!Number.isNaN(audNum)) {
    const sNum = sgd != null && sgd !== '' ? Number(sgd) : NaN;
    let t = `DPMQ AUD ${audNum.toFixed(2)}`;
    if (!Number.isNaN(sNum)) t += `, 참고 NZD ${sNum.toFixed(2)}`;
    return t;
  }
  const haiku = String(result.pbs_haiku_estimate || '').trim();
  if (haiku) return haiku;
  return '참고 가격 정보 없음';
}

/** 시장조사 완료/오류 노트 표시 */
function _showP1Note(msg, isErr) {
  const el = document.getElementById('p1-result-note');
  if (!el) return;
  el.textContent = msg;
  el.className   = 'p1-result-note' + (isErr ? ' err' : '');
  el.style.display = '';
}

function _hideP1Note() {
  const el = document.getElementById('p1-result-note');
  if (el) el.style.display = 'none';
}

/** XSS 방지 HTML 이스케이프 */
function _escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}



/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §10-b. 메인 프리뷰 탭 · 통계 · 지도 · 뉴스
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

function goPreviewTab(id, el) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('on'));
  document.querySelectorAll('.topbar-tab').forEach(t => t.classList.remove('on'));
  const page = document.getElementById(id);
  if (page) page.classList.add('on');
  if (el)   el.classList.add('on');
  if (id === 'preview' && !window._previewMapInited) {
    setTimeout(initPreviewMap, 50);
    window._previewMapInited = true;
  }
}

async function loadPreviewStats() {
  try {
    const res = await fetch('/api/preview/stats');
    const d   = await res.json();
    _setPStat('psc-gdp',    d.gdp?.value          || 'US$ 48,800',  'psc-gdp-src',    '');
    _setPStat('psc-pop',    d.population?.value   || '5,127,000명', 'psc-pop-src',    d.population?.source   || 'Stats NZ');
    _setPStat('psc-pharma', d.pharma_market?.value || '$1.26B',     'psc-pharma-src', d.pharma_market?.source || 'Statista');
    _setPStat('psc-import', d.import_dep?.value   || '~90%',        'psc-import-src', d.import_dep?.source   || 'KOTRA / ITA');
  } catch (_) {
    _setPStat('psc-gdp',    'US$ 48,800',  'psc-gdp-src',    '');
    _setPStat('psc-pop',    '5,127,000명', 'psc-pop-src',    'Stats NZ');
    _setPStat('psc-pharma', '$1.26B',      'psc-pharma-src', 'Statista');
    _setPStat('psc-import', '~90%',        'psc-import-src', 'KOTRA / ITA');
  }
}

function _setPStat(valId, val, srcId, src) {
  const ve = document.getElementById(valId);
  const se = document.getElementById(srcId);
  if (ve) ve.textContent = val;
  if (se) se.textContent = src;
}

function initPreviewMap() {
  if (typeof L === 'undefined') { setTimeout(initPreviewMap, 200); return; }
  const el = document.getElementById('preview-map');
  if (!el) return;
  if (el.classList.contains('leaflet-container')) {
    if (window._previewMapInstance) window._previewMapInstance.invalidateSize();
    return;
  }
  if (el.offsetWidth === 0) { setTimeout(initPreviewMap, 200); return; }
  const map = L.map('preview-map', { zoomControl: true }).setView([-41.5, 173.5], 5);
  window._previewMapInstance = map;
  window._previewMapInited   = true;
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 18,
  }).addTo(map);
  try {
    L.marker([-41.2865, 174.7762]).addTo(map).bindPopup('<b>New Zealand</b><br>Wellington · PHARMAC / Medsafe').openPopup();
  } catch (_) {}
  setTimeout(() => map.invalidateSize(), 150);
}

async function loadPreviewNews() {
  const listEl = document.getElementById('preview-news-list');
  const btn    = document.getElementById('btn-preview-news-refresh');
  if (!listEl) return;
  if (btn) btn.disabled = true;
  listEl.innerHTML = '<div class="pvnews-loading">뉴스 로드 중…</div>';
  try {
    const res  = await fetch('/api/news');
    const data = await res.json();
    if (!data.ok || !data.items?.length) {
      listEl.innerHTML = `<div class="pvnews-empty">${data.error || '뉴스를 불러올 수 없습니다.'}</div>`;
      return;
    }
    listEl.innerHTML = data.items.map(item => {
      const href   = item.link ? `href="${_escHtml(item.link)}" target="_blank" rel="noopener"` : '';
      const tag    = item.link ? 'a' : 'div';
      const source = [item.source, item.date].filter(Boolean).join(' · ');
      return `<${tag} class="pvnews-item" ${href}>
        <div class="pvnews-title">${_escHtml(item.title)}</div>
        ${source ? `<div class="pvnews-source">${_escHtml(source)}</div>` : ''}
      </${tag}>`;
    }).join('');
  } catch (_) {
    listEl.innerHTML = '<div class="pvnews-empty">뉴스 조회 실패 — 잠시 후 다시 시도해 주세요</div>';
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §11. 시장 신호 · 뉴스 (Perplexity)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

async function loadNews() {
  const listEl = document.getElementById('news-list');
  const btn    = document.getElementById('btn-news-refresh');
  if (!listEl) return;

  if (btn) btn.disabled = true;
  listEl.innerHTML = '<div class="irow" style="color:var(--muted);font-size:12px;text-align:center;padding:20px 0;">뉴스 로드 중…</div>';

  try {
    const res  = await fetch('/api/news');
    const data = await res.json();

    if (!data.ok || !data.items?.length) {
      listEl.innerHTML = `<div class="irow" style="color:var(--muted);font-size:12px;text-align:center;padding:16px 0;">${data.error || '뉴스를 불러올 수 없습니다.'}</div>`;
      return;
    }

    listEl.innerHTML = data.items.map(item => {
      const href   = item.link ? `href="${_escHtml(item.link)}" target="_blank" rel="noopener"` : '';
      const tag    = item.link ? 'a' : 'div';
      const source = [item.source, item.date].filter(Boolean).join(' · ');
      return `
        <${tag} class="irow news-item" ${href} style="${item.link ? 'text-decoration:none;display:block;' : ''}">
          <div class="tit">${_escHtml(item.title)}</div>
          ${source ? `<div class="sub">${_escHtml(source)}</div>` : ''}
        </${tag}>`;
    }).join('');
  } catch (e) {
    listEl.innerHTML = '<div class="irow" style="color:var(--muted);font-size:12px;text-align:center;padding:16px 0;">뉴스 조회 실패 — 잠시 후 다시 시도해 주세요</div>';
    console.warn('뉴스 로드 실패:', e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §11. 3공정 — 바이어 발굴 (P3)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

let _p3PollTimer        = null;
let _p3Buyers           = [];
let _p3DisplayedBuyers  = [];   // 재배열 후 현재 표시 순서
let _p3PdfName          = null;
let _p3SelectedReportId = '';

function _syncP3ReportOptions() {
  const sel = document.getElementById('p3-report-select');
  if (!sel) return;
  const p1Reports = _loadReports().filter(r => r.report_type === 'p1');
  sel.innerHTML = ['<option value="">시장조사 보고서를 선택하세요</option>']
    .concat(p1Reports.map(r => {
      const name = r.product || r.report_title || '보고서';
      return `<option value="${r.id}">시장조사 보고서 · ${_escHtml(name)} · ${_escHtml(r.timestamp || '')}</option>`;
    })).join('');

  const readyBanner    = document.getElementById('p3-ready-banner');
  const noReportBanner = document.getElementById('p3-no-report-banner');
  if (p1Reports.length) {
    if (readyBanner)    readyBanner.style.display    = '';
    if (noReportBanner) noReportBanner.style.display = 'none';
    if (!_p3SelectedReportId || !p1Reports.find(r => String(r.id) === _p3SelectedReportId)) {
      _p3SelectedReportId = String(p1Reports[0].id);
    }
    sel.value = _p3SelectedReportId;
  } else {
    if (readyBanner)    readyBanner.style.display    = 'none';
    if (noReportBanner) noReportBanner.style.display = '';
  }
}

function onP3ReportChange() {
  const sel = document.getElementById('p3-report-select');
  _p3SelectedReportId = sel?.value || '';
  // 선택된 보고서의 품목을 product-select에 연동
  const report = _loadReports().find(r => String(r.id) === _p3SelectedReportId);
  if (!report) return;
  const productSel = document.getElementById('product-select');
  if (!productSel) return;
  const productName = (report.product || '').toLowerCase();
  const matched = [...productSel.options].find(o =>
    o.text.toLowerCase().includes(productName) || productName.includes(o.value.split('_')[1] || '')
  );
  if (matched) productSel.value = matched.value;
}

function _p3Log(msg, level = 'info') {
  const box = document.getElementById('p3-log-box');
  if (!box) return;
  if (box.querySelector('.log-line.log-info')?.textContent === '— 로그 대기 중 —') box.innerHTML = '';
  const line = document.createElement('div');
  line.className = `log-line log-${level}`;
  const now = new Date().toLocaleTimeString('ko-KR');
  line.textContent = `[${now}] ${msg}`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function _setP3Progress(stepId, state) {
  const el  = document.getElementById('p3prog-' + stepId);
  const dot = el?.querySelector('.prog-dot');
  if (!el) return;
  const labels = { crawl: '1', enrich: '2', rank: '3', report: '4' };
  const num = labels[stepId] || '?';
  if (state === 'done')  { el.className = 'prog-step done';  if (dot) dot.textContent = '✓'; }
  else if (state === 'active') { el.className = 'prog-step active'; if (dot) dot.textContent = num; }
  else if (state === 'error')  { el.className = 'prog-step error';  if (dot) dot.textContent = '✕'; }
  else                         { el.className = 'prog-step';        if (dot) dot.textContent = num; }
}

function _showP3Progress() {
  const row = document.getElementById('p3-progress-row');
  if (row) row.classList.add('visible');
}

function _resetP3Progress() {
  const row = document.getElementById('p3-progress-row');
  if (row) row.classList.remove('visible');
  for (const s of ['crawl', 'enrich', 'rank', 'report']) _setP3Progress(s, '');
}

/* SSE 실시간 로그 수신 */
(function _startP3SSE() {
  const es = new EventSource('/api/stream');
  es.onmessage = (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.phase === 'buyer') _p3Log(d.message, d.level || 'info');
    } catch (_) {}
  };
  es.onerror = () => {};
})();

async function runP3Pipeline() {
  const btn     = document.getElementById('btn-p3-run');
  const icon    = document.getElementById('p3-run-icon');
  const errEl   = document.getElementById('p3-error-msg');
  const product = document.getElementById('product-select')?.value || 'NZ_sereterol_activair';
  const targetCountry = 'New Zealand';
  const targetRegion  = 'Oceania';

  if (btn) btn.disabled = true;
  if (icon) icon.textContent = '…';
  if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }
  // 스켈레톤으로 레이아웃 고정 후 섹션 표시
  _renderP3Skeleton();
  document.getElementById('p3-result-section').style.display = '';
  const _p3LoadEl = document.getElementById('p3-loading-state');
  if (_p3LoadEl) _p3LoadEl.style.display = '';

  try {
    const res = await fetch('/api/buyers/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        product_key:    product,
        target_country: targetCountry,
        target_region:  targetRegion,
      }),
    });
    const data = await res.json();
    // 409 = 이미 실행 중 → 폴링만 시작
    if (res.status !== 409 && !res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    if (data.task_id) sessionStorage.setItem('p3_task_id', data.task_id);
    if (_p3PollTimer) clearInterval(_p3PollTimer);
    _p3PollTimer = setInterval(_pollP3, 2500);
  } catch (e) {
    if (errEl) { errEl.style.display = ''; errEl.textContent = `오류: ${e.message}`; }
    if (btn) btn.disabled = false;
    if (icon) icon.textContent = '▶';
    const _p3LoadElErr = document.getElementById('p3-loading-state');
    if (_p3LoadElErr) _p3LoadElErr.style.display = 'none';
  }
}

/* 페이지 로드 시 실행 중인 파이프라인 자동 감지 — 내가 시작한 작업만 재개 */
(async function _p3AutoResume() {
  try {
    const res  = await fetch('/api/buyers/status');
    const data = await res.json();
    const myTaskId = sessionStorage.getItem('p3_task_id');
    const isMyTask = myTaskId && myTaskId === data.task_id;
    if (data.status === 'running' && isMyTask) {
      const btn  = document.getElementById('btn-p3-run');
      const icon = document.getElementById('p3-run-icon');
      if (btn)  btn.disabled = true;
      if (icon) icon.textContent = '…';
      const _p3LoadElResume = document.getElementById('p3-loading-state');
      if (_p3LoadElResume) _p3LoadElResume.style.display = '';
      if (_p3PollTimer) clearInterval(_p3PollTimer);
      _p3PollTimer = setInterval(_pollP3, 2500);
    } else if (data.status === 'done' && isMyTask) {
      const rr     = await fetch('/api/buyers/result');
      const result = await rr.json();
      _p3Buyers  = [];                    // 재방문 시 카드 초기화
      _p3PdfName = result.pdf || null;
      document.getElementById('p3-result-section').style.display = '';
      document.getElementById('p3-cards').innerHTML = '';
    }
  } catch (_) {}
})();

async function _pollP3() {
  try {
    const res  = await fetch('/api/buyers/status');
    const data = await res.json();

    const stepOrder = ['crawl', 'enrich', 'rank', 'report'];

    if (data.status === 'done') {
      clearInterval(_p3PollTimer); _p3PollTimer = null;
      const _p3LoadDone = document.getElementById('p3-loading-state');
      if (_p3LoadDone) _p3LoadDone.style.display = 'none';
      _p3Log('파이프라인 완료 — 결과 불러오는 중…', 'success');

      const rr     = await fetch('/api/buyers/result');
      const result = await rr.json();
      _p3Buyers  = result.buyers || [];
      _p3PdfName = result.pdf   || null;
      _p3Log(`Top ${_p3Buyers.length}개 바이어 선정`, 'success');

      _renderP3Cards(_p3Buyers);
      document.getElementById('p3-result-section').style.display = '';
      if (_p3PdfName) {
        _addReportEntry({ trade_name: '바이어 발굴', inn: null, verdict: '—' }, _p3PdfName, 'p3');
      }
      const btn  = document.getElementById('btn-p3-run');
      const icon = document.getElementById('p3-run-icon');
      if (btn)  btn.disabled = false;
      if (icon) icon.textContent = '▶';

    } else if (data.status === 'error') {
      clearInterval(_p3PollTimer); _p3PollTimer = null;
      const errEl = document.getElementById('p3-error-msg');
      if (errEl) { errEl.style.display = ''; errEl.textContent = `오류: ${data.step_label || '파이프라인 실패'}`; }
      const _p3LoadErr = document.getElementById('p3-loading-state');
      if (_p3LoadErr) _p3LoadErr.style.display = 'none';
      _p3Log(`오류: ${data.step_label || '파이프라인 실패'}`, 'error');
      const btn  = document.getElementById('btn-p3-run');
      const icon = document.getElementById('p3-run-icon');
      if (btn)  btn.disabled = false;
      if (icon) icon.textContent = '▶';
    }
  } catch (_) {}
}


/** Top 10 스켈레톤 렌더링 (로딩 중 표시) */
function _renderP3Skeleton() {
  const wrap = document.getElementById('p3-cards');
  if (!wrap) return;
  wrap.innerHTML = Array.from({ length: 10 }, (_, i) => `
    <div class="p3-list-row" style="pointer-events:none;">
      <span class="p3-card-rank" style="opacity:.25;">${i + 1}</span>
      <div class="p3-skel-line" style="height:13px;width:55%;border-radius:4px;"></div>
    </div>`).join('');
}

/** Top 10 리스트 렌더링 — 번호 + 회사명만 표시, 클릭 시 상세 모달 */
function _renderP3Cards(buyers) {
  const wrap = document.getElementById('p3-cards');
  if (!wrap) return;

  _p3DisplayedBuyers = buyers;

  if (!buyers.length) {
    wrap.innerHTML = '<div class="p3-empty">발굴된 바이어가 없습니다.</div>';
    return;
  }

  wrap.innerHTML = buyers.map((b, i) => `
    <div class="p3-list-row" onclick="showBuyerDetail(${i})">
      <span class="p3-card-rank">${i + 1}</span>
      <span class="p3-list-name">${_escHtml(b.company_name || '-')}</span>
    </div>`).join('');

  const criteriaBox = document.getElementById('p3-criteria-box');
  const cardsTitle  = document.getElementById('p3-cards-title');
  const reportBar   = document.getElementById('p3-report-bar');
  if (criteriaBox) criteriaBox.style.display = '';
  if (cardsTitle)  cardsTitle.style.display  = '';
  if (reportBar)   reportBar.style.display   = '';
}

/** 바이어 상세 모달 열기 — 재배열 후에도 현재 표시 순서(_p3DisplayedBuyers) 기준 */
function showBuyerDetail(idx) {
  const b = _p3DisplayedBuyers[idx] || _p3Buyers[idx];
  if (!b) return;
  const e = b.enriched || {};
  const priLabel = b.priority === 1 ? '성분 일치' : 'New Zealand';
  const priClass = b.priority === 1 ? 'p3-tag-p1' : 'p3-tag-p2';

  function row(label, val) {
    if (!val || val === '-' || val === null || val === undefined) return '';
    return `<tr><th>${label}</th><td>${_escHtml(String(val))}</td></tr>`;
  }
  function ynRow(label, val) {
    if (val === true)  return `<tr><th>${label}</th><td><span class="bm-yes">✓ 있음</span></td></tr>`;
    if (val === false) return `<tr><th>${label}</th><td><span class="bm-no">✗ 없음</span></td></tr>`;
    return '';
  }

  const hasSource = (e.source_urls || []).length > 0 || !!b.perplexity_text;
  const matched    = (b.matched_ingredients || []).join(' · ');
  const territories = (e.territories || []).join(', ');

  const metaParts = [b.country, b.category].filter(v => v && v !== '-').map(v => _escHtml(v)).join(' · ');

  const contactRows = [
    row('주소', b.address), row('전화', b.phone), row('팩스', b.fax),
    row('이메일', b.email), row('웹사이트', b.website), row('부스', b.booth),
  ].join('');

  const sizeRows = [
    row('연 매출', e.revenue), row('임직원 수', e.employees), row('설립연도', e.founded),
    territories ? `<tr><th>사업 지역</th><td>${_escHtml(territories)}</td></tr>` : '',
  ].join('');

  const capRows = [
    ynRow('GMP 인증', e.has_gmp),
    ynRow('수입 이력', e.import_history),
    ynRow('공공조달 이력', e.procurement_history),
  ].join('');

  const channelRows = [
    ynRow('공공 채널', e.public_channel), ynRow('민간 채널', e.private_channel),
    ynRow('약국 체인', e.has_pharmacy_chain), ynRow('MAH 대행', e.mah_capable),
    row('한국 거래 경험', e.korea_experience),
  ].join('');

  const overview   = (e.company_overview_kr    || '').trim();
  const reason     = (e.recommendation_reason  || '').trim();

  document.getElementById('buyer-modal-body').innerHTML = `
    <div class="bm-header">
      <div class="bm-rank">${idx+1}</div>
      <div class="bm-title">
        <div class="bm-name">${_escHtml(b.company_name || '-')}</div>
        <div class="bm-meta">${metaParts}
          <span class="p3-tag ${priClass}" style="margin-left:6px;">${priLabel}</span>
        </div>
      </div>
    </div>

    ${overview && overview !== '-' ? `<div class="bm-section">기업 개요</div><div class="bm-summary">${_escHtml(overview)}</div>` : ''}
    ${reason  && reason  !== '-' ? `<div class="bm-section">채택 이유</div><div class="bm-summary">${_escHtml(reason)}</div>`  : ''}

    ${contactRows ? `<div class="bm-section">연락처</div><table class="bm-table">${contactRows}</table>` : ''}
    ${sizeRows    ? `<div class="bm-section">기업 규모</div><table class="bm-table">${sizeRows}</table>` : ''}
    ${capRows     ? `<div class="bm-section">역량 · 실적</div><table class="bm-table">${capRows}</table>` : ''}
    ${channelRows ? `<div class="bm-section">채널 · 파트너 적합성</div><table class="bm-table">${channelRows}</table>` : ''}

    ${matched   ? `<div class="bm-section">성분 매칭</div><div class="bm-match">🧪 ${_escHtml(matched)}</div>` : ''}
    ${hasSource ? `<div class="bm-section">출처</div><div class="bm-sources">Perplexity 분석</div>` : ''}
  `;

  const overlay = document.getElementById('buyer-modal-overlay');
  overlay.style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function p3ReRank() {
  const cbs = [...document.querySelectorAll('.p3-cb:checked')];
  let ordered;
  if (!cbs.length) {
    ordered = [..._p3Buyers];
  } else {
    const scored = _p3Buyers.map(b => {
      const scores = b.scores || {};
      const e = b.enriched || {};
      let total = 0;
      cbs.forEach(cb => {
        const key = cb.dataset.key;
        const w   = parseFloat(cb.dataset.weight) || 0;
        const v   = key === 'pharmacy_chain'
          ? (e.has_pharmacy_chain ? 100 : 0)
          : (scores[key] || 0);
        total += (v * w) / 100;
      });
      return { ...b, _rerank: total };
    });
    scored.sort((a, b) => b._rerank - a._rerank);
    ordered = scored;
  }
  _renderP3Cards(ordered);

  // 프론트엔드 재배열 순서를 백엔드에 전달해 PDF 재생성
  fetch('/api/buyers/rerank', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ordered_buyers: ordered }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.pdf) {
        _p3PdfName = data.pdf;
      }
    })
    .catch(() => {});
}

function p3ClearAll() {
  document.querySelectorAll('.p3-cb').forEach(cb => cb.checked = false);
  _renderP3Cards([..._p3Buyers]);
}

function closeBuyerModal(e) {
  if (e && e.target !== document.getElementById('buyer-modal-overlay')) return;
  document.getElementById('buyer-modal-overlay').style.display = 'none';
  document.body.style.overflow = '';
}

function downloadBuyerReport() {
  const url = _p3PdfName
    ? `/api/buyers/report/download?name=${encodeURIComponent(_p3PdfName)}`
    : '/api/buyers/report/download';
  window.open(url, '_blank');
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   §12. 초기화
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

loadKeyStatus();        // API 키 배지
loadExchange();         // 환율 즉시 로드
_syncP3ReportOptions(); // P3 보고서 드롭다운 초기화
setInterval(() => { loadExchange(); }, 10000); // yfinance 실시간 반영 강화
loadMacro();            // 거시 지표 로드
renderReportTab();      // 보고서 탭 초기 렌더
initP2Strategy();       // 수출 가격 전략 초기화

// P1 품목 선택 → P3 레이블 + 타깃 국가 자동 연동
const _PRODUCT_COUNTRY_MAP = {
  NZ_cilostazol_cr_200:         'New Zealand',
  NZ_hydrine_hydroxyurea_500:  'New Zealand',
  NZ_gadvoa_gadobutrol_604:    'New Zealand',
  NZ_sereterol_activair:       'New Zealand',
  NZ_omethyl_omega3_2g:        'New Zealand',
  NZ_rosumeg_combigel:         'New Zealand',
  NZ_atmeg_combigel:           'New Zealand',
  NZ_ciloduo_cilosta_rosuva:   'New Zealand',
  NZ_gastiin_cr_mosapride:     'New Zealand',
};
(function () {
  const p1Select = document.getElementById('product-select');
  if (p1Select) {
    p1Select.addEventListener('change', function() {
      const countryEl = document.getElementById('inp-target-country');
      if (countryEl) {
        const mapped = _PRODUCT_COUNTRY_MAP[p1Select.value];
        if (mapped) countryEl.value = mapped;
      }
    });
  }
})();
loadNews();             // 시장 뉴스 즉시 로드
loadPreviewStats();     // 프리뷰 통계 로드
loadPreviewNews();      // 프리뷰 뉴스 로드
// Leaflet 지도 초기화 — 페이지 준비 완료 후 실행
setTimeout(initPreviewMap, 300);
