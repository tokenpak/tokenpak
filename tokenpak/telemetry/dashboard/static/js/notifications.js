/**
 * TokenPak Dashboard — In-App Notification Bell
 * Polls /dashboard/notifications every 60s, updates badge, renders dropdown.
 */
(function () {
  'use strict';

  var POLL_INTERVAL = 60000; // 60 seconds
  var API_BASE = '/dashboard/notifications';

  var btn = document.getElementById('tp-notif-btn');
  var panel = document.getElementById('tp-notif-panel');
  var badge = document.getElementById('tp-notif-badge');
  var list = document.getElementById('tp-notif-list');
  var markAllBtn = document.getElementById('tp-notif-mark-all');

  if (!btn || !panel) return;

  // --- Helpers ---

  function relativeTime(ts) {
    var diff = Math.floor(Date.now() / 1000) - ts;
    if (diff < 60) return diff + ' sec ago';
    if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
    if (diff < 86400) return Math.floor(diff / 3600) + ' hr ago';
    return Math.floor(diff / 86400) + 'd ago';
  }

  var TYPE_ICON = { alert: '🚨', warning: '⚠️', info: 'ℹ️' };

  function renderNotifications(notifications) {
    if (!notifications || notifications.length === 0) {
      list.innerHTML = '<div style="padding:16px;color:#999;text-align:center;font-size:.85em">No notifications</div>';
      return;
    }
    list.innerHTML = notifications.map(function (n) {
      var icon = TYPE_ICON[n.type] || 'ℹ️';
      var readStyle = n.read ? 'opacity:.55;' : 'background:#f0f7ff;';
      return '<div class="tp-notif-item" data-id="' + n.id + '" role="menuitem" tabindex="0"' +
        ' style="' + readStyle + 'padding:10px 14px;cursor:pointer;border-bottom:1px solid #f0f0f0">' +
        '<div style="display:flex;gap:8px;align-items:flex-start">' +
        '<span style="font-size:1.1em;flex-shrink:0">' + icon + '</span>' +
        '<div style="flex:1;min-width:0">' +
        '<div style="font-weight:' + (n.read ? 'normal' : '600') + ';font-size:.88em;color:#222">' + escHtml(n.title) + '</div>' +
        '<div style="font-size:.8em;color:#555;margin-top:2px;white-space:normal">' + escHtml(n.message) + '</div>' +
        '<div style="font-size:.75em;color:#aaa;margin-top:4px">' + relativeTime(n.ts) + '</div>' +
        '</div></div></div>';
    }).join('');

    // Bind click → mark read
    list.querySelectorAll('.tp-notif-item').forEach(function (el) {
      el.addEventListener('click', function () {
        markRead(el.dataset.id);
      });
      el.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') markRead(el.dataset.id);
      });
    });
  }

  function escHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function updateBadge(unread) {
    if (unread > 0) {
      badge.textContent = unread > 99 ? '99+' : unread;
      badge.style.display = 'inline-block';
    } else {
      badge.style.display = 'none';
    }
  }

  // --- API calls ---

  function fetchNotifications(limit) {
    limit = limit || 20;
    return fetch(API_BASE + '?limit=' + limit)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        updateBadge(data.unread || 0);
        renderNotifications(data.notifications || []);
        return data;
      })
      .catch(function () {
        list.innerHTML = '<div style="padding:16px;color:#c00;font-size:.85em;text-align:center">Failed to load notifications</div>';
      });
  }

  function markRead(id) {
    fetch(API_BASE + '/' + id + '/read', { method: 'POST' })
      .then(function () { return fetchNotifications(); })
      .catch(function () {});
  }

  function markAllRead() {
    fetch(API_BASE + '/read-all', { method: 'POST' })
      .then(function () { return fetchNotifications(); })
      .catch(function () {});
  }

  // --- Toggle panel ---

  btn.addEventListener('click', function (e) {
    e.stopPropagation();
    var open = panel.style.display !== 'none';
    panel.style.display = open ? 'none' : 'block';
    if (!open) fetchNotifications();
  });

  document.addEventListener('click', function (e) {
    if (!e.target.closest('.tp-notif-wrap')) {
      panel.style.display = 'none';
    }
  });

  if (markAllBtn) {
    markAllBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      markAllRead();
    });
  }

  // --- Initial load + polling ---
  fetchNotifications();
  setInterval(fetchNotifications, POLL_INTERVAL);
})();
