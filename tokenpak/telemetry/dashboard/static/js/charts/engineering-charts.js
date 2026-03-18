/**
 * TokenPak Dashboard — Engineering Charts
 * 3 charts: Token Composition, Latency Breakdown (avg/p95/p99), Error & Retry Trends
 *
 * Depends on: chart-factory.js (TokenPakCharts global)
 */

'use strict';

(function () {

  const C = window.TokenPakCharts;

  // Segment type → consistent color mapping
  const SEGMENT_COLORS = {
    system:           '#6366f1',
    user:             '#3b82f6',
    assistant:        '#10b981',
    tool_schema:      '#f59e0b',
    tool_response:    '#f97316',
    retrieved_context:'#8b5cf6',
    memory:           '#06b6d4',
    other:            '#64748b',
  };

  function segmentColor(type) {
    return SEGMENT_COLORS[type] || '#64748b';
  }

  function applyFilter(key, value) {
    const url = new URL(window.location.href);
    url.searchParams.set(key, value);
    window.history.pushState({}, '', url.toString());
    document.body.dispatchEvent(new CustomEvent('filter-changed', { bubbles: true }));
  }

  // ─── 1. Token Composition (Stacked Area / Bar toggle) ──────────────────────

  let _tokenChart = null;
  let _tokenMode = 'area';   // 'area' | 'bar'
  let _tokenComposition = [];

  function initTokenCompositionChart(tokenTs, composition) {
    const id = 'eng-token-composition';
    const theme = C.getActiveTheme();
    const canvas = document.getElementById(id);
    if (!canvas) return;

    _tokenComposition = composition || [];

    // Build total-tokens-per-day dataset + per-segment breakdown from composition
    const labels = tokenTs.map(d => d.bucket ? d.bucket.slice(0, 10) : '');
    const totalValues = tokenTs.map(d => +(d.value || 0));

    if (!totalValues.some(v => v > 0) && !composition.length) {
      C.showChartEmpty(id, 'No token data for this period');
      return;
    }
    C.hideChartEmpty(id);

    // If we have segment composition data, distribute tokens proportionally per day
    const totalComp = composition.reduce((s, r) => s + r.tokens, 0);
    const segments = Object.keys(SEGMENT_COLORS);
    const activeSegments = composition.length
      ? composition.map(r => r.segment_type)
      : ['user', 'assistant', 'system', 'other'];

    const datasets = activeSegments.map(seg => {
      const segData = composition.find(r => r.segment_type === seg);
      const proportion = segData && totalComp > 0 ? segData.tokens / totalComp : 1 / activeSegments.length;
      const color = segmentColor(seg);
      return {
        label: seg.replace(/_/g, ' '),
        data: totalValues.map(v => Math.round(v * proportion)),
        borderColor: color,
        backgroundColor: color + 'cc',
        borderWidth: 1,
        fill: _tokenMode === 'area' ? 'origin' : false,
        tension: 0.4,
        pointRadius: 0,
        hoverRadius: 4,
        formatFn: C.formatTokens,
        _segType: seg,
      };
    });

    function buildConfig(mode) {
      const type = mode === 'bar' ? 'bar' : 'line';
      return C.createChartConfig(type, { labels, datasets }, {
        scales: {
          x: { stacked: true },
          y: {
            stacked: true,
            ticks: { callback: v => C.formatTokens(v) }
          }
        },
        onClick(evt, elements) {
          if (!elements.length) return;
          // Click on a segment layer → could filter, but we just dispatch an event for now
          const dsIndex = elements[0].datasetIndex;
          const seg = datasets[dsIndex]?._segType;
          if (seg) {
            document.body.dispatchEvent(new CustomEvent('token-segment-click', { detail: { segment: seg }, bubbles: true }));
          }
        }
      });
    }

    if (_tokenChart) { _tokenChart.destroy(); _tokenChart = null; }
    _tokenChart = new Chart(canvas, buildConfig(_tokenMode));
    C.renderChartLegend(_tokenChart, 'legend-' + id);

    // Bind toggle buttons
    document.querySelectorAll('[data-token-mode]').forEach(btn => {
      btn.addEventListener('click', function () {
        _tokenMode = this.dataset.tokenMode;
        document.querySelectorAll('[data-token-mode]').forEach(b => b.classList.toggle('active', b === this));
        if (_tokenChart) {
          _tokenChart.destroy();
          _tokenChart = new Chart(canvas, buildConfig(_tokenMode));
          C.renderChartLegend(_tokenChart, 'legend-' + id);
        }
      });
    });

    return _tokenChart;
  }

  // ─── 2. Latency Breakdown (avg / p95 / p99) ────────────────────────────────

  function initLatencyChart(latencyData) {
    const id = 'eng-latency';
    const theme = C.getActiveTheme();
    const canvas = document.getElementById(id);
    if (!canvas) return;

    const labels = latencyData.map(d => d.bucket ? d.bucket.slice(0, 10) : '');
    const avgValues = latencyData.map(d => +(d.avg_ms || 0));
    const p95Values = latencyData.map(d => +(d.p95_ms || 0));
    const p99Values = latencyData.map(d => +(d.p99_ms || 0));

    if (!avgValues.some(v => v > 0)) {
      C.showChartEmpty(id, 'No latency data — duration_ms may not be recorded yet');
      return;
    }
    C.hideChartEmpty(id);

    const config = C.createChartConfig('line', {
      labels,
      datasets: [
        {
          label: 'p99',
          data: p99Values,
          borderColor: theme.danger + '66',
          backgroundColor: 'transparent',
          borderWidth: 1,
          borderDash: [2, 4],
          pointRadius: 0,
          hoverRadius: 3,
          tension: 0.4,
          formatFn: C.formatLatency,
        },
        {
          label: 'p95',
          data: p95Values,
          borderColor: theme.warning,
          backgroundColor: theme.warning + '18',
          borderWidth: 1.5,
          borderDash: [4, 4],
          fill: '-1',   // fill between p95 and p99
          pointRadius: 0,
          hoverRadius: 3,
          tension: 0.4,
          formatFn: C.formatLatency,
        },
        {
          label: 'Avg',
          data: avgValues,
          borderColor: theme.info,
          backgroundColor: theme.info + '15',
          borderWidth: 2,
          fill: true,
          pointRadius: 0,
          hoverRadius: 4,
          tension: 0.4,
          formatFn: C.formatLatency,
        }
      ]
    }, {
      scales: {
        y: {
          ticks: { callback: v => C.formatLatency(v) }
        }
      },
      onClick(evt, elements) {
        if (!elements.length) return;
        const idx = elements[0].index;
        const dayData = latencyData[idx];
        if (dayData && dayData.bucket) {
          // Filter to high-latency traces on this date
          applyFilter('date', dayData.bucket);
          applyFilter('latency_min', Math.round(dayData.p95_ms || dayData.avg_ms || 0));
        }
      }
    });

    // Custom tooltip with all percentiles + request count
    const chart = new Chart(canvas, config);
    canvas._latencyData = latencyData;
    chart.options.plugins.tooltip.external = function (ctx) {
      const { chart, tooltip } = ctx;
      let el = chart.canvas.parentNode.querySelector('.chart-tooltip');
      if (!el) {
        el = document.createElement('div');
        el.className = 'chart-tooltip';
        chart.canvas.parentNode.appendChild(el);
      }
      if (tooltip.opacity === 0) { el.style.opacity = '0'; return; }

      const idx = tooltip.dataPoints?.[0]?.dataIndex;
      if (idx == null) return;
      const d = (chart.canvas._latencyData || [])[idx] || {};

      el.innerHTML = `
        <div class="tooltip-header">${C.escapeHtml(d.bucket || '')}</div>
        <div class="tooltip-row"><span class="tooltip-label">Avg:</span><span class="tooltip-value">${C.formatLatency(d.avg_ms)}</span></div>
        <div class="tooltip-row"><span class="tooltip-label">p95: <span class="tooltip-hint" title="95% of requests faster than this">ⓘ</span></span><span class="tooltip-value">${C.formatLatency(d.p95_ms)}</span></div>
        <div class="tooltip-row"><span class="tooltip-label">p99: <span class="tooltip-hint" title="99% of requests faster than this">ⓘ</span></span><span class="tooltip-value">${C.formatLatency(d.p99_ms)}</span></div>
        <div class="tooltip-row"><span class="tooltip-label">Requests:</span><span class="tooltip-value">${C.formatNumber(d.requests)}</span></div>
        <div class="tooltip-action" style="margin-top:8px;color:var(--color-primary);font-size:11px;">Click spike to filter outliers →</div>
      `;
      el.style.opacity = '1';
      el.style.pointerEvents = 'auto';
      el.style.left = (chart.canvas.offsetLeft + tooltip.caretX + 12) + 'px';
      el.style.top = (chart.canvas.offsetTop + tooltip.caretY - 20) + 'px';
    };
    chart.update();

    C.renderChartLegend(chart, 'legend-' + id);
    return chart;
  }

  // ─── 3. Error & Retry Trends ────────────────────────────────────────────────

  function initErrorRetryChart(errorData) {
    const id = 'eng-errors';
    const theme = C.getActiveTheme();
    const canvas = document.getElementById(id);
    if (!canvas) return;

    const labels = errorData.map(d => d.bucket ? d.bucket.slice(0, 10) : '');
    const errorRates = errorData.map(d => +(d.error_rate || 0));
    const retryRates = errorData.map(d => +(d.retry_rate || 0));
    const totals = errorData.map(d => +(d.total || 0));

    if (!errorRates.some(v => v > 0) && !retryRates.some(v => v > 0)) {
      C.showChartEmpty(id, 'No errors recorded — system running clean');
      return;
    }
    C.hideChartEmpty(id);

    // 5% threshold line data
    const thresholdData = labels.map(() => 5);

    const config = C.createChartConfig('line', {
      labels,
      datasets: [
        {
          label: 'Error Rate',
          data: errorRates,
          borderColor: theme.danger,
          backgroundColor: theme.danger + '18',
          borderWidth: 2,
          fill: true,
          tension: 0.4,
          pointRadius: errorRates.map(v => v > 5 ? 4 : 0),
          pointBackgroundColor: theme.danger,
          hoverRadius: 4,
          formatFn: v => C.formatPercent(v),
        },
        {
          label: 'Retry Rate',
          data: retryRates,
          borderColor: theme.warning,
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          borderDash: [4, 4],
          fill: false,
          tension: 0.4,
          pointRadius: 0,
          hoverRadius: 4,
          formatFn: v => C.formatPercent(v),
        },
        {
          label: '5% Threshold',
          data: thresholdData,
          borderColor: theme.neutral + '88',
          backgroundColor: 'transparent',
          borderWidth: 1,
          borderDash: [2, 6],
          fill: false,
          pointRadius: 0,
          hoverRadius: 0,
          formatFn: v => C.formatPercent(v),
        }
      ]
    }, {
      scales: {
        y: {
          min: 0,
          ticks: { callback: v => C.formatPercent(v) }
        }
      },
      onClick(evt, elements) {
        if (!elements.length) return;
        const dsIndex = elements[0].datasetIndex;
        const idx = elements[0].index;
        const date = labels[idx];
        if (!date) return;
        if (dsIndex === 0) {
          // Error rate click → filter to errors
          applyFilter('date', date);
          applyFilter('status', 'error');
        } else if (dsIndex === 1) {
          applyFilter('date', date);
        }
      }
    });

    const chart = new Chart(canvas, config);

    // Custom tooltip
    canvas._errorData = errorData;
    chart.options.plugins.tooltip.external = function (ctx) {
      const { chart, tooltip } = ctx;
      let el = chart.canvas.parentNode.querySelector('.chart-tooltip');
      if (!el) {
        el = document.createElement('div');
        el.className = 'chart-tooltip';
        chart.canvas.parentNode.appendChild(el);
      }
      if (tooltip.opacity === 0) { el.style.opacity = '0'; return; }

      const idx = tooltip.dataPoints?.[0]?.dataIndex;
      if (idx == null) return;
      const d = (chart.canvas._errorData || [])[idx] || {};
      const errCount = Math.round((d.error_rate || 0) / 100 * (d.total || 0));
      const retryCount = Math.round((d.retry_rate || 0) / 100 * (d.total || 0));

      el.innerHTML = `
        <div class="tooltip-header">${C.escapeHtml(d.bucket || '')}</div>
        <div class="tooltip-row"><span class="tooltip-label">Error rate:</span><span class="tooltip-value" style="color:var(--color-danger)">${C.formatPercent(d.error_rate)}</span></div>
        <div class="tooltip-row"><span class="tooltip-label">Errors:</span><span class="tooltip-value">${C.formatNumber(errCount)}</span></div>
        <div class="tooltip-row"><span class="tooltip-label">Retry rate:</span><span class="tooltip-value" style="color:var(--color-warning)">${C.formatPercent(d.retry_rate)}</span></div>
        <div class="tooltip-row"><span class="tooltip-label">Retries:</span><span class="tooltip-value">${C.formatNumber(retryCount)}</span></div>
        <div class="tooltip-row"><span class="tooltip-label">Total reqs:</span><span class="tooltip-value">${C.formatNumber(d.total)}</span></div>
      `;
      el.style.opacity = '1';
      el.style.pointerEvents = 'auto';
      el.style.left = (chart.canvas.offsetLeft + tooltip.caretX + 12) + 'px';
      el.style.top = (chart.canvas.offsetTop + tooltip.caretY - 20) + 'px';
    };
    chart.update();

    C.renderChartLegend(chart, 'legend-' + id);
    return chart;
  }

  // ─── Public API ────────────────────────────────────────────────────────────

  window.TokenPakEngineering = {
    init(tokenTs, latencyTs, errorTs, tokenComposition) {
      if (!window.TokenPakCharts) {
        console.error('TokenPakCharts not loaded');
        return;
      }
      initTokenCompositionChart(tokenTs, tokenComposition || []);
      initLatencyChart(latencyTs || []);
      initErrorRetryChart(errorTs || []);
    }
  };

})();
