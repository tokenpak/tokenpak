/**
 * TokenPak Dashboard — Global Filter System
 *
 * Responsibilities:
 *  1. Read initial state from URL params on page load
 *  2. Sync all filter controls to that state
 *  3. On any filter change, debounce 300ms then:
 *     a. Update hidden inputs (chips)
 *     b. Push new URL (history.replaceState)
 *     c. Trigger HTMX partial reload of all dashboard sections
 *  4. Clear All — reset to defaults and reload
 *  5. Refresh button — immediate HTMX reload
 */

(function () {
  'use strict';

  // ── Defaults ──────────────────────────────────────────────────────────
  const DEFAULTS = {
    days: '7',
    provider: '',
    model: '',
    agent: '',
    status: 'all',
    compression: 'all',
  };

  // ── Debounce ───────────────────────────────────────────────────────────
  let _debounceTimer = null;
  function debounce(fn, ms) {
    clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(fn, ms);
  }

  // ── URL helpers ────────────────────────────────────────────────────────
  function getFilterState() {
    const params = new URLSearchParams(window.location.search);
    return {
      days:        params.get('days')        || DEFAULTS.days,
      provider:    params.get('provider')    || DEFAULTS.provider,
      model:       params.get('model')       || DEFAULTS.model,
      agent:       params.get('agent')       || DEFAULTS.agent,
      status:      params.get('status')      || DEFAULTS.status,
      compression: params.get('compression') || DEFAULTS.compression,
    };
  }

  function buildQueryString(state) {
    const p = new URLSearchParams();
    if (state.days        && state.days        !== DEFAULTS.days)        p.set('days',        state.days);
    if (state.provider    && state.provider    !== DEFAULTS.provider)    p.set('provider',    state.provider);
    if (state.model       && state.model       !== DEFAULTS.model)       p.set('model',       state.model);
    if (state.agent       && state.agent       !== DEFAULTS.agent)       p.set('agent',       state.agent);
    if (state.status      && state.status      !== DEFAULTS.status)      p.set('status',      state.status);
    if (state.compression && state.compression !== DEFAULTS.compression) p.set('compression', state.compression);
    const qs = p.toString();
    return qs ? '?' + qs : window.location.pathname;
  }

  function pushUrl(state) {
    const url = window.location.pathname + buildQueryString(state);
    history.replaceState(state, '', url);
  }

  // ── Read current filter values from DOM ────────────────────────────────
  function readDomState() {
    return {
      days:        document.getElementById('filter-range')?.value       || DEFAULTS.days,
      provider:    document.getElementById('filter-provider')?.value    || DEFAULTS.provider,
      model:       document.getElementById('filter-model')?.value       || DEFAULTS.model,
      agent:       document.getElementById('filter-agent')?.value       || DEFAULTS.agent,
      status:      document.getElementById('filter-status-input')?.value      || DEFAULTS.status,
      compression: document.getElementById('filter-compression-input')?.value || DEFAULTS.compression,
    };
  }

  // ── Apply state → DOM ─────────────────────────────────────────────────
  function applyStateToDom(state) {
    const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
    setVal('filter-range',            state.days);
    setVal('filter-provider',         state.provider);
    setVal('filter-model',            state.model);
    setVal('filter-agent',            state.agent);
    setVal('filter-status-input',     state.status);
    setVal('filter-compression-input',state.compression);

    // Chip active states
    document.querySelectorAll('[data-filter="status"]').forEach(btn => {
      const active = btn.dataset.value === state.status;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    document.querySelectorAll('[data-filter="compression"]').forEach(btn => {
      const active = btn.dataset.value === state.compression;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  // ── HTMX partial reload for all sections ───────────────────────────────
  function reloadSections(state) {
    const qs = buildQueryString(state).replace(/^\?/, '&');
    const base = new URLSearchParams();
    base.set('days',        state.days);
    base.set('provider',    state.provider);
    base.set('model',       state.model);
    base.set('agent',       state.agent);
    base.set('status',      state.status);
    base.set('compression', state.compression);
    base.set('partial',     '1');

    // Trigger each registered HTMX section
    document.querySelectorAll('[data-filter-section]').forEach(el => {
      const basePath = el.dataset.filterSection;
      el.setAttribute('hx-get', basePath + '?' + base.toString());
      htmx.process(el);
      htmx.trigger(el, 'filter-changed');
    });

    // Also update the main content block if it exists
    const main = document.getElementById('main-content');
    if (main && main.dataset.basePath) {
      main.setAttribute('hx-get', main.dataset.basePath + '?' + base.toString());
      htmx.process(main);
      htmx.trigger(main, 'filter-changed');
    }
  }

  // ── On filter change: debounce → update URL → reload ──────────────────
  function onFilterChange() {
    debounce(function () {
      const state = readDomState();
      pushUrl(state);
      reloadSections(state);
    }, 300);
  }

  // ── Chip click handler ─────────────────────────────────────────────────
  function onChipClick(e) {
    const btn = e.currentTarget;
    const filterName = btn.dataset.filter;      // status | compression
    const value      = btn.dataset.value;
    const inputId    = 'filter-' + filterName + '-input';

    // Update hidden input
    const input = document.getElementById(inputId);
    if (input) input.value = value;

    // Update active state on all chips in group
    btn.closest('.filter-chips').querySelectorAll('.filter-chip').forEach(b => {
      const active = b.dataset.value === value;
      b.classList.toggle('active', active);
      b.setAttribute('aria-pressed', active ? 'true' : 'false');
    });

    onFilterChange();
  }

  // ── Clear All ──────────────────────────────────────────────────────────
  function onClearAll() {
    applyStateToDom(DEFAULTS);
    pushUrl(DEFAULTS);
    reloadSections(DEFAULTS);
  }

  // ── Refresh button ─────────────────────────────────────────────────────
  function onRefresh() {
    const state = readDomState();
    reloadSections(state);
  }

  // ── Init ───────────────────────────────────────────────────────────────
  function init() {
    // 1. Read URL state and apply to DOM
    const urlState = getFilterState();
    applyStateToDom(urlState);

    // 2. Wire select/dropdown changes
    ['filter-range', 'filter-provider', 'filter-model', 'filter-agent'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', onFilterChange);
    });

    // 3. Wire chip clicks
    document.querySelectorAll('.filter-chip').forEach(btn => {
      btn.addEventListener('click', onChipClick);
    });

    // 4. Clear All
    const clearBtn = document.getElementById('filter-clear-btn');
    if (clearBtn) clearBtn.addEventListener('click', onClearAll);

    // 5. Refresh button
    const refreshBtn = document.getElementById('filter-refresh-btn');
    if (refreshBtn) refreshBtn.addEventListener('click', onRefresh);

    // 6. Browser back/forward
    window.addEventListener('popstate', function (e) {
      const state = e.state || getFilterState();
      applyStateToDom(state);
      reloadSections(state);
    });
  }

  // Run after DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
