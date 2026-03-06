/**
 * TokenPak Dashboard — Alert Configuration UI
 * Form validation, save, test alert, field toggling.
 */

'use strict';

(function () {

  // ─── Helpers ──────────────────────────────────────────────────────────────

  function showBanner(id, msg, type) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = msg;
    el.style.display = 'flex';
    if (type) el.className = `alert-banner alert-banner-${type}`;
    setTimeout(() => { el.style.display = 'none'; }, 5000);
  }

  function showError(msg) { showBanner('alert-error-banner', msg, 'error'); }
  function showSuccess(msg) { showBanner('alert-success-banner', msg, 'success'); }

  function showFieldError(inputEl, msg) {
    inputEl.classList.add('field-error');
    let hint = inputEl.parentNode.parentNode.querySelector('.field-hint');
    if (!hint) hint = inputEl.parentNode.querySelector('.field-hint');
    if (hint) { hint.textContent = msg; hint.classList.add('field-hint-error'); }
  }

  function clearFieldErrors() {
    document.querySelectorAll('.field-error').forEach(el => el.classList.remove('field-error'));
    document.querySelectorAll('.field-hint-error').forEach(el => el.classList.remove('field-hint-error'));
  }

  // ─── Field toggling ────────────────────────────────────────────────────────

  window.toggleEmailFields = function (enabled) {
    const fields = document.getElementById('email-fields');
    if (fields) fields.style.display = enabled ? 'block' : 'none';
  };

  window.toggleQuietHours = function (enabled) {
    const fields = document.getElementById('quiet-hours-fields');
    if (fields) fields.style.display = enabled ? 'block' : 'none';
  };

  window.updateCostSpikeFields = function () {
    const type = document.getElementById('cost-spike-type')?.value;
    const pctRow = document.getElementById('cost-spike-pct-row');
    const absRow = document.getElementById('cost-spike-abs-row');
    if (pctRow) pctRow.style.display = type === 'abs' ? 'none' : 'flex';
    if (absRow) absRow.style.display = type === 'abs' ? 'flex' : 'none';
  };

  // ─── Form serialization ────────────────────────────────────────────────────

  function formToConfig() {
    const form = document.getElementById('alert-config-form');
    if (!form) return {};

    function val(name) {
      const el = form.querySelector(`[name="${name}"]`);
      return el ? el.value : null;
    }
    function checked(name) {
      const el = form.querySelector(`[name="${name}"]`);
      return el ? el.checked : false;
    }
    function num(name) {
      const v = val(name);
      return v !== null && v !== '' ? parseFloat(v) : null;
    }

    const csType = val('cost_spike.threshold_type') || 'pct';

    return {
      cost_spike: {
        enabled: checked('cost_spike.enabled'),
        threshold_pct: num('cost_spike.threshold_pct'),
        threshold_abs: csType === 'abs' ? num('cost_spike.threshold_abs') : null,
        threshold_type: csType,
        severity: val('cost_spike.severity') || 'warning',
        basis_days: 7,
      },
      savings_drop: {
        enabled: checked('savings_drop.enabled'),
        threshold_pct: num('savings_drop.threshold_pct'),
        severity: val('savings_drop.severity') || 'warning',
      },
      retry_spike: {
        enabled: checked('retry_spike.enabled'),
        threshold_pct: num('retry_spike.threshold_pct'),
        severity: val('retry_spike.severity') || 'critical',
        window: 'hourly',
      },
      latency: {
        enabled: checked('latency.enabled'),
        metric: val('latency.metric') || 'p95',
        threshold_ms: num('latency.threshold_ms'),
        severity: val('latency.severity') || 'warning',
      },
      error_rate: {
        enabled: checked('error_rate.enabled'),
        threshold_pct: num('error_rate.threshold_pct'),
        severity: val('error_rate.severity') || 'warning',
      },
      channels: {
        in_app: true,
        email: {
          enabled: checked('channels.email.enabled'),
          address: val('channels.email.address') || '',
          min_severity: val('channels.email.min_severity') || 'warning',
        },
        webhook: { enabled: false, url: '' },
      },
      quiet_hours: {
        enabled: checked('quiet_hours.enabled'),
        start: val('quiet_hours.start') || '22:00',
        end: val('quiet_hours.end') || '08:00',
      },
    };
  }

  // ─── Validation ────────────────────────────────────────────────────────────

  function validateForm(config) {
    const errors = [];

    function numRange(val, name, min, max) {
      if (val === null || isNaN(val)) { errors.push(`${name}: required number`); return; }
      if (val < min || val > max) errors.push(`${name}: must be ${min}–${max}`);
    }

    numRange(config.cost_spike.threshold_pct, 'Cost spike %', 0, 500);
    if (config.cost_spike.threshold_type === 'abs' && config.cost_spike.threshold_abs !== null) {
      numRange(config.cost_spike.threshold_abs, 'Cost spike $', 0, 10000);
    }
    numRange(config.savings_drop.threshold_pct, 'Savings drop %', 0, 100);
    numRange(config.retry_spike.threshold_pct, 'Retry spike %', 0, 100);
    numRange(config.latency.threshold_ms, 'Latency threshold ms', 0, 60000);
    numRange(config.error_rate.threshold_pct, 'Error rate %', 0, 100);

    if (config.channels.email.enabled) {
      const addr = config.channels.email.address;
      if (!addr || !addr.includes('@') || !addr.split('@')[1]?.includes('.')) {
        errors.push('Email address is invalid');
      }
    }

    return errors;
  }

  // ─── Populate form from config ─────────────────────────────────────────────

  function populateForm(config) {
    const form = document.getElementById('alert-config-form');
    if (!form) return;

    function setVal(name, value) {
      const el = form.querySelector(`[name="${name}"]`);
      if (el) el.value = value ?? '';
    }
    function setChecked(name, value) {
      const el = form.querySelector(`[name="${name}"]`);
      if (el) el.checked = !!value;
    }

    const cs = config.cost_spike || {};
    setChecked('cost_spike.enabled', cs.enabled !== false);
    setVal('cost_spike.threshold_pct', cs.threshold_pct ?? 50);
    setVal('cost_spike.threshold_abs', cs.threshold_abs ?? '');
    setVal('cost_spike.threshold_type', cs.threshold_type || 'pct');
    setVal('cost_spike.severity', cs.severity || 'warning');
    updateCostSpikeFields();

    const sd = config.savings_drop || {};
    setChecked('savings_drop.enabled', sd.enabled !== false);
    setVal('savings_drop.threshold_pct', sd.threshold_pct ?? 30);
    setVal('savings_drop.severity', sd.severity || 'warning');

    const rs = config.retry_spike || {};
    setChecked('retry_spike.enabled', rs.enabled !== false);
    setVal('retry_spike.threshold_pct', rs.threshold_pct ?? 20);
    setVal('retry_spike.severity', rs.severity || 'critical');

    const lt = config.latency || {};
    setChecked('latency.enabled', lt.enabled !== false);
    setVal('latency.metric', lt.metric || 'p95');
    setVal('latency.threshold_ms', lt.threshold_ms ?? 2000);
    setVal('latency.severity', lt.severity || 'warning');

    const er = config.error_rate || {};
    setChecked('error_rate.enabled', er.enabled !== false);
    setVal('error_rate.threshold_pct', er.threshold_pct ?? 10);
    setVal('error_rate.severity', er.severity || 'warning');

    const email = config.channels?.email || {};
    setChecked('channels.email.enabled', !!email.enabled);
    setVal('channels.email.address', email.address || '');
    setVal('channels.email.min_severity', email.min_severity || 'warning');
    toggleEmailFields(!!email.enabled);

    const qh = config.quiet_hours || {};
    setChecked('quiet_hours.enabled', !!qh.enabled);
    setVal('quiet_hours.start', qh.start || '22:00');
    setVal('quiet_hours.end', qh.end || '08:00');
    toggleQuietHours(!!qh.enabled);
  }

  // ─── Load from API ─────────────────────────────────────────────────────────

  async function loadAndPopulate() {
    try {
      const res = await fetch('/v1/settings/alerts');
      if (res.ok) {
        const data = await res.json();
        if (data.config) populateForm(data.config);
      }
    } catch (e) {
      // Server-rendered config is used as fallback
    }
  }

  // ─── Save ──────────────────────────────────────────────────────────────────

  async function saveSettings(e) {
    e.preventDefault();
    clearFieldErrors();

    const config = formToConfig();
    const errors = validateForm(config);

    if (errors.length) {
      showError('❌ ' + errors.join(' · '));
      return;
    }

    const btn = document.getElementById('save-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

    try {
      const res = await fetch('/v1/settings/alerts', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });

      if (res.ok) {
        showSuccess('✓ Settings saved successfully');
        if (window.a11yAnnounce) window.a11yAnnounce('Alert settings saved');
      } else {
        const err = await res.json().catch(() => ({}));
        showError('❌ Save failed: ' + (err.detail || `HTTP ${res.status}`));
      }
    } catch (e) {
      showError('❌ Network error: ' + e.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Save Settings'; }
    }
  }

  // ─── Test Alert ────────────────────────────────────────────────────────────

  window.sendTestAlert = async function () {
    const btn = document.getElementById('test-alert-btn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Sending…'; }
    try {
      const res = await fetch('/v1/settings/alerts/test', { method: 'POST' });
      if (res.ok) {
        showSuccess('✓ Test alert sent successfully');
        if (window.a11yAnnounce) window.a11yAnnounce('Test alert sent');
      } else {
        showError('❌ Test alert failed');
      }
    } catch (e) {
      showError('❌ Network error: ' + e.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🔔 Send Test Alert'; }
    }
  };

  // ─── Init ──────────────────────────────────────────────────────────────────

  function init() {
    const form = document.getElementById('alert-config-form');
    if (form) form.addEventListener('submit', saveSettings);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  window.AlertSettings = { loadAndPopulate, populateForm, formToConfig };

})();
