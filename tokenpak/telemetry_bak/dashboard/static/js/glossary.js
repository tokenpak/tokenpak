/**
 * TokenPak Dashboard — Terminology Glossary & Educational Tooltips
 *
 * Provides:
 *  1. GLOSSARY data (reads from window.TOKENPAK_GLOSSARY injected by server)
 *  2. Two-level tooltip system:
 *     Level 1: hover → short "what" definition (200ms delay)
 *     Level 2: click/More → full 5W1H card with formula
 *  3. Glossary modal (searchable, categorized)
 *  4. Unit format rules enforcement (display only)
 *  5. Aggregation & trend explanations
 *  6. Accessible (aria-describedby, keyboard nav)
 */
'use strict';

(function () {

  // ─── Category groups ───────────────────────────────────────────────────────
  const CATEGORIES = {
    cost:        { label: 'Cost',        icon: '💰', keys: ['baseline_cost','actual_cost','savings','savings_pct','cost_per_request'] },
    tokens:      { label: 'Tokens',      icon: '🔢', keys: ['raw_tokens','final_tokens','cache_tokens','compression_ratio','request_count'] },
    performance: { label: 'Performance', icon: '⚡', keys: ['latency_avg','latency_p95','latency_p99','error_rate','retry_rate'] },
    status:      { label: 'Status',      icon: '🔍', keys: ['reconciled','estimated'] },
  };

  // ─── Unit format rules ─────────────────────────────────────────────────────
  const UNIT_RULES = {
    currency:    { example: '$0.0042',    pattern: '$X.XX or $X.XXXX for small values' },
    latency:     { example: '142 ms',     pattern: 'X ms (integer, space before unit)' },
    percentage:  { example: '23.4%',      pattern: 'X.X% (one decimal, no space)' },
    token_count: { example: '1,234,567',  pattern: 'X,XXX with locale commas' },
    ratio:       { example: '1.4×',       pattern: 'X.X× (multiplication sign)' },
  };

  // ─── Aggregation explanations ──────────────────────────────────────────────
  const AGGREGATION = {
    daily:      'Aggregated by request date (UTC). Midnight-to-midnight boundaries.',
    hourly:     'Grouped by hour boundary (UTC). Partial hours are included.',
    per_request:'Individual request data. No aggregation — each row is one API call.',
  };

  // ─── Trend types ───────────────────────────────────────────────────────────
  const TREND_TYPES = {
    cumulative: 'Running total over time — values always increase or stay flat.',
    discrete:   'Value per period — shows change within each interval.',
  };

  // ─── Tooltip state ─────────────────────────────────────────────────────────
  let _hoverTimer = null;
  let _l1Tip = null;   // Level 1: hover short
  let _l2Tip = null;   // Level 2: click expanded

  function getGlossary() {
    return window.TOKENPAK_GLOSSARY || {};
  }

  function getTerm(key) {
    return getGlossary()[key] || null;
  }

  // ─── Level 1 tooltip (hover, short) ────────────────────────────────────────

  function showL1(key, anchor) {
    if (_l2Tip) return; // don't show L1 while L2 is open
    const card = getTerm(key);
    if (!card) return;

    hideL1();
    const tip = document.createElement('div');
    tip.className = 'gloss-tip gloss-tip-l1';
    tip.setAttribute('role', 'tooltip');
    tip.id = 'gloss-tip-l1-' + key;
    tip.innerHTML =
      '<div class="gloss-tip-what">' + escHtml(card.what) + '</div>' +
      '<div class="gloss-tip-hint">Click <span class="gloss-tip-icon">ⓘ</span> for details</div>';

    document.body.appendChild(tip);
    _l1Tip = tip;
    positionTip(tip, anchor);

    if (anchor) anchor.setAttribute('aria-describedby', tip.id);
  }

  function hideL1() {
    if (_l1Tip) { _l1Tip.remove(); _l1Tip = null; }
    clearTimeout(_hoverTimer);
    _hoverTimer = null;
  }

  // ─── Level 2 tooltip (click, full 5W1H) ────────────────────────────────────

  function showL2(key, anchor) {
    const card = getTerm(key);
    if (!card) return;

    // Toggle: if same key open, close it
    if (_l2Tip && _l2Tip.dataset.termKey === key) {
      hideL2(); return;
    }
    hideL2();
    hideL1();

    const formula = card.how
      ? '<div class="gloss-tip-row"><span class="gloss-tip-label">Formula</span><code class="gloss-tip-formula">' + escHtml(card.how) + '</code></div>'
      : '';

    const notThis = card.not_this
      ? '<div class="gloss-tip-row"><span class="gloss-tip-label">Not this</span><span class="gloss-tip-not">' + escHtml(card.not_this) + '</span></div>'
      : '';

    const aliases = (card.aliases || []).length
      ? '<div class="gloss-tip-row"><span class="gloss-tip-label">Also called</span><span class="gloss-tip-aliases">' + card.aliases.slice(0,3).map(escHtml).join(', ') + '</span></div>'
      : '';

    const unitBlock = buildUnitBlock(key);

    const tip = document.createElement('div');
    tip.className = 'gloss-tip gloss-tip-l2';
    tip.setAttribute('role', 'dialog');
    tip.setAttribute('aria-label', card.term + ' definition');
    tip.id = 'gloss-tip-l2-' + key;
    tip.dataset.termKey = key;
    tip.innerHTML =
      '<div class="gloss-tip-header">' +
        '<span class="gloss-tip-term">' + escHtml(formatTermLabel(key, card.term)) + '</span>' +
        '<button class="gloss-tip-close" aria-label="Close definition">×</button>' +
      '</div>' +
      '<div class="gloss-tip-divider"></div>' +
      '<div class="gloss-tip-what gloss-tip-what-lg">' + escHtml(card.what) + '</div>' +
      '<div class="gloss-tip-rows">' +
        (card.why ? '<div class="gloss-tip-row"><span class="gloss-tip-label">Why it matters</span><span>' + escHtml(card.why) + '</span></div>' : '') +
        formula +
        unitBlock +
        notThis +
        aliases +
      '</div>' +
      '<div class="gloss-tip-footer">' +
        '<a class="gloss-tip-glossary-link" href="/dashboard/glossary" aria-label="Open full glossary">📖 Full glossary</a>' +
      '</div>';

    document.body.appendChild(tip);
    _l2Tip = tip;
    positionTip(tip, anchor);

    tip.querySelector('.gloss-tip-close').addEventListener('click', hideL2);

    if (anchor) anchor.setAttribute('aria-describedby', tip.id);

    setTimeout(() => {
      document.addEventListener('click', handleOutsideClick, { capture: true });
    }, 0);
  }

  function hideL2() {
    if (_l2Tip) {
      _l2Tip.remove();
      _l2Tip = null;
      document.removeEventListener('click', handleOutsideClick, { capture: true });
    }
  }

  function handleOutsideClick(e) {
    if (_l2Tip && !_l2Tip.contains(e.target)) {
      hideL2();
    }
  }

  // ─── Unit block helper ─────────────────────────────────────────────────────

  function buildUnitBlock(key) {
    const unitMap = {
      baseline_cost:    UNIT_RULES.currency,
      actual_cost:      UNIT_RULES.currency,
      savings:          UNIT_RULES.currency,
      savings_pct:      UNIT_RULES.percentage,
      cost_per_request: UNIT_RULES.currency,
      compression_ratio: { example: '1.4×', pattern: 'X.X× (raw ÷ final)' },
      error_rate:       UNIT_RULES.percentage,
      retry_rate:       UNIT_RULES.percentage,
      latency_avg:      UNIT_RULES.latency,
      latency_p95:      UNIT_RULES.latency,
      latency_p99:      UNIT_RULES.latency,
      raw_tokens:       UNIT_RULES.token_count,
      final_tokens:     UNIT_RULES.token_count,
      cache_tokens:     UNIT_RULES.token_count,
      request_count:    UNIT_RULES.token_count,
    };
    const rule = unitMap[key];
    if (!rule) return '';
    return '<div class="gloss-tip-row"><span class="gloss-tip-label">Format</span>' +
           '<code class="gloss-tip-unit">' + escHtml(rule.example) + '</code>' +
           '<span class="gloss-tip-unit-desc"> — ' + escHtml(rule.pattern) + '</span></div>';
  }

  // ─── Position helper ───────────────────────────────────────────────────────

  function positionTip(tip, anchor) {
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const scrollY = window.scrollY || 0;
    const scrollX = window.scrollX || 0;

    tip.style.position = 'absolute';
    tip.style.zIndex = '10000';
    tip.style.left = Math.max(8, rect.left + scrollX) + 'px';
    tip.style.top = (rect.bottom + scrollY + 8) + 'px';

    requestAnimationFrame(() => {
      const tw = tip.offsetWidth;
      const vw = window.innerWidth;
      const left = parseFloat(tip.style.left);
      if (left + tw > vw - 8) tip.style.left = Math.max(8, vw - tw - 8) + 'px';

      // Flip above if below viewport
      const th = tip.offsetHeight;
      const vh = window.innerHeight;
      const top = parseFloat(tip.style.top) - scrollY;
      if (top + th > vh - 8) {
        tip.style.top = (rect.top + scrollY - th - 8) + 'px';
      }
    });
  }

  // ─── Attach to [data-gloss] and [data-metric] ────────────────────────────

  function attachTooltips() {
    // Support both data-gloss="key" and data-metric="key" (bridge with trust.js)
    const sel = '[data-gloss], .metric-info-icon[data-metric]';
    document.querySelectorAll(sel).forEach(el => {
      if (el.dataset.glossBound) return;
      el.dataset.glossBound = '1';

      const key = el.dataset.gloss || el.dataset.metric;
      // Normalise trust.js keys like 'total_cost' → glossary key
      const normKey = normaliseKey(key);

      if (!el.getAttribute('tabindex')) el.setAttribute('tabindex', '0');
      el.style.cursor = 'help';

      // Hover → L1
      el.addEventListener('mouseenter', () => {
        _hoverTimer = setTimeout(() => showL1(normKey, el), 200);
      });
      el.addEventListener('mouseleave', () => {
        clearTimeout(_hoverTimer);
        _hoverTimer = null;
        // Small delay so user can read, but L2 click takes over
        setTimeout(() => { if (!_l2Tip) hideL1(); }, 300);
      });

      // Click → L2
      el.addEventListener('click', e => {
        e.stopPropagation();
        hideL1();
        if (getTerm(normKey)) {
          showL2(normKey, el);
        }
      });

      // Keyboard
      el.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          hideL1();
          showL2(normKey, el);
        }
        if (e.key === 'Escape') { hideL1(); hideL2(); }
      });
    });
  }

  // Map trust.js metric keys to glossary keys
  function normaliseKey(k) {
    const MAP = {
      total_cost: 'actual_cost',
      total_savings: 'savings',
      savings_pct: 'savings_pct',
      baseline_cost: 'baseline_cost',
      actual_cost: 'actual_cost',
      request_count: 'request_count',
      avg_latency_ms: 'latency_avg',
      error_rate: 'error_rate',
      token_count: 'raw_tokens',
      compression_ratio: 'compression_ratio',
      cost_per_request: 'cost_per_request',
      total_requests: 'request_count',
    };
    return MAP[k] || k;
  }

  function formatTermLabel(key, raw) {
    const LABELS = {
      baseline_cost:    'Baseline Cost',
      actual_cost:      'Actual Cost',
      savings:          'Savings',
      savings_pct:      'Savings %',
      compression_ratio:'Compression Ratio',
      error_rate:       'Error Rate',
      retry_rate:       'Retry Rate',
      latency_avg:      'Latency (Avg)',
      latency_p95:      'Latency (p95)',
      latency_p99:      'Latency (p99)',
      raw_tokens:       'Raw Tokens',
      final_tokens:     'Final Tokens',
      reconciled:       'Reconciled',
      estimated:        'Estimated',
      cache_tokens:     'Cache Tokens',
      request_count:    'Request Count',
      cost_per_request: 'Cost / Request',
    };
    return LABELS[key] || raw || key;
  }

  // ─── Glossary Modal ────────────────────────────────────────────────────────

  function openGlossaryModal() {
    if (document.getElementById('gloss-modal')) return;

    const glossary = getGlossary();
    const modal = document.createElement('div');
    modal.id = 'gloss-modal';
    modal.className = 'gloss-modal-overlay';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-label', 'Terminology Glossary');

    const html = buildModalHTML(glossary);
    modal.innerHTML = html;
    document.body.appendChild(modal);

    // Bind search
    const searchInput = modal.querySelector('#gloss-search');
    if (searchInput) {
      searchInput.addEventListener('input', () => filterGlossaryModal(searchInput.value));
      searchInput.focus();
    }

    // Close button
    modal.querySelector('.gloss-modal-close').addEventListener('click', closeGlossaryModal);

    // ESC key
    modal._keyHandler = e => { if (e.key === 'Escape') closeGlossaryModal(); };
    document.addEventListener('keydown', modal._keyHandler);

    // Click outside
    modal.addEventListener('click', e => { if (e.target === modal) closeGlossaryModal(); });

    document.body.style.overflow = 'hidden';
  }

  function buildModalHTML(glossary) {
    const cats = Object.entries(CATEGORIES);
    const catSections = cats.map(([catKey, cat]) => {
      const items = cat.keys.map(key => {
        const card = glossary[key];
        if (!card) return '';
        return '<li class="gloss-item" data-term-key="' + escHtml(key) + '" ' +
               'data-search="' + escHtml([formatTermLabel(key, card.term), card.what, (card.aliases||[]).join(' ')].join(' ').toLowerCase()) + '">' +
               '<div class="gloss-item-header">' +
               '<span class="gloss-item-term">' + escHtml(formatTermLabel(key, card.term)) + '</span>' +
               (card.aliases && card.aliases.length ? '<span class="gloss-item-alias">aka: ' + card.aliases.slice(0,2).map(escHtml).join(', ') + '</span>' : '') +
               '</div>' +
               '<div class="gloss-item-what">' + escHtml(card.what) + '</div>' +
               (card.how ? '<code class="gloss-item-formula">' + escHtml(card.how) + '</code>' : '') +
               (card.not_this ? '<div class="gloss-item-not">≠ ' + escHtml(card.not_this) + '</div>' : '') +
               '</li>';
      }).join('');

      return '<div class="gloss-cat" data-cat="' + catKey + '">' +
             '<div class="gloss-cat-header">' + cat.icon + ' ' + cat.label + '</div>' +
             '<ul class="gloss-cat-list">' + items + '</ul>' +
             '</div>';
    }).join('');

    const unitSection = '<div class="gloss-unit-section">' +
      '<div class="gloss-section-title">📐 Unit Format Rules</div>' +
      '<ul class="gloss-unit-list">' +
      Object.entries(UNIT_RULES).map(([, r]) =>
        '<li><code class="gloss-unit-ex">' + escHtml(r.example) + '</code> — ' + escHtml(r.pattern) + '</li>'
      ).join('') +
      '</ul></div>';

    const aggrSection = '<div class="gloss-aggr-section">' +
      '<div class="gloss-section-title">📊 Aggregation Types</div>' +
      '<ul class="gloss-aggr-list">' +
      Object.entries(AGGREGATION).map(([k, v]) =>
        '<li><span class="gloss-aggr-key">' + escHtml(k.replace('_',' ')) + '</span> — ' + escHtml(v) + '</li>'
      ).join('') +
      '</ul>' +
      '<div class="gloss-section-title" style="margin-top:12px">📈 Trend Types</div>' +
      '<ul class="gloss-aggr-list">' +
      Object.entries(TREND_TYPES).map(([k, v]) =>
        '<li><span class="gloss-aggr-key">' + escHtml(k) + '</span> — ' + escHtml(v) + '</li>'
      ).join('') +
      '</ul></div>';

    return '<div class="gloss-modal-box" role="document">' +
      '<div class="gloss-modal-header">' +
        '<h2 class="gloss-modal-title">📖 Terminology Glossary</h2>' +
        '<button class="gloss-modal-close" aria-label="Close glossary">×</button>' +
      '</div>' +
      '<div class="gloss-modal-search">' +
        '<input id="gloss-search" type="search" placeholder="Search terms, formulas, aliases…" autocomplete="off" aria-label="Search glossary">' +
      '</div>' +
      '<div class="gloss-modal-body">' +
        '<div id="gloss-cat-container">' + catSections + '</div>' +
        '<hr class="gloss-divider">' +
        unitSection +
        '<hr class="gloss-divider">' +
        aggrSection +
      '</div>' +
    '</div>';
  }

  function filterGlossaryModal(query) {
    const q = (query || '').toLowerCase().trim();
    const items = document.querySelectorAll('#gloss-modal .gloss-item');
    items.forEach(item => {
      const match = !q || item.dataset.search.includes(q);
      item.style.display = match ? '' : 'none';
    });

    // Show/hide category headers if all items hidden
    document.querySelectorAll('#gloss-modal .gloss-cat').forEach(cat => {
      const visible = cat.querySelectorAll('.gloss-item:not([style*="display: none"])').length;
      cat.style.display = visible ? '' : 'none';
    });
  }

  function closeGlossaryModal() {
    const modal = document.getElementById('gloss-modal');
    if (modal) {
      document.removeEventListener('keydown', modal._keyHandler);
      modal.remove();
      document.body.style.overflow = '';
    }
  }

  // ─── "?" help icon in sidebar ─────────────────────────────────────────────
  // Wired via data-gloss-open attribute in HTML

  function attachGlossaryTriggers() {
    document.querySelectorAll('[data-gloss-open]').forEach(el => {
      if (el.dataset.glossOpenBound) return;
      el.dataset.glossOpenBound = '1';
      el.addEventListener('click', e => { e.preventDefault(); openGlossaryModal(); });
      el.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openGlossaryModal(); }
      });
    });
  }

  // ─── Helpers ───────────────────────────────────────────────────────────────

  function escHtml(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ─── Init ──────────────────────────────────────────────────────────────────

  function init() {
    attachTooltips();
    attachGlossaryTriggers();

    // Re-attach after HTMX swaps
    document.addEventListener('htmx:afterSwap', () => {
      attachTooltips();
      attachGlossaryTriggers();
    });
  }

  // ─── Public API ───────────────────────────────────────────────────────────

  window.TokenPakGlossary = {
    open: openGlossaryModal,
    close: closeGlossaryModal,
    getTerm,
    CATEGORIES,
    UNIT_RULES,
    AGGREGATION,
    TREND_TYPES,
    attachTooltips,
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

})();
