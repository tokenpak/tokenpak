/**
 * TokenPak Dashboard — Onboarding Experience
 * First-visit detection, welcome modal (4 steps), guided spotlight tour.
 *
 * Public API (window.TPOnboarding):
 *   .start()          — launch from step 0
 *   .startTour()      — jump straight to guided tour
 *   .reset()          — clear localStorage, re-enable auto-show
 */

'use strict';

(function () {
  const LS_DONE    = 'tp-onboarding-completed';
  const LS_NO_SHOW = 'tp-onboarding-no-show';

  // ─── Tour steps: selector + title + body ──────────────────
  const TOUR_STEPS = [
    {
      selector: '.sidebar-logo',
      title: '⚡ KPI Cards',
      body: 'Your at-a-glance summary — total cost, tokens used, compression ratio, and savings — all updated in real time.',
    },
    {
      selector: '.nav',
      title: '📊 Views',
      body: 'Switch between FinOps (cost focus), Engineering (performance), Compare (A/B models), and Audit (detailed traces).',
    },
    {
      selector: '#tp-search',
      title: '🔍 Search & Filters',
      body: 'Filter by provider, model, agent, or status. Search across sessions by name or ID. Drill down to exactly what you need.',
    },
    {
      selector: '#tp-export-btn',
      title: '⬇ Export',
      body: 'Download your data as CSV or JSON for offline analysis, reporting, or sharing with your team.',
    },
    {
      selector: '#tp-help-btn',
      title: '❓ Help & Onboarding',
      body: 'Come back here anytime — re-launch this tour or reset the welcome flow from the Help menu.',
    },
  ];

  // ─── State ────────────────────────────────────────────────
  let currentStep  = 0;
  let dontShow     = false;
  let tourActive   = false;
  let tourStep     = 0;
  let tourOverlay  = null;
  let tourTooltip  = null;
  let tourHighlight = null;

  // ─── Build modal HTML ─────────────────────────────────────
  function buildModal() {
    const el = document.createElement('div');
    el.id = 'tp-onboard-backdrop';
    el.className = 'tp-onboard-backdrop';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-label', 'Welcome to TokenPak — onboarding');
    el.innerHTML = `
      <div class="tp-onboard-modal" id="tp-onboard-modal">

        <!-- Step dots + title -->
        <div class="tp-onboard-header" id="tp-ob-header"></div>

        <!-- Dynamic body -->
        <div class="tp-onboard-body" id="tp-ob-body"></div>

        <!-- Footer nav -->
        <div class="tp-onboard-footer">
          <div class="tp-onboard-footer-left">
            <button class="tp-onboard-skip" id="tp-ob-skip">Skip all</button>
            <label class="tp-onboard-dont-show">
              <input type="checkbox" id="tp-ob-no-show"> Don't show again
            </label>
          </div>
          <div class="tp-onboard-footer-right">
            <button class="tp-ob-btn tp-ob-btn-secondary" id="tp-ob-back" style="display:none">← Back</button>
            <button class="tp-ob-btn tp-ob-btn-primary" id="tp-ob-next">Next →</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(el);
    document.getElementById('tp-ob-skip').addEventListener('click', closeModal);
    document.getElementById('tp-ob-back').addEventListener('click', () => showStep(currentStep - 1));
    document.getElementById('tp-ob-next').addEventListener('click', () => {
      if (currentStep < STEPS.length - 1) {
        showStep(currentStep + 1);
      } else {
        finishModal();
      }
    });
    document.getElementById('tp-ob-no-show').addEventListener('change', e => {
      dontShow = e.target.checked;
    });
    // Close on backdrop click (not modal click)
    el.addEventListener('click', e => {
      if (e.target === el) closeModal();
    });
  }

  // ─── Step definitions ─────────────────────────────────────
  const STEPS = [
    {
      title: 'Welcome to TokenPak Telemetry',
      subtitle: 'Track your AI costs and compression savings',
      render() {
        return `
          <div class="tp-ob-welcome-bullets">
            <div class="tp-ob-bullet">
              <div class="tp-ob-bullet-icon">💸</div>
              <div class="tp-ob-bullet-text">
                <h4>Real cost visibility</h4>
                <p>See exactly what you're paying per request, per model, per agent — broken down to the token.</p>
              </div>
            </div>
            <div class="tp-ob-bullet">
              <div class="tp-ob-bullet-icon">⚡</div>
              <div class="tp-ob-bullet-text">
                <h4>Compression savings</h4>
                <p>TokenPak's QMD engine compresses prompts before they hit the API. Watch the savings add up in real time.</p>
              </div>
            </div>
            <div class="tp-ob-bullet">
              <div class="tp-ob-bullet-icon">📈</div>
              <div class="tp-ob-bullet-text">
                <h4>Engineering-grade telemetry</h4>
                <p>Latency, token ratios, compression rates, model comparisons — everything you need to optimize.</p>
              </div>
            </div>
          </div>`;
      },
      nextLabel: 'Start Tour →',
    },
    {
      title: 'How TokenPak Works',
      subtitle: 'Your prompts travel through a compression pipeline',
      render() {
        return `
          <div class="tp-ob-flow-diagram">
            <div class="tp-ob-flow-stage">
              <div class="tp-ob-flow-box">
                <span class="tp-ob-flow-box-label">📝 Raw Prompt</span>
                <span class="tp-ob-flow-box-value">1,200 tokens</span>
              </div>
              <div class="tp-ob-flow-annotation">What your app sends</div>
            </div>
            <div class="tp-ob-flow-arrow">→</div>
            <div class="tp-ob-flow-stage">
              <div class="tp-ob-flow-box tp-ob-flow-highlight">
                <span class="tp-ob-flow-box-label">⚡ QMD Engine</span>
                <span class="tp-ob-flow-box-value">Compression</span>
                <span class="tp-ob-flow-box-badge">-38%</span>
              </div>
              <div class="tp-ob-flow-annotation">TokenPak optimizes</div>
            </div>
            <div class="tp-ob-flow-arrow">→</div>
            <div class="tp-ob-flow-stage">
              <div class="tp-ob-flow-box">
                <span class="tp-ob-flow-box-label">🔌 Provider API</span>
                <span class="tp-ob-flow-box-value">744 tokens</span>
              </div>
              <div class="tp-ob-flow-annotation">What the AI sees</div>
            </div>
            <div class="tp-ob-flow-arrow">→</div>
            <div class="tp-ob-flow-stage">
              <div class="tp-ob-flow-box tp-ob-flow-savings">
                <span class="tp-ob-flow-box-label">✅ Response</span>
                <span class="tp-ob-flow-box-value" style="color:#10b981">$0.0023 saved</span>
              </div>
              <div class="tp-ob-flow-annotation">You pocket the diff</div>
            </div>
          </div>
          <p style="font-size:13px;color:#64748b;margin:4px 0 0">
            Every request flows through this pipeline. This dashboard shows you exactly what happened at each stage.
          </p>`;
      },
      nextLabel: 'Got it →',
    },
    {
      title: 'The Baseline Math',
      subtitle: 'Understanding what you saved vs. what you paid',
      render() {
        return `
          <div class="tp-ob-math-grid">
            <div class="tp-ob-math-card">
              <div class="amount">$0.0037</div>
              <div class="label">Baseline Cost</div>
              <div class="desc">What you would have paid without compression (1,200 tokens × rate)</div>
            </div>
            <div class="tp-ob-math-op">−</div>
            <div class="tp-ob-math-card positive">
              <div class="amount">$0.0023</div>
              <div class="label">Actual Cost</div>
              <div class="desc">What you actually paid (744 compressed tokens × rate)</div>
            </div>
          </div>
          <div class="tp-ob-math-formula">
            Baseline Cost = What you would have paid without compression<br>
            Actual Cost   = What you actually paid<br>
            <span>Savings = Baseline − Actual</span>
          </div>
          <p style="font-size:13px;color:#64748b;margin:14px 0 0">
            Across thousands of requests, these micro-savings compound fast. The dashboard shows you cumulative savings over any time range.
          </p>`;
      },
      nextLabel: 'Show me a trace →',
    },
    {
      title: 'A Typical Request Trace',
      subtitle: 'This is what one request looks like in the audit log',
      render() {
        return `
          <div class="tp-ob-trace-card">
            <div class="tp-ob-trace-header">
              <span class="tp-ob-trace-id">trace_8f3a2c1d</span>
              <span class="tp-ob-trace-model">claude-sonnet-4-6</span>
            </div>
            <div class="tp-ob-trace-fields">
              <div class="tp-ob-trace-field">
                <span class="key">Input Tokens</span>
                <span class="val">1,200</span>
              </div>
              <div class="tp-ob-trace-field">
                <span class="key">Compressed Tokens</span>
                <span class="val accent">744</span>
              </div>
              <div class="tp-ob-trace-field">
                <span class="key">Output Tokens</span>
                <span class="val">382</span>
              </div>
              <div class="tp-ob-trace-field">
                <span class="key">Compression Ratio</span>
                <span class="val positive">38%</span>
              </div>
              <div class="tp-ob-trace-field">
                <span class="key">Total Cost</span>
                <span class="val">$0.0014</span>
              </div>
              <div class="tp-ob-trace-field">
                <span class="key">Savings</span>
                <span class="val positive">$0.0023</span>
              </div>
            </div>
            <div class="tp-ob-trace-callout">
              💡 Every request in your Audit log has this breakdown. Click any row to see the full prompt, response, and compression details.
            </div>
          </div>`;
      },
      nextLabel: 'Take the Tour →',
      isFinal: true,
    },
  ];

  // ─── Render a step ────────────────────────────────────────
  function showStep(n) {
    currentStep = Math.max(0, Math.min(n, STEPS.length - 1));
    const step = STEPS[currentStep];

    // Dots
    const dots = STEPS.map((_, i) => {
      const cls = i < currentStep ? 'done' : i === currentStep ? 'active' : '';
      return `<div class="tp-onboard-dot ${cls}"></div>`;
    }).join('');

    document.getElementById('tp-ob-header').innerHTML = `
      <div class="tp-onboard-step-indicator">${dots}</div>
      <div class="tp-onboard-title">${step.title}</div>
      <div class="tp-onboard-subtitle">${step.subtitle}</div>
    `;
    document.getElementById('tp-ob-body').innerHTML = step.render();

    // Back button
    document.getElementById('tp-ob-back').style.display = currentStep > 0 ? '' : 'none';

    // Next label
    const nextBtn = document.getElementById('tp-ob-next');
    nextBtn.textContent = step.nextLabel || (currentStep < STEPS.length - 1 ? 'Next →' : 'Start Tour →');
  }

  function openModal() {
    const bd = document.getElementById('tp-onboard-backdrop');
    if (!bd) buildModal();
    showStep(0);
    document.getElementById('tp-onboard-backdrop').classList.add('active');
    document.getElementById('tp-ob-no-show').checked = false;
    dontShow = false;
    // Trap focus
    setTimeout(() => document.getElementById('tp-ob-next')?.focus(), 50);
  }

  function closeModal() {
    const bd = document.getElementById('tp-onboard-backdrop');
    if (bd) bd.classList.remove('active');
    if (dontShow) {
      localStorage.setItem(LS_NO_SHOW, '1');
      localStorage.setItem(LS_DONE, '1');
    }
  }

  function finishModal() {
    localStorage.setItem(LS_DONE, '1');
    if (dontShow) localStorage.setItem(LS_NO_SHOW, '1');
    closeModal();
    startTour();
  }

  // ─── Guided Tour ──────────────────────────────────────────
  function startTour() {
    if (tourActive) return;
    tourActive = true;
    tourStep = 0;

    // Overlay
    tourOverlay = document.createElement('div');
    tourOverlay.className = 'tp-tour-overlay';
    tourOverlay.addEventListener('click', closeTour);
    document.body.appendChild(tourOverlay);

    // Highlight box
    tourHighlight = document.createElement('div');
    tourHighlight.className = 'tp-tour-highlight-box';
    document.body.appendChild(tourHighlight);

    // Tooltip
    tourTooltip = document.createElement('div');
    tourTooltip.className = 'tp-tour-tooltip';
    document.body.appendChild(tourTooltip);

    renderTourStep();
  }

  function renderTourStep() {
    const step = TOUR_STEPS[tourStep];
    const target = document.querySelector(step.selector);

    if (!target) {
      // skip if element not in DOM
      if (tourStep < TOUR_STEPS.length - 1) { tourStep++; renderTourStep(); }
      else closeTour();
      return;
    }

    // Position highlight
    const rect = target.getBoundingClientRect();
    const pad = 6;
    Object.assign(tourHighlight.style, {
      top:    (rect.top - pad + window.scrollY) + 'px',
      left:   (rect.left - pad) + 'px',
      width:  (rect.width + pad * 2) + 'px',
      height: (rect.height + pad * 2) + 'px',
    });

    // Tooltip content
    const isLast = tourStep === TOUR_STEPS.length - 1;
    tourTooltip.innerHTML = `
      <div class="tp-tour-tooltip-title">${step.title}</div>
      <div class="tp-tour-tooltip-body">${step.body}</div>
      <div class="tp-tour-tooltip-footer">
        <span class="tp-tour-counter">${tourStep + 1} / ${TOUR_STEPS.length}</span>
        <div class="tp-tour-nav">
          <button class="tp-ob-btn tp-ob-btn-secondary" style="font-size:12px;padding:6px 12px" onclick="window.TPOnboarding._tourSkip()">Skip</button>
          <button class="tp-ob-btn tp-ob-btn-primary" style="font-size:12px;padding:6px 12px" onclick="window.TPOnboarding._tourNext()">
            ${isLast ? '✓ Done' : 'Next →'}
          </button>
        </div>
      </div>
    `;

    // Position tooltip: below target by default, flip if off-screen
    const tRect = target.getBoundingClientRect();
    const tipH = 140; // approx
    let tipTop = tRect.bottom + 12 + window.scrollY;
    let tipLeft = Math.max(10, tRect.left);
    if (tRect.bottom + tipH + 12 > window.innerHeight) {
      tipTop = tRect.top - tipH - 12 + window.scrollY;
    }
    if (tipLeft + 260 > window.innerWidth) {
      tipLeft = window.innerWidth - 270;
    }
    Object.assign(tourTooltip.style, {
      top:  tipTop + 'px',
      left: tipLeft + 'px',
    });
  }

  function closeTour() {
    tourActive = false;
    [tourOverlay, tourHighlight, tourTooltip].forEach(el => el?.remove());
    tourOverlay = tourHighlight = tourTooltip = null;
  }

  // ─── Help Menu ────────────────────────────────────────────
  function buildHelpButton() {
    // Find header-actions
    const ha = document.querySelector('.header-actions');
    if (!ha) return;

    const wrap = document.createElement('div');
    wrap.className = 'tp-help-dropdown';
    wrap.innerHTML = `
      <button class="tp-help-btn" id="tp-help-btn" aria-haspopup="true" aria-label="Help menu">? Help</button>
      <div class="tp-help-menu" id="tp-help-menu" role="menu">
        <button class="tp-help-menu-item" role="menuitem" onclick="window.TPOnboarding.start()">
          🎓 Show onboarding tour
        </button>
        <button class="tp-help-menu-item" role="menuitem" onclick="window.TPOnboarding.startTour()">
          👆 Guided UI tour
        </button>
        <button class="tp-help-menu-item" role="menuitem" onclick="window.TPOnboarding.reset();window.TPOnboarding.start()">
          🔄 Reset onboarding
        </button>
      </div>
    `;

    // Insert before the first child
    ha.insertBefore(wrap, ha.firstChild);

    // Toggle menu
    document.getElementById('tp-help-btn').addEventListener('click', e => {
      e.stopPropagation();
      document.getElementById('tp-help-menu').classList.toggle('open');
    });
    document.addEventListener('click', () => {
      document.getElementById('tp-help-menu')?.classList.remove('open');
    });
  }

  // ─── Init ─────────────────────────────────────────────────
  function init() {
    buildHelpButton();

    // Auto-show on first visit (unless suppressed)
    const done   = localStorage.getItem(LS_DONE);
    const noShow = localStorage.getItem(LS_NO_SHOW);
    if (!done && !noShow) {
      // Slight delay so page renders first
      setTimeout(openModal, 600);
    }
  }

  // ─── Public API ───────────────────────────────────────────
  window.TPOnboarding = {
    start()     { openModal(); },
    startTour() { startTour(); },
    reset()     {
      localStorage.removeItem(LS_DONE);
      localStorage.removeItem(LS_NO_SHOW);
    },
    // Internal (called from inline onclick in tour tooltip)
    _tourNext() {
      if (tourStep < TOUR_STEPS.length - 1) { tourStep++; renderTourStep(); }
      else closeTour();
    },
    _tourSkip() { closeTour(); },
  };

  // Kick off after DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
