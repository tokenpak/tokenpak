/**
 * TokenPak Dashboard — Breadcrumb & Drill State Navigation
 * 
 * Manages drill path state in URL params, renders breadcrumb trail,
 * filter chips, depth indicator, back navigation, and reset.
 */

'use strict';

(function () {

  // ─── Drill path model ─────────────────────────────────────────────────────
  // Each segment: { type: 'provider'|'model'|'agent'|'date'|'status', value, label }
  // URL encoding: ?drill=provider:anthropic,model:claude-sonnet-4

  const FILTER_TYPES = ['provider', 'model', 'agent', 'date', 'status'];
  const FILTER_LABELS = {
    provider: 'Provider', model: 'Model', agent: 'Agent',
    date: 'Date', status: 'Status'
  };

  // Depth levels (Summary → Detailed → Trace → Segment)
  const DEPTH_LEVELS = ['Overview', 'Filtered', 'Trace', 'Segment'];

  function getActiveFilters() {
    const url = new URL(window.location.href);
    const filters = [];
    for (const type of FILTER_TYPES) {
      const val = url.searchParams.get(type);
      if (val && val !== 'all' && val !== '') {
        filters.push({
          type,
          value: val,
          label: FILTER_LABELS[type] || type,
        });
      }
    }
    return filters;
  }

  function getCurrentDepth() {
    // Depth inferred from active filter count + whether we're in trace view
    const filters = getActiveFilters();
    const isTrace = window.location.pathname.includes('/trace/');
    if (isTrace) return 2;
    return Math.min(filters.length, DEPTH_LEVELS.length - 1);
  }

  function getViewLabel() {
    const path = window.location.pathname;
    if (path.includes('/finops')) return 'FinOps';
    if (path.includes('/engineering')) return 'Engineering';
    if (path.includes('/audit')) return 'Audit';
    if (path.includes('/settings')) return 'Settings';
    return 'Dashboard';
  }

  // ─── URL helpers ──────────────────────────────────────────────────────────

  function buildFilterURL(keepTypes) {
    const url = new URL(window.location.href);
    // Remove all filters not in keepTypes
    for (const type of FILTER_TYPES) {
      if (!keepTypes.includes(type)) url.searchParams.delete(type);
    }
    url.searchParams.set('page', '1');
    return url.toString();
  }

  function removeFilter(type) {
    const url = new URL(window.location.href);
    url.searchParams.delete(type);
    url.searchParams.set('page', '1');
    window.location.href = url.toString();
  }

  function resetToHome() {
    const url = new URL(window.location.href);
    for (const type of FILTER_TYPES) url.searchParams.delete(type);
    url.searchParams.delete('page');
    url.searchParams.delete('search');
    window.location.href = url.toString();
  }
  window.resetDashboard = resetToHome;

  // Navigate back to a specific drill depth (keep first N filter types)
  function drillBackTo(index) {
    const filters = getActiveFilters();
    const keepTypes = filters.slice(0, index).map(f => f.type);
    window.location.href = buildFilterURL(keepTypes);
  }

  // ─── Breadcrumb render ────────────────────────────────────────────────────

  function renderBreadcrumb() {
    const container = document.getElementById('breadcrumb-trail');
    if (!container) return;

    const filters = getActiveFilters();
    const viewLabel = getViewLabel();
    const segments = [];

    // Root segment (always)
    segments.push({
      label: viewLabel,
      href: buildFilterURL([]),
      isCurrent: filters.length === 0,
      depth: 0,
    });

    // One segment per active filter
    filters.forEach((f, i) => {
      segments.push({
        label: `${f.label}: ${truncate(f.value, 20)}`,
        href: buildFilterURL(filters.slice(0, i + 1).map(x => x.type)),
        isCurrent: i === filters.length - 1,
        depth: i + 1,
        filterType: f.type,
        filterValue: f.value,
      });
    });

    const html = segments.map((seg, i) => {
      const sep = i > 0 ? '<span class="bc-sep" aria-hidden="true">›</span>' : '';
      if (seg.isCurrent) {
        return `${sep}<span class="bc-item bc-current" aria-current="page">${escHtml(seg.label)}</span>`;
      }
      return `${sep}<a class="bc-item bc-link" href="${escAttr(seg.href)}">${escHtml(seg.label)}</a>`;
    }).join('');

    container.innerHTML = `
      <nav class="breadcrumb" aria-label="Drill path navigation">
        ${html}
        ${filters.length > 0
          ? `<button class="bc-reset" onclick="resetDashboard()" title="Reset to overview (Esc)">↺ Reset</button>`
          : ''}
      </nav>`;
  }

  // ─── Filter chips render ──────────────────────────────────────────────────

  function renderFilterChips() {
    const container = document.getElementById('filter-chips');
    if (!container) return;

    const filters = getActiveFilters();
    const search = new URL(window.location.href).searchParams.get('search');

    if (!filters.length && !search) {
      container.innerHTML = '';
      container.style.display = 'none';
      return;
    }

    container.style.display = 'flex';
    const chips = filters.map(f => `
      <span class="filter-chip-nav" data-filter-type="${escAttr(f.type)}">
        <span class="chip-label">${escHtml(f.label)}: <strong>${escHtml(truncate(f.value, 20))}</strong></span>
        <button class="chip-remove" onclick="removeFilter('${escAttr(f.type)}')" aria-label="Remove ${escHtml(f.label)} filter">×</button>
      </span>`).join('');

    const searchChip = search
      ? `<span class="filter-chip-nav chip-search">
           <span class="chip-label">ID: <strong>${escHtml(search)}</strong></span>
           <button class="chip-remove" onclick="removeFilter('search')" aria-label="Remove search filter">×</button>
         </span>`
      : '';

    const clearAll = (filters.length > 1 || (filters.length > 0 && search))
      ? `<button class="chip-clear-all" onclick="resetDashboard()">Clear all</button>`
      : '';

    container.innerHTML = chips + searchChip + clearAll;
  }

  // Expose removeFilter globally for chip onclick
  window.removeFilter = removeFilter;

  // ─── Depth indicator render ───────────────────────────────────────────────

  function renderDepthIndicator() {
    const container = document.getElementById('depth-indicator');
    if (!container) return;

    const depth = getCurrentDepth();
    const dots = DEPTH_LEVELS.map((label, i) => {
      const cls = i < depth ? 'depth-dot filled' : i === depth ? 'depth-dot active' : 'depth-dot';
      return `<span class="${cls}" title="${label}" aria-label="${label}${i === depth ? ' (current)' : ''}"></span>`;
    }).join('');

    container.innerHTML = `
      <div class="depth-indicator" aria-label="Drill depth: ${DEPTH_LEVELS[depth]}">
        ${dots}
        <span class="depth-label">${DEPTH_LEVELS[depth]}</span>
      </div>`;
  }

  // ─── Keyboard shortcuts ───────────────────────────────────────────────────

  document.addEventListener('keydown', e => {
    // Esc on non-drawer contexts → reset filters
    if (e.key === 'Escape') {
      const drawer = document.querySelector('.context-drawer');
      if (drawer && drawer.classList.contains('open')) return; // Let drawer handle ESC
      const filters = getActiveFilters();
      const search = new URL(window.location.href).searchParams.get('search');
      if (filters.length > 0 || search) {
        e.preventDefault();
        resetToHome();
      }
    }
    // Home key → reset to home state
    if (e.key === 'Home' && e.altKey) {
      e.preventDefault();
      resetToHome();
    }
  });

  // ─── Init + re-render hooks ───────────────────────────────────────────────

  function render() {
    renderBreadcrumb();
    renderFilterChips();
    renderDepthIndicator();
  }

  // Re-render after HTMX filter swaps (URL already updated by filter JS)
  document.addEventListener('htmx:afterSwap', render);
  document.addEventListener('filter-changed', render);

  // popstate: browser back/forward
  window.addEventListener('popstate', render);

  function init() {
    render();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  // ─── Utility ─────────────────────────────────────────────────────────────

  function truncate(str, max) {
    return str && str.length > max ? str.slice(0, max - 1) + '…' : (str || '');
  }
  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function escAttr(s) { return escHtml(s); }

  window.TokenPakBreadcrumb = { render, resetToHome, removeFilter, drillBackTo };

})();
