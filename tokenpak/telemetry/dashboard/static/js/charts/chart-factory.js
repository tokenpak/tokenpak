/**
 * TokenPak Dashboard — Chart System Foundation
 * Premium chart infrastructure: factory, theme, tooltips, utilities
 */

'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// Theme System
// ─────────────────────────────────────────────────────────────────────────────

const CHART_THEMES = {
  dark: {
    backgroundColor: '#0a0a0b',
    cardBackground: '#1a1a24',
    gridColor: 'rgba(255, 255, 255, 0.05)',
    borderColor: 'rgba(255, 255, 255, 0.08)',
    textColor: '#cbd5e1',
    textMuted: '#94a3b8',
    primary: '#6366f1',
    primaryHover: '#818cf8',
    positive: '#10b981',
    warning: '#f59e0b',
    danger: '#ef4444',
    info: '#3b82f6',
    neutral: '#64748b',
    baseline: '#64748b',
    actual: '#3b82f6',
    savings: '#10b981',
    cost: '#6366f1',
    tokens: '#06b6d4',
    latency: '#f59e0b',
    errors: '#ef4444',
    palette: ['#6366f1','#10b981','#3b82f6','#f59e0b','#8b5cf6','#ec4899','#06b6d4','#f97316']
  },
  light: {
    backgroundColor: '#ffffff',
    cardBackground: '#f8fafc',
    gridColor: 'rgba(0, 0, 0, 0.06)',
    borderColor: 'rgba(0, 0, 0, 0.1)',
    textColor: '#334155',
    textMuted: '#64748b',
    primary: '#4f46e5',
    primaryHover: '#6366f1',
    positive: '#059669',
    warning: '#d97706',
    danger: '#dc2626',
    info: '#2563eb',
    neutral: '#94a3b8',
    baseline: '#94a3b8',
    actual: '#2563eb',
    savings: '#059669',
    cost: '#4f46e5',
    tokens: '#0891b2',
    latency: '#d97706',
    errors: '#dc2626',
    palette: ['#4f46e5','#059669','#2563eb','#d97706','#7c3aed','#db2777','#0891b2','#ea580c']
  }
};

function getActiveTheme() {
  const isDark = !document.body.classList.contains('theme-light');
  return isDark ? CHART_THEMES.dark : CHART_THEMES.light;
}

// ─────────────────────────────────────────────────────────────────────────────
// Number Formatting Utilities
// ─────────────────────────────────────────────────────────────────────────────

function formatTokens(value) {
  if (value == null || isNaN(value)) return '—';
  const n = Number(value);
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
  return n.toLocaleString();
}

function formatCurrency(value, { decimals = null } = {}) {
  if (value == null || isNaN(value)) return '—';
  const n = Number(value);
  if (decimals !== null) return '$' + n.toFixed(decimals);
  if (Math.abs(n) >= 100) return '$' + n.toFixed(2);
  if (Math.abs(n) >= 1) return '$' + n.toFixed(3);
  return '$' + n.toFixed(4);
}

function formatPercent(value, { decimals = 1 } = {}) {
  if (value == null || isNaN(value)) return '—';
  return Number(value).toFixed(decimals) + '%';
}

function formatLatency(value) {
  if (value == null || isNaN(value)) return '—';
  const n = Number(value);
  if (n >= 1000) return (n / 1000).toFixed(1) + 's';
  return Math.round(n) + 'ms';
}

function formatNumber(value) {
  if (value == null || isNaN(value)) return '—';
  return Number(value).toLocaleString();
}

// ─────────────────────────────────────────────────────────────────────────────
// Deep Merge Helper
// ─────────────────────────────────────────────────────────────────────────────

function deepMerge(...sources) {
  const result = {};
  for (const source of sources) {
    if (!source || typeof source !== 'object') continue;
    for (const key of Object.keys(source)) {
      if (
        source[key] && typeof source[key] === 'object' &&
        !Array.isArray(source[key]) &&
        result[key] && typeof result[key] === 'object'
      ) {
        result[key] = deepMerge(result[key], source[key]);
      } else {
        result[key] = source[key];
      }
    }
  }
  return result;
}

// ─────────────────────────────────────────────────────────────────────────────
// Base & Type-Specific Chart Options
// ─────────────────────────────────────────────────────────────────────────────

function getBaseOptions(theme) {
  const t = theme || getActiveTheme();
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    animation: { duration: 200 },
    scales: {
      x: {
        grid: { color: t.gridColor, drawBorder: false, tickLength: 0 },
        border: { display: false },
        ticks: {
          color: t.textMuted,
          font: { size: 12, family: "'Inter', sans-serif" },
          maxRotation: 0,
          padding: 8
        }
      },
      y: {
        grid: { color: t.gridColor, drawBorder: false },
        border: { display: false },
        ticks: {
          color: t.textMuted,
          font: { size: 12, family: "'Inter', sans-serif" },
          padding: 8
        }
      }
    },
    plugins: {
      legend: { display: false },
      tooltip: { enabled: false, external: createExternalTooltip }
    }
  };
}

function getTypeOptions(type) {
  const opts = {
    line: {
      elements: {
        line: { tension: 0.4, borderWidth: 2 },
        point: { radius: 0, hoverRadius: 4, hitRadius: 8 }
      }
    },
    bar: {
      elements: { bar: { borderRadius: 4, borderSkipped: 'bottom' } },
      scales: { x: { grid: { display: false } } }
    },
    area: {
      elements: {
        line: { tension: 0.4, borderWidth: 2 },
        point: { radius: 0, hoverRadius: 4, hitRadius: 8 }
      },
      fill: true
    },
    doughnut: {
      cutout: '70%',
      plugins: { legend: { display: false } }
    }
  };
  return opts[type] || {};
}

// ─────────────────────────────────────────────────────────────────────────────
// Chart Configuration Factory
// ─────────────────────────────────────────────────────────────────────────────

function createChartConfig(type, dataset, options = {}) {
  const theme = getActiveTheme();
  const effectiveType = type === 'area' ? 'line' : type;
  const merged = deepMerge(getBaseOptions(theme), getTypeOptions(type), options);
  return { type: effectiveType, data: dataset, options: merged };
}

// ─────────────────────────────────────────────────────────────────────────────
// HTML Safety
// ─────────────────────────────────────────────────────────────────────────────

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ─────────────────────────────────────────────────────────────────────────────
// External HTML Tooltip
// ─────────────────────────────────────────────────────────────────────────────

function createExternalTooltip(context) {
  const { chart, tooltip } = context;

  let tooltipEl = chart.canvas.parentNode.querySelector('.chart-tooltip');
  if (!tooltipEl) {
    tooltipEl = document.createElement('div');
    tooltipEl.className = 'chart-tooltip';
    chart.canvas.parentNode.appendChild(tooltipEl);
  }

  if (tooltip.opacity === 0) {
    tooltipEl.style.opacity = '0';
    tooltipEl.style.pointerEvents = 'none';
    return;
  }

  const title = tooltip.title ? tooltip.title[0] : '';
  const items = tooltip.dataPoints || [];

  let html = `<div class="tooltip-header">${escapeHtml(title)}</div>`;

  for (const item of items) {
    const label = item.dataset.label || '';
    const raw = item.raw;
    const formatted = item.dataset.formatFn ? item.dataset.formatFn(raw) : (typeof raw === 'number' ? raw.toLocaleString() : raw);
    const color = item.dataset.borderColor || item.dataset.backgroundColor || '#6366f1';
    const colorDot = `<span class="tooltip-dot" style="background:${color}"></span>`;
    const isPositive = label.toLowerCase().includes('saving');
    html += `<div class="tooltip-row${isPositive ? ' positive' : ''}">
      ${colorDot}
      <span class="tooltip-label">${escapeHtml(label)}:</span>
      <span class="tooltip-value">${escapeHtml(String(formatted))}</span>
    </div>`;
  }

  const dataSource = chart.canvas.dataset.source;
  if (dataSource) {
    const badges = { billed: ['✔','badge-billed'], estimated: ['⚠','badge-estimated'], mixed: ['◻','badge-mixed'], missing: ['✖','badge-missing'] };
    const [icon, cls] = badges[dataSource] || ['⚠','badge-estimated'];
    const label = dataSource.charAt(0).toUpperCase() + dataSource.slice(1);
    html += `<div class="tooltip-badge ${cls}">${icon} ${label}</div>`;
  }

  if (chart.canvas.dataset.traceLink) {
    html += `<a class="tooltip-action" href="${chart.canvas.dataset.traceLink}">View traces →</a>`;
  }

  tooltipEl.innerHTML = html;
  tooltipEl.style.opacity = '1';
  tooltipEl.style.pointerEvents = 'auto';

  const left = chart.canvas.offsetLeft + tooltip.caretX;
  const top = chart.canvas.offsetTop + tooltip.caretY;
  tooltipEl.style.left = left + 'px';
  tooltipEl.style.top = top + 'px';

  // Flip left if near right edge
  const tooltipW = tooltipEl.offsetWidth;
  const canvasW = chart.canvas.getBoundingClientRect().width;
  if (tooltip.caretX + tooltipW + 20 > canvasW) {
    tooltipEl.style.left = (left - tooltipW - 12) + 'px';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Data Status Badges
// ─────────────────────────────────────────────────────────────────────────────

const DATA_BADGES = {
  billed:    { icon: '✔', label: 'Billed',    cls: 'badge-billed' },
  estimated: { icon: '⚠', label: 'Estimated', cls: 'badge-estimated' },
  mixed:     { icon: '◻', label: 'Mixed',     cls: 'badge-mixed' },
  missing:   { icon: '✖', label: 'Missing',   cls: 'badge-missing' }
};

function createDataBadge(status) {
  const def = DATA_BADGES[status] || DATA_BADGES.estimated;
  return `<span class="data-badge ${def.cls}">${def.icon} ${def.label}</span>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Chart Container Template
// ─────────────────────────────────────────────────────────────────────────────

function createChartContainer({ id, title='', subtitle='', source=null, actions=true, traceLink=null, height='220px' } = {}) {
  const actionsHtml = actions ? `
    <div class="chart-actions">
      <button class="chart-action" data-chart-id="${id}" data-action="csv" title="Export CSV">CSV</button>
      <button class="chart-action" data-chart-id="${id}" data-action="png" title="Export PNG">PNG</button>
      <button class="chart-action" data-chart-id="${id}" data-action="reset-zoom" title="Reset zoom">↺</button>
    </div>` : '';

  const badgeHtml = source ? `<div class="chart-status">${createDataBadge(source)}</div>` : '';

  const el = document.createElement('div');
  el.className = 'chart-container';
  el.innerHTML = `
    <div class="chart-header">
      <div class="chart-header-text">
        ${title ? `<h3 class="chart-title">${escapeHtml(title)}</h3>` : ''}
        ${subtitle ? `<span class="chart-subtitle">${escapeHtml(subtitle)}</span>` : ''}
      </div>
      ${actionsHtml}
    </div>
    ${badgeHtml}
    <div class="chart-canvas-wrapper" style="position:relative;height:${height};">
      <canvas id="${id}"
        ${traceLink ? `data-trace-link="${traceLink}"` : ''}
        ${source ? `data-source="${source}"` : ''}></canvas>
      <div class="chart-empty" style="display:none;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:8px;">
        <p>No data for selected filters</p>
        <p class="chart-empty-hint">Try expanding the date range</p>
        <button class="btn btn-sm" onclick="TokenPakCharts.clearChartFilters(this)">Clear filters</button>
      </div>
    </div>
    <div class="chart-legend" id="legend-${id}"></div>
  `;
  return el;
}

// ─────────────────────────────────────────────────────────────────────────────
// Empty State Helpers
// ─────────────────────────────────────────────────────────────────────────────

function showChartEmpty(chartId, message) {
  const canvas = document.getElementById(chartId);
  if (!canvas) return;
  const wrapper = canvas.closest('.chart-canvas-wrapper');
  if (!wrapper) return;
  const empty = wrapper.querySelector('.chart-empty');
  if (empty) {
    if (message) empty.querySelector('p').textContent = message;
    empty.style.display = 'flex';
  }
  canvas.style.display = 'none';
}

function hideChartEmpty(chartId) {
  const canvas = document.getElementById(chartId);
  if (!canvas) return;
  const wrapper = canvas.closest('.chart-canvas-wrapper');
  if (!wrapper) return;
  const empty = wrapper.querySelector('.chart-empty');
  if (empty) empty.style.display = 'none';
  canvas.style.display = 'block';
}

function clearChartFilters() {
  document.body.dispatchEvent(new CustomEvent('chart-clear-filters'));
}

// ─────────────────────────────────────────────────────────────────────────────
// Custom Legend
// ─────────────────────────────────────────────────────────────────────────────

function renderChartLegend(chart, legendId) {
  const legendEl = document.getElementById(legendId);
  if (!legendEl) return;
  const items = chart.data.datasets.map((ds, i) => ({
    label: ds.label || '',
    color: ds.borderColor || ds.backgroundColor || CHART_THEMES.dark.palette[i % 8],
    hidden: !chart.isDatasetVisible(i),
    index: i
  }));
  legendEl.innerHTML = items.map(item => `
    <div class="legend-item${item.hidden ? ' legend-hidden' : ''}"
         data-dataset-index="${item.index}"
         data-chart-canvas="${chart.canvas.id}"
         onclick="TokenPakCharts.toggleChartDataset(this)">
      <span class="legend-dot" style="background:${item.color}"></span>
      <span class="legend-label">${escapeHtml(item.label)}</span>
    </div>`).join('');
}

function toggleChartDataset(el) {
  const canvas = document.getElementById(el.dataset.chartCanvas);
  if (!canvas) return;
  const chart = Chart.getChart(canvas);
  if (!chart) return;
  const index = parseInt(el.dataset.datasetIndex, 10);
  const meta = chart.getDatasetMeta(index);
  meta.hidden = !meta.hidden;
  el.classList.toggle('legend-hidden', meta.hidden);
  chart.update();
}

// ─────────────────────────────────────────────────────────────────────────────
// Chart Action Handlers (CSV / PNG / Reset Zoom)
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener('click', function(e) {
  const btn = e.target.closest('.chart-action[data-action]');
  if (!btn) return;
  const { action, chartId } = btn.dataset;
  const canvas = document.getElementById(chartId);
  if (!canvas) return;
  const chart = Chart.getChart(canvas);

  if (action === 'png') {
    const a = document.createElement('a');
    a.download = (chartId || 'chart') + '.png';
    a.href = canvas.toDataURL('image/png');
    a.click();
  } else if (action === 'csv') {
    if (!chart) return;
    const labels = chart.data.labels || [];
    const datasets = chart.data.datasets || [];
    const headers = ['Date', ...datasets.map(d => d.label || 'Value')];
    const rows = labels.map((lbl, i) => [lbl, ...datasets.map(d => d.data[i] ?? '')]);
    const csv = [headers, ...rows].map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const a = document.createElement('a');
    a.download = (chartId || 'chart') + '.csv';
    a.href = URL.createObjectURL(blob);
    a.click();
    URL.revokeObjectURL(a.href);
  } else if (action === 'reset-zoom') {
    if (chart && chart.resetZoom) chart.resetZoom();
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// HTMX Lifecycle Management
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener('htmx:beforeSwap', function(e) {
  const target = e.detail.target;
  if (!target) return;
  target.querySelectorAll('canvas').forEach(canvas => {
    const chart = Chart.getChart(canvas);
    if (chart) chart.destroy();
  });
});

document.addEventListener('htmx:afterSwap', function(e) {
  document.body.dispatchEvent(new CustomEvent('charts:reinit', {
    detail: { target: e.detail.target }
  }));
});

// ─────────────────────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────────────────────

window.TokenPakCharts = {
  createChartConfig,
  createChartContainer,
  getActiveTheme,
  CHART_THEMES,
  formatTokens,
  formatCurrency,
  formatPercent,
  formatLatency,
  formatNumber,
  createDataBadge,
  DATA_BADGES,
  renderChartLegend,
  toggleChartDataset,
  showChartEmpty,
  hideChartEmpty,
  clearChartFilters,
  createExternalTooltip,
  deepMerge,
  getBaseOptions,
  getTypeOptions,
  escapeHtml
};
