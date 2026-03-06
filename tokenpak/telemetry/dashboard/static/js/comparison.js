/**
 * TokenPak Dashboard — Comparison Mode
 * URL-encoded state, compare toggle, preset selector, table sorting.
 */
'use strict';

(function () {

  // ── URL helpers ────────────────────────────────────────────────────────────

  function getParam(key, def) {
    return new URL(window.location.href).searchParams.get(key) ?? def;
  }

  function navigate(patch) {
    const url = new URL(window.location.href);
    Object.entries(patch).forEach(([k, v]) => {
      if (v === null || v === '' || v === false) url.searchParams.delete(k);
      else url.searchParams.set(k, String(v));
    });
    window.location.href = url.toString();
  }

  // ── Toggle comparison on/off ───────────────────────────────────────────────

  function onToggle(enabled) {
    const presets = document.getElementById('compare-presets');
    if (presets) presets.style.display = enabled ? 'flex' : 'none';
    navigate({ compare: enabled ? 'true' : null });
  }

  // ── Preset selector ───────────────────────────────────────────────────────

  function setRange(range) {
    navigate({ compare: 'true', compare_range: range });
  }

  // ── Table sort ────────────────────────────────────────────────────────────

  function sortTable(tableId, colAttr, colType) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));

    // Determine current sort direction
    const th = table.querySelector(`[data-col="${colAttr}"]`);
    const asc = th ? th.dataset.sortDir !== 'asc' : true;

    // Update all headers
    table.querySelectorAll('th[data-col]').forEach(h => { h.dataset.sortDir = ''; h.classList.remove('sort-asc','sort-desc'); });
    if (th) { th.dataset.sortDir = asc ? 'asc' : 'desc'; th.classList.add(asc ? 'sort-asc' : 'sort-desc'); }

    const colIndex = Array.from(table.querySelectorAll('th')).findIndex(h => h.getAttribute('onclick')?.includes(`'${colAttr}'`));

    rows.sort((a, b) => {
      const aCell = a.querySelectorAll('td')[colIndex];
      const bCell = b.querySelectorAll('td')[colIndex];
      const aVal = aCell?.dataset?.val ?? aCell?.textContent?.trim() ?? '';
      const bVal = bCell?.dataset?.val ?? bCell?.textContent?.trim() ?? '';
      if (colType === 'num') {
        return asc ? parseFloat(aVal) - parseFloat(bVal) : parseFloat(bVal) - parseFloat(aVal);
      }
      return asc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    });

    rows.forEach(r => tbody.appendChild(r));
    announceSort(colAttr, asc);
  }

  // ── Accessibility announcement ────────────────────────────────────────────

  function announceSort(col, asc) {
    const live = document.getElementById('a11y-live') || document.querySelector('[aria-live]');
    if (live) live.textContent = `Sorted by ${col} ${asc ? 'ascending' : 'descending'}`;
  }

  // ── Sync compare-toggle checkbox state from URL ────────────────────────────

  function init() {
    const compareOn = getParam('compare', 'false') === 'true';
    const toggle = document.getElementById('compare-toggle');
    if (toggle) toggle.checked = compareOn;
    const presets = document.getElementById('compare-presets');
    if (presets) presets.style.display = compareOn ? 'flex' : 'none';

    // Highlight active preset
    const range = getParam('compare_range', 'previous');
    document.querySelectorAll('.preset-btn').forEach(btn => {
      const val = btn.getAttribute('onclick')?.match(/'([^']+)'/)?.[1];
      btn.classList.toggle('active', val === range);
    });
  }

  // ── Public API ────────────────────────────────────────────────────────────

  window.CompareMode = { onToggle, setRange, sortTable };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

})();
