(function(){
  'use strict';

  let slowTimer = null;

  function updateOrientation() {
    const params = new URLSearchParams(window.location.search);
    const days = params.get('days') || '7';
    const provider = params.get('provider') || 'All providers';
    const mode = window.localStorage.getItem('dashboard-mode') || 'Advanced';

    const rangeEl = document.getElementById('orientation-range');
    const providerEl = document.getElementById('orientation-provider');
    const modeEl = document.getElementById('orientation-mode');
    const freshEl = document.getElementById('orientation-freshness');

    if (rangeEl) rangeEl.textContent = `Last ${days} days`;
    if (providerEl) providerEl.textContent = provider;
    if (modeEl) modeEl.textContent = mode.charAt(0).toUpperCase() + mode.slice(1);
    if (freshEl) freshEl.textContent = 'Updated just now';
  }

  function showLoading() {
    const overlay = document.getElementById('first-load-overlay');
    if (overlay) overlay.classList.add('active');
    clearTimeout(slowTimer);
    slowTimer = setTimeout(() => {
      const msg = document.getElementById('slow-load-msg');
      if (msg) msg.classList.add('active');
    }, 3000);
  }

  function hideLoading() {
    const overlay = document.getElementById('first-load-overlay');
    const msg = document.getElementById('slow-load-msg');
    if (overlay) overlay.classList.remove('active');
    if (msg) msg.classList.remove('active');
    clearTimeout(slowTimer);

    const freshEl = document.getElementById('orientation-freshness');
    if (freshEl) freshEl.textContent = 'Updated moments ago';
  }

  document.addEventListener('DOMContentLoaded', updateOrientation);
  document.body.addEventListener('filter-changed', showLoading);
  document.body.addEventListener('htmx:beforeRequest', showLoading);
  document.body.addEventListener('htmx:afterSwap', function(){
    hideLoading();
    updateOrientation();
  });
  document.body.addEventListener('htmx:responseError', hideLoading);
})();
