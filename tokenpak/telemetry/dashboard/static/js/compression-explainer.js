/**
 * TokenPak Dashboard — Compression Pipeline Explainer
 * 
 * Educational overlay for the token compression pipeline.
 * Explains Raw → QMD → TokenPak → Final stages with expandable details.
 */
'use strict';

(function () {

  // ─── Stage definitions ──────────────────────────────────────────────────

  const STAGES = {
    raw: {
      label: 'Raw Tokens',
      what: 'Original tokens from your application before any processing.',
      why: 'This is your baseline — what you would pay without compression.',
      benefit: 'Establishes the "would have cost" reference point.',
      color: '#94a3b8',
    },
    qmd: {
      label: 'QMD',
      what: 'Structural compression that removes redundant formatting.',
      why: 'Markdown and whitespace often inflate token counts unnecessarily.',
      benefit: 'Typically reduces tokens 10-20% with no semantic loss.',
      color: '#3b82f6',
    },
    tokenpak: {
      label: 'TokenPak',
      what: 'Semantic compression that preserves meaning while reducing tokens.',
      why: 'Context can often be expressed more efficiently.',
      benefit: 'Can achieve 30-50% reduction in high-redundancy content.',
      color: '#10b981',
    },
    final: {
      label: 'Final',
      what: 'Tokens actually sent to the provider API.',
      why: 'This is what you are billed for.',
      benefit: 'Savings = Raw − Final.',
      color: '#6366f1',
    },
  };

  // ─── Segment type glossary ─────────────────────────────────────────────

  const SEGMENTS = {
    system: 'System prompt and instructions that guide the model behavior.',
    user: 'User message content and queries.',
    assistant: 'Previous assistant responses included in context.',
    tool_schema: 'Function and tool definitions describing available tools.',
    tool_response: 'Results returned from tool/function calls.',
    retrieved_context: 'RAG-retrieved documents or knowledge base content.',
    memory: 'Persistent memory or conversation history.',
    compression_metadata: 'TokenPak compression overhead and metadata.',
    other: 'Unclassified or miscellaneous token segments.',
  };

  // ─── Render pipeline explainer ────────────────────────────────────────

  /**
   * Inject the explainer overlay into a container.
   * data: { raw, qmd, tokenpak, final, cost_raw, cost_final, savings }
   */
  function renderExplainer(containerId, data) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const { raw, qmd, tokenpak, final, cost_raw, cost_final, savings } = data;

    const stages = [
      { key: 'raw', count: raw, pct: 100 },
      { key: 'qmd', count: qmd, pct: raw > 0 ? Math.round(qmd / raw * 100) : 0 },
      { key: 'tokenpak', count: tokenpak, pct: raw > 0 ? Math.round(tokenpak / raw * 100) : 0 },
      { key: 'final', count: final, pct: raw > 0 ? Math.round(final / raw * 100) : 0 },
    ];

    const stagesHTML = stages.map((s, i) => {
      const cfg = STAGES[s.key];
      const arrow = i < stages.length - 1 ? '<span class="pipe-arrow">→</span>' : '';
      return `
        <div class="pipe-stage" data-stage="${s.key}">
          <div class="stage-label">${escHtml(cfg.label)}</div>
          <div class="stage-count">${(s.count || 0).toLocaleString()}</div>
          <div class="stage-pct">(${s.pct}%)</div>
          <button class="stage-learn-more" onclick="CompressionExplainer.toggleStageDetail('${s.key}')" 
                  aria-label="Learn more about ${cfg.label}" aria-expanded="false">
            ℹ Learn More
          </button>
          <div class="stage-detail" id="stage-detail-${s.key}" style="display:none;">
            <div class="detail-row"><strong>What:</strong> ${escHtml(cfg.what)}</div>
            <div class="detail-row"><strong>Why:</strong> ${escHtml(cfg.why)}</div>
            <div class="detail-row"><strong>Benefit:</strong> ${escHtml(cfg.benefit)}</div>
          </div>
        </div>${arrow}`;
    }).join('');

    const savingsPct = cost_raw > 0 ? Math.round((cost_raw - cost_final) / cost_raw * 100) : 0;

    container.innerHTML = `
      <div class="compression-explainer">
        <div class="explainer-header">
          <h3 class="explainer-title">Compression Pipeline</h3>
          <div class="explainer-actions">
            <button class="explainer-btn" onclick="CompressionExplainer.toggleHowItWorks()" aria-expanded="false">
              📚 How It Works
            </button>
          </div>
        </div>

        <div class="pipeline-flow">
          ${stagesHTML}
        </div>

        <div class="cost-impact">
          <div class="cost-row">
            <span class="cost-label">Without compression:</span>
            <span class="cost-value">$${(cost_raw || 0).toFixed(3)}</span>
          </div>
          <div class="cost-row">
            <span class="cost-label">With compression:</span>
            <span class="cost-value cost-savings">$${(cost_final || 0).toFixed(3)}</span>
          </div>
          <div class="cost-row cost-total">
            <span class="cost-label">You saved:</span>
            <span class="cost-value cost-highlight">$${(savings || 0).toFixed(3)} (${savingsPct}%)</span>
          </div>
        </div>

        <div class="how-it-works-panel" id="how-it-works-panel" style="display:none;">
          <h4>How TokenPak Compression Works</h4>
          <p>TokenPak uses a multi-stage compression pipeline to reduce token counts while preserving semantic meaning:</p>
          <ol>
            <li><strong>QMD (Quantized Markdown)</strong> removes redundant formatting and whitespace.</li>
            <li><strong>TokenPak</strong> applies semantic compression using sub-block slicing, template stripping, and context deduplication.</li>
            <li>The result is fewer tokens sent to the API, lowering costs without affecting response quality.</li>
          </ol>
          <p><strong>FAQ: Does compression affect response quality?</strong><br>
          No. TokenPak is designed for semantic preservation — the meaning is retained even as tokens are reduced.</p>
        </div>
      </div>`;
  }

  // ─── Toggle stage detail ──────────────────────────────────────────────

  function toggleStageDetail(stageKey) {
    const detail = document.getElementById(`stage-detail-${stageKey}`);
    const btn = detail?.previousElementSibling;
    if (!detail) return;

    const isOpen = detail.style.display !== 'none';
    detail.style.display = isOpen ? 'none' : 'block';
    if (btn) btn.setAttribute('aria-expanded', String(!isOpen));
    if (window.a11yAnnounce) window.a11yAnnounce(`${STAGES[stageKey].label} detail ${isOpen ? 'collapsed' : 'expanded'}`);
  }

  // ─── Toggle "How It Works" panel ─────────────────────────────────────

  function toggleHowItWorks() {
    const panel = document.getElementById('how-it-works-panel');
    const btn = document.querySelector('.explainer-btn');
    if (!panel) return;

    const isOpen = panel.style.display !== 'none';
    panel.style.display = isOpen ? 'none' : 'block';
    if (btn) btn.setAttribute('aria-expanded', String(!isOpen));
  }

  // ─── Render segment glossary ──────────────────────────────────────────

  function renderSegmentGlossary(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const rows = Object.entries(SEGMENTS).map(([key, def]) => `
      <tr>
        <td class="seg-key"><span class="seg-badge" data-segment="${key}">${escHtml(key)}</span></td>
        <td class="seg-def">${escHtml(def)}</td>
      </tr>`).join('');

    container.innerHTML = `
      <div class="segment-glossary">
        <h4>Token Segment Types</h4>
        <table class="glossary-table">
          <thead>
            <tr><th>Segment</th><th>Definition</th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  // ─── Add segment tooltips ─────────────────────────────────────────────

  function addSegmentTooltips() {
    document.querySelectorAll('[data-segment]').forEach(el => {
      const seg = el.dataset.segment;
      const def = SEGMENTS[seg];
      if (def && !el.title) {
        el.title = def;
        el.setAttribute('aria-label', `${seg}: ${def}`);
      }
    });
  }

  // ─── Init ─────────────────────────────────────────────────────────────

  function init() {
    addSegmentTooltips();
    // Re-run on HTMX swaps
    document.addEventListener('htmx:afterSwap', addSegmentTooltips);
  }

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ─── Public API ───────────────────────────────────────────────────────

  window.CompressionExplainer = {
    renderExplainer,
    renderSegmentGlossary,
    toggleStageDetail,
    toggleHowItWorks,
    addSegmentTooltips,
    STAGES,
    SEGMENTS,
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

})();
