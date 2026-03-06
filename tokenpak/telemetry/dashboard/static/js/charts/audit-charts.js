/**
 * TokenPak Dashboard — Audit Charts
 * Trace Timeline: scatter plot of traces (time vs tokens, colored by provider)
 *
 * Depends on: chart.umd.min.js (Chart.js 3/4)
 * Note: uses linear scale for time (no adapter required).
 */

'use strict';

(function () {

  const PROVIDER_COLORS = {
    anthropic: '#4a90e2',
    openai:    '#f44336',
    google:    '#4caf50',
    gemini:    '#4caf50',
  };

  function providerColor(name) {
    if (!name) return '#9c27b0';
    const key = name.toLowerCase();
    for (const [k, v] of Object.entries(PROVIDER_COLORS)) {
      if (key.includes(k)) return v;
    }
    return '#9c27b0';
  }

  function fmtTs(ms) {
    const d = new Date(ms);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
      + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }

  function initTraceTimeline() {
    const canvas = document.getElementById('audit-trace-timeline');
    if (!canvas) return;

    // Parse trace data from embedded JSON script tag
    const dataEl = document.getElementById('audit-timeline-data');
    let traces = [];
    try {
      traces = JSON.parse(dataEl ? dataEl.textContent.trim() : '[]');
    } catch (e) {
      console.warn('audit-charts: could not parse trace data', e);
    }

    if (!traces || traces.length === 0) {
      const empty = document.getElementById('audit-timeline-empty');
      if (empty) { empty.style.display = 'flex'; canvas.style.display = 'none'; }
      return;
    }

    // Destroy previous chart if exists
    if (typeof Chart !== 'undefined' && Chart.getChart && Chart.getChart(canvas)) {
      Chart.getChart(canvas).destroy();
    }

    // Build scatter data
    const points = traces.map(t => ({
      x: (t.ts || 0) * 1000,  // ms
      y: t.input_billed || t.total_tokens_billed || 0,
      traceId: t.trace_id || '',
      provider: (t.provider || '').toLowerCase(),
      model: t.model || '—',
    })).filter(p => p.x > 0);

    if (!points.length) return;

    const minTs = Math.min(...points.map(p => p.x));
    const maxTs = Math.max(...points.map(p => p.x));
    const span  = maxTs - minTs || 1;

    new Chart(canvas, {
      type: 'scatter',
      data: {
        datasets: [{
          label: 'Traces',
          data: points,
          backgroundColor: points.map(p => providerColor(p.provider) + 'cc'),
          borderColor:     points.map(p => providerColor(p.provider)),
          borderWidth: 1,
          pointRadius: points.length > 200 ? 3 : 5,
          pointHoverRadius: 8,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label(ctx) {
                const p = ctx.raw;
                return [
                  `ID: ${p.traceId.slice(0,12)}…`,
                  `Provider: ${p.provider || '—'}`,
                  `Model: ${(p.model || '').slice(0, 30)}`,
                  `Tokens: ${p.y.toLocaleString()}`,
                  `Time: ${fmtTs(p.x)}`,
                ];
              },
            },
          },
        },
        scales: {
          x: {
            type: 'linear',
            min: minTs - span * 0.02,
            max: maxTs + span * 0.02,
            grid: { color: 'rgba(255,255,255,0.06)' },
            ticks: {
              color: '#aaa',
              maxTicksLimit: 7,
              callback: v => fmtTs(v),
            },
          },
          y: {
            grid: { color: 'rgba(255,255,255,0.06)' },
            ticks: {
              color: '#aaa',
              callback: v => v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v,
            },
            title: {
              display: true,
              text: 'Input Tokens',
              color: '#888',
              font: { size: 11 },
            },
          },
        },
        onClick(event, elements) {
          if (!elements.length) return;
          const idx = elements[0].index;
          const p = points[idx];
          if (!p.traceId) return;

          // Try HTMX drilldown into trace-detail-panel
          const detailPanel = document.getElementById('trace-detail-panel');
          if (detailPanel && typeof htmx !== 'undefined') {
            htmx.ajax('GET', `/dashboard/audit/trace/${p.traceId}`, {
              target: '#trace-detail-panel',
              swap: 'innerHTML',
            });
            detailPanel.scrollIntoView({ behavior: 'smooth' });
          } else {
            // Fall back: highlight trace row in table
            const row = document.querySelector(`tr[data-trace-id="${p.traceId}"]`);
            if (row) {
              row.scrollIntoView({ behavior: 'smooth', block: 'center' });
              row.classList.add('trace-row-highlight');
              setTimeout(() => row.classList.remove('trace-row-highlight'), 2000);
              row.click();
            } else {
              // Last resort: navigate to trace detail page
              window.location.href = `/dashboard/audit/trace/${p.traceId}`;
            }
          }
        },
      },
    });
  }

  // Auto-initialize when DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTraceTimeline);
  } else {
    setTimeout(initTraceTimeline, 0);
  }

  // Re-init on HTMX content swap
  document.addEventListener('htmx:afterSwap', function (e) {
    if (e.target && (e.target.id === 'main-content' || e.target.closest('#main-content'))) {
      setTimeout(initTraceTimeline, 50);
    }
  });

  window.TokenPakAuditCharts = { init: initTraceTimeline };

})();
