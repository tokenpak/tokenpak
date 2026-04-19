/**
 * TokenPak Dashboard — Accessibility & System Health
 *
 * Provides:
 *  1. ARIA labels for charts (auto-generated from data)
 *  2. Keyboard navigation (table arrow keys, ESC for drawer)
 *  3. Screen reader announcements (aria-live regions)
 *  4. Focus management (drawer focus trap)
 *  5. System health indicator (polling /v1/health every 60s)
 *  6. Stale data warning (if last_event > 5min old)
 */
'use strict';

(function () {

  // ─── 1. ARIA for charts ────────────────────────────────────────────────────

  function addChartAria() {
    document.querySelectorAll('canvas[id$="-chart"]').forEach(canvas => {
      if (canvas.getAttribute('aria-label')) return; // already set
      const container = canvas.closest('[data-chart-id]') || canvas.parentElement;
      const title = container?.querySelector('.chart-title')?.textContent?.trim() || 'Chart';
      canvas.setAttribute('role', 'img');
      canvas.setAttribute('aria-label', `${title} visualization`);
    });
  }

  // ─── 2. Keyboard navigation for tables ────────────────────────────────────

  function enableTableKeyboardNav() {
    document.querySelectorAll('table[aria-label]').forEach(table => {
      if (table.dataset.keyboardNav) return;
      table.dataset.keyboardNav = '1';

      const rows = () => Array.from(table.querySelectorAll('tbody tr'));
      let focusedIndex = -1;

      table.addEventListener('keydown', e => {
        const allRows = rows();
        if (!allRows.length) return;

        if (e.key === 'ArrowDown') {
          e.preventDefault();
          focusedIndex = Math.min(focusedIndex + 1, allRows.length - 1);
          allRows[focusedIndex]?.focus();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          focusedIndex = Math.max(focusedIndex - 1, 0);
          allRows[focusedIndex]?.focus();
        } else if (e.key === 'Enter' && focusedIndex >= 0) {
          allRows[focusedIndex]?.click();
        }
      });

      // Make rows focusable
      allRows.forEach((row, i) => {
        if (!row.getAttribute('tabindex')) row.setAttribute('tabindex', '0');
        row.addEventListener('focus', () => { focusedIndex = i; });
      });
    });
  }

  // ─── 3. ESC to close drawer ────────────────────────────────────────────────

  function enableDrawerEscape() {
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        const drawer = document.querySelector('.context-drawer.open');
        if (drawer && window.TokenPakDrawer?.close) {
          e.preventDefault();
          window.TokenPakDrawer.close();
        }
      }
    });
  }

  // ─── 4. Focus trap in drawer ───────────────────────────────────────────────

  function trapFocusInDrawer() {
    document.addEventListener('keydown', e => {
      const drawer = document.querySelector('.context-drawer.open');
      if (!drawer || e.key !== 'Tab') return;

      const focusable = Array.from(drawer.querySelectorAll(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
      ));
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    });
  }

  // ─── 5. Screen reader announcements ────────────────────────────────────────

  let _liveRegion = null;

  function announce(message, priority = 'polite') {
    if (!_liveRegion) {
      _liveRegion = document.createElement('div');
      _liveRegion.id = 'a11y-live';
      _liveRegion.className = 'sr-only';
      _liveRegion.setAttribute('aria-live', priority);
      _liveRegion.setAttribute('aria-atomic', 'true');
      document.body.appendChild(_liveRegion);
    }
    _liveRegion.textContent = message;
  }

  window.a11yAnnounce = announce;

  // ─── 6. Health indicator polling ───────────────────────────────────────────

  let _healthState = { status: 'unknown', error_rate_1h: 0, last_event_age_s: 0 };

  async function pollHealth() {
    try {
      const res = await fetch('/v1/health');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      updateHealthIndicator(data);
      _healthState = data;
    } catch (e) {
      console.warn('Health poll failed:', e);
      updateHealthIndicator({ status: 'error', error: e.message });
    }
  }

  function updateHealthIndicator(data) {
    const badge = document.getElementById('health-badge');
    if (!badge) return;

    const { status, error_rate_1h, last_event_age_s, requests_24h, is_stale } = data;

    let icon, cls, label;
    if (status === 'healthy') {
      icon = '🟢'; cls = 'health-ok'; label = 'Healthy';
    } else if (status === 'degraded' || error_rate_1h > 0.05) {
      icon = '🟡'; cls = 'health-warn'; label = 'Degraded';
    } else if (status === 'down' || status === 'error') {
      icon = '🔴'; cls = 'health-down'; label = 'Down';
    } else {
      icon = '⚫'; cls = 'health-unknown'; label = 'Unknown';
    }

    badge.className = `health-badge ${cls}`;
    badge.innerHTML = `<span class="health-icon">${icon}</span><span class="health-label">${label}</span>`;
    badge.title = `System status: ${label}. Click for details.`;
    badge.setAttribute('aria-label', `System health: ${label}`);

    // Update detail panel if open
    updateHealthPanel(data);

    // Stale data warning
    if (is_stale || (last_event_age_s && last_event_age_s > 300)) {
      showStaleWarning(last_event_age_s);
    } else {
      hideStaleWarning();
    }
  }

  function showStaleWarning(age_s) {
    const banner = document.getElementById('stale-data-banner');
    if (!banner) return;
    const min = Math.floor(age_s / 60);
    banner.textContent = `⚠ Data may be stale — last event ${min}m ago. Click Update to refresh.`;
    banner.style.display = 'flex';
    banner.setAttribute('role', 'alert');
  }

  function hideStaleWarning() {
    const banner = document.getElementById('stale-data-banner');
    if (banner) banner.style.display = 'none';
  }

  function updateHealthPanel(data) {
    const panel = document.getElementById('health-detail-panel');
    if (!panel || !panel.offsetParent) return; // not visible

    const { ingest_active, last_event_ts, error_rate_1h, requests_24h, last_event_age_s } = data;
    const rows = [
      { key: 'Ingest Status', val: ingest_active ? 'Active' : 'Paused' },
      { key: 'Last Event', val: last_event_ts ? relTime(last_event_age_s) : 'Unknown' },
      { key: 'Error Rate (1h)', val: error_rate_1h !== undefined ? (error_rate_1h * 100).toFixed(2) + '%' : '—' },
      { key: 'Requests (24h)', val: requests_24h !== undefined ? requests_24h.toLocaleString() : '—' },
    ];

    const tbody = panel.querySelector('tbody');
    if (tbody) {
      tbody.innerHTML = rows.map(r => `<tr><td class="hp-key">${escHtml(r.key)}</td><td class="hp-val">${escHtml(r.val)}</td></tr>`).join('');
    }
  }

  function relTime(age_s) {
    if (!age_s || age_s < 0) return 'just now';
    const m = Math.floor(age_s / 60);
    const h = Math.floor(age_s / 3600);
    if (age_s < 60) return age_s + 's ago';
    if (m < 60) return m + 'm ago';
    return h + 'h ago';
  }

  function toggleHealthPanel() {
    let panel = document.getElementById('health-detail-panel');
    if (panel) {
      panel.remove();
      return;
    }

    panel = document.createElement('div');
    panel.id = 'health-detail-panel';
    panel.className = 'health-detail-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'System health details');
    panel.innerHTML = `
      <div class="hp-header">
        <strong>System Health</strong>
        <button class="hp-close" onclick="this.closest('#health-detail-panel').remove()" aria-label="Close">×</button>
      </div>
      <table class="hp-table">
        <tbody></tbody>
      </table>
    `;

    document.body.appendChild(panel);

    // Position below badge
    const badge = document.getElementById('health-badge');
    if (badge) {
      const r = badge.getBoundingClientRect();
      panel.style.top = (r.bottom + window.scrollY + 6) + 'px';
      panel.style.left = Math.max(8, r.right + window.scrollX - panel.offsetWidth) + 'px';
    }

    updateHealthPanel(_healthState);
  }

  window.toggleHealthPanel = toggleHealthPanel;

  // ─── 7. Skip to main content ───────────────────────────────────────────────

  function addSkipLink() {
    if (document.getElementById('skip-link')) return;
    const link = document.createElement('a');
    link.id = 'skip-link';
    link.className = 'skip-link';
    link.href = '#main-content';
    link.textContent = 'Skip to main content';
    link.addEventListener('click', e => {
      e.preventDefault();
      const main = document.getElementById('main-content');
      if (main) {
        main.setAttribute('tabindex', '-1');
        main.focus();
        main.removeAttribute('tabindex');
      }
    });
    document.body.insertBefore(link, document.body.firstChild);
  }

  // ─── 8. Heading hierarchy check (dev mode) ────────────────────────────────

  function checkHeadingHierarchy() {
    const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'));
    const levels = headings.map(h => parseInt(h.tagName[1]));
    const h1s = headings.filter(h => h.tagName === 'H1');

    if (h1s.length === 0) console.warn('A11Y: No <h1> found on page');
    if (h1s.length > 1) console.warn('A11Y: Multiple <h1> tags found:', h1s);

    for (let i = 1; i < levels.length; i++) {
      if (levels[i] - levels[i - 1] > 1) {
        console.warn(`A11Y: Heading skip from <h${levels[i - 1]}> to <h${levels[i]}>`, headings[i]);
      }
    }
  }

  // ─── 9. Focus ring enhancement ─────────────────────────────────────────────

  function enhanceFocusRings() {
    // Add visible focus rings for keyboard navigation
    const style = document.createElement('style');
    style.textContent = `
      *:focus-visible {
        outline: 2px solid var(--color-primary, #6366f1);
        outline-offset: 2px;
      }
      button:focus-visible, a:focus-visible, [role="button"]:focus-visible {
        outline: 2px solid var(--color-primary, #6366f1);
        outline-offset: 2px;
      }
    `;
    document.head.appendChild(style);
  }

  // ─── Init ─────────────────────────────────────────────────────────────────

  function init() {
    addChartAria();
    enableTableKeyboardNav();
    enableDrawerEscape();
    trapFocusInDrawer();
    addSkipLink();
    enhanceFocusRings();

    // Health polling
    pollHealth();
    setInterval(pollHealth, 60000);

    // Re-run accessibility helpers on HTMX swaps
    document.addEventListener('htmx:afterSwap', () => {
      addChartAria();
      enableTableKeyboardNav();
    });

    // Dev mode checks
    if (window.location.search.includes('debug')) {
      checkHeadingHierarchy();
    }
  }

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ─── Public API ───────────────────────────────────────────────────────────

  window.TokenPakA11y = {
    announce,
    addChartAria,
    enableTableKeyboardNav,
    pollHealth,
    toggleHealthPanel,
    checkHeadingHierarchy,
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

})();
