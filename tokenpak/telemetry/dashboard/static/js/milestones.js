/**
 * TokenPak Dashboard — Savings Milestones UI
 *
 * Provides:
 *  - renderSavingsProgressionChart: Chart.js chart with cumulative + daily datasets
 *  - dismissMilestone: POST /milestones/{id}/acknowledge + localStorage dedup
 *  - filterSeenMilestones: hide already-ack'd toasts on load
 *  - HTMX afterSwap hook: re-init chart and filter toasts
 */
(function () {
  'use strict';

  const SEEN_KEY = 'tp_seen_milestones';
  let _chartInstance = null;

  // ── localStorage helpers ─────────────────────────────────────────────────

  function getSeenIds() {
    try { return JSON.parse(localStorage.getItem(SEEN_KEY) || '[]'); }
    catch (_) { return []; }
  }

  function markSeen(id) {
    const ids = getSeenIds();
    if (!ids.includes(id)) { ids.push(id); localStorage.setItem(SEEN_KEY, JSON.stringify(ids)); }
  }

  // ── Dismiss milestone ────────────────────────────────────────────────────

  function dismissMilestone(id, btn) {
    markSeen(id);
    const toast = btn ? btn.closest('.milestone-toast') : document.querySelector('[data-milestone-id="' + id + '"]');
    if (toast) {
      toast.style.transition = 'opacity 200ms ease, transform 200ms ease';
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(20px)';
      setTimeout(() => toast.remove(), 210);
    }
    // POST acknowledge to API
    fetch('/api/milestones/' + id + '/acknowledge', { method: 'POST' }).catch(() => {});
  }

  // ── Filter seen milestones on load ───────────────────────────────────────

  function filterSeenMilestones() {
    const seenIds = getSeenIds();
    document.querySelectorAll('.milestone-toast[data-milestone-id]').forEach(toast => {
      const id = parseInt(toast.getAttribute('data-milestone-id'), 10);
      if (seenIds.includes(id)) toast.remove();
    });
  }

  // ── Savings progression chart ────────────────────────────────────────────

  function renderSavingsProgressionChart(canvasId, data) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    // Destroy existing instance to avoid canvas reuse error
    if (_chartInstance) { _chartInstance.destroy(); _chartInstance = null; }

    // Handle empty state
    if (!data || data.length === 0) {
      const wrapper = canvas.closest('.chart-wrapper') || canvas.parentElement;
      if (wrapper) {
        const empty = document.createElement('p');
        empty.className = 'chart-empty-state';
        empty.textContent = 'No savings data yet. Start using TokenPak to see your savings grow.';
        wrapper.appendChild(empty);
      }
      return;
    }

    const labels = data.map(d => d.date || d.day || '');
    const daily = data.map(d => +(d.daily_savings || 0));
    const cumulative = data.map(d => +(d.cumulative_savings || 0));

    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    _chartInstance = new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'Daily Savings ($)',
            data: daily,
            backgroundColor: 'rgba(99,102,241,0.5)',
            borderColor: 'rgba(99,102,241,0.9)',
            borderWidth: 1,
            order: 2,
          },
          {
            label: 'Cumulative Savings ($)',
            data: cumulative,
            type: 'line',
            borderColor: 'rgba(16,185,129,1)',
            backgroundColor: 'rgba(16,185,129,0.1)',
            borderWidth: 2,
            pointRadius: 3,
            fill: true,
            tension: 0.3,
            order: 1,
          },
        ],
      },
      options: {
        responsive: true,
        animation: { duration: reduced ? 0 : 300, easing: 'easeOutCubic' },
        plugins: {
          legend: { position: 'top' },
          tooltip: { mode: 'index', intersect: false },
        },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' } },
        },
      },
    });
  }

  // ── HTMX hook ────────────────────────────────────────────────────────────

  document.addEventListener('htmx:afterSwap', function (evt) {
    const target = evt.detail && evt.detail.target;
    if (!target) return;
    // Re-init chart if canvas is in swapped content
    const canvas = target.querySelector('#savings-progression-chart') ||
                   document.getElementById('savings-progression-chart');
    if (canvas) {
      let payload = [];
      try { payload = JSON.parse(canvas.getAttribute('data-chart-payload') || '[]'); } catch (_) {}
      renderSavingsProgressionChart('savings-progression-chart', payload);
    }
    filterSeenMilestones();
  });

  // ── Init ─────────────────────────────────────────────────────────────────

  function init() {
    filterSeenMilestones();
    const canvas = document.getElementById('savings-progression-chart');
    if (canvas) {
      let payload = [];
      try { payload = JSON.parse(canvas.getAttribute('data-chart-payload') || '[]'); } catch (_) {}
      renderSavingsProgressionChart('savings-progression-chart', payload);
    }
  }

  // ── Public API ────────────────────────────────────────────────────────────

  window.TokenPakMilestones = { dismissMilestone, filterSeenMilestones, renderSavingsProgressionChart };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
