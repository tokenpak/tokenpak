/**
 * TokenPak Dashboard — Long-Session Comfort & Eye Care
 *
 * Handles: warm mode, focus mode, auto-refresh pause, reduced motion,
 * session duration indicator, and last-refresh timestamp.
 *
 * No external dependencies. Reads tp_settings from localStorage.
 */

'use strict';

(function () {

  // ─── Config ──────────────────────────────────────────────────────────────

  var SETTINGS_KEY = 'tp_settings';
  var LS_SESSION_START = 'tp_session_start';
  var LS_WARM_MODE = 'tp_warm_mode';
  var LS_FOCUS_MODE = 'tp_focus_mode';
  var LS_AUTOREFRESH_PAUSED = 'tp_autorefresh_paused';

  var BREAK_INTERVAL_MS = 30 * 60 * 1000;   // remind after 30 min
  var BREAK_SNOOZE_MS   = 10 * 60 * 1000;   // snooze for 10 min
  var REFRESH_INTERVAL_MS = 5 * 60 * 1000;  // auto-refresh every 5 min

  // ─── State ───────────────────────────────────────────────────────────────

  var sessionStart = parseInt(localStorage.getItem(LS_SESSION_START) || '0', 10) || Date.now();
  var warmMode = localStorage.getItem(LS_WARM_MODE) === 'true';
  var focusMode = localStorage.getItem(LS_FOCUS_MODE) === 'true';
  var refreshPaused = localStorage.getItem(LS_AUTOREFRESH_PAUSED) === 'true';
  var lastRefresh = Date.now();
  var refreshTimer = null;
  var breakTimer = null;

  // Persist session start
  localStorage.setItem(LS_SESSION_START, String(sessionStart));

  // ─── Theme: Warm Mode ────────────────────────────────────────────────────

  function applyWarmMode(enabled) {
    warmMode = enabled;
    localStorage.setItem(LS_WARM_MODE, String(enabled));
    document.body.classList.toggle('theme-warm', enabled);

    // Sync with settings if present
    var btn = document.getElementById('tp-warm-toggle');
    if (btn) {
      btn.setAttribute('aria-pressed', String(enabled));
      btn.title = enabled ? 'Warm mode ON — click to disable' : 'Enable warm/night mode';
      btn.classList.toggle('active', enabled);
    }
  }

  // Also sync theme from settings-ui.js (data-theme attribute)
  function syncThemeFromSettings() {
    var raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return;
    try {
      var settings = JSON.parse(raw);
      var theme = (settings.personal && settings.personal.theme) || 'system';
      if (theme === 'dark' || theme === 'high-contrast' || theme === 'light') {
        document.documentElement.setAttribute('data-theme', theme);
      } else if (theme === 'system') {
        document.documentElement.removeAttribute('data-theme');
      }
      // Warm mode from settings (if added there)
      if (settings.personal && settings.personal.warmMode !== undefined) {
        applyWarmMode(settings.personal.warmMode);
      }
    } catch (e) {}
  }

  // ─── Focus Mode ──────────────────────────────────────────────────────────

  function applyFocusMode(enabled) {
    focusMode = enabled;
    localStorage.setItem(LS_FOCUS_MODE, String(enabled));
    document.body.classList.toggle('focus-mode', enabled);

    var btn = document.getElementById('tp-focus-toggle');
    if (btn) {
      btn.setAttribute('aria-pressed', String(enabled));
      btn.title = enabled ? 'Exit focus mode (F)' : 'Enter focus mode (F)';
      btn.classList.toggle('active', enabled);
      btn.textContent = enabled ? '⊠ Exit Focus' : '⊡ Focus';
    }

    // Announce to screen readers
    var region = document.getElementById('tp-lsux-announce');
    if (region) region.textContent = enabled ? 'Focus mode enabled' : 'Focus mode disabled';
  }

  function toggleFocusMode() {
    applyFocusMode(!focusMode);
  }

  // ─── Auto-Refresh ────────────────────────────────────────────────────────

  function updateRefreshIndicator() {
    var el = document.getElementById('tp-last-refresh');
    if (!el) return;
    var secAgo = Math.round((Date.now() - lastRefresh) / 1000);
    if (secAgo < 60) {
      el.textContent = 'Updated just now';
    } else {
      var minAgo = Math.round(secAgo / 60);
      el.textContent = 'Updated ' + minAgo + 'm ago';
    }
    el.setAttribute('aria-label', el.textContent);
  }

  function triggerRefresh() {
    if (refreshPaused) return;
    lastRefresh = Date.now();
    updateRefreshIndicator();

    // Trigger htmx re-poll on refresh targets
    var targets = document.querySelectorAll('[data-autorefresh]');
    targets.forEach(function (el) {
      if (typeof htmx !== 'undefined') {
        htmx.trigger(el, 'refresh');
      } else {
        // Fallback: dispatch custom event
        el.dispatchEvent(new CustomEvent('tp:refresh'));
      }
    });

    // Subtle flash on data areas (not jarring)
    var dataAreas = document.querySelectorAll('.tp-kpi-card, .tp-chart-container');
    dataAreas.forEach(function (el) {
      el.classList.add('tp-refresh-pulse');
      setTimeout(function () { el.classList.remove('tp-refresh-pulse'); }, 600);
    });
  }

  function setRefreshPaused(paused) {
    refreshPaused = paused;
    localStorage.setItem(LS_AUTOREFRESH_PAUSED, String(paused));
    clearInterval(refreshTimer);
    if (!paused) {
      refreshTimer = setInterval(triggerRefresh, REFRESH_INTERVAL_MS);
    }
    var btn = document.getElementById('tp-autorefresh-toggle');
    if (btn) {
      btn.setAttribute('aria-pressed', String(paused));
      btn.title = paused ? 'Auto-refresh paused — click to resume' : 'Pause auto-refresh';
      btn.textContent = paused ? '⏸ Paused' : '↺ Live';
      btn.classList.toggle('paused', paused);
    }
  }

  // ─── Break Reminder ──────────────────────────────────────────────────────

  function scheduleBreakReminder() {
    clearTimeout(breakTimer);
    var elapsed = Date.now() - sessionStart;
    var nextReminder = BREAK_INTERVAL_MS - (elapsed % BREAK_INTERVAL_MS);
    breakTimer = setTimeout(showBreakReminder, nextReminder);
  }

  function showBreakReminder() {
    // Only show if user has been here 30+ min
    var elapsed = Date.now() - sessionStart;
    if (elapsed < BREAK_INTERVAL_MS) { scheduleBreakReminder(); return; }

    var banner = document.getElementById('tp-break-reminder');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'tp-break-reminder';
      banner.className = 'tp-break-banner';
      banner.setAttribute('role', 'status');
      banner.setAttribute('aria-live', 'polite');
      banner.innerHTML =
        '<span class="tp-break-icon">👁</span>' +
        '<span class="tp-break-text">You\'ve been here a while — consider a short break.</span>' +
        '<button class="tp-break-dismiss" id="tp-break-dismiss" aria-label="Dismiss break reminder">Snooze 10m</button>' +
        '<button class="tp-break-close" id="tp-break-close" aria-label="Close break reminder">✕</button>';
      document.body.appendChild(banner);

      document.getElementById('tp-break-dismiss').addEventListener('click', function () {
        banner.style.display = 'none';
        clearTimeout(breakTimer);
        breakTimer = setTimeout(showBreakReminder, BREAK_SNOOZE_MS);
      });
      document.getElementById('tp-break-close').addEventListener('click', function () {
        banner.style.display = 'none';
        // Don't re-schedule — user dismissed
      });
    } else {
      banner.style.display = 'flex';
    }

    // Auto-hide after 15s if no interaction
    setTimeout(function () { if (banner) banner.style.display = 'none'; }, 15000);

    // Schedule next
    clearTimeout(breakTimer);
    breakTimer = setTimeout(showBreakReminder, BREAK_INTERVAL_MS);
  }

  // ─── Session Duration Display ─────────────────────────────────────────────

  function formatDuration(ms) {
    var totalMin = Math.floor(ms / 60000);
    if (totalMin < 60) return totalMin + 'm';
    var h = Math.floor(totalMin / 60);
    var m = totalMin % 60;
    return h + 'h ' + (m > 0 ? m + 'm' : '');
  }

  function updateSessionDuration() {
    var el = document.getElementById('tp-session-duration');
    if (!el) return;
    var elapsed = Date.now() - sessionStart;
    el.textContent = formatDuration(elapsed);
    el.setAttribute('title', 'Time in current session');
  }

  // ─── Toolbar Injection ───────────────────────────────────────────────────

  function injectComfortToolbar() {
    // Find the top bar or page header
    var topBar = document.querySelector('.tp-topbar-right, .tp-header-actions, .nav-bar .right');
    if (!topBar) return;

    var toolbar = document.createElement('div');
    toolbar.id = 'tp-comfort-toolbar';
    toolbar.className = 'tp-comfort-toolbar';
    toolbar.setAttribute('aria-label', 'Comfort controls');
    toolbar.innerHTML =
      // Session timer
      '<span class="tp-session-timer" title="Time in session">' +
      '⏱ <span id="tp-session-duration">0m</span>' +
      '</span>' +
      // Last refresh
      '<span class="tp-refresh-status">' +
      '<span id="tp-last-refresh" aria-live="polite" aria-atomic="true">Updated just now</span>' +
      '</span>' +
      // Auto-refresh toggle
      '<button id="tp-autorefresh-toggle" class="tp-comfort-btn" aria-pressed="false" title="Pause auto-refresh">↺ Live</button>' +
      // Warm mode toggle
      '<button id="tp-warm-toggle" class="tp-comfort-btn" aria-pressed="false" title="Enable warm/night mode">🌙</button>' +
      // Focus mode toggle
      '<button id="tp-focus-toggle" class="tp-comfort-btn" aria-pressed="false" title="Enter focus mode (F)">⊡ Focus</button>';

    topBar.insertBefore(toolbar, topBar.firstChild);

    // Bind events
    document.getElementById('tp-autorefresh-toggle').addEventListener('click', function () {
      setRefreshPaused(!refreshPaused);
    });
    document.getElementById('tp-warm-toggle').addEventListener('click', function () {
      applyWarmMode(!warmMode);
    });
    document.getElementById('tp-focus-toggle').addEventListener('click', function () {
      toggleFocusMode();
    });
  }

  // ─── Keyboard Shortcuts ──────────────────────────────────────────────────

  function bindKeyboardShortcuts() {
    document.addEventListener('keydown', function (e) {
      // Ignore if in input/textarea
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      switch (e.key) {
        case 'f':
        case 'F':
          toggleFocusMode();
          e.preventDefault();
          break;
        case 'w':
        case 'W':
          applyWarmMode(!warmMode);
          e.preventDefault();
          break;
      }
    });
  }

  // ─── Reduced Motion ───────────────────────────────────────────────────────

  function applyReducedMotion() {
    var mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    function onMotionChange(e) {
      document.documentElement.classList.toggle('tp-reduced-motion', e.matches);
    }
    onMotionChange(mq);
    if (mq.addEventListener) mq.addEventListener('change', onMotionChange);
    else if (mq.addListener) mq.addListener(onMotionChange);
  }

  // ─── Live Indicator Updates ───────────────────────────────────────────────

  function startLiveUpdates() {
    // Session duration: update every minute
    updateSessionDuration();
    setInterval(updateSessionDuration, 60000);

    // Last refresh: update every 30s
    updateRefreshIndicator();
    setInterval(updateRefreshIndicator, 30000);

    // Auto-refresh: every 5 minutes (unless paused)
    if (!refreshPaused) {
      refreshTimer = setInterval(triggerRefresh, REFRESH_INTERVAL_MS);
    }

    // Break reminder
    scheduleBreakReminder();
  }

  // ─── Announce region for screen readers ──────────────────────────────────

  function injectAnnounceRegion() {
    if (document.getElementById('tp-lsux-announce')) return;
    var region = document.createElement('div');
    region.id = 'tp-lsux-announce';
    region.className = 'sr-only';
    region.setAttribute('aria-live', 'polite');
    region.setAttribute('aria-atomic', 'true');
    document.body.appendChild(region);
  }

  // ─── Init ─────────────────────────────────────────────────────────────────

  function init() {
    // Apply saved states
    applyReducedMotion();
    syncThemeFromSettings();
    applyWarmMode(warmMode);
    applyFocusMode(focusMode);
    setRefreshPaused(refreshPaused);

    injectAnnounceRegion();
    injectComfortToolbar();
    bindKeyboardShortcuts();
    startLiveUpdates();

    // Re-sync theme if settings change in another tab
    window.addEventListener('storage', function (e) {
      if (e.key === SETTINGS_KEY) syncThemeFromSettings();
      if (e.key === LS_WARM_MODE) applyWarmMode(e.newValue === 'true');
      if (e.key === LS_FOCUS_MODE) applyFocusMode(e.newValue === 'true');
    });

    // Reset session timer on page load (new navigation = new session start)
    // Only reset if >4h old (assume new session)
    var storedStart = parseInt(localStorage.getItem(LS_SESSION_START) || '0', 10);
    if (Date.now() - storedStart > 4 * 60 * 60 * 1000) {
      sessionStart = Date.now();
      localStorage.setItem(LS_SESSION_START, String(sessionStart));
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
