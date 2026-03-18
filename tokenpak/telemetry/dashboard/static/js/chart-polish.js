/**
 * TokenPak Dashboard — Chart Interactivity & Visual Polish
 */
'use strict';
(function () {

  const SPIKE_COST_PCT   = 0.5;
  const SPIKE_ERROR_MULT = 3.0;
  const SPIKE_LAT_MULT   = 2.0;
  const GRANULARITY_THRESHOLD = 5;

  const ANN_COLOR = {
    cost:    'rgba(239,68,68,0.85)',
    error:   'rgba(245,158,11,0.85)',
    latency: 'rgba(139,92,246,0.85)',
    version: 'rgba(99,102,241,0.6)',
  };

  // ── 1. Spike detection ─────────────────────────────────────────────────────

  function detectSpikes(labels, values, multiplier, kind, labelFn) {
    if (!values || values.length < 3) return [];
    const nonZero = values.filter(v => v && v > 0);
    if (!nonZero.length) return [];
    const avg = nonZero.reduce((s,v)=>s+v,0)/nonZero.length;
    return values.reduce((out, v, i) => {
      if (v && avg > 0) {
        const ratio = kind === 'cost' ? (v - avg) / avg : v / avg;
        if (ratio > multiplier) {
          out.push({ index: i, label: labels[i], value: v, text: labelFn(v, avg, ratio), kind });
        }
      }
      return out;
    }, []);
  }

  function detectCostSpikes(labels, values) {
    return detectSpikes(labels, values, SPIKE_COST_PCT, 'cost',
      (v, avg, r) => `\u2191 Cost +${Math.round(r*100)}%`);
  }
  function detectErrorSpikes(labels, values) {
    return detectSpikes(labels, values, SPIKE_ERROR_MULT, 'error',
      (v, avg, r) => `\u2191 Errors \xd7${r.toFixed(1)}`);
  }
  function detectLatencySpikes(labels, values) {
    return detectSpikes(labels, values, SPIKE_LAT_MULT, 'latency',
      (v, avg, r) => `\u2191 Latency \xd7${r.toFixed(1)}`);
  }

  // ── 2. Custom annotation plugin ────────────────────────────────────────────

  const AnnotationPlugin = {
    id: 'tokenpakAnnotations',
    afterDraw(chart) {
      const anns = chart._tokenpakAnnotations;
      if (!anns || !anns.length) return;
      const ctx = chart.ctx;
      const { top, bottom } = chart.chartArea;
      const xScale = chart.scales.x;
      const yScale = chart.scales.y;
      if (!xScale) return;
      ctx.save();
      anns.forEach(ann => {
        const { index, value, text, kind, isVersion } = ann;
        const x = xScale.getPixelForValue ? xScale.getPixelForValue(index) : xScale.getPixelForIndex(index);
        if (!x || isNaN(x)) return;
        const color = ANN_COLOR[kind] || ANN_COLOR.version;
        if (isVersion) {
          ctx.strokeStyle = color; ctx.lineWidth = 1.5;
          ctx.setLineDash([4,4]);
          ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
          ctx.setLineDash([]);
          ctx.save(); ctx.translate(x+4, top+4);
          ctx.fillStyle = color; ctx.font = '10px Inter,system-ui,sans-serif';
          ctx.fillText(text, 0, 0); ctx.restore();
          return;
        }
        let y = bottom - 20;
        if (yScale && value !== undefined) y = Math.max(top+4, yScale.getPixelForValue(value));
        ctx.beginPath(); ctx.arc(x, y-10, 4, 0, Math.PI*2);
        ctx.fillStyle = color; ctx.fill();
        ctx.font = 'bold 10px Inter,system-ui,sans-serif';
        const tw = ctx.measureText(text).width;
        const px=5, py=3;
        const lx = Math.max(2, Math.min(x - tw/2 - px, chart.width - tw - px*2 - 4));
        const ly = y - 30;
        ctx.fillStyle = color;
        if (ctx.roundRect) ctx.roundRect(lx, ly, tw+px*2, 14+py*2, 4);
        else ctx.rect(lx, ly, tw+px*2, 14+py*2);
        ctx.fill();
        ctx.fillStyle = '#fff'; ctx.fillText(text, lx+px, ly+py+11);
        ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash([2,2]);
        ctx.beginPath(); ctx.moveTo(x, ly+14+py*2); ctx.lineTo(x, y-10); ctx.stroke();
        ctx.setLineDash([]);
        ann._px=lx; ann._py=ly; ann._pw=tw+px*2; ann._ph=14+py*2;
      });
      ctx.restore();
    },
    afterEvent(chart, args) {
      const anns = chart._tokenpakAnnotations;
      if (!anns || !anns.length) return;
      const { event } = args;
      if (event.type !== 'click') return;
      anns.forEach(ann => {
        if (ann._px && event.x >= ann._px && event.x <= ann._px+ann._pw &&
            event.y >= ann._py && event.y <= ann._py+ann._ph) {
          drillToDate(ann.label);
        }
      });
    },
  };

  function registerPlugin() {
    if (!window.Chart) return;
    try {
      if (!Chart.registry.plugins.get('tokenpakAnnotations')) Chart.register(AnnotationPlugin);
    } catch(e) { /* already registered */ }
    applyGlobalDefaults();
  }

  // ── 3. Global Chart.js defaults ────────────────────────────────────────────

  function applyGlobalDefaults() {
    if (!window.Chart) return;
    const D = Chart.defaults;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    D.animation = reduced ? false : { duration: 500, easing: 'easeInOutQuart' };
    if (D.transitions) D.transitions.active = { animation: { duration: reduced ? 0 : 200 } };
    if (D.scale?.grid)  { D.scale.grid.color = 'rgba(255,255,255,0.04)'; D.scale.grid.drawBorder = false; }
    if (D.scale?.ticks) {
      D.scale.ticks.maxTicksLimit = 7;
      D.scale.ticks.font = { family: 'Inter, system-ui, sans-serif', size: 11 };
      D.scale.ticks.color = 'rgba(148,163,184,0.8)';
    }
    D.elements.line.borderWidth = 2;
    D.elements.point.radius = 3;
    D.elements.point.hoverRadius = 5;
    if (D.plugins?.legend)  D.plugins.legend.display = false;
    if (D.plugins?.tooltip) {
      D.plugins.tooltip.backgroundColor = 'rgba(15,23,42,0.95)';
      D.plugins.tooltip.borderColor = 'rgba(99,102,241,0.4)';
      D.plugins.tooltip.borderWidth = 1;
      D.plugins.tooltip.cornerRadius = 8;
      D.plugins.tooltip.padding = 10;
    }
  }

  // ── 4. Attach annotations ─────────────────────────────────────────────────

  function attachAnnotations(chart, opts) {
    if (!chart) return;
    opts = opts || {};
    const labels = chart.data.labels || [];
    const anns = [];
    if (opts.costSeries)    detectCostSpikes(labels, opts.costSeries).forEach(a => anns.push(a));
    if (opts.errorSeries)   detectErrorSpikes(labels, opts.errorSeries).forEach(a => anns.push(a));
    if (opts.latencySeries) detectLatencySpikes(labels, opts.latencySeries).forEach(a => anns.push(a));
    (opts.versions || []).forEach(v => {
      const idx = labels.indexOf(v.date);
      if (idx >= 0) anns.push({ index: idx, label: v.date, text: v.label, kind: 'version', isVersion: true });
    });
    chart._tokenpakAnnotations = anns;
    chart.update('none');
    if (anns.length) {
      const spikeCount = anns.filter(a => !a.isVersion).length;
      const wrapperId = chart.canvas?.closest?.('[data-chart-id]')?.dataset?.chartId
                     || chart.canvas?.id?.replace('-canvas','');
      if (wrapperId) {
        const container = document.getElementById(wrapperId);
        if (container) {
          let badge = container.querySelector('.annotation-count');
          if (!badge) { badge = document.createElement('span'); badge.className = 'annotation-count'; container.querySelector('.chart-title-row')?.appendChild(badge); }
          badge.textContent = `${spikeCount} spike${spikeCount !== 1 ? 's' : ''}`;
          badge.title = 'Automatic spike annotations';
        }
      }
    }
  }

  // ── 5. Missing / sparse data ──────────────────────────────────────────────

  function analyzeDataQuality(labels, values) {
    const total = labels.length;
    const nullCount = values.filter(v => v === null || v === undefined).length;
    const nonEmpty = total - nullCount;
    return {
      total, nullCount, nonEmpty,
      isMissing: total === 0 || nonEmpty === 0,
      isSparse: total > 0 && nonEmpty > 0 && nonEmpty < GRANULARITY_THRESHOLD,
      hasNulls: nullCount > 0,
      missingFraction: total > 0 ? nullCount / total : 0,
    };
  }

  function showInsufficientData(containerId, opts) {
    opts = opts || {};
    const container = document.getElementById(containerId);
    if (!container) return;
    const canvas = container.querySelector('canvas');
    if (canvas) canvas.style.display = 'none';
    let empty = container.querySelector('.chart-empty-enhanced');
    if (!empty) { empty = document.createElement('div'); empty.className = 'chart-empty-enhanced'; container.appendChild(empty); }
    empty.style.display = 'flex';
    empty.innerHTML = `
      <div class="empty-icon">📊</div>
      <div class="empty-title">${opts.title || 'Insufficient data'}</div>
      <div class="empty-desc">${opts.desc || 'Not enough data points for this time range.'}</div>
      ${opts.action ? `<a class="empty-action" href="${opts.action.href}">${opts.action.label}</a>` : ''}`;
  }

  function hideInsufficientData(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const canvas = container.querySelector('canvas');
    if (canvas) canvas.style.display = '';
    const empty = container.querySelector('.chart-empty-enhanced');
    if (empty) empty.style.display = 'none';
  }

  function showGranularityBadge(containerId, granularity) {
    const container = document.getElementById(containerId);
    if (!container) return;
    let badge = container.querySelector('.granularity-badge');
    if (!badge) { badge = document.createElement('span'); badge.className = 'granularity-badge'; container.querySelector('.chart-header')?.appendChild(badge); }
    badge.textContent = `Auto: ${granularity}`;
    badge.title = `Switched to ${granularity} due to sparse data`;
  }

  function suggestGranularity(dataPoints, currentGranularity) {
    if (dataPoints < GRANULARITY_THRESHOLD && currentGranularity === 'daily') return 'hourly';
    if (dataPoints > 180 && currentGranularity === 'hourly') return 'daily';
    return currentGranularity;
  }

  function splitEstimatedData(labels, values, isEstimated) {
    return {
      confirmed: values.map((v, i) => isEstimated[i] ? null : v),
      estimated: values.map((v, i) => isEstimated[i] ? v : null),
    };
  }

  // ── 6. Loading skeleton ───────────────────────────────────────────────────

  function showChartSkeleton(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    let skel = container.querySelector('.chart-skeleton');
    if (!skel) {
      skel = document.createElement('div'); skel.className = 'chart-skeleton';
      skel.setAttribute('aria-hidden','true');
      skel.innerHTML = '<div class="skeleton-shimmer"></div>';
      container.appendChild(skel);
    }
    skel.style.display = '';
    const canvas = container.querySelector('canvas');
    if (canvas) { canvas.style.opacity = '0'; canvas.style.transition = 'opacity 0.3s'; }
  }

  function hideChartSkeleton(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const skel = container.querySelector('.chart-skeleton');
    if (skel) skel.style.display = 'none';
    const canvas = container.querySelector('canvas');
    if (canvas) { canvas.style.opacity = '1'; }
  }

  // ── 7. Tooltip trace link ─────────────────────────────────────────────────

  function traceFilterURL(label, extraParams) {
    const url = new URL('/dashboard/audit', window.location.origin);
    if (label && label.match(/\d{4}-\d{2}-\d{2}/)) url.searchParams.set('date', label);
    if (extraParams) Object.entries(extraParams).forEach(([k,v]) => url.searchParams.set(k,v));
    return url.toString();
  }

  function appendTooltipTraceLink(tooltipEl, label, extraParams) {
    if (!tooltipEl) return;
    let link = tooltipEl.querySelector('.tooltip-trace-link');
    if (!link) { link = document.createElement('a'); link.className = 'tooltip-trace-link'; tooltipEl.appendChild(link); }
    link.href = traceFilterURL(label, extraParams);
    link.textContent = 'View traces \u2192';
  }

  // ── 8. Data accuracy checks ───────────────────────────────────────────────

  function checkDataAccuracy(data) {
    const issues = [];
    if (data.baseline && data.actual) {
      data.baseline.forEach((b, i) => {
        const a = data.actual[i];
        if (b !== null && a !== null && b < a)
          issues.push({ type: 'baseline_impossible', index: i, msg: `Baseline < Actual at index ${i}` });
      });
    }
    if (data.savings_pct) {
      data.savings_pct.forEach((s, i) => {
        if (s !== null && s > 100)
          issues.push({ type: 'savings_over_100', index: i, msg: `Savings ${s.toFixed(1)}% > 100%` });
      });
    }
    return issues;
  }

  function showAccuracyWarning(containerId, issues) {
    if (!issues || !issues.length) return;
    const container = document.getElementById(containerId);
    if (!container) return;
    let badge = container.querySelector('.accuracy-badge');
    if (!badge) { badge = document.createElement('span'); badge.className = 'accuracy-badge'; container.querySelector('.chart-title-row')?.appendChild(badge); }
    badge.textContent = '\u26a0 Data integrity';
    badge.title = issues.slice(0,3).map(i=>i.msg).join('\n');
    badge.setAttribute('role','img');
    badge.setAttribute('aria-label', 'Data integrity warning');
  }

  // ── 9. Export enhancements ────────────────────────────────────────────────

  function exportChartPNG(chart, title, meta) {
    if (!chart) return;
    meta = meta || {};
    const orig = chart.canvas;
    const exp = document.createElement('canvas');
    const H = 52;
    exp.width = orig.width; exp.height = orig.height + H;
    const ctx = exp.getContext('2d');
    ctx.fillStyle = '#0f172a'; ctx.fillRect(0, 0, exp.width, exp.height);
    ctx.fillStyle = '#f1f5f9'; ctx.font = 'bold 14px Inter,system-ui,sans-serif';
    ctx.fillText(title || 'TokenPak Chart', 12, 22);
    const parts = [];
    if (meta.dateRange) parts.push(meta.dateRange);
    if (meta.provider) parts.push('Provider: '+meta.provider);
    if (meta.model) parts.push('Model: '+meta.model);
    if (meta.pricingVersion) parts.push('Pricing: '+meta.pricingVersion);
    if (parts.length) {
      ctx.fillStyle = '#94a3b8'; ctx.font = '11px Inter,system-ui,sans-serif';
      ctx.fillText(parts.join(' \xb7 '), 12, 42);
    }
    ctx.drawImage(orig, 0, H);
    const a = document.createElement('a');
    a.download = 'tokenpak-'+(title||'chart').toLowerCase().replace(/\s+/g,'-')+'.png';
    a.href = exp.toDataURL('image/png'); a.click();
  }

  function exportChartCSV(chart, title, meta) {
    if (!chart || !chart.data) return;
    meta = meta || {};
    const { labels, datasets } = chart.data;
    const lines = [
      '# TokenPak Export: '+(title||'Chart'),
      meta.dateRange ? '# Date Range: '+meta.dateRange : null,
      meta.provider  ? '# Provider: '+meta.provider : null,
      meta.model     ? '# Model: '+meta.model : null,
      meta.pricingVersion ? '# Pricing Version: '+meta.pricingVersion : null,
      '# Exported: '+new Date().toISOString(),
      '',
      ['Date', ...datasets.map(d=>d.label||'Value')].join(','),
    ].filter(l => l !== null);
    (labels||[]).forEach((label, i) => {
      lines.push([label, ...datasets.map(d => { const v=d.data[i]; return v===null||v===undefined?'':v; })].join(','));
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.download = 'tokenpak-'+(title||'chart').toLowerCase().replace(/\s+/g,'-')+'.csv';
    a.href = URL.createObjectURL(blob); a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  // ── 10. External HTML legend ──────────────────────────────────────────────

  function renderHTMLLegend(chart, legendContainerId) {
    const container = document.getElementById(legendContainerId);
    if (!container || !chart) return;
    function rebuild() {
      container.innerHTML = '';
      chart.data.datasets.forEach((ds, i) => {
        const hidden = chart.getDatasetMeta(i).hidden;
        const btn = document.createElement('button');
        btn.className = 'chart-legend-item'+(hidden?' hidden':'');
        btn.setAttribute('aria-pressed', String(!hidden));
        btn.setAttribute('aria-label', 'Toggle '+escHtml(ds.label||''));
        btn.innerHTML = `<span class="legend-swatch" style="background:${ds.borderColor||ds.backgroundColor}"></span><span class="legend-label">${escHtml(ds.label||'')}</span>`;
        btn.addEventListener('click', () => {
          chart.getDatasetMeta(i).hidden = !chart.getDatasetMeta(i).hidden;
          chart.update(); rebuild();
        });
        btn.addEventListener('mouseenter', () => {
          chart.data.datasets.forEach((_, j) => { chart.getDatasetMeta(j).hidden = j !== i; });
          chart.update('none');
        });
        btn.addEventListener('mouseleave', () => {
          chart.data.datasets.forEach((_, j) => { chart.getDatasetMeta(j).hidden = false; });
          chart.update('none');
        });
        container.appendChild(btn);
      });
    }
    rebuild();
  }

  // ── 11. Native wheel zoom shim ────────────────────────────────────────────

  function enableNativeZoom(chart) {
    if (!chart?.canvas) return;
    chart.canvas.addEventListener('wheel', e => {
      e.preventDefault();
      const xScale = chart.scales.x;
      if (!xScale) return;
      const min = xScale.min ?? 0;
      const max = xScale.max ?? (chart.data.labels?.length - 1 ?? 100);
      const range = max - min;
      const delta = e.deltaY > 0 ? 0.1 : -0.1;
      const newRange = Math.max(3, range + range * delta);
      const center = (min + max) / 2;
      xScale.options.min = Math.max(0, Math.floor(center - newRange/2));
      xScale.options.max = Math.min((chart.data.labels?.length-1)||100, Math.ceil(center + newRange/2));
      chart.update('none');
      showZoomReset(chart);
    }, { passive: false });
  }

  function showZoomReset(chart) {
    const c = chart.canvas?.parentElement;
    if (!c) return;
    let btn = c.querySelector('.reset-zoom-btn');
    if (!btn) {
      btn = document.createElement('button'); btn.className = 'reset-zoom-btn'; btn.textContent = '\u21ba Reset zoom';
      btn.addEventListener('click', () => {
        const x = chart.scales.x;
        if (x) { x.options.min = undefined; x.options.max = undefined; }
        chart.update(); btn.style.display = 'none';
      });
      c.appendChild(btn);
    }
    btn.style.display = '';
  }

  // ── Drill to date ─────────────────────────────────────────────────────────

  function drillToDate(label) {
    if (!label) return;
    const url = new URL(window.location.href);
    if (label.match(/\d{4}-\d{2}-\d{2}/)) url.searchParams.set('date', label);
    window.location.href = url.toString();
  }

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── Public API ────────────────────────────────────────────────────────────

  window.TokenPakPolish = {
    attachAnnotations,
    detectCostSpikes, detectErrorSpikes, detectLatencySpikes,
    analyzeDataQuality, showInsufficientData, hideInsufficientData,
    showGranularityBadge, suggestGranularity, splitEstimatedData,
    checkDataAccuracy, showAccuracyWarning,
    showChartSkeleton, hideChartSkeleton,
    exportChartPNG, exportChartCSV,
    renderHTMLLegend,
    appendTooltipTraceLink, traceFilterURL,
    enableNativeZoom, drillToDate,
    applyGlobalDefaults,
  };

  function init() { registerPlugin(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
  document.addEventListener('htmx:afterSwap', () => setTimeout(registerPlugin, 100));

})();
