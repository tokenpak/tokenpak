/**
 * TokenPak Dashboard — Persona View Mode
 * Basic (FinOps) / Advanced (Engineering) mode toggle with progressive disclosure.
 * Persists in localStorage. No page reload required.
 */

'use strict';

(function () {

  const STORAGE_KEY = 'tokenpak-dashboard-mode';
  const MODES = ['basic', 'advanced'];

  // ─── State ──────────────────────────────────────────────────────────────────

  let _mode = localStorage.getItem(STORAGE_KEY) || 'advanced';
  if (!MODES.includes(_mode)) _mode = 'advanced';

  // ─── Apply Mode ─────────────────────────────────────────────────────────────

  function applyMode(mode) {
    _mode = mode;
    localStorage.setItem(STORAGE_KEY, mode);

    // Set body attribute — CSS uses [data-mode="basic"] selectors
    document.body.setAttribute('data-mode', mode);

    // Update toggle button states
    document.querySelectorAll('[data-mode-btn]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.modeBtn === mode);
      btn.setAttribute('aria-pressed', btn.dataset.modeBtn === mode ? 'true' : 'false');
    });

    // Update mode label
    document.querySelectorAll('.mode-label').forEach(el => {
      el.textContent = mode === 'basic' ? 'FinOps View' : 'Engineering View';
    });

    // Dispatch event for other modules
    document.body.dispatchEvent(new CustomEvent('mode-changed', { detail: { mode }, bubbles: true }));
  }

  // ─── Progressive Disclosure ──────────────────────────────────────────────────

  function initDisclosure() {
    // Handle expand/collapse for [data-disclosure] sections
    document.addEventListener('click', function (e) {
      const trigger = e.target.closest('[data-disclosure-trigger]');
      if (!trigger) return;

      const targetId = trigger.dataset.disclosureTrigger;
      const target = document.getElementById(targetId);
      if (!target) return;

      const isExpanded = target.classList.toggle('disclosure-expanded');
      trigger.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
      trigger.querySelector('.disclosure-icon')?.classList.toggle('rotated', isExpanded);
    });
  }

  // ─── Trace Search (Auditor support) ─────────────────────────────────────────

  function initTraceSearch() {
    const form = document.getElementById('trace-search-form');
    if (!form) return;

    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      const input = form.querySelector('input[name="trace_id"]');
      const traceId = (input?.value || '').trim();
      if (!traceId) return;

      const resultEl = document.getElementById('trace-search-result');
      if (resultEl) resultEl.innerHTML = '<div class="search-loading">Searching…</div>';

      try {
        const res = await fetch(`/v1/trace/${encodeURIComponent(traceId)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderTraceResult(data, resultEl);
      } catch (err) {
        if (resultEl) {
          resultEl.innerHTML = `<div class="search-error">Trace not found: <code>${escHtml(traceId)}</code></div>`;
        }
      }
    });
  }

  function renderTraceResult(data, container) {
    if (!container) return;
    const trace = data.trace || data;
    const event = data.event || trace;
    const usage = data.usage || {};
    const cost = data.cost || {};
    const segments = data.segments || [];

    const exportBtn = `<button class="btn btn-sm" onclick="TokenPakPersona.exportTrace(${escHtml(JSON.stringify(data))})">⬇ Export JSON</button>`;

    let segRows = '';
    if (segments.length) {
      segRows = `
        <details class="trace-segments">
          <summary class="trace-segments-toggle">${segments.length} segments</summary>
          <table class="trace-segtable">
            <thead><tr><th>Type</th><th>Tokens (raw)</th><th>Tokens (final)</th></tr></thead>
            <tbody>
              ${segments.map(s => `
                <tr>
                  <td>${escHtml(s.segment_type || '—')}</td>
                  <td>${s.raw_token_count || 0}</td>
                  <td>${s.final_token_count || 0}</td>
                </tr>`).join('')}
            </tbody>
          </table>
        </details>`;
    }

    container.innerHTML = `
      <div class="trace-result">
        <div class="trace-result-header">
          <code class="trace-id">${escHtml(event.trace_id || data.trace_id || '—')}</code>
          ${exportBtn}
        </div>
        <div class="trace-result-grid">
          <div class="trace-kv"><span>Provider</span><strong>${escHtml(event.provider || '—')}</strong></div>
          <div class="trace-kv"><span>Model</span><strong>${escHtml(event.model || '—')}</strong></div>
          <div class="trace-kv"><span>Status</span><strong>${escHtml(event.status || '—')}</strong></div>
          <div class="trace-kv"><span>Duration</span><strong>${event.duration_ms ? event.duration_ms + 'ms' : '—'}</strong></div>
          <div class="trace-kv"><span>Input tokens</span><strong>${usage.input_tokens || '—'}</strong></div>
          <div class="trace-kv"><span>Output tokens</span><strong>${usage.output_tokens || '—'}</strong></div>
          <div class="trace-kv"><span>Actual cost</span><strong>${cost.actual_cost != null ? '$' + Number(cost.actual_cost).toFixed(6) : '—'}</strong></div>
          <div class="trace-kv"><span>Savings</span><strong>${cost.savings != null ? '$' + Number(cost.savings).toFixed(6) : '—'}</strong></div>
        </div>
        ${segRows}
      </div>`;
  }

  function escHtml(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ─── Insight Cards (FinOps narrative) ───────────────────────────────────────

  function buildInsightCards(totals) {
    const container = document.getElementById('insight-cards');
    if (!container || !totals) return;

    const actualCost = +(totals.total_actual_cost || 0);
    const savings = +(totals.total_savings || 0);
    const baseline = actualCost + savings;
    const pct = baseline > 0 ? (savings / baseline * 100).toFixed(1) : 0;
    const requests = +(totals.total_requests || 0);
    const tokens = +(totals.total_tokens || 0);

    const cards = [
      { label: 'What we spent', value: '$' + actualCost.toFixed(2), sub: 'actual cost this period', icon: '💳' },
      { label: 'What we\'d have paid', value: '$' + baseline.toFixed(2), sub: 'without compression', icon: '📊' },
      { label: 'We saved', value: '$' + savings.toFixed(2), sub: `${pct}% reduction`, icon: '✅', positive: true },
      { label: 'Across', value: requests.toLocaleString() + ' requests', sub: tokens.toLocaleString() + ' tokens', icon: '📡' },
    ];

    container.innerHTML = cards.map(c => `
      <div class="insight-card${c.positive ? ' positive' : ''}">
        <div class="insight-icon">${c.icon}</div>
        <div class="insight-label">${c.label}</div>
        <div class="insight-value">${c.value}</div>
        <div class="insight-sub">${c.sub}</div>
      </div>`).join('');
  }

  // ─── Init ────────────────────────────────────────────────────────────────────

  function init() {
    applyMode(_mode);
    initDisclosure();
    initTraceSearch();

    // Wire toggle buttons
    document.addEventListener('click', function (e) {
      const btn = e.target.closest('[data-mode-btn]');
      if (btn) applyMode(btn.dataset.modeBtn);
    });

    // Re-apply after HTMX swaps (mode class may be lost on content div)
    document.addEventListener('htmx:afterSwap', function () {
      document.body.setAttribute('data-mode', _mode);
      initTraceSearch();
    });
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // ─── Public API ─────────────────────────────────────────────────────────────

  window.TokenPakPersona = {
    setMode: applyMode,
    getMode: () => _mode,
    buildInsightCards,
    exportTrace(data) {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      const id = data.trace_id || data.event?.trace_id || 'trace';
      a.download = `tokenpak-trace-${id}.json`;
      a.href = URL.createObjectURL(blob);
      a.click();
      URL.revokeObjectURL(a.href);
    }
  };

})();
