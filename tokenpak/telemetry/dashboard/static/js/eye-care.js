/**
 * TokenPak Dashboard — Long-Session Comfort & Eye Care
 *
 * Features:
 *  1. Warm mode toggle (shifts to amber palette)
 *  2. Focus mode toggle (hides nav/filters, F key shortcut)
 *  3. Session timer with optional break nudge (30 min)
 *  4. Auto-refresh pause / indicator
 *  5. Preference persistence (localStorage)
 *  6. No blocking animations, all non-intrusive
 */

(function () {
  'use strict';

  const STORAGE_KEY = 'tp-eye-care';
  const BREAK_NUDGE_MS = 30 * 60 * 1000; // 30 minutes
  const FOCUS_KEY = 'f'; // keyboard shortcut

  // ── Persistence ──────────────────────────────────────────────────────────

  function loadPrefs() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
    catch (e) { return {}; }
  }

  function savePrefs(prefs) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs)); } catch (e) {}
  }

  // ── Warm Mode ─────────────────────────────────────────────────────────────

  let warmMode = false;

  function setWarmMode(on) {
    warmMode = on;
    document.body.classList.toggle('tp-warm', on);
    const btn = document.getElementById('tp-warm-toggle');
    if (btn) btn.setAttribute('aria-pressed', String(on));
    const prefs = loadPrefs();
    prefs.warm = on;
    savePrefs(prefs);
  }

  function toggleWarmMode() {
    setWarmMode(!warmMode);
  }

  // ── Focus Mode ────────────────────────────────────────────────────────────

  let focusMode = false;

  function setFocusMode(on) {
    focusMode = on;
    document.body.classList.toggle('tp-focus', on);
    const btn = document.getElementById('tp-focus-toggle');
    if (btn) btn.setAttribute('aria-pressed', String(on));
    const prefs = loadPrefs();
    prefs.focus = on;
    savePrefs(prefs);
  }

  function toggleFocusMode() {
    setFocusMode(!focusMode);
  }

  // ── Session Timer ─────────────────────────────────────────────────────────

  const sessionStart = Date.now();
  let breakNudgeDismissed = false;
  let timerInterval = null;

  function formatDuration(ms) {
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    const h = Math.floor(m / 60);
    const mm = String(m % 60).padStart(2, '0');
    const ss = String(s % 60).padStart(2, '0');
    return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
  }

  function updateTimer() {
    const elapsed = Date.now() - sessionStart;
    const clockEl = document.getElementById('tp-session-clock');
    if (clockEl) clockEl.textContent = formatDuration(elapsed);

    // Break nudge after 30 minutes
    if (!breakNudgeDismissed && elapsed >= BREAK_NUDGE_MS) {
      const nudge = document.getElementById('tp-break-nudge');
      if (nudge) nudge.classList.add('visible');
    }
  }

  // ── Auto-Refresh Management ───────────────────────────────────────────────

  let refreshPaused = false;
  let lastRefreshTime = Date.now();

  function setRefreshPaused(paused) {
    refreshPaused = paused;
    const indicator = document.getElementById('tp-refresh-indicator');
    const btn = document.getElementById('tp-pause-refresh');
    if (indicator) indicator.classList.toggle('paused', paused);
    if (btn) btn.textContent = paused ? '▶ Resume' : '⏸ Pause';
    const prefs = loadPrefs();
    prefs.refreshPaused = paused;
    savePrefs(prefs);
    // Expose to HTMX/other scripts
    window.tpRefreshPaused = paused;
  }

  function updateLastRefreshLabel() {
    const el = document.getElementById('tp-last-refresh');
    if (!el) return;
    const s = Math.round((Date.now() - lastRefreshTime) / 1000);
    el.textContent = s < 60 ? `${s}s ago` : `${Math.floor(s / 60)}m ago`;
  }

  // Called by HTMX after data refresh
  function onDataRefreshed() {
    lastRefreshTime = Date.now();
    const el = document.getElementById('tp-last-refresh');
    if (el) el.textContent = 'just now';
  }

  // ── DOM Injection ─────────────────────────────────────────────────────────

  function injectComfortBar() {
    // Inject eye-care toolbar if not already present
    if (document.getElementById('tp-comfort-bar')) return;

    const bar = document.createElement('div');
    bar.id = 'tp-comfort-bar';
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', 'Display comfort controls');
    bar.style.cssText = [
      'display:flex', 'align-items:center', 'gap:12px',
      'padding:6px 16px', 'background:var(--bg-section)',
      'border-bottom:1px solid var(--border-subtle)',
      'font-size:12px', 'color:var(--text-muted)',
      'user-select:none'
    ].join(';');

    bar.innerHTML = `
      <span id="tp-session-timer" aria-live="off" title="Session duration">
        🕐 <span id="tp-session-clock" aria-label="Session time">00:00</span>
      </span>
      <span aria-hidden="true" style="color:var(--border-default)">|</span>
      <div id="tp-refresh-bar">
        <div id="tp-refresh-indicator" aria-hidden="true"></div>
        <span>Live</span>
        <span id="tp-last-refresh" style="opacity:.6">just now</span>
        <button id="tp-pause-refresh" aria-label="Pause auto-refresh" title="Pause data refresh">⏸ Pause</button>
      </div>
      <span aria-hidden="true" style="color:var(--border-default)">|</span>
      <button id="tp-warm-toggle" aria-pressed="false" title="Toggle warm/amber mode for eye comfort">🌅 Warm</button>
      <button id="tp-focus-toggle" aria-pressed="false" title="Focus mode: hide nav (F)">⊞ Focus</button>
      <div id="tp-break-nudge" role="status" aria-live="polite">
        👀 Take a break?
        <button id="tp-break-dismiss" aria-label="Dismiss break reminder" title="Dismiss">✕</button>
      </div>
    `;

    // Prepend to body (before main content)
    const main = document.querySelector('main, .main-content, [role="main"], body > div');
    if (main && main.parentNode) {
      main.parentNode.insertBefore(bar, main);
    } else {
      document.body.prepend(bar);
    }

    // Inject focus-exit button
    if (!document.getElementById('tp-focus-exit')) {
      const exitBtn = document.createElement('button');
      exitBtn.id = 'tp-focus-exit';
      exitBtn.textContent = '✕ Exit Focus';
      exitBtn.setAttribute('aria-label', 'Exit focus mode');
      document.body.appendChild(exitBtn);
      exitBtn.addEventListener('click', () => setFocusMode(false));
    }
  }

  function bindComfortBar() {
    const warmBtn = document.getElementById('tp-warm-toggle');
    if (warmBtn) warmBtn.addEventListener('click', toggleWarmMode);

    const focusBtn = document.getElementById('tp-focus-toggle');
    if (focusBtn) focusBtn.addEventListener('click', toggleFocusMode);

    const pauseBtn = document.getElementById('tp-pause-refresh');
    if (pauseBtn) pauseBtn.addEventListener('click', () => setRefreshPaused(!refreshPaused));

    const dismissBtn = document.getElementById('tp-break-dismiss');
    if (dismissBtn) dismissBtn.addEventListener('click', () => {
      breakNudgeDismissed = true;
      const nudge = document.getElementById('tp-break-nudge');
      if (nudge) nudge.classList.remove('visible');
    });
  }

  // ── Keyboard shortcut ─────────────────────────────────────────────────────

  function onKeyDown(e) {
    // F key for focus mode (not when typing in inputs)
    if (e.key.toLowerCase() === FOCUS_KEY &&
        !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
      e.preventDefault();
      toggleFocusMode();
    }
    // Escape: exit focus mode
    if (e.key === 'Escape' && focusMode) {
      setFocusMode(false);
    }
  }

  // ── HTMX integration ─────────────────────────────────────────────────────

  document.addEventListener('htmx:beforeRequest', function (evt) {
    if (refreshPaused) {
      evt.preventDefault(); // Block auto-refresh when paused
    }
  });

  document.addEventListener('htmx:afterSettle', function () {
    onDataRefreshed();
  });

  // ── Public API ────────────────────────────────────────────────────────────

  window.TokenPakEyeCare = {
    setWarmMode,
    toggleWarmMode,
    setFocusMode,
    toggleFocusMode,
    onDataRefreshed,
    isRefreshPaused: () => refreshPaused,
  };

  // ── Init ──────────────────────────────────────────────────────────────────

  function init() {
    injectComfortBar();
    bindComfortBar();

    // Restore saved prefs
    const prefs = loadPrefs();
    if (prefs.warm) setWarmMode(true);
    if (prefs.focus) setFocusMode(false); // don't restore focus mode on load
    if (prefs.refreshPaused) setRefreshPaused(true);

    // Start session timer
    updateTimer();
    timerInterval = setInterval(() => {
      updateTimer();
      updateLastRefreshLabel();
    }, 1000);

    // Keyboard shortcuts
    document.addEventListener('keydown', onKeyDown);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
