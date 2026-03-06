/**
 * TokenPak Dashboard — Executive Summary
 * Auto-generated CFO-ready summary with paragraph/bullet toggle.
 */
'use strict';

(function () {

  let _currentFormat = localStorage.getItem('executive-summary-format') || 'paragraph';

  // ─── Fetch and render summary ─────────────────────────────────────────────

  async function loadSummary() {
    const container = document.getElementById('executive-summary-content');
    if (!container) return;

    const days = getDaysParam();
    const provider = getParam('provider', '');
    const model = getParam('model', '');

    try {
      const url = `/dashboard/executive-summary?days=${days}&provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}&format=${_currentFormat}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      container.innerHTML = formatSummary(data.summary, data.format);
      updateFormatToggle(data.format);
    } catch (e) {
      console.error('Failed to load executive summary:', e);
      container.innerHTML = '<p class="summary-error">Failed to load summary.</p>';
    }
  }

  function formatSummary(text, format) {
    if (format === 'bullets') {
      // Convert markdown-style bullets to HTML
      const lines = text.split('\n');
      let html = '';
      for (const line of lines) {
        if (line.startsWith('•')) {
          html += `<li>${escapeHtml(line.substring(1).trim())}</li>`;
        } else if (line.startsWith('📊')) {
          html += `<h3 class="summary-heading">${escapeHtml(line)}</h3><ul class="summary-bullets">`;
        } else if (line.trim() === '') {
          // skip empty
        } else {
          html += `<p>${escapeHtml(line)}</p>`;
        }
      }
      html += '</ul>';
      return html;
    } else {
      return `<p class="summary-paragraph">${escapeHtml(text)}</p>`;
    }
  }

  // ─── Format toggle ────────────────────────────────────────────────────────

  function toggleFormat() {
    _currentFormat = _currentFormat === 'paragraph' ? 'bullets' : 'paragraph';
    localStorage.setItem('executive-summary-format', _currentFormat);
    loadSummary();
  }
  window.toggleSummaryFormat = toggleFormat;

  function updateFormatToggle(format) {
    const btn = document.getElementById('summary-format-toggle');
    if (btn) {
      btn.textContent = format === 'paragraph' ? '🔹 Bullets' : '📄 Paragraph';
      btn.setAttribute('aria-pressed', 'false'); // not a binary toggle, shows next state
    }
  }

  // ─── Print summary ────────────────────────────────────────────────────────

  function printSummary() {
    const content = document.getElementById('executive-summary-content')?.innerHTML;
    if (!content) return;

    const win = window.open('', '', 'width=800,height=600');
    win.document.write(`
      <!DOCTYPE html>
      <html>
      <head>
        <title>Executive Summary</title>
        <style>
          body { font-family: Inter, system-ui, sans-serif; padding: 40px; max-width: 700px; margin: 0 auto; }
          h3 { color: #1e293b; margin-bottom: 16px; }
          p, li { color: #475569; line-height: 1.6; }
          ul { padding-left: 20px; }
          .summary-heading { font-size: 18px; }
        </style>
      </head>
      <body>${content}</body>
      </html>
    `);
    win.document.close();
    win.print();
  }
  window.printSummary = printSummary;

  // ─── Copy to clipboard ────────────────────────────────────────────────────

  async function copySummary() {
    const content = document.getElementById('executive-summary-content');
    if (!content) return;
    const text = content.textContent || '';
    try {
      await navigator.clipboard.writeText(text);
      if (window.a11yAnnounce) window.a11yAnnounce('Summary copied to clipboard');
      showCopyFeedback();
    } catch (e) {
      console.error('Copy failed:', e);
    }
  }
  window.copySummary = copySummary;

  function showCopyFeedback() {
    const btn = document.getElementById('copy-summary-btn');
    if (!btn) return;
    const orig = btn.textContent;
    btn.textContent = '✓ Copied';
    setTimeout(() => { btn.textContent = orig; }, 2000);
  }

  // ─── Collapse/expand panel ────────────────────────────────────────────────

  function togglePanel() {
    const panel = document.getElementById('executive-summary-panel');
    if (!panel) return;
    const isOpen = panel.classList.toggle('collapsed');
    const icon = panel.querySelector('.collapse-icon');
    if (icon) icon.textContent = isOpen ? '▶' : '▼';
    localStorage.setItem('executive-summary-collapsed', isOpen ? 'true' : 'false');
  }
  window.toggleSummaryPanel = togglePanel;

  // ─── Init ─────────────────────────────────────────────────────────────────

  function init() {
    loadSummary();

    // Restore collapsed state
    const panel = document.getElementById('executive-summary-panel');
    if (panel && localStorage.getItem('executive-summary-collapsed') === 'true') {
      panel.classList.add('collapsed');
      const icon = panel.querySelector('.collapse-icon');
      if (icon) icon.textContent = '▶';
    }

    // Reload on filter change
    document.addEventListener('filter-changed', loadSummary);
    document.addEventListener('htmx:afterSwap', () => {
      if (document.getElementById('executive-summary-content')) loadSummary();
    });
  }

  function getDaysParam() {
    return parseInt(new URL(window.location.href).searchParams.get('days') || '7', 10);
  }

  function getParam(key, def) {
    return new URL(window.location.href).searchParams.get(key) || def;
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  window.TokenPakSummary = { loadSummary, toggleFormat, printSummary, copySummary, togglePanel };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

})();
