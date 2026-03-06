(function () {
  'use strict';

  function copyToClipboard(text) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'absolute';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    try { document.execCommand('copy'); } finally { document.body.removeChild(textarea); }
    return Promise.resolve();
  }

  function showToast(message, type) {
    if (window.showToast) return window.showToast(message, type || 'info');
    console.log(message);
  }

  function setStatus(health) {
    const statusEl = document.querySelector('#integration-status .status-label');
    const dot = document.querySelector('#integration-status .status-dot');
    const last = document.getElementById('status-last-ingest');
    const traces = document.getElementById('status-traces');
    const rate = document.getElementById('status-rate');

    if (!health) return;
    const status = health.status || 'unknown';
    const lastTs = health.last_event_ts || '—';
    const requests24h = health.requests_24h || 0;
    const ratePerMin = requests24h ? Math.round(requests24h / 1440) : 0;

    if (dot) {
      dot.classList.remove('status-ok', 'status-warn', 'status-bad', 'status-unknown');
      if (status === 'healthy') dot.classList.add('status-ok');
      else if (status === 'degraded') dot.classList.add('status-warn');
      else if (status === 'down') dot.classList.add('status-bad');
      else dot.classList.add('status-unknown');
    }

    if (statusEl) {
      const label = status === 'healthy' ? 'Connected' : status === 'degraded' ? 'Issues detected' : status === 'down' ? 'No data' : 'Unknown';
      statusEl.textContent = label;
    }
    if (last) last.textContent = lastTs;
    if (traces) traces.textContent = requests24h.toLocaleString();
    if (rate) rate.textContent = ratePerMin ? `~${ratePerMin}/min` : '—';
  }

  function setPricing(data) {
    const version = document.getElementById('pricing-version');
    const models = document.getElementById('pricing-models');
    if (!data) return;
    if (version) version.textContent = data.version || '—';
    if (models) models.textContent = data.models ? data.models.length : '—';
  }

  function hydrateUrls() {
    const origin = window.location.origin;
    const ingestUrl = `${origin}/v1/telemetry/ingest`;
    const ingestEl = document.getElementById('ingest-url');
    if (ingestEl) ingestEl.textContent = ingestUrl;

    const curlEl = document.getElementById('curl-example');
    if (curlEl) {
      curlEl.textContent = `curl -X POST ${ingestUrl} \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "trace_id": "test-123",\n    "timestamp": "2026-02-27T12:00:00Z",\n    "provider": "anthropic",\n    "model": "claude-sonnet-4",\n    "final_input_tokens": 1000,\n    "output_tokens": 500,\n    "status": "success"\n  }'`;
    }

    const envEl = document.getElementById('env-example');
    if (envEl) {
      envEl.textContent = `TELEMETRY_ENDPOINT=${ingestUrl}\n\ntelemetry_endpoint: "${ingestUrl}"`;
    }

    const embedWeb = document.getElementById('embed-web');
    if (embedWeb) {
      embedWeb.textContent = `<script src="${origin}/dashboard/static/js/tokenpak-dashboard.js"></script>\n<tokenpak-dashboard data-source="${origin}/dashboard"></tokenpak-dashboard>`;
    }

    const embedIframe = document.getElementById('embed-iframe');
    if (embedIframe) {
      embedIframe.textContent = `<iframe src="${origin}/dashboard?embed=1" width="100%" height="720" frameborder="0"></iframe>`;
    }
  }

  function bindCopyButtons() {
    document.querySelectorAll('[data-copy-target]').forEach(btn => {
      btn.addEventListener('click', () => {
        const targetId = btn.getAttribute('data-copy-target');
        const target = document.getElementById(targetId);
        if (!target) return;
        copyToClipboard(target.textContent)
          .then(() => showToast('Copied to clipboard', 'success'))
          .catch(() => showToast('Copy failed', 'error'));
      });
    });
  }

  function bindToggles() {
    document.querySelectorAll('.toggle').forEach(btn => {
      btn.addEventListener('click', () => {
        const isOn = btn.classList.toggle('toggle-on');
        btn.textContent = isOn ? 'On' : 'Off';
        showToast('Capture setting updated', 'info');
      });
    });
  }

  function fetchHealth() {
    return fetch('/v1/health')
      .then(r => r.json())
      .then(setStatus)
      .catch(() => showToast('Health check failed', 'warning'));
  }

  function fetchPricing() {
    return fetch('/v1/pricing')
      .then(r => r.json())
      .then(setPricing)
      .catch(() => showToast('Pricing lookup failed', 'warning'));
  }

  function init() {
    hydrateUrls();
    bindCopyButtons();
    bindToggles();
    fetchHealth();
    fetchPricing();

    const diag = document.getElementById('run-diagnostics');
    if (diag) {
      diag.addEventListener('click', () => {
        fetchHealth().then(() => showToast('Diagnostics complete', 'success'));
      });
    }
  }

  window.addEventListener('DOMContentLoaded', init);
})();
