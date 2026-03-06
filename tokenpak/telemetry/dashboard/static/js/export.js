/**
 * TokenPak Dashboard — Export System
 * CSV and JSON export with proper metadata inclusion.
 */
'use strict';

(function () {

  // ─── Export functions ──────────────────────────────────────────────────────

  function buildExportURL(format) {
    const url = new URL(window.location.origin + `/dashboard/export/${format}`);
    const params = new URLSearchParams(window.location.search);
    
    // Copy relevant filter params
    ['days', 'provider', 'model', 'agent', 'status'].forEach(key => {
      if (params.has(key)) url.searchParams.set(key, params.get(key));
    });
    
    return url.toString();
  }

  function exportData(format) {
    const btn = document.querySelector('.export-btn');
    if (btn) {
      btn.disabled = true;
      btn.textContent = `Exporting ${format.toUpperCase()}...`;
    }

    const url = buildExportURL(format);
    
    // Trigger download
    const link = document.createElement('a');
    link.href = url;
    link.download = '';  // filename from Content-Disposition header
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    
    setTimeout(() => {
      document.body.removeChild(link);
      if (btn) {
        btn.disabled = false;
        btn.textContent = '📥 Export';
      }
      if (window.a11yAnnounce) window.a11yAnnounce(`${format.toUpperCase()} export started`);
    }, 500);
  }

  function exportTrace(traceId) {
    if (!traceId) return;
    const url = `/dashboard/export/trace/${traceId}`;
    
    const link = document.createElement('a');
    link.href = url;
    link.download = '';
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    
    setTimeout(() => document.body.removeChild(link), 500);
    if (window.a11yAnnounce) window.a11yAnnounce('Trace export started');
  }

  function toggleExportMenu() {
    const menu = document.getElementById('export-dropdown');
    if (!menu) return;
    menu.style.display = menu.style.display === 'block' ? 'none' : 'block';
  }

  // Close menu on outside click
  document.addEventListener('click', e => {
    const menu = document.getElementById('export-dropdown');
    const btn = document.querySelector('.export-btn');
    if (menu && btn && !btn.contains(e.target) && !menu.contains(e.target)) {
      menu.style.display = 'none';
    }
  });

  // ─── Public API ───────────────────────────────────────────────────────────

  window.TokenPakExport = {
    exportData,
    exportTrace,
    toggleExportMenu,
  };

})();
