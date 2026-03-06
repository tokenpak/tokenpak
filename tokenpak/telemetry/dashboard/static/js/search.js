/**
 * TokenPak Dashboard — Global Search
 * Debounced fetch to /dashboard/search, dropdown results, keyboard nav.
 */
'use strict';

(function () {
  var DEBOUNCE_MS = 300;
  var MIN_CHARS = 2;
  var TYPE_ICONS = { model: '🤖', session: '💬', date: '🗓' };

  var input, dropdown, debounceTimer, activeIdx = -1;

  function init() {
    input = document.getElementById('tp-search');
    if (!input) return;

    dropdown = document.createElement('div');
    dropdown.id = 'tp-search-dropdown';
    dropdown.setAttribute('role', 'listbox');
    dropdown.setAttribute('aria-label', 'Search results');
    dropdown.style.cssText = 'display:none;position:absolute;top:100%;left:0;right:0;background:#fff;border:1px solid #ddd;border-radius:0 0 4px 4px;box-shadow:0 4px 12px rgba(0,0,0,.15);z-index:200;max-height:320px;overflow-y:auto';
    var wrap = input.parentElement;
    if (wrap) {
      wrap.style.position = 'relative';
      wrap.appendChild(dropdown);
    }

    input.setAttribute('autocomplete', 'off');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', 'tp-search-dropdown');
    input.setAttribute('aria-haspopup', 'listbox');

    input.addEventListener('input', onInput);
    input.addEventListener('keydown', onKeydown);
    document.addEventListener('click', function (e) {
      if (!input.contains(e.target) && !dropdown.contains(e.target)) closeDropdown();
    });
  }

  function onInput() {
    clearTimeout(debounceTimer);
    var q = input.value.trim();
    if (q.length < MIN_CHARS) { closeDropdown(); return; }
    debounceTimer = setTimeout(function () { fetchResults(q); }, DEBOUNCE_MS);
  }

  function fetchResults(q) {
    fetch('/dashboard/search?q=' + encodeURIComponent(q) + '&limit=20')
      .then(function (r) { return r.json(); })
      .then(function (data) { renderDropdown(data.results || []); })
      .catch(function () { closeDropdown(); });
  }

  function renderDropdown(results) {
    activeIdx = -1;
    if (!results.length) { closeDropdown(); return; }
    dropdown.innerHTML = results.map(function (r, i) {
      var icon = TYPE_ICONS[r.type] || '🔍';
      return '<div class="tp-search-item" role="option" tabindex="-1" data-url="' + esc(r.url) + '" data-idx="' + i + '"' +
        ' style="padding:8px 14px;cursor:pointer;display:flex;align-items:center;gap:8px;border-bottom:1px solid #f0f0f0">' +
        '<span style="font-size:1em" aria-hidden="true">' + icon + '</span>' +
        '<div style="min-width:0"><div style="font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + esc(r.label) + '</div>' +
        '<div style="font-size:.8em;color:#888">' + esc(r.meta || '') + '</div></div>' +
        '</div>';
    }).join('');
    dropdown.style.display = 'block';

    dropdown.querySelectorAll('.tp-search-item').forEach(function (el) {
      el.addEventListener('mousedown', function (e) {
        e.preventDefault();
        navigate(el.dataset.url);
      });
      el.addEventListener('mouseover', function () { setActive(parseInt(el.dataset.idx, 10)); });
    });
  }

  function onKeydown(e) {
    var items = dropdown.querySelectorAll('.tp-search-item');
    if (e.key === 'Escape') { closeDropdown(); input.blur(); return; }
    if (!items.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive(Math.min(activeIdx + 1, items.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive(Math.max(activeIdx - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (activeIdx >= 0 && items[activeIdx]) navigate(items[activeIdx].dataset.url);
    }
  }

  function setActive(idx) {
    activeIdx = idx;
    dropdown.querySelectorAll('.tp-search-item').forEach(function (el, i) {
      el.style.background = i === idx ? '#f0f4ff' : '';
      el.setAttribute('aria-selected', i === idx ? 'true' : 'false');
    });
  }

  function navigate(url) {
    closeDropdown();
    input.value = '';
    if (url) window.location.href = url;
  }

  function closeDropdown() {
    if (dropdown) dropdown.style.display = 'none';
    activeIdx = -1;
  }

  function esc(str) {
    return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
