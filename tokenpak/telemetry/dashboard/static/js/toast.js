/**
 * TokenPak Dashboard — Toast Notification System
 * 
 * Lightweight toast manager for user feedback.
 * Usage: showToast('Filter applied', 'success')
 */
'use strict';

(function () {

  let _toastContainer = null;
  let _toastId = 0;

  // ─── Toast creation ────────────────────────────────────────────────────────

  function showToast(message, type = 'info', duration = 4000) {
    if (!_toastContainer) {
      _toastContainer = document.createElement('div');
      _toastContainer.id = 'toast-container';
      _toastContainer.className = 'toast-container';
      _toastContainer.setAttribute('aria-live', 'polite');
      _toastContainer.setAttribute('aria-atomic', 'false');
      document.body.appendChild(_toastContainer);
    }

    const id = `toast-${_toastId++}`;
    const toast = document.createElement('div');
    toast.id = id;
    toast.className = `toast toast-${type}`;
    toast.setAttribute('role', 'status');

    const icon = {
      success: '✓',
      error: '✗',
      warning: '⚠',
      info: 'ⓘ',
    }[type] || 'ⓘ';

    toast.innerHTML = `
      <span class="toast-icon">${icon}</span>
      <span class="toast-message">${escHtml(message)}</span>
      <button class="toast-close" onclick="dismissToast('${id}')" aria-label="Dismiss notification">×</button>
    `;

    _toastContainer.appendChild(toast);

    // Entrance animation
    requestAnimationFrame(() => {
      toast.classList.add('toast-enter');
    });

    // Auto-dismiss
    if (duration > 0) {
      setTimeout(() => dismissToast(id), duration);
    }

    return id;
  }

  function dismissToast(id) {
    const toast = document.getElementById(id);
    if (!toast) return;
    toast.classList.remove('toast-enter');
    toast.classList.add('toast-exit');
    setTimeout(() => toast.remove(), 300);
  }

  function clearAllToasts() {
    if (_toastContainer) {
      _toastContainer.innerHTML = '';
    }
  }

  window.dismissToast = dismissToast;

  // ─── Utility ───────────────────────────────────────────────────────────────

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ─── Public API ────────────────────────────────────────────────────────────

  window.showToast = showToast;
  window.clearAllToasts = clearAllToasts;

})();
