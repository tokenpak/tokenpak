/**
 * TokenPak Dashboard — Trace Detail Drawer
 * Slides in from right with full forensic trace inspection:
 * Header, Summary, Token Flow, Cost Breakdown, Retry Timeline, Segments, Raw Payload
 */

(function () {
  'use strict';

  // ─── Drawer shell elements ─────────────────────────────────────────────────

  const drawer  = document.querySelector('.context-drawer');
  const overlay = document.querySelector('.drawer-overlay');
  const body    = document.body;

  if (!drawer || !overlay) {
    console.warn('TokenPak: Drawer elements not found');
    return;
  }

  // ─── Open / Close ──────────────────────────────────────────────────────────

  function openDrawer() {
    drawer.classList.add('open');
    overlay.classList.add('open');
    body.classList.add('drawer-open');
    drawer.focus();
  }

  function closeDrawer() {
    drawer.classList.remove('open');
    overlay.classList.remove('open');
    body.classList.remove('drawer-open');
    // Clear content after transition
    setTimeout(() => {
      const body = drawer.querySelector('.drawer-body');
      if (body && !drawer.classList.contains('open')) {
        body.innerHTML = '<p class="drawer-placeholder">Select a trace to inspect</p>';
      }
      const title = drawer.querySelector('.drawer-title');
      if (title) title.textContent = 'Trace Details';
      const footer = drawer.querySelector('.drawer-footer');
      if (footer) footer.innerHTML = '<button class="btn btn-secondary" data-drawer-close>Close</button>';
    }, 300);
  }

  // ESC key
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && drawer.classList.contains('open')) closeDrawer();
  });

  // Overlay / close buttons
  overlay.addEventListener('click', e => { if (e.target === overlay) closeDrawer(); });
  document.addEventListener('click', e => {
    if (e.target.closest('[data-drawer-close]')) closeDrawer();
  });

  // ─── Load trace into drawer ────────────────────────────────────────────────

  async function loadTrace(traceId) {
    const drawerBody = drawer.querySelector('.drawer-body');
    const drawerTitle = drawer.querySelector('.drawer-title');
    const drawerFooter = drawer.querySelector('.drawer-footer');

    // Set title immediately
    if (drawerTitle) drawerTitle.textContent = 'Trace: ' + traceId.slice(0, 20) + (traceId.length > 20 ? '…' : '');

    // Show skeleton
    if (drawerBody) drawerBody.innerHTML = buildSkeleton();

    // Render footer immediately
    if (drawerFooter) {
      drawerFooter.innerHTML = `
        <button class="btn btn-sm" id="drawer-copy-json" title="Copy full trace JSON">📋 Copy JSON</button>
        <a class="btn btn-sm" id="drawer-export" href="/v1/exports/trace/${escHtml(traceId)}" download="${escHtml(traceId)}.json">⬇ Export</a>
        <button class="btn btn-secondary btn-sm" data-drawer-close>Close</button>
      `;
    }

    openDrawer();

    // Fetch trace data
    try {
      const res = await fetch(`/dashboard/audit/trace/${encodeURIComponent(traceId)}?format=json`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (drawerBody) drawerBody.innerHTML = buildDrawerContent(data, traceId);
      setupDrawerInteractivity(drawerBody, traceId, data);
    } catch (err) {
      if (drawerBody) drawerBody.innerHTML = `
        <div class="drawer-error">
          <p>Failed to load trace details.</p>
          <p class="drawer-error-detail">${escHtml(String(err))}</p>
          <button class="btn btn-sm" onclick="TokenPakDrawer.loadTrace('${escHtml(traceId)}')">Retry</button>
        </div>`;
    }
  }

  // ─── Content builders ──────────────────────────────────────────────────────

  function buildSkeleton() {
    return `
      <div class="drawer-skeleton">
        <div class="skel skel-title"></div>
        <div class="skel skel-row"></div>
        <div class="skel skel-row"></div>
        <div class="skel skel-row short"></div>
        <div class="skel skel-block"></div>
        <div class="skel skel-row"></div>
        <div class="skel skel-row"></div>
      </div>`;
  }

  function buildDrawerContent(data, traceId) {
    const event    = data.event    || {};
    const usage    = data.usage    || {};
    const cost     = data.cost     || {};
    const segments = data.segments || [];

    return [
      buildHeader(traceId, event),
      buildSummaryCard(event, usage, cost),
      buildTokenFlow(usage, segments),
      buildCostBreakdown(usage, cost),
      buildRetryTimeline(event),
      buildSegmentBreakdown(segments),
      buildRawPayload(data, traceId),
    ].join('');
  }

  // 1. Header section (inside drawer-body, above the other sections)
  function buildHeader(traceId, event) {
    const status = event.status || 'ok';
    const statusColor = status === 'ok' ? 'var(--color-positive)' : 'var(--color-danger)';
    const statusIcon  = status === 'ok' ? '✓' : '✗';
    return `
      <div class="drawer-section drawer-header-section">
        <div class="drawer-trace-id">
          <code class="trace-id-full" title="${escHtml(traceId)}">${escHtml(traceId.slice(0,32))}${traceId.length > 32 ? '…' : ''}</code>
          <button class="icon-btn" title="Copy trace ID" onclick="copyText('${escHtml(traceId)}', this)">📋</button>
        </div>
        <span class="status-badge" style="color:${statusColor};">${statusIcon} ${escHtml(status)}</span>
      </div>`;
  }

  // 2. Summary card
  function buildSummaryCard(event, usage, cost) {
    const ts = event.ts_iso ? event.ts_iso.replace('T', ' ').slice(0, 19) : '—';
    const latency = event.duration_ms != null ? fmtLatency(event.duration_ms) : '—';
    const source = cost.cost_source || 'unknown';
    return `
      <div class="drawer-section">
        <div class="drawer-section-title">Summary</div>
        <div class="summary-grid">
          <div class="summary-item"><span class="summary-label">Provider</span><span class="summary-value">${escHtml(event.provider || '—')}</span></div>
          <div class="summary-item"><span class="summary-label">Model</span><span class="summary-value mono">${escHtml(event.model || '—')}</span></div>
          <div class="summary-item"><span class="summary-label">Agent</span><span class="summary-value">${escHtml(event.agent_id || '—')}</span></div>
          <div class="summary-item"><span class="summary-label">Timestamp</span><span class="summary-value mono">${escHtml(ts)}</span></div>
          <div class="summary-item"><span class="summary-label">Latency</span><span class="summary-value">${escHtml(latency)}</span></div>
          <div class="summary-item"><span class="summary-label">Data Source</span><span class="summary-value">${escHtml(source)}</span></div>
        </div>
      </div>`;
  }

  // 3. Token Flow visualization
  function buildTokenFlow(usage, segments) {
    const raw    = sumSegField(segments, 'tokens_raw') || usage.input_billed || 0;
    const qmd    = sumSegField(segments, 'tokens_qmd') || Math.round(raw * 0.85);
    const tp     = sumSegField(segments, 'tokens_tp')  || usage.input_billed || 0;
    const final_ = usage.input_billed || tp;

    function pct(from, to) {
      if (!from || from === 0) return '';
      const p = Math.round((to - from) / from * 100);
      return p < 0 ? `${p}%` : `+${p}%`;
    }

    function flowStage(label, value, reduction) {
      const cls = reduction && reduction.startsWith('-') ? 'flow-pct negative' : 'flow-pct neutral';
      return `
        <div class="flow-stage">
          <div class="flow-label">${label}</div>
          <div class="flow-value">${fmtTokens(value)}</div>
          ${reduction ? `<div class="${cls}">${reduction}</div>` : ''}
        </div>`;
    }

    function flowArrow() { return '<div class="flow-arrow">→</div>'; }

    return `
      <div class="drawer-section">
        <div class="drawer-section-title">Token Flow</div>
        <div class="token-flow">
          ${flowStage('Raw', raw, '')}
          ${flowArrow()}
          ${flowStage('QMD', qmd, pct(raw, qmd))}
          ${flowArrow()}
          ${flowStage('TokenPak', tp, pct(qmd, tp))}
          ${flowArrow()}
          ${flowStage('Final', final_, pct(tp, final_))}
        </div>
      </div>`;
  }

  // 4. Cost Breakdown (expandable)
  function buildCostBreakdown(usage, cost) {
    const inputTokens  = usage.input_billed || 0;
    const outputTokens = usage.output_billed || 0;
    const cacheRead    = usage.cache_read    || 0;
    const cacheWrite   = usage.cache_write   || 0;

    const costInput  = cost.cost_input  || 0;
    const costOutput = cost.cost_output || 0;
    const costCacheR = cost.cost_cache_read  || 0;
    const costCacheW = cost.cost_cache_write || 0;
    const costTotal  = cost.cost_total  || 0;
    const costSaving = cost.savings_total || 0;
    const baseline   = costTotal + costSaving;
    const savingsPct = baseline > 0 ? (costSaving / baseline * 100) : 0;
    const pricingVer = cost.pricing_version || '—';

    // Per-token rates (approximate from total / tokens)
    const rateIn  = inputTokens  > 0 ? costInput  / inputTokens  : 0;
    const rateOut = outputTokens > 0 ? costOutput / outputTokens : 0;

    return `
      <div class="drawer-section collapsible-section" id="section-cost">
        <div class="drawer-section-title collapsible-trigger" onclick="toggleSection('section-cost')">
          Cost Breakdown <span class="collapse-icon">▾</span>
        </div>
        <div class="collapsible-body">
          <div class="cost-tree">
            <div class="cost-row cost-header">Baseline Cost: <strong>${fmtCurrency(baseline)}</strong></div>
            <div class="cost-row indent">├─ Input: ${fmtNum(inputTokens)} × ${fmtCurrency(rateIn, 7)} = <strong>${fmtCurrency(costInput)}</strong></div>
            <div class="cost-row indent">├─ Output: ${fmtNum(outputTokens)} × ${fmtCurrency(rateOut, 7)} = <strong>${fmtCurrency(costOutput)}</strong></div>
            ${cacheRead > 0  ? `<div class="cost-row indent">├─ Cache Read: ${fmtNum(cacheRead)} = <strong>${fmtCurrency(costCacheR)}</strong></div>` : ''}
            ${cacheWrite > 0 ? `<div class="cost-row indent">├─ Cache Write: ${fmtNum(cacheWrite)} = <strong>${fmtCurrency(costCacheW)}</strong></div>` : ''}
            <div class="cost-row cost-spacer"></div>
            <div class="cost-row cost-header">Actual Cost: <strong>${fmtCurrency(costTotal)}</strong></div>
            <div class="cost-row cost-spacer"></div>
            <div class="cost-row cost-savings">Savings: <strong>${fmtCurrency(costSaving)} (${fmtPct(savingsPct)})</strong></div>
            <div class="cost-row cost-meta">Pricing Version: ${escHtml(pricingVer)}</div>
          </div>
        </div>
      </div>`;
  }

  // 5. Retry Timeline (conditional)
  function buildRetryTimeline(event) {
    const retryCount = event.retry_count || 0;
    if (retryCount === 0) return '';

    // Build placeholder attempts (real data would come from retry events)
    let attempts = '';
    for (let i = 1; i <= retryCount + 1; i++) {
      const isFinal = i === retryCount + 1;
      const icon  = isFinal ? '✓' : '✗';
      const color = isFinal ? 'var(--color-positive)' : 'var(--color-danger)';
      const label = isFinal ? 'Success' : 'Failed';
      attempts += `<div class="retry-attempt"><span class="retry-icon" style="color:${color}">${icon}</span><span class="retry-label">Attempt ${i}: ${label}</span></div>`;
    }

    return `
      <div class="drawer-section collapsible-section" id="section-retry">
        <div class="drawer-section-title collapsible-trigger" onclick="toggleSection('section-retry')">
          Retry Timeline (${retryCount} retr${retryCount === 1 ? 'y' : 'ies'}) <span class="collapse-icon">▾</span>
        </div>
        <div class="collapsible-body">
          <div class="retry-timeline">${attempts}</div>
        </div>
      </div>`;
  }

  // 6. Segment Breakdown table
  function buildSegmentBreakdown(segments) {
    if (!segments || !segments.length) {
      return `<div class="drawer-section"><div class="drawer-section-title">Segments</div><p class="drawer-empty">No segment data for this trace.</p></div>`;
    }

    // Sort by savings impact descending
    const sorted = [...segments].sort((a, b) => {
      const savedA = (a.tokens_raw || 0) - (a.tokens_tp || a.tokens_raw || 0);
      const savedB = (b.tokens_raw || 0) - (b.tokens_tp || b.tokens_raw || 0);
      return savedB - savedA;
    });

    const rows = sorted.map(seg => {
      const raw    = seg.tokens_raw || 0;
      const tp     = seg.tokens_tp  || raw;
      const saved  = raw - tp;
      const pct    = raw > 0 ? (saved / raw * 100) : 0;
      const pctCls = pct > 0 ? 'positive' : '';
      const type   = seg.segment_type || 'unknown';
      const hint   = SEGMENT_HINTS[type] || type;
      return `
        <tr>
          <td><span class="seg-type-badge" title="${escHtml(hint)}">${escHtml(type)}</span></td>
          <td class="number">${fmtNum(raw)}</td>
          <td class="number">${fmtNum(tp)}</td>
          <td class="number ${pctCls}">${saved > 0 ? '-' + fmtNum(saved) : '—'}</td>
          <td class="number ${pctCls}">${pct > 0 ? fmtPct(pct) : '—'}</td>
        </tr>`;
    }).join('');

    return `
      <div class="drawer-section collapsible-section" id="section-segments">
        <div class="drawer-section-title collapsible-trigger" onclick="toggleSection('section-segments')">
          Segment Breakdown (${segments.length}) <span class="collapse-icon">▾</span>
        </div>
        <div class="collapsible-body">
          <table class="drawer-table">
            <thead><tr><th>Type</th><th>Raw</th><th>Final</th><th>Δ</th><th>%</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  }

  const SEGMENT_HINTS = {
    system: 'System prompt instructions',
    user: 'User message content',
    assistant: 'Assistant response',
    tool_schema: 'Tool/function definitions',
    tool_response: 'Tool call results',
    retrieved_context: 'RAG / retrieved documents',
    memory: 'Memory injection',
    other: 'Other segment type',
  };

  // 7. Raw Payload (collapsible, collapsed by default)
  function buildRawPayload(data, traceId) {
    const json = JSON.stringify(data, null, 2);
    const sizeKb = Math.round(json.length / 1024 * 10) / 10;
    const warning = sizeKb > 100
      ? `<div class="payload-warning">⚠ Large payload (${sizeKb} KB) — display may be slow</div>`
      : '';
    return `
      <div class="drawer-section collapsible-section collapsed" id="section-payload">
        <div class="drawer-section-title collapsible-trigger" onclick="toggleSection('section-payload')">
          Raw Payload <span class="payload-size">(${sizeKb} KB)</span> <span class="collapse-icon">▸</span>
        </div>
        <div class="collapsible-body" style="display:none;">
          ${warning}
          <div class="payload-toolbar">
            <button class="btn btn-sm" onclick="copyText(document.getElementById('raw-json-${escHtml(traceId.slice(0,8))}').textContent, this)">📋 Copy</button>
          </div>
          <pre class="raw-payload" id="raw-json-${escHtml(traceId.slice(0,8))}">${escHtml(json)}</pre>
        </div>
      </div>`;
  }

  // ─── Interactivity ─────────────────────────────────────────────────────────

  function setupDrawerInteractivity(drawerBody, traceId, data) {
    if (!drawerBody) return;
    // Copy JSON button in footer
    const copyBtn = document.getElementById('drawer-copy-json');
    if (copyBtn) {
      copyBtn.onclick = () => copyText(JSON.stringify(data, null, 2), copyBtn);
    }
  }

  function toggleSection(id) {
    const section = document.getElementById(id);
    if (!section) return;
    const body = section.querySelector('.collapsible-body');
    const icon = section.querySelector('.collapse-icon');
    const isCollapsed = section.classList.contains('collapsed');
    if (isCollapsed) {
      section.classList.remove('collapsed');
      if (body) body.style.display = '';
      if (icon) icon.textContent = '▾';
    } else {
      section.classList.add('collapsed');
      if (body) body.style.display = 'none';
      if (icon) icon.textContent = '▸';
    }
  }
  window.toggleSection = toggleSection;

  async function copyText(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
      if (btn) {
        const orig = btn.textContent;
        btn.textContent = '✓ Copied!';
        setTimeout(() => { btn.textContent = orig; }, 1500);
      }
    } catch (e) {
      console.warn('Clipboard copy failed:', e);
    }
  }
  window.copyText = copyText;

  // ─── Format helpers ────────────────────────────────────────────────────────

  function fmtNum(n) {
    if (n == null || isNaN(n)) return '—';
    return Number(n).toLocaleString();
  }
  function fmtTokens(n) {
    if (n == null || isNaN(n)) return '—';
    const v = Number(n);
    if (v >= 1e6) return (v/1e6).toFixed(1).replace(/\.0$/,'') + 'M';
    if (v >= 1e3) return (v/1e3).toFixed(1).replace(/\.0$/,'') + 'K';
    return v.toLocaleString();
  }
  function fmtCurrency(n, decimals) {
    if (n == null || isNaN(n)) return '—';
    const d = decimals != null ? decimals : (Math.abs(n) >= 1 ? 4 : 6);
    return '$' + Number(n).toFixed(d);
  }
  function fmtPct(n) {
    if (n == null || isNaN(n)) return '—';
    return Number(n).toFixed(1) + '%';
  }
  function fmtLatency(n) {
    if (n == null || isNaN(n)) return '—';
    const v = Number(n);
    return v >= 1000 ? (v/1000).toFixed(1) + 's' : Math.round(v) + 'ms';
  }
  function escHtml(s) {
    return String(s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function sumSegField(segs, field) {
    return segs.reduce((s, seg) => s + (seg[field] || 0), 0);
  }

  // ─── Public API ────────────────────────────────────────────────────────────

  window.TokenPakDrawer = {
    open: openDrawer,
    close: closeDrawer,
    loadTrace,
  };

})();
