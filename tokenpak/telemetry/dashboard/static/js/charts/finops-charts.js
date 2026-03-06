/**
 * TokenPak Dashboard — FinOps Charts
 * 4 core charts: Baseline vs Actual, Cost by Provider, Cost by Model, Savings Over Time
 *
 * Depends on: chart-factory.js (TokenPakCharts global)
 */

'use strict';

(function () {

  // ─── Helpers ──────────────────────────────────────────────────────────────

  const C = window.TokenPakCharts;

  function applyFilter(key, value) {
    const url = new URL(window.location.href);
    url.searchParams.set(key, value);
    window.history.pushState({}, '', url.toString());
    // Trigger HTMX filter-changed event to refresh dashboard
    document.body.dispatchEvent(new CustomEvent('filter-changed', { bubbles: true }));
    // Update filter chip UI if present
    document.body.dispatchEvent(new CustomEvent('filter-applied', {
      detail: { key, value },
      bubbles: true
    }));
  }

  function shortLabel(str, max = 22) {
    if (!str) return '—';
    return str.length > max ? str.slice(0, max - 1) + '…' : str;
  }

  function providerColor(name, theme) {
    const map = {
      anthropic: theme.info,
      openai: theme.positive,
      google: theme.warning,
      cohere: theme.primary,
      mistral: '#ec4899',
    };
    const key = (name || '').toLowerCase();
    return map[key] || theme.palette[Object.keys(map).length % theme.palette.length];
  }

  // ─── 1. Baseline vs Actual Cost ────────────────────────────────────────────

  function initBaselineActualChart(costData, savingsData) {
    const id = 'finops-baseline-actual';
    const theme = C.getActiveTheme();

    const labels = costData.map(d => d.bucket ? d.bucket.slice(0, 10) : '');
    const actualValues = costData.map(d => +(d.value || 0));
    const savingsValues = savingsData.map(d => +(d.value || 0));
    // Baseline = actual + savings
    const baselineValues = actualValues.map((v, i) => v + (savingsValues[i] || 0));

    const canvas = document.getElementById(id);
    if (!canvas) return;

    if (!actualValues.some(v => v > 0) && !baselineValues.some(v => v > 0)) {
      C.showChartEmpty(id, 'No cost data for this period');
      return;
    }
    C.hideChartEmpty(id);

    const config = C.createChartConfig('line', {
      labels,
      datasets: [
        {
          label: 'Baseline',
          data: baselineValues,
          borderColor: theme.baseline,
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          borderDash: [4, 4],
          formatFn: C.formatCurrency,
          pointRadius: 0,
          hoverRadius: 4,
          tension: 0.4,
        },
        {
          label: 'Actual',
          data: actualValues,
          borderColor: theme.actual,
          backgroundColor: 'rgba(59,130,246,0.08)',
          borderWidth: 2,
          fill: true,
          formatFn: C.formatCurrency,
          pointRadius: 0,
          hoverRadius: 4,
          tension: 0.4,
        },
        {
          label: 'Savings',
          data: savingsValues,
          borderColor: theme.savings,
          backgroundColor: 'rgba(16,185,129,0.08)',
          borderWidth: 1.5,
          fill: true,
          formatFn: C.formatCurrency,
          pointRadius: 0,
          hoverRadius: 4,
          tension: 0.4,
        }
      ]
    }, {
      scales: {
        y: {
          ticks: {
            callback: v => C.formatCurrency(v, { decimals: 2 })
          }
        }
      },
      onClick(evt, elements) {
        if (!elements.length) return;
        const idx = elements[0].index;
        const date = labels[idx];
        if (date) applyFilter('date', date);
      }
    });

    const chart = new Chart(canvas, config);

    // Set data-source badge from savings presence
    const hasSavings = savingsValues.some(v => v > 0);
    canvas.dataset.source = hasSavings ? 'estimated' : 'missing';

    C.renderChartLegend(chart, 'legend-' + id);
    return chart;
  }

  // ─── 2. Cost by Provider ───────────────────────────────────────────────────

  function initCostByProviderChart(byProvider) {
    const id = 'finops-cost-provider';
    const theme = C.getActiveTheme();
    const canvas = document.getElementById(id);
    if (!canvas) return;

    // Sort by cost descending
    const sorted = [...byProvider].sort((a, b) => {
      const ca = +(a.cost || a.total_cost || 0);
      const cb = +(b.cost || b.total_cost || 0);
      return cb - ca;
    });

    if (!sorted.length) {
      C.showChartEmpty(id, 'No provider data yet');
      return;
    }
    C.hideChartEmpty(id);

    const labels = sorted.map(r => r.provider || 'unknown');
    const costs = sorted.map(r => +(r.cost || r.total_cost || 0));
    const baselines = sorted.map(r => {
      const cost = +(r.cost || r.total_cost || 0);
      const savings = +(r.savings || 0);
      return cost + savings;
    });
    const colors = labels.map(l => providerColor(l, theme));

    const config = C.createChartConfig('bar', {
      labels: labels.map(l => shortLabel(l, 18)),
      datasets: [
        {
          label: 'Actual Cost',
          data: costs,
          backgroundColor: colors.map(c => c + 'cc'),
          borderColor: colors,
          borderWidth: 1,
          borderRadius: 4,
          formatFn: C.formatCurrency,
        }
      ]
    }, {
      scales: {
        x: { grid: { display: false } },
        y: {
          ticks: { callback: v => C.formatCurrency(v, { decimals: 2 }) }
        }
      },
      onClick(evt, elements) {
        if (!elements.length) return;
        const provider = labels[elements[0].index];
        applyFilter('provider', provider);
      }
    });

    const chart = new Chart(canvas, config);

    // Custom tooltip with full provider info
    canvas._providerData = sorted;
    chart.options.plugins.tooltip.external = function (context) {
      const { chart, tooltip } = context;
      let el = chart.canvas.parentNode.querySelector('.chart-tooltip');
      if (!el) {
        el = document.createElement('div');
        el.className = 'chart-tooltip';
        chart.canvas.parentNode.appendChild(el);
      }
      if (tooltip.opacity === 0) { el.style.opacity = '0'; return; }

      const idx = tooltip.dataPoints?.[0]?.dataIndex;
      if (idx == null) return;
      const row = (chart.canvas._providerData || [])[idx] || {};
      const cost = +(row.cost || row.total_cost || 0);
      const reqs = +(row.requests || row.total_requests || 0);
      const savings = +(row.savings || 0);
      const baseline = cost + savings;
      const avgCost = reqs > 0 ? cost / reqs : 0;
      const savingsPct = baseline > 0 ? (savings / baseline * 100) : 0;

      el.innerHTML = `
        <div class="tooltip-header">${C.escapeHtml(row.provider || '—')}</div>
        <div class="tooltip-row"><span class="tooltip-label">Requests:</span><span class="tooltip-value">${C.formatNumber(reqs)}</span></div>
        <div class="tooltip-row"><span class="tooltip-label">Baseline:</span><span class="tooltip-value">${C.formatCurrency(baseline)}</span></div>
        <div class="tooltip-row"><span class="tooltip-label">Actual:</span><span class="tooltip-value">${C.formatCurrency(cost)}</span></div>
        <div class="tooltip-row positive"><span class="tooltip-label">Savings:</span><span class="tooltip-value">${C.formatCurrency(savings)} (${C.formatPercent(savingsPct)})</span></div>
        <div class="tooltip-row"><span class="tooltip-label">Avg/req:</span><span class="tooltip-value">${C.formatCurrency(avgCost)}</span></div>
        <div class="tooltip-action" style="margin-top:8px;color:var(--color-primary);font-size:11px;cursor:pointer;" onclick="TokenPakFinOps.filterProvider('${C.escapeHtml(row.provider || '')}')">Filter to provider →</div>
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

  // ─── 3. Cost by Model (Horizontal) ────────────────────────────────────────

  function initCostByModelChart(byModel) {
    const id = 'finops-cost-model';
    const theme = C.getActiveTheme();
    const canvas = document.getElementById(id);
    if (!canvas) return;

    // Sort descending, top 10
    const sorted = [...byModel]
      .sort((a, b) => (+(b.cost || b.total_cost || 0)) - (+(a.cost || a.total_cost || 0)))
      .slice(0, 10);

    if (!sorted.length) {
      C.showChartEmpty(id, 'No model data yet');
      return;
    }
    C.hideChartEmpty(id);

    const labels = sorted.map(r => shortLabel(r.model || 'unknown', 30));
    const costs = sorted.map(r => +(r.cost || r.total_cost || 0));

    const config = C.createChartConfig('bar', {
      labels,
      datasets: [{
        label: 'Actual Cost',
        data: costs,
        backgroundColor: theme.primary + 'aa',
        borderColor: theme.primary,
        borderWidth: 1,
        borderRadius: 4,
        formatFn: C.formatCurrency,
      }]
    }, {
      indexAxis: 'y',
      scales: {
        x: {
          ticks: { callback: v => C.formatCurrency(v, { decimals: 2 }) }
        },
        y: {
          grid: { display: false },
          ticks: { font: { size: 11 } }
        }
      },
      onClick(evt, elements) {
        if (!elements.length) return;
        const model = sorted[elements[0].index]?.model;
        if (model) applyFilter('model', model);
      }
    });

    const chart = new Chart(canvas, config);

    // Custom tooltip
    canvas._modelData = sorted;
    chart.options.plugins.tooltip.external = function (context) {
      const { chart, tooltip } = context;
      let el = chart.canvas.parentNode.querySelector('.chart-tooltip');
      if (!el) {
        el = document.createElement('div');
        el.className = 'chart-tooltip';
        chart.canvas.parentNode.appendChild(el);
      }
      if (tooltip.opacity === 0) { el.style.opacity = '0'; return; }

      const idx = tooltip.dataPoints?.[0]?.dataIndex;
      if (idx == null) return;
      const row = (chart.canvas._modelData || [])[idx] || {};
      const cost = +(row.cost || row.total_cost || 0);
      const reqs = +(row.requests || row.total_requests || 0);
      const tokens = +(row.tokens || row.total_tokens || 0);
      const compression = +(row.compression_ratio || 0);
      const avgTokens = reqs > 0 ? Math.round(tokens / reqs) : 0;

      el.innerHTML = `
        <div class="tooltip-header">${C.escapeHtml(row.model || '—')}</div>
        <div class="tooltip-row"><span class="tooltip-label">Provider:</span><span class="tooltip-value">${C.escapeHtml(row.provider || '—')}</span></div>
        <div class="tooltip-row"><span class="tooltip-label">Requests:</span><span class="tooltip-value">${C.formatNumber(reqs)}</span></div>
        <div class="tooltip-row"><span class="tooltip-label">Cost:</span><span class="tooltip-value">${C.formatCurrency(cost)}</span></div>
        <div class="tooltip-row"><span class="tooltip-label">Avg tokens/req:</span><span class="tooltip-value">${C.formatNumber(avgTokens)}</span></div>
        ${compression > 0 ? `<div class="tooltip-row positive"><span class="tooltip-label">Compression:</span><span class="tooltip-value">${C.formatPercent(compression * 100)}</span></div>` : ''}
        <div class="tooltip-action" style="margin-top:8px;color:var(--color-primary);font-size:11px;cursor:pointer;" onclick="TokenPakFinOps.filterModel('${C.escapeHtml(row.model || '')}')">Filter to model →</div>
      `;
      el.style.opacity = '1';
      el.style.pointerEvents = 'auto';
      el.style.left = (chart.canvas.offsetLeft + tooltip.caretX + 12) + 'px';
      el.style.top = (chart.canvas.offsetTop + tooltip.caretY - 20) + 'px';
    };

    chart.update();
    return chart;
  }

  // ─── 4. Savings Over Time ──────────────────────────────────────────────────

  let _savingsChart = null;
  let _savingsMode = 'dollars';   // 'dollars' | 'percent' | 'tokens'
  let _savingsData = {};

  function initSavingsOverTimeChart(costData, savingsData, tokenSavingsData) {
    const id = 'finops-savings-time';
    const theme = C.getActiveTheme();
    const canvas = document.getElementById(id);
    if (!canvas) return;

    _savingsData = { costData, savingsData, tokenSavingsData };

    const labels = savingsData.map(d => d.bucket ? d.bucket.slice(0, 10) : '');
    const dollarValues = savingsData.map(d => +(d.value || 0));
    const costValues = costData.map(d => +(d.value || 0));
    const baselineValues = costValues.map((v, i) => v + dollarValues[i]);
    const percentValues = baselineValues.map((b, i) =>
      b > 0 ? (dollarValues[i] / b) * 100 : 0
    );
    const tokenValues = (tokenSavingsData || []).map(d => +(d.value || 0));

    if (!dollarValues.some(v => v > 0)) {
      C.showChartEmpty(id, 'No savings data — compression may not be active');
      return;
    }
    C.hideChartEmpty(id);

    function buildDataset(mode) {
      const modeMap = {
        dollars: { data: dollarValues, label: 'Savings ($)', color: theme.savings, fmt: C.formatCurrency },
        percent: { data: percentValues, label: 'Savings (%)', color: '#8b5cf6', fmt: v => C.formatPercent(v) },
        tokens: { data: tokenValues.length ? tokenValues : dollarValues.map(() => 0), label: 'Tokens Saved', color: theme.tokens, fmt: C.formatTokens }
      };
      const m = modeMap[mode];
      return {
        label: m.label,
        data: m.data,
        borderColor: m.color,
        backgroundColor: m.color + '18',
        borderWidth: 2,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
        hoverRadius: 4,
        formatFn: m.fmt
      };
    }

    const config = C.createChartConfig('line', {
      labels,
      datasets: [buildDataset(_savingsMode)]
    }, {
      scales: {
        y: {
          ticks: {
            callback: v => {
              if (_savingsMode === 'dollars') return C.formatCurrency(v, { decimals: 2 });
              if (_savingsMode === 'percent') return C.formatPercent(v);
              return C.formatTokens(v);
            }
          }
        }
      },
      onClick(evt, elements) {
        if (!elements.length) return;
        const date = labels[elements[0].index];
        if (date) applyFilter('date', date);
      }
    });

    if (_savingsChart) { _savingsChart.destroy(); _savingsChart = null; }
    _savingsChart = new Chart(canvas, config);

    // Bind toggle buttons
    document.querySelectorAll('[data-savings-mode]').forEach(btn => {
      btn.addEventListener('click', function () {
        _savingsMode = this.dataset.savingsMode;
        document.querySelectorAll('[data-savings-mode]').forEach(b => b.classList.toggle('active', b === this));
        if (_savingsChart) {
          _savingsChart.data.datasets[0] = buildDataset(_savingsMode);
          _savingsChart.options.scales.y.ticks.callback = v => {
            if (_savingsMode === 'dollars') return C.formatCurrency(v, { decimals: 2 });
            if (_savingsMode === 'percent') return C.formatPercent(v);
            return C.formatTokens(v);
          };
          _savingsChart.update();
        }
      });
    });

    return _savingsChart;
  }


  // ─── 5. Savings by Model (horizontal bar, sorted by savings) ─────────────

  function initSavingsByModelChart(byModel) {
    const id = 'finops-savings-by-model';
    const canvas = document.getElementById(id);
    if (!canvas) return;
    if (Chart.getChart(canvas)) Chart.getChart(canvas).destroy();

    const theme = C.getActiveTheme();
    const sorted = [...byModel]
      .filter(r => (r.savings || r.total_savings || 0) > 0)
      .sort((a, b) => (b.savings || b.total_savings || 0) - (a.savings || a.total_savings || 0))
      .slice(0, 5);

    if (!sorted.length) {
      const empty = canvas.closest('.chart-canvas-wrapper')?.querySelector('.chart-empty');
      if (empty) { empty.style.display = 'flex'; canvas.style.display = 'none'; }
      return;
    }

    const labels = sorted.map(r => {
      const m = r.model || r.name || '—';
      return m.length > 28 ? m.slice(0, 27) + '…' : m;
    });
    const values = sorted.map(r => +(r.savings || r.total_savings || 0));

    new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Savings ($)',
          data: values,
          backgroundColor: values.map((_, i) =>
            `hsla(${140 - i * 20},60%,50%,0.75)`),
          borderWidth: 0,
          borderRadius: 4,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => `$${ctx.parsed.x.toFixed(4)} saved`,
            },
          },
        },
        scales: {
          x: {
            grid: { color: theme.gridColor || 'rgba(255,255,255,0.08)' },
            ticks: {
              color: theme.tickColor || '#aaa',
              callback: v => '$' + v.toFixed(4),
            },
          },
          y: {
            grid: { display: false },
            ticks: { color: theme.tickColor || '#aaa', font: { size: 11 } },
          },
        },
        onClick(event, elements) {
          if (elements.length) {
            const idx = elements[0].index;
            applyFilter('model', sorted[idx].model || sorted[idx].name || '');
          }
        },
      },
    });
  }

  // ─── 6. Compression Gauge (doughnut semicircle) ────────────────────────────

  function initCompressionGauge(compressionPct) {
    const id = 'finops-compression-gauge';
    const canvas = document.getElementById(id);
    if (!canvas) return;
    if (Chart.getChart(canvas)) Chart.getChart(canvas).destroy();

    const pct = Math.max(0, Math.min(100, compressionPct || 0));
    const remainder = 100 - pct;

    // Color: green ≥50, yellow 30-50, red <30
    const fill = pct >= 50 ? '#4caf50' : pct >= 30 ? '#ff9800' : '#f44336';
    const label = pct.toFixed(1) + '%';

    new Chart(canvas, {
      type: 'doughnut',
      data: {
        datasets: [{
          data: [pct, remainder],
          backgroundColor: [fill, 'rgba(255,255,255,0.08)'],
          borderWidth: 0,
          circumference: 180,
          rotation: 270,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '72%',
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
        },
      },
      plugins: [{
        id: 'gaugeLabel',
        afterDraw(chart) {
          const { ctx, chartArea } = chart;
          if (!chartArea) return;
          const cx = (chartArea.left + chartArea.right) / 2;
          const cy = chartArea.bottom - 8;
          ctx.save();
          ctx.fillStyle = fill;
          ctx.font = 'bold 22px Inter, sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'bottom';
          ctx.fillText(label, cx, cy);
          ctx.fillStyle = '#aaa';
          ctx.font = '11px Inter, sans-serif';
          ctx.fillText('compression', cx, cy + 14);
          ctx.restore();
        },
      }],
    });
  }



  // ─── Public API ────────────────────────────────────────────────────────────

  window.TokenPakFinOps = {
    init(costData, savingsData, byProvider, byModel, compressionPct) {
      // Verify chart-factory loaded
      if (!window.TokenPakCharts) {
        console.error('TokenPakCharts not loaded — ensure chart-factory.js is included before finops-charts.js');
        return;
      }
      initBaselineActualChart(costData, savingsData);
      initCostByProviderChart(byProvider);
      initCostByModelChart(byModel);
      initSavingsOverTimeChart(costData, savingsData, null);
      initSavingsByModelChart(byModel);
      initCompressionGauge(compressionPct || 0);
    },
    filterProvider(provider) { applyFilter('provider', provider); },
    filterModel(model) { applyFilter('model', model); },
    filterDate(date) { applyFilter('date', date); },
  };

  // Re-init on HTMX reinit event
  document.addEventListener('charts:reinit', function (e) {
    const target = e.detail?.target;
    if (target && target.id === 'main-content') {
      // Charts re-init is triggered by inline script in finops_partial.html
    }
  });

})();
