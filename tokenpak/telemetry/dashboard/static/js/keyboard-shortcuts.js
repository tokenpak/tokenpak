/**
 * TokenPak Dashboard — Keyboard Shortcuts
 * Power-user navigation and action shortcuts. Vanilla JS, no dependencies.
 *
 * Sequential shortcuts (g→f, g→e, g→a, g→s) have a 500ms window.
 * All shortcuts disabled when focus is inside input/textarea/select.
 */

'use strict';

(function () {

  // ─── Shortcuts Registry ─────────────────────────────────────────────────

  var SHORTCUTS = [
    { key: '?',       label: 'Show keyboard shortcuts',  action: 'show-help' },
    { key: 'g f',     label: 'Go to FinOps',             action: 'nav-finops' },
    { key: 'g e',     label: 'Go to Engineering',        action: 'nav-engineering' },
    { key: 'g a',     label: 'Go to Audit',              action: 'nav-audit' },
    { key: 'g s',     label: 'Go to Settings',           action: 'nav-settings' },
    { key: 'f',       label: 'Focus search / filter',    action: 'focus-search' },
    { key: 'r',       label: 'Refresh current data',     action: 'refresh' },
    { key: 'Escape',  label: 'Close modal / drawer',     action: 'close-modal' },
  ];

  // ─── State ──────────────────────────────────────────────────────────────

  var pendingPrefix = null;      // 'g' while waiting for second key
  var prefixTimer = null;        // timeout to reset pending prefix
  var PREFIX_TIMEOUT_MS = 500;

  // ─── Helpers ────────────────────────────────────────────────────────────

  function inInputField() {
    var tag = (document.activeElement || {}).tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
  }

  function navigate(path) {
    window.location.href = path;
  }

  function focusSearch() {
    var el = document.querySelector(
      'input[type="search"], input[placeholder*="ilter"], input[placeholder*="earch"], #filter-input, .tp-filter-input'
    );
    if (el) { el.focus(); el.select(); }
  }

  function triggerRefresh() {
    var btn = document.getElementById('telemetry-refresh-btn');
    if (btn && !btn.disabled) btn.click();
  }

  function closeModal() {
    // Close any open overlay/modal/drawer
    var selectors = [
      '#tp-shortcuts-modal',
      '#tp-confirm-overlay',
      '.tp-modal[style*="flex"]',
      '.tp-drawer.open',
      '[role="dialog"]',
    ];
    selectors.forEach(function (sel) {
      document.querySelectorAll(sel).forEach(function (el) {
        el.style.display = 'none';
        el.classList.remove('open');
      });
    });
  }

  // ─── Help Modal ─────────────────────────────────────────────────────────

  function buildHelpModal() {
    var existing = document.getElementById('tp-shortcuts-modal');
    if (existing) return existing;

    var overlay = document.createElement('div');
    overlay.id = 'tp-shortcuts-modal';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Keyboard shortcuts');
    overlay.style.cssText = [
      'display:none',
      'position:fixed',
      'inset:0',
      'background:rgba(0,0,0,.5)',
      'z-index:9999',
      'align-items:center',
      'justify-content:center',
    ].join(';');

    var rows = SHORTCUTS.map(function (s) {
      return '<tr><td class="tp-shortcut-key"><kbd>' + escHtml(s.key) + '</kbd></td>' +
             '<td class="tp-shortcut-desc">' + escHtml(s.label) + '</td></tr>';
    }).join('');

    overlay.innerHTML =
      '<div class="tp-shortcuts-dialog" style="background:#fff;border-radius:8px;padding:24px;max-width:480px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.25)">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">' +
      '<h2 style="margin:0;font-size:1.1rem">⌨ Keyboard Shortcuts</h2>' +
      '<button id="tp-shortcuts-close" style="border:none;background:none;font-size:1.25rem;cursor:pointer;line-height:1" aria-label="Close">✕</button>' +
      '</div>' +
      '<table style="width:100%;border-collapse:collapse">' +
      '<thead><tr style="text-align:left;border-bottom:2px solid #eee">' +
      '<th style="padding:6px 12px 6px 0;font-size:.8rem;color:#666">KEY</th>' +
      '<th style="padding:6px 0;font-size:.8rem;color:#666">ACTION</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>' +
      '<p style="margin-top:12px;font-size:.75rem;color:#999">Shortcuts disabled while typing in inputs.</p>' +
      '</div>';

    document.body.appendChild(overlay);

    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) closeModal();
    });
    overlay.querySelector('#tp-shortcuts-close').addEventListener('click', closeModal);

    return overlay;
  }

  function showHelp() {
    var modal = buildHelpModal();
    modal.style.display = 'flex';
    modal.querySelector('#tp-shortcuts-close').focus();
  }

  // ─── Key Handler ────────────────────────────────────────────────────────

  function handleKey(e) {
    if (inInputField()) return;

    var key = e.key;

    // Escape always works
    if (key === 'Escape') {
      closeModal();
      pendingPrefix = null;
      clearTimeout(prefixTimer);
      return;
    }

    // Sequential prefix 'g'
    if (pendingPrefix === 'g') {
      clearTimeout(prefixTimer);
      pendingPrefix = null;

      if (key === 'f') { e.preventDefault(); navigate('/dashboard/finops'); }
      else if (key === 'e') { e.preventDefault(); navigate('/dashboard/engineering'); }
      else if (key === 'a') { e.preventDefault(); navigate('/dashboard/audit'); }
      else if (key === 's') { e.preventDefault(); navigate('/dashboard/settings'); }
      return;
    }

    // Single-key shortcuts — ignore if modifier keys held (except Shift for ?)
    if (e.ctrlKey || e.altKey || e.metaKey) return;

    if (key === '?' || (key === '/' && e.shiftKey)) {
      e.preventDefault();
      showHelp();
      return;
    }

    if (key === 'g') {
      e.preventDefault();
      pendingPrefix = 'g';
      prefixTimer = setTimeout(function () { pendingPrefix = null; }, PREFIX_TIMEOUT_MS);
      return;
    }

    if (key === 'f') {
      e.preventDefault();
      focusSearch();
      return;
    }

    if (key === 'r') {
      e.preventDefault();
      triggerRefresh();
      return;
    }
  }

  document.addEventListener('keydown', handleKey);

  // ─── Footer Badge ────────────────────────────────────────────────────────

  function addFooterBadge() {
    var footer = document.querySelector('.dashboard-footer, footer, .tp-footer');
    if (!footer) return;

    var badge = document.createElement('button');
    badge.id = 'tp-shortcuts-badge';
    badge.textContent = '?';
    badge.title = 'Keyboard shortcuts';
    badge.setAttribute('aria-label', 'Show keyboard shortcuts');
    badge.style.cssText = [
      'position:fixed',
      'bottom:16px',
      'right:16px',
      'width:28px',
      'height:28px',
      'border-radius:50%',
      'border:1px solid #ccc',
      'background:#f5f5f5',
      'font-size:.85rem',
      'font-weight:bold',
      'cursor:pointer',
      'z-index:100',
      'line-height:28px',
      'text-align:center',
      'padding:0',
      'color:#666',
    ].join(';');
    badge.addEventListener('click', showHelp);
    document.body.appendChild(badge);
  }

  // ─── Public API (for tests) ───────────────────────────────────────────────

  window.TokenPakShortcuts = {
    registry: SHORTCUTS,
    showHelp: showHelp,
    closeModal: closeModal,
    triggerRefresh: triggerRefresh,
    focusSearch: focusSearch,
  };

  // ─── Init ────────────────────────────────────────────────────────────────

  function escHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function init() {
    buildHelpModal();
    addFooterBadge();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
