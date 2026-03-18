/**
 * TokenPak Dashboard — Settings UI
 * Left-side navigation, section rendering, auto-save, saved views,
 * confirmation modals, toast notifications.
 */

'use strict';

(function () {
  const STORAGE_KEY = 'tp_settings';
  const AUTOSAVE_DELAY = 800;

  const SECTIONS = [
    { id: 'personal',     label: 'Personal',     icon: '👤' },
    { id: 'dashboard',    label: 'Dashboard',    icon: '📊' },
    { id: 'data',         label: 'Data',         icon: '🗄' },
    { id: 'pricing',      label: 'Pricing',      icon: '💰' },
    { id: 'alerts',       label: 'Alerts',       icon: '🔔' },
    { id: 'access',       label: 'Access',       icon: '🔑' },
    { id: 'integrations', label: 'Integrations', icon: '🔌' },
    { id: 'system',       label: 'System',       icon: '⚙️' },
  ];

  const DEFAULTS = {
    personal: {
      landingPage: 'finops',
      timeRange: '7d',
      aggregation: 'daily',
      theme: 'system',
      tableDensity: 'comfortable',
    },
    dashboard: {
      advancedMode: false,
      comparisonDefault: false,
      kpiShowSavingsPct: true,
      kpiShowTokensSaved: true,
      kpiShowCompressionRatio: false,
      kpiShowLatency: true,
    },
    data: {
      captureMode: 'off',
      debugSamplingRate: 5,
      retentionPeriod: '30d',
    },
    savedViews: [],
  };

  let state = JSON.parse(JSON.stringify(DEFAULTS));
  let currentSection = 'personal';
  let autosaveTimer = null;

  // ─── Storage ────────────────────────────────────────────────────────────

  function loadSettings() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) state = deepMerge(JSON.parse(JSON.stringify(DEFAULTS)), JSON.parse(raw));
    } catch (e) { console.warn('settings-ui: load error', e); }
  }

  function saveSettings() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      // Dispatch event so user-prefs.js can sync
      var evt = new CustomEvent('tp-settings-changed', { detail: { state: state } });
      document.body.dispatchEvent(evt);
      showToast('Settings saved');
    } catch (e) { console.warn('settings-ui: save error', e); }
  }

  function deepMerge(target, source) {
    for (const key of Object.keys(source)) {
      if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
        if (!target[key]) target[key] = {};
        deepMerge(target[key], source[key]);
      } else {
        target[key] = source[key];
      }
    }
    return target;
  }

  function scheduleAutosave() {
    clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(saveSettings, AUTOSAVE_DELAY);
  }

  // ─── Toast ──────────────────────────────────────────────────────────────

  function showToast(msg, type) {
    let toast = document.getElementById('tp-settings-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'tp-settings-toast';
      toast.setAttribute('role', 'status');
      toast.setAttribute('aria-live', 'polite');
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.className = 'tp-settings-toast' + (type ? ' tp-settings-toast--' + type : '');
    toast.style.opacity = '1';
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { toast.style.opacity = '0'; }, 3000);
  }

  // ─── Confirm Modal ──────────────────────────────────────────────────────

  function showConfirmModal(title, message, onConfirm) {
    let overlay = document.getElementById('tp-confirm-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'tp-confirm-overlay';
      overlay.innerHTML =
        '<div class="tp-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="tp-confirm-title">' +
        '<h3 id="tp-confirm-title" class="tp-confirm-title"></h3>' +
        '<p class="tp-confirm-message"></p>' +
        '<div class="tp-confirm-actions">' +
        '<button id="tp-confirm-cancel" class="tp-btn tp-btn--secondary">Cancel</button>' +
        '<button id="tp-confirm-ok" class="tp-btn tp-btn--danger">Confirm</button>' +
        '</div></div>';
      document.body.appendChild(overlay);
    }
    overlay.querySelector('.tp-confirm-title').textContent = title;
    overlay.querySelector('.tp-confirm-message').textContent = message;
    overlay.style.display = 'flex';
    overlay.querySelector('#tp-confirm-cancel').onclick = () => { overlay.style.display = 'none'; };
    const okBtn = overlay.querySelector('#tp-confirm-ok');
    okBtn.onclick = () => { overlay.style.display = 'none'; onConfirm(); };
    okBtn.focus();
  }

  // ─── Theme ──────────────────────────────────────────────────────────────

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme === 'system' ? '' : theme);
  }

  // ─── Navigation ─────────────────────────────────────────────────────────

  function navigateTo(sectionId) {
    currentSection = sectionId;
    history.pushState({}, '', '/dashboard/settings/' + sectionId);
    renderNav();
    renderContent();
  }

  function renderNav() {
    const nav = document.getElementById('tp-settings-nav');
    if (!nav) return;
    nav.innerHTML = SECTIONS.map(function(s) {
      return '<button class="tp-settings-nav-item' + (s.id === currentSection ? ' tp-settings-nav-item--active' : '') +
        '" data-section="' + s.id + '" aria-current="' + (s.id === currentSection ? 'page' : 'false') + '">' +
        '<span class="tp-settings-nav-icon" aria-hidden="true">' + s.icon + '</span>' +
        '<span class="tp-settings-nav-label">' + s.label + '</span></button>';
    }).join('');
    nav.querySelectorAll('.tp-settings-nav-item').forEach(function(btn) {
      btn.addEventListener('click', function() { navigateTo(btn.dataset.section); });
    });
  }

  // ─── Section Renderers ───────────────────────────────────────────────────

  function esc(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function sel(val, opt) { return val === opt ? ' selected' : ''; }
  function chk(v) { return v ? ' checked' : ''; }

  function renderPersonal() {
    var p = state.personal;
    return '<h2 class="tp-settings-section-title">Personal Preferences</h2>' +
      '<div class="tp-settings-group">' +
      '<label class="tp-settings-label">Default Landing Page' +
      '<select class="tp-settings-select" data-section="personal" data-key="landingPage">' +
      '<option value="finops"' + sel(p.landingPage,'finops') + '>FinOps</option>' +
      '<option value="engineering"' + sel(p.landingPage,'engineering') + '>Engineering</option>' +
      '<option value="audit"' + sel(p.landingPage,'audit') + '>Audit</option>' +
      '<option value="last"' + sel(p.landingPage,'last') + '>Last Visited</option>' +
      '</select></label>' +
      '<label class="tp-settings-label">Default Time Range' +
      '<select class="tp-settings-select" data-section="personal" data-key="timeRange">' +
      '<option value="24h"' + sel(p.timeRange,'24h') + '>24 hours</option>' +
      '<option value="7d"' + sel(p.timeRange,'7d') + '>7 days</option>' +
      '<option value="30d"' + sel(p.timeRange,'30d') + '>30 days</option>' +
      '<option value="custom"' + sel(p.timeRange,'custom') + '>Custom</option>' +
      '</select></label>' +
      '<fieldset class="tp-settings-fieldset"><legend class="tp-settings-legend">Default Aggregation</legend>' +
      ['hourly','daily','per-request'].map(function(v) {
        return '<label class="tp-settings-radio-label"><input type="radio" name="aggregation" value="' + v + '"' +
          (p.aggregation === v ? ' checked' : '') + ' data-section="personal" data-key="aggregation"> ' +
          v.charAt(0).toUpperCase() + v.slice(1) + '</label>';
      }).join('') +
      '</fieldset>' +
      '<label class="tp-settings-label">Theme' +
      '<select class="tp-settings-select" data-section="personal" data-key="theme">' +
      '<option value="system"' + sel(p.theme,'system') + '>System</option>' +
      '<option value="light"' + sel(p.theme,'light') + '>Light</option>' +
      '<option value="dark"' + sel(p.theme,'dark') + '>Dark</option>' +
      '<option value="high-contrast"' + sel(p.theme,'high-contrast') + '>High Contrast</option>' +
      '</select></label>' +
      '<label class="tp-settings-label">Table Density' +
      '<div class="tp-settings-toggle-group">' +
      '<button class="tp-density-btn' + (p.tableDensity === 'comfortable' ? ' active' : '') + '" data-density="comfortable">Comfortable</button>' +
      '<button class="tp-density-btn' + (p.tableDensity === 'compact' ? ' active' : '') + '" data-density="compact">Compact</button>' +
      '</div></label>' +
      '</div>' +
      '<div class="tp-settings-footer"><button class="tp-btn tp-btn--secondary" id="tp-reset-personal">Reset to Defaults</button></div>';
  }

  function renderDashboard() {
    var d = state.dashboard;
    var views = state.savedViews || [];
    return '<h2 class="tp-settings-section-title">Dashboard Preferences</h2>' +
      '<div class="tp-settings-group">' +
      '<div class="tp-settings-toggle-row"><span class="tp-settings-toggle-label">Advanced Mode</span>' +
      '<label class="tp-toggle"><input type="checkbox" data-section="dashboard" data-key="advancedMode"' + chk(d.advancedMode) + '><span class="tp-toggle-slider"></span></label></div>' +
      '<div class="tp-settings-toggle-row"><span class="tp-settings-toggle-label">Auto-enable Period Comparison</span>' +
      '<label class="tp-toggle"><input type="checkbox" data-section="dashboard" data-key="comparisonDefault"' + chk(d.comparisonDefault) + '><span class="tp-toggle-slider"></span></label></div>' +
      '<fieldset class="tp-settings-fieldset"><legend class="tp-settings-legend">KPI Display</legend>' +
      '<label class="tp-settings-checkbox-label"><input type="checkbox" data-section="dashboard" data-key="kpiShowSavingsPct"' + chk(d.kpiShowSavingsPct) + '> Show Savings %</label>' +
      '<label class="tp-settings-checkbox-label"><input type="checkbox" data-section="dashboard" data-key="kpiShowTokensSaved"' + chk(d.kpiShowTokensSaved) + '> Show Tokens Saved</label>' +
      '<label class="tp-settings-checkbox-label"><input type="checkbox" data-section="dashboard" data-key="kpiShowCompressionRatio"' + chk(d.kpiShowCompressionRatio) + '> Show Compression Ratio</label>' +
      '<label class="tp-settings-checkbox-label"><input type="checkbox" data-section="dashboard" data-key="kpiShowLatency"' + chk(d.kpiShowLatency) + '> Show Latency</label>' +
      '</fieldset></div>' +
      '<h3 class="tp-settings-subsection-title">Saved Views</h3>' +
      '<div class="tp-settings-saved-views" id="tp-saved-views-list">' +
      (views.length ? views.map(function(v, i) {
        return '<div class="tp-saved-view-row" data-view-index="' + i + '">' +
          '<button class="tp-view-star' + (v.default ? ' active' : '') + '" data-action="star" data-idx="' + i + '" aria-label="' + (v.default ? 'Default view' : 'Set as default') + '">★</button>' +
          '<span class="tp-view-name" id="tp-view-name-' + i + '">' + esc(v.name) + '</span>' +
          '<button class="tp-btn tp-btn--icon" data-action="edit" data-idx="' + i + '" aria-label="Rename view">✎</button>' +
          '<button class="tp-btn tp-btn--icon tp-btn--danger-icon" data-action="delete" data-idx="' + i + '" aria-label="Delete view">🗑</button>' +
          '</div>';
      }).join('') : '<p class="tp-muted">No saved views yet.</p>') +
      '</div>' +
      '<button class="tp-btn tp-btn--secondary" id="tp-add-view">+ Create New View</button>' +
      '<div class="tp-settings-footer"><button class="tp-btn tp-btn--secondary" id="tp-reset-dashboard">Reset to Defaults</button></div>';
  }

  function renderData() {
    var d = state.data;
    return '<h2 class="tp-settings-section-title">Data &amp; Capture Settings</h2>' +
      '<div class="tp-settings-warning" role="alert">⚠ Changes to capture mode and retention may affect storage usage and data availability.</div>' +
      '<div class="tp-settings-group">' +
      '<label class="tp-settings-label">Segment Capture Mode' +
      '<select class="tp-settings-select" data-section="data" data-key="captureMode">' +
      '<option value="off"' + sel(d.captureMode,'off') + '>Off (default)</option>' +
      '<option value="counts"' + sel(d.captureMode,'counts') + '>Token counts only</option>' +
      '<option value="full"' + sel(d.captureMode,'full') + '>Full segment detail</option>' +
      '<option value="payload"' + sel(d.captureMode,'payload') + '>Full payload (redacted)</option>' +
      '</select></label>' +
      '<label class="tp-settings-label">Debug Sampling Rate: <span id="tp-sampling-display">' + d.debugSamplingRate + '%</span>' +
      '<input type="range" class="tp-settings-slider" min="0" max="100" step="5" value="' + d.debugSamplingRate + '" data-section="data" data-key="debugSamplingRate" aria-label="Debug sampling rate">' +
      '</label>' +
      '<label class="tp-settings-label">Retention Period' +
      '<select class="tp-settings-select" data-section="data" data-key="retentionPeriod">' +
      '<option value="7d"' + sel(d.retentionPeriod,'7d') + '>7 days</option>' +
      '<option value="30d"' + sel(d.retentionPeriod,'30d') + '>30 days</option>' +
      '<option value="90d"' + sel(d.retentionPeriod,'90d') + '>90 days</option>' +
      '</select></label>' +
      '<div class="tp-settings-info-row">' +
      '<span class="tp-settings-label">Database Size</span>' +
      '<span id="tp-db-size-value" class="tp-muted">Loading…</span>' +
      '</div></div>' +
      '<div class="tp-settings-footer">' +
      '<button class="tp-btn tp-btn--secondary" id="tp-reset-data">Reset to Defaults</button>' +
      '<button class="tp-btn tp-btn--danger" id="tp-clear-data">Clear All Data</button>' +
      '</div>';
  }

  function renderSystem() {
    return '<h2 class="tp-settings-section-title">System</h2>' +
      '<div class="tp-settings-card">' +
      '<div class="tp-settings-section-label">Onboarding</div>' +
      '<div class="tp-settings-info-row">' +
      '<span class="tp-settings-label">First-visit tour</span>' +
      '<button class="tp-btn tp-btn--secondary" id="tp-show-onboarding">Show Tour</button>' +
      '</div>' +
      '<div class="tp-settings-info-row">' +
      '<span class="tp-settings-label">Reset onboarding</span>' +
      '<button class="tp-btn tp-btn--secondary" id="tp-reset-onboarding">Reset</button>' +
      '</div>' +
      '</div>';
  }

  function bindSystemEvents() {
    var showBtn = document.getElementById('tp-show-onboarding');
    if (showBtn) {
      showBtn.addEventListener('click', function() {
        if (window.TPOnboarding) window.TPOnboarding.start();
      });
    }
    var resetBtn = document.getElementById('tp-reset-onboarding');
    if (resetBtn) {
      resetBtn.addEventListener('click', function() {
        if (window.TPOnboarding) { window.TPOnboarding.reset(); showToast('Onboarding reset — will show on next visit', 'success'); }
      });
    }
  }

  function renderContent() {
    var panel = document.getElementById('tp-settings-panel');
    if (!panel) return;
    if (currentSection === 'personal') { panel.innerHTML = renderPersonal(); }
    else if (currentSection === 'dashboard') { panel.innerHTML = renderDashboard(); }
    else if (currentSection === 'data') { panel.innerHTML = renderData(); loadDbSize(); }
    else if (currentSection === 'system') { panel.innerHTML = renderSystem(); bindSystemEvents(); }
    else {
      var sec = SECTIONS.filter(function(s) { return s.id === currentSection; })[0];
      panel.innerHTML = '<h2 class="tp-settings-section-title">' + (sec ? esc(sec.label) : currentSection) + '</h2><p class="tp-muted">This section is coming soon.</p>';
    }
    bindSectionEvents();
  }

  // ─── Saved Views ─────────────────────────────────────────────────────────

  function bindSavedViewEvents() {
    var list = document.getElementById('tp-saved-views-list');
    if (list) {
      list.addEventListener('click', function(e) {
        var btn = e.target.closest('[data-action]');
        if (!btn) return;
        var idx = parseInt(btn.dataset.idx, 10);
        var action = btn.dataset.action;
        if (action === 'star') {
          state.savedViews.forEach(function(v, i) { v.default = (i === idx); });
          scheduleAutosave(); renderContent();
        } else if (action === 'edit') {
          var nameEl = document.getElementById('tp-view-name-' + idx);
          if (!nameEl) return;
          var current = state.savedViews[idx] ? state.savedViews[idx].name : '';
          var input = document.createElement('input');
          input.type = 'text'; input.value = current; input.className = 'tp-view-name-input';
          nameEl.replaceWith(input); input.focus();
          input.addEventListener('blur', function() {
            var newName = input.value.trim() || current;
            if (state.savedViews[idx]) state.savedViews[idx].name = newName;
            scheduleAutosave(); renderContent();
          });
          input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') input.blur();
            if (e.key === 'Escape') { input.value = current; input.blur(); }
          });
        } else if (action === 'delete') {
          var name = state.savedViews[idx] ? state.savedViews[idx].name : 'this view';
          showConfirmModal('Delete View', 'Delete "' + name + '"? This cannot be undone.', function() {
            state.savedViews.splice(idx, 1);
            scheduleAutosave(); renderContent();
          });
        }
      });
    }
    var addBtn = document.getElementById('tp-add-view');
    if (addBtn) {
      addBtn.addEventListener('click', function() {
        var name = prompt('View name:');
        if (name && name.trim()) {
          state.savedViews.push({ name: name.trim(), default: false });
          scheduleAutosave(); renderContent();
        }
      });
    }
  }

  // ─── DB Size ─────────────────────────────────────────────────────────────

  function loadDbSize() {
    var el = document.getElementById('tp-db-size-value');
    if (!el) return;
    fetch('/dashboard/settings/system/db-size')
      .then(function(r) { return r.json(); })
      .then(function(d) { el.textContent = d.size_human || (d.size_bytes + ' bytes'); })
      .catch(function() { el.textContent = 'Unavailable'; });
  }

  // ─── Bind Events ─────────────────────────────────────────────────────────

  function bindSectionEvents() {
    document.querySelectorAll('.tp-settings-select').forEach(function(el) {
      el.addEventListener('change', function() {
        var sec = el.dataset.section, key = el.dataset.key;
        if (!state[sec]) state[sec] = {};
        state[sec][key] = el.value;
        if (key === 'theme') applyTheme(el.value);
        scheduleAutosave();
      });
    });
    document.querySelectorAll('input[type="radio"][data-section]').forEach(function(el) {
      el.addEventListener('change', function() {
        if (!el.checked) return;
        var sec = el.dataset.section, key = el.dataset.key;
        if (!state[sec]) state[sec] = {};
        state[sec][key] = el.value;
        scheduleAutosave();
      });
    });
    document.querySelectorAll('input[type="checkbox"][data-section]').forEach(function(el) {
      el.addEventListener('change', function() {
        var sec = el.dataset.section, key = el.dataset.key;
        if (!state[sec]) state[sec] = {};
        state[sec][key] = el.checked;
        scheduleAutosave();
      });
    });
    document.querySelectorAll('input[type="range"][data-section]').forEach(function(el) {
      el.addEventListener('input', function() {
        var sec = el.dataset.section, key = el.dataset.key;
        if (!state[sec]) state[sec] = {};
        state[sec][key] = parseInt(el.value, 10);
        var disp = document.getElementById('tp-sampling-display');
        if (disp) disp.textContent = el.value + '%';
        scheduleAutosave();
      });
    });
    document.querySelectorAll('.tp-density-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
        document.querySelectorAll('.tp-density-btn').forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
        state.personal.tableDensity = btn.dataset.density;
        scheduleAutosave();
      });
    });
    // Reset buttons
    ['personal','dashboard','data'].forEach(function(sec) {
      var btn = document.getElementById('tp-reset-' + sec);
      if (!btn) return;
      btn.addEventListener('click', function() {
        showConfirmModal('Reset ' + sec.charAt(0).toUpperCase() + sec.slice(1) + ' Settings',
          'Reset all ' + sec + ' settings to defaults?', function() {
          state[sec] = JSON.parse(JSON.stringify(DEFAULTS[sec]));
          if (sec === 'personal') applyTheme(state.personal.theme);
          saveSettings(); renderContent();
        });
      });
    });
    // Clear data
    var clearBtn = document.getElementById('tp-clear-data');
    if (clearBtn) {
      clearBtn.addEventListener('click', function() {
        showConfirmModal('Clear All Data',
          'This will permanently delete all telemetry data. This cannot be undone.',
          function() {
            fetch('/dashboard/settings/system/clear-data', { method: 'POST' })
              .then(function(r) { return r.json(); })
              .then(function() { showToast('All data cleared', 'warning'); })
              .catch(function() { showToast('Failed to clear data', 'error'); });
          });
      });
    }
    bindSavedViewEvents();
  }

  // ─── Layout ───────────────────────────────────────────────────────────────

  function buildLayout(container) {
    container.innerHTML =
      '<div class="tp-settings-layout">' +
      '<nav class="tp-settings-sidebar" aria-label="Settings sections">' +
      '<div id="tp-settings-nav" class="tp-settings-nav"></div>' +
      '</nav>' +
      '<main class="tp-settings-content" id="tp-settings-panel" role="main"></main>' +
      '</div>';
  }

  // ─── Init ────────────────────────────────────────────────────────────────

  function init() {
    var app = document.getElementById('tp-settings-app');
    if (!app) return;
    loadSettings();
    applyTheme(state.personal.theme);
    var match = location.pathname.match(/\/dashboard\/settings\/([\w-]+)/);
    if (match) currentSection = match[1];
    buildLayout(app);
    renderNav();
    renderContent();
    window.addEventListener('popstate', function() {
      var m = location.pathname.match(/\/dashboard\/settings\/([\w-]+)/);
      currentSection = m ? m[1] : 'personal';
      renderNav(); renderContent();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
