/**
 * TokenPak Dashboard — Trust & Transparency Layer
 *
 * Provides:
 *  1. Metric definition tooltips (definition, formula, data source)
 *  2. Data source badges (Estimated / Billed / Verified)
 *  3. Pricing version display
 *  4. Last refresh timestamp (relative + stale detection)
 *  5. Reconciliation status badge
 *  6. Formula expansion in Advanced mode
 *  7. Pre-calculated deltas (no mental math)
 */
'use strict';

(function () {

  // ─── Metric definitions registry ──────────────────────────────────────────

  const METRICS = {
    total_cost: {
      label: 'Total Cost',
      definition: 'Total USD spent on API calls in this period.',
      formula: 'Σ (token_count × price_per_token) per request',
      source: 'Estimated from token counts × current model pricing',
      sourceType: 'estimated',
    },
    total_savings: {
      label: 'Total Savings',
      definition: 'Money saved through TokenPak compression.',
      formula: 'baseline_cost − actual_cost',
      source: 'Estimated: baseline uses uncompressed token counts × pricing',
      sourceType: 'estimated',
    },
    savings_pct: {
      label: 'Savings Rate',
      definition: 'Percentage of potential cost avoided via compression.',
      formula: '(baseline_cost − actual_cost) / baseline_cost × 100',
      source: 'Estimated from token composition segments',
      sourceType: 'estimated',
    },
    baseline_cost: {
      label: 'Baseline Cost',
      definition: 'What you would have spent without TokenPak.',
      formula: 'Σ (original_token_count × price_per_token)',
      source: 'Estimated using raw segment totals before compression',
      sourceType: 'estimated',
    },
    actual_cost: {
      label: 'Actual Cost',
      definition: 'What you actually spent after compression.',
      formula: 'Σ (compressed_token_count × price_per_token)',
      source: 'Estimated from compressed token counts × pricing',
      sourceType: 'estimated',
    },
    request_count: {
      label: 'Request Count',
      definition: 'Number of API requests recorded in this period.',
      formula: 'COUNT(tp_events) WHERE status != error',
      source: 'Direct count from telemetry event log',
      sourceType: 'verified',
    },
    avg_latency_ms: {
      label: 'Avg Latency',
      definition: 'Mean end-to-end request duration.',
      formula: 'AVG(duration_ms) over all requests in period',
      source: 'Measured: wall-clock time from request start to response end',
      sourceType: 'verified',
    },
    error_rate: {
      label: 'Error Rate',
      definition: 'Fraction of requests that resulted in an API error.',
      formula: 'error_count / total_request_count',
      source: 'Derived from tp_events.status field',
      sourceType: 'verified',
    },
    token_count: {
      label: 'Token Count',
      definition: 'Total tokens sent and received across all requests.',
      formula: 'Σ (input_tokens + output_tokens)',
      source: 'Counted by TokenPak tokenizer (tiktoken-compatible)',
      sourceType: 'estimated',
    },
    compression_ratio: {
      label: 'Compression Ratio',
      definition: 'How much smaller the compressed context is vs original.',
      formula: 'compressed_tokens / original_tokens',
      source: 'Measured during compression pipeline (sub-block slicing)',
      sourceType: 'verified',
    },
  };

  const SOURCE_CONFIG = {
    verified:  { label: 'Verified',  cls: 'badge-verified',  title: 'Verified against actual API billing records' },
    estimated: { label: 'Estimated', cls: 'badge-estimated', title: 'Estimated from token counts × model pricing. May differ from actual billing.' },
    billed:    { label: 'Billed',    cls: 'badge-billed',    title: 'Sourced directly from provider billing API' },
    live:      { label: 'Live',      cls: 'badge-live',      title: 'Real-time, unreconciled data' },
  };

  // ─── 1. Tooltip engine ────────────────────────────────────────────────────

  let _activeTooltip = null;

  function createTooltip(metricKey, anchorEl) {
    const m = METRICS[metricKey];
    if (!m) return;

    destroyTooltip();

    const tip = document.createElement('div');
    tip.className = 'trust-tooltip';
    tip.setAttribute('role', 'tooltip');
    tip.setAttribute('id', 'trust-tip-' + metricKey);
    tip.innerHTML = `
      <div class="tip-title">${escHtml(m.label)}</div>
      <div class="tip-divider"></div>
      <div class="tip-row"><span class="tip-key">Definition</span><span class="tip-val">${escHtml(m.definition)}</span></div>
      <div class="tip-row"><span class="tip-key">Formula</span><span class="tip-val tip-mono">${escHtml(m.formula)}</span></div>
      <div class="tip-row"><span class="tip-key">Source</span><span class="tip-val">${escHtml(m.source)}</span></div>
    `;

    document.body.appendChild(tip);
    _activeTooltip = tip;

    // Position below the anchor
    positionTooltip(tip, anchorEl);

    // Dismiss on scroll/outside click
    setTimeout(() => {
      document.addEventListener('click', destroyTooltip, { once: true });
    }, 0);

    if (anchorEl) anchorEl.setAttribute('aria-describedby', tip.id);
  }

  function positionTooltip(tip, anchor) {
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const scrollY = window.scrollY || 0;
    const scrollX = window.scrollX || 0;

    tip.style.position = 'absolute';
    tip.style.zIndex = '9999';
    tip.style.left = Math.max(8, rect.left + scrollX) + 'px';
    tip.style.top = (rect.bottom + scrollY + 6) + 'px';

    // Clamp to viewport width
    requestAnimationFrame(() => {
      const tw = tip.offsetWidth;
      const vw = window.innerWidth;
      const left = parseFloat(tip.style.left);
      if (left + tw > vw - 8) tip.style.left = Math.max(8, vw - tw - 8) + 'px';
    });
  }

  function destroyTooltip() {
    if (_activeTooltip) { _activeTooltip.remove(); _activeTooltip = null; }
  }

  // Attach to all elements with data-metric attribute
  function attachTooltips() {
    document.querySelectorAll('[data-metric]').forEach(el => {
      if (el.dataset.trustBound) return;
      el.dataset.trustBound = '1';
      const key = el.dataset.metric;
      el.setAttribute('aria-label', (el.getAttribute('aria-label') || '') + ' (click for details)');
      el.style.cursor = 'help';

      el.addEventListener('click', e => {
        e.stopPropagation();
        createTooltip(key, el);
      });
      el.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); createTooltip(key, el); }
        if (e.key === 'Escape') destroyTooltip();
      });
      if (!el.getAttribute('tabindex')) el.setAttribute('tabindex', '0');
    });
  }

  // ─── 2. Data source badges ─────────────────────────────────────────────────

  function injectSourceBadges() {
    document.querySelectorAll('[data-metric]').forEach(el => {
      if (el.dataset.badgeInjected) return;
      const key = el.dataset.metric;
      const m = METRICS[key];
      if (!m) return;
      const cfg = SOURCE_CONFIG[m.sourceType] || SOURCE_CONFIG.estimated;
      const badge = document.createElement('span');
      badge.className = `source-badge ${cfg.cls}`;
      badge.textContent = cfg.label;
      badge.title = cfg.title;
      badge.setAttribute('aria-label', `Data source: ${cfg.label}. ${cfg.title}`);
      el.appendChild(badge);
      el.dataset.badgeInjected = '1';
    });
  }

  // ─── 3. Pricing version display ───────────────────────────────────────────

  function injectPricingVersion() {
    const containers = document.querySelectorAll('.trust-pricing-slot');
    const version = 'v2026.02';
    const desc = 'OpenAI & Anthropic rates as of Feb 2026';
    containers.forEach(el => {
      el.innerHTML = `<span class="pricing-version" title="${escHtml(desc)}">📋 Pricing: ${version}</span>`;
    });
  }

  // ─── 4. Last refresh timestamp ────────────────────────────────────────────

  let _lastRefreshTime = new Date();

  function updateTimestamp() {
    const now = new Date();
    const diffMs = now - _lastRefreshTime;
    const diffMin = Math.floor(diffMs / 60000);
    const diffSec = Math.floor(diffMs / 1000);
    const isStale = diffMs > 5 * 60 * 1000; // stale after 5 min

    let relStr;
    if (diffSec < 10) relStr = 'just now';
    else if (diffSec < 60) relStr = `${diffSec}s ago`;
    else if (diffMin < 60) relStr = `${diffMin}m ago`;
    else relStr = `${Math.floor(diffMin / 60)}h ago`;

    const absStr = _lastRefreshTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    document.querySelectorAll('.trust-timestamp').forEach(el => {
      el.textContent = `Data as of ${absStr} (${relStr})`;
      el.title = _lastRefreshTime.toISOString();
      el.classList.toggle('timestamp-stale', isStale);
    });

    // Stale banner
    const banner = document.getElementById('stale-data-banner');
    if (banner) {
      banner.style.display = isStale ? 'flex' : 'none';
      if (isStale) banner.textContent = `⚠ Data may be stale — last refreshed ${relStr}. Click Update to refresh.`;
    }
  }

  function markRefreshed() {
    _lastRefreshTime = new Date();
    updateTimestamp();
  }

  // ─── 5. Reconciliation status badge ──────────────────────────────────────

  const RECON_STATES = {
    reconciled: { icon: '✓', label: 'Reconciled', cls: 'recon-ok',       desc: 'Data verified against billing records.' },
    estimated:  { icon: '⚠', label: 'Estimated',  cls: 'recon-warn',     desc: 'Based on token counts. Not verified against billing.' },
    live:       { icon: '⚡', label: 'Live',       cls: 'recon-live',     desc: 'Real-time data, not yet reconciled.' },
    stale:      { icon: '⏸', label: 'Stale',      cls: 'recon-stale',    desc: 'Data has not been refreshed recently.' },
  };

  function setReconciliationStatus(state) {
    const cfg = RECON_STATES[state] || RECON_STATES.estimated;
    document.querySelectorAll('.recon-badge').forEach(el => {
      el.className = `recon-badge ${cfg.cls}`;
      el.innerHTML = `<span class="recon-icon">${cfg.icon}</span>${cfg.label}`;
      el.title = cfg.desc;
      el.setAttribute('aria-label', `Reconciliation status: ${cfg.label}. ${cfg.desc}`);
      el.dataset.reconState = state;
    });
  }

  function initReconciliationBadge() {
    // Default to 'estimated' until backend confirms otherwise
    setReconciliationStatus('estimated');
    // Wire click to show detail panel
    document.querySelectorAll('.recon-badge').forEach(el => {
      if (el.dataset.reconBound) return;
      el.dataset.reconBound = '1';
      el.addEventListener('click', showReconDetail);
      el.style.cursor = 'pointer';
      el.setAttribute('tabindex', '0');
      el.addEventListener('keydown', e => { if (e.key === 'Enter') showReconDetail(); });
    });
  }

  function showReconDetail() {
    let panel = document.getElementById('recon-detail-panel');
    if (panel) { panel.remove(); return; }
    panel = document.createElement('div');
    panel.id = 'recon-detail-panel';
    panel.className = 'recon-detail-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Reconciliation details');
    panel.innerHTML = `
      <div class="recon-detail-header">
        <strong>Reconciliation Details</strong>
        <button class="recon-close" onclick="this.closest('#recon-detail-panel').remove()" aria-label="Close">×</button>
      </div>
      <div class="recon-detail-body">
        <p class="recon-detail-row"><span class="recon-detail-key">Status</span><span class="recon-detail-val">Estimated</span></p>
        <p class="recon-detail-row"><span class="recon-detail-key">Source</span><span class="recon-detail-val">Token counts × model pricing</span></p>
        <p class="recon-detail-row"><span class="recon-detail-key">Pricing version</span><span class="recon-detail-val">v2026.02 (Feb 2026 rates)</span></p>
        <p class="recon-detail-row"><span class="recon-detail-key">Last reconciled</span><span class="recon-detail-val">Never (billing API not configured)</span></p>
        <p class="recon-detail-note">To verify against billing: configure provider API keys in TokenPak settings.</p>
      </div>`;
    document.body.appendChild(panel);
    // Position below recon badge
    const badge = document.querySelector('.recon-badge');
    if (badge) {
      const r = badge.getBoundingClientRect();
      panel.style.top = (r.bottom + window.scrollY + 6) + 'px';
      panel.style.left = Math.max(8, r.left + window.scrollX) + 'px';
    }
  }

  // ─── 6. Formula expansion (Advanced mode) ────────────────────────────────

  let _advancedMode = false;

  function toggleAdvancedMode(on) {
    _advancedMode = on !== undefined ? on : !_advancedMode;
    document.body.classList.toggle('advanced-mode', _advancedMode);
    document.querySelectorAll('.formula-expansion').forEach(el => {
      el.style.display = _advancedMode ? '' : 'none';
    });
    document.querySelectorAll('.advanced-mode-btn').forEach(btn => {
      btn.classList.toggle('active', _advancedMode);
      btn.setAttribute('aria-pressed', String(_advancedMode));
    });
    if (window.a11yAnnounce) window.a11yAnnounce(`Advanced mode ${_advancedMode ? 'on' : 'off'}`);
  }

  /**
   * Build formula expansion HTML for a KPI card.
   * kpiData: { baseline_tokens, actual_tokens, price_per_token, baseline_cost, actual_cost, savings }
   */
  function buildFormulaExpansion(kpiData) {
    const d = kpiData || {};
    const fmt = (v, pre) => (pre || '') + (v !== undefined ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 4 }) : '—');
    return `
      <div class="formula-expansion" style="display:${_advancedMode ? '' : 'none'}">
        <div class="formula-tree">
          <div class="formula-line formula-result">Savings = ${fmt(d.savings, '$')}</div>
          <div class="formula-line formula-branch">├─ Baseline: ${fmt(d.baseline_tokens)} tokens × ${fmt(d.price_per_token, '$')} = ${fmt(d.baseline_cost, '$')}</div>
          <div class="formula-line formula-branch">├─ Actual:   ${fmt(d.actual_tokens)} tokens × ${fmt(d.price_per_token, '$')} = ${fmt(d.actual_cost, '$')}</div>
          <div class="formula-line formula-leaf">└─ Saved:    ${fmt(d.savings, '$')}</div>
        </div>
      </div>`;
  }

  // ─── 7. Pre-calculated deltas (no mental math) ───────────────────────────

  /**
   * Injects a delta indicator next to a value element.
   * el: target DOM node
   * current, previous: numbers
   * direction: 'higher'|'lower'|'neutral'
   */
  function injectDelta(el, current, previous, direction) {
    if (el.dataset.deltaInjected) return;
    const delta = current - previous;
    const pct = previous !== 0 ? (delta / previous * 100).toFixed(1) : null;
    const isBetter = (direction === 'higher' && delta > 0) || (direction === 'lower' && delta < 0);
    const cls = isBetter ? 'delta-good' : delta === 0 ? 'delta-flat' : 'delta-bad';
    const sign = delta >= 0 ? '+' : '';
    const arrow = isBetter ? '↑' : delta === 0 ? '' : '↓';

    const span = document.createElement('span');
    span.className = `kpi-inline-delta ${cls}`;
    span.textContent = `${arrow} ${sign}${pct !== null ? pct + '%' : ''}`.trim();
    span.title = `${sign}${delta.toFixed(4)} vs previous period`;
    span.setAttribute('aria-label', `Change: ${span.textContent}`);
    el.appendChild(span);
    el.dataset.deltaInjected = '1';
  }

  // ─── Trust bar injection ──────────────────────────────────────────────────

  /**
   * Inject the full trust bar into a container element.
   * containerId: id of element to inject into (replaces innerHTML)
   */
  function injectTrustBar(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = `
      <div class="trust-bar" role="complementary" aria-label="Data trust and transparency info">
        <span class="recon-badge recon-warn" role="status" title="Reconciliation status" tabindex="0">
          <span class="recon-icon">⚠</span>Estimated
        </span>
        <span class="trust-sep">·</span>
        <span class="trust-pricing-slot"></span>
        <span class="trust-sep">·</span>
        <span class="trust-timestamp" title="Last data refresh time"></span>
        <span class="trust-sep">·</span>
        <button class="advanced-mode-btn" onclick="TokenPakTrust.toggleAdvancedMode()"
                aria-pressed="false" title="Show calculation formulas for each metric">
          ƒ Advanced
        </button>
      </div>`;
    initReconciliationBadge();
    injectPricingVersion();
    updateTimestamp();
  }

  // ─── Init ─────────────────────────────────────────────────────────────────

  function init() {
    attachTooltips();
    injectSourceBadges();
    injectPricingVersion();
    initReconciliationBadge();
    updateTimestamp();

    // Inject trust bars for any pre-existing containers
    document.querySelectorAll('[data-trust-bar]').forEach(el => {
      injectTrustBar(el.id);
    });

    // Refresh timestamp on HTMX swap (data updated)
    document.addEventListener('htmx:afterSwap', () => {
      markRefreshed();
      attachTooltips();
      injectSourceBadges();
      initReconciliationBadge();
    });

    // Periodic timestamp update
    setInterval(updateTimestamp, 30000);

    // Wire refresh button
    const refreshBtn = document.getElementById('telemetry-refresh-btn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => setTimeout(markRefreshed, 1500));
    }
  }

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ─── Public API ───────────────────────────────────────────────────────────

  window.TokenPakTrust = {
    attachTooltips,
    injectSourceBadges,
    injectPricingVersion,
    updateTimestamp,
    markRefreshed,
    setReconciliationStatus,
    toggleAdvancedMode,
    buildFormulaExpansion,
    injectDelta,
    injectTrustBar,
    METRICS,
    SOURCE_CONFIG,
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

})();
