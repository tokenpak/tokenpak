/**
 * TokenPak Dashboard — User Preferences & Memory
 *
 * Responsibilities:
 *  1. Persist last visited page, filters, view mode, aggregation, theme
 *  2. Restore state on return visit (stale check: 7 days)
 *  3. Redirect fresh visits to user's default dashboard
 *  4. Recent filter memory (last 3 combos, 7-day expiry)
 *  5. "Welcome back" toast on state restore
 *  6. Onboarding skip memory
 *  7. Privacy: no PII, filter keys only (not values beyond enums)
 *
 * Storage keys:
 *  tp_prefs         — persistent (default page, view mode, theme, onboarding)
 *  tp_session       — session-like (last page, last filters, timestamp)
 *  tp_recent_filters — recent filter history (array, max 3)
 *
 * Schema version: 1
 */

'use strict';

(function () {

  // ─── Config ─────────────────────────────────────────────────────────────

  var PREFS_KEY          = 'tp_prefs';
  var SESSION_KEY        = 'tp_session';
  var RECENT_FILTERS_KEY = 'tp_recent_filters';
  var SCHEMA_VERSION     = 1;
  var STALE_MS           = 7 * 24 * 60 * 60 * 1000; // 7 days
  var MAX_RECENT_FILTERS = 3;

  var DASHBOARD_PAGES = ['finops', 'engineering', 'audit'];
  var PAGE_PATHS = {
    finops:      '/dashboard/',
    engineering: '/dashboard/engineering',
    audit:       '/dashboard/audit',
  };

  // ─── Helpers ────────────────────────────────────────────────────────────

  function now() { return Date.now(); }

  function safeGet(key) {
    try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; }
  }

  function safeSet(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) {}
  }

  function safeDel(key) {
    try { localStorage.removeItem(key); } catch (e) {}
  }

  function isStale(ts) {
    return !ts || (now() - ts) > STALE_MS;
  }

  function currentPage() {
    var path = location.pathname.replace(/\/+$/, '');
    if (path === '/dashboard' || path === '/dashboard/') return 'finops';
    if (path.indexOf('/dashboard/engineering') === 0) return 'engineering';
    if (path.indexOf('/dashboard/audit') === 0) return 'audit';
    return null; // settings, integration, etc.
  }

  function hasUrlFilters() {
    return location.search.length > 1;
  }

  // ─── Prefs (persistent) ─────────────────────────────────────────────────

  function getPrefs() {
    var p = safeGet(PREFS_KEY);
    if (!p || p.v !== SCHEMA_VERSION) {
      return {
        v: SCHEMA_VERSION,
        defaultPage: 'finops',
        viewMode: 'basic',
        theme: 'system',
        onboardingDone: false,
      };
    }
    return p;
  }

  function savePrefs(p) {
    p.v = SCHEMA_VERSION;
    safeSet(PREFS_KEY, p);
  }

  // ─── Session state ───────────────────────────────────────────────────────

  function getSession() {
    var s = safeGet(SESSION_KEY);
    if (!s || s.v !== SCHEMA_VERSION) return null;
    return s;
  }

  function saveSession(page, filters) {
    safeSet(SESSION_KEY, {
      v: SCHEMA_VERSION,
      page: page,
      filters: filters || {},
      ts: now(),
    });
  }

  // ─── Recent filters ──────────────────────────────────────────────────────

  function getRecentFilters() {
    var rf = safeGet(RECENT_FILTERS_KEY);
    if (!Array.isArray(rf)) return [];
    // Prune stale entries
    var fresh = rf.filter(function(f) { return !isStale(f.ts); });
    if (fresh.length !== rf.length) safeSet(RECENT_FILTERS_KEY, fresh);
    return fresh;
  }

  function saveRecentFilter(filters) {
    if (!filters || Object.keys(filters).length === 0) return;
    var rf = getRecentFilters();
    // De-dupe by serialized key
    var sig = JSON.stringify(filters);
    rf = rf.filter(function(f) { return JSON.stringify(f.filters) !== sig; });
    rf.unshift({ filters: filters, ts: now() });
    if (rf.length > MAX_RECENT_FILTERS) rf = rf.slice(0, MAX_RECENT_FILTERS);
    safeSet(RECENT_FILTERS_KEY, rf);
  }

  // ─── Filter extraction ───────────────────────────────────────────────────

  function getFiltersFromUrl() {
    var params = new URLSearchParams(location.search);
    var filters = {};
    ['days', 'provider', 'model', 'agent', 'status', 'compression'].forEach(function(k) {
      var v = params.get(k);
      if (v) filters[k] = v;
    });
    return filters;
  }

  function applyFiltersToUrl(filters) {
    var params = new URLSearchParams();
    Object.keys(filters).forEach(function(k) { params.set(k, filters[k]); });
    var qs = params.toString();
    var newUrl = location.pathname + (qs ? '?' + qs : '');
    history.replaceState({}, '', newUrl);
    // Trigger HTMX reload if available
    if (window.htmx) {
      document.body.dispatchEvent(new CustomEvent('filter-changed', { bubbles: true }));
    }
  }

  // ─── Toast ───────────────────────────────────────────────────────────────

  function showToast(msg, type, duration) {
    // Use existing toast module if available
    if (window.TPToast && typeof window.TPToast.show === 'function') {
      window.TPToast.show(msg, type || 'info');
      return;
    }
    var toast = document.getElementById('tp-prefs-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'tp-prefs-toast';
      toast.setAttribute('role', 'status');
      toast.setAttribute('aria-live', 'polite');
      Object.assign(toast.style, {
        position: 'fixed', bottom: '1.5rem', left: '50%',
        transform: 'translateX(-50%)',
        background: 'var(--tp-surface-2, #1e293b)',
        color: 'var(--tp-text, #f1f5f9)',
        padding: '0.6rem 1.2rem',
        borderRadius: '0.5rem',
        fontSize: '0.875rem',
        zIndex: '9999',
        transition: 'opacity 0.4s',
        boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
      });
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.style.opacity = '1';
    clearTimeout(toast._t);
    toast._t = setTimeout(function() { toast.style.opacity = '0'; }, duration || 4000);
  }

  // ─── Recent filters UI ───────────────────────────────────────────────────

  function renderRecentFiltersDropdown() {
    var container = document.getElementById('tp-recent-filters-container');
    if (!container) return;
    var rf = getRecentFilters();
    if (rf.length === 0) { container.style.display = 'none'; return; }
    container.style.display = '';
    var select = document.getElementById('tp-recent-filters-select');
    if (!select) {
      container.innerHTML =
        '<label class="tp-recent-label" for="tp-recent-filters-select">Recent filters:</label>' +
        '<select id="tp-recent-filters-select" class="tp-recent-select">' +
        '<option value="">— pick a recent filter set —</option>' +
        '</select>';
      select = container.querySelector('select');
      select.addEventListener('change', function() {
        var idx = parseInt(this.value, 10);
        if (isNaN(idx)) return;
        var entry = getRecentFilters()[idx];
        if (entry) { applyFiltersToUrl(entry.filters); this.value = ''; }
      });
    }
    // Rebuild options
    var opts = '<option value="">— pick a recent filter set —</option>';
    rf.forEach(function(entry, i) {
      var label = Object.keys(entry.filters).map(function(k) {
        return k + '=' + entry.filters[k];
      }).join(', ') || '(defaults)';
      var age = Math.round((now() - entry.ts) / 60000);
      var ageStr = age < 60 ? age + 'm ago' : Math.round(age/60) + 'h ago';
      opts += '<option value="' + i + '">' + esc(label) + ' (' + ageStr + ')</option>';
    });
    select.innerHTML = opts;
  }

  function esc(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ─── Clear preferences ───────────────────────────────────────────────────

  window.TPPrefs = {
    clearAll: function() {
      safeDel(PREFS_KEY);
      safeDel(SESSION_KEY);
      safeDel(RECENT_FILTERS_KEY);
      showToast('Preferences cleared — starting fresh', 'info');
    },
    getPrefs: getPrefs,
    savePrefs: savePrefs,
    getRecentFilters: getRecentFilters,
    renderRecentFiltersDropdown: renderRecentFiltersDropdown,
  };

  // ─── Onboarding ──────────────────────────────────────────────────────────

  function handleOnboarding() {
    var prefs = getPrefs();
    var onboardingEl = document.getElementById('tp-onboarding');
    if (!onboardingEl) return;
    if (prefs.onboardingDone) {
      onboardingEl.style.display = 'none';
      return;
    }
    onboardingEl.style.display = '';
    var doneBtn = onboardingEl.querySelector('[data-onboarding-done]');
    if (doneBtn) {
      doneBtn.addEventListener('click', function() {
        prefs.onboardingDone = true;
        savePrefs(prefs);
        onboardingEl.style.display = 'none';
      });
    }
  }

  // ─── Default page redirect ────────────────────────────────────────────────

  function handleDefaultRedirect() {
    // Only redirect on root dashboard (no URL params, no explicit page)
    if (hasUrlFilters()) return;
    var path = location.pathname.replace(/\/+$/, '');
    if (path !== '/dashboard' && path !== '') return; // not the root
    var prefs = getPrefs();
    var defaultPage = prefs.defaultPage || 'finops';
    // Check session — was last visit to a different dashboard page?
    var session = getSession();
    var targetPage = session && !isStale(session.ts) ? (session.page || defaultPage) : defaultPage;
    if (targetPage === 'finops' || !PAGE_PATHS[targetPage]) return; // already here
    location.replace(PAGE_PATHS[targetPage]);
  }

  // ─── Main init ───────────────────────────────────────────────────────────

  function init() {
    var page = currentPage();

    // Handle non-dashboard pages (settings, etc.) — just save/restore nothing
    if (!page) return;

    // 1. Default page redirect (only on root finops page)
    handleDefaultRedirect();

    // 2. Restore last filters if session is fresh and no URL filters
    var session = getSession();
    var restored = false;
    if (session && !isStale(session.ts) && !hasUrlFilters()) {
      var savedFilters = session.filters;
      if (savedFilters && Object.keys(savedFilters).length > 0) {
        // Only restore if we're on the same page that was saved
        if (session.page === page) {
          applyFiltersToUrl(savedFilters);
          restored = true;
        }
      }
    }

    // 3. Apply saved view mode
    var prefs = getPrefs();
    var storedMode = prefs.viewMode || 'basic';
    var modeEl = document.getElementById('tp-view-mode-toggle');
    if (modeEl && modeEl.value !== storedMode) {
      modeEl.value = storedMode;
      // Dispatch change so persona-mode.js picks it up
      modeEl.dispatchEvent(new Event('change', { bubbles: true }));
    }

    // 4. Show welcome back toast if restoring non-trivial state
    if (restored) {
      showToast('↩ Restored your last view', 'info', 4000);
    }

    // 5. Onboarding skip
    handleOnboarding();

    // 6. Render recent filters dropdown (if container exists)
    renderRecentFiltersDropdown();

    // ── Event listeners ───────────────────────────────────────────────────

    // Save page + filters on HTMX navigation / filter changes
    function persistCurrentState() {
      var p = currentPage();
      if (!p) return;
      var filters = getFiltersFromUrl();
      saveSession(p, filters);
      if (Object.keys(filters).length > 0) {
        saveRecentFilter(filters);
        renderRecentFiltersDropdown();
      }
    }

    document.body.addEventListener('htmx:afterSwap', persistCurrentState);
    document.body.addEventListener('filter-changed', function() {
      // Small delay to let URL update first
      setTimeout(persistCurrentState, 50);
    });

    // Save view mode changes
    document.body.addEventListener('change', function(e) {
      if (e.target && e.target.id === 'tp-view-mode-toggle') {
        var p2 = getPrefs();
        p2.viewMode = e.target.value;
        savePrefs(p2);
      }
    });

    // Save initial state on load
    persistCurrentState();
  }

  // ─── Boot ────────────────────────────────────────────────────────────────

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
