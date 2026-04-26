// SPDX-License-Identifier: Apache-2.0
//
// Intent Layer Phase 1.1 — dashboard-card fetch + render.
//
// Reads /api/intent/report?window=14d (the documented contract;
// see docs/reference/intent-dashboard.md). Updates nine card
// elements + three tables + the operator panel.
//
// Read-only. No prompt content; the API never emits raw prompts.
// All percentages are pre-computed server-side; this script just
// renders them.

async function fetchAndUpdateIntentDashboard() {
    let payload;
    try {
        const response = await fetch('/api/intent/report?window=14d');
        if (!response.ok) {
            return;  // Silent — the panel stays blank on transient failure.
        }
        payload = await response.json();
    } catch (err) {
        return;
    }
    if (!payload || !payload.cards) {
        return;
    }

    const cards = payload.cards;
    const panel = payload.operator_panel || {};
    const meta = payload.metadata || {};

    // Window badge (e.g. "— last 14d (observation-only)").
    const winBadge = document.getElementById('intent-window-badge');
    if (winBadge && meta.window_days != null) {
        winBadge.textContent = `— last ${meta.window_days}d (observation-only)`;
    }

    // Top stat cards.
    setText('intent-total-classified', cards.total_classified.value);
    setText('intent-avg-confidence', (cards.average_confidence.value || 0).toFixed(2));
    setText('intent-low-confidence', cards.low_confidence_count.value);
    setText('intent-headers-emitted', cards.tip_headers_emitted_vs_telemetry_only.tip_headers_emitted);
    setText('intent-telemetry-only', cards.tip_headers_emitted_vs_telemetry_only.telemetry_only);
    setText('intent-adapters-eligible', cards.adapters_eligible.count);
    setText('intent-adapters-blocking', cards.adapters_blocking.count);

    // Intent class distribution table.
    const distBody = document.getElementById('intent-class-distribution-body');
    if (distBody) {
        distBody.innerHTML = '';
        for (const item of cards.intent_class_distribution.items) {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${escapeHtml(item.intent_class)}</td>
                <td>${item.count}</td>
                <td>${item.pct.toFixed(1)}%</td>
                <td>${item.avg_confidence.toFixed(2)}</td>
            `;
            distBody.appendChild(row);
        }
    }

    // Top missing slots.
    const missingBody = document.getElementById('intent-top-missing-slots-body');
    if (missingBody) {
        missingBody.innerHTML = '';
        for (const item of cards.top_missing_slots.items) {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${escapeHtml(item.slot)}</td>
                <td>${item.count}</td>
                <td>${item.pct.toFixed(1)}%</td>
            `;
            missingBody.appendChild(row);
        }
    }

    // Top catch-all reasons.
    const catchAllBody = document.getElementById('intent-catch-all-body');
    if (catchAllBody) {
        catchAllBody.innerHTML = '';
        for (const item of cards.catch_all_reason_distribution.items) {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${escapeHtml(item.catch_all_reason)}</td>
                <td>${item.count}</td>
                <td>${item.pct.toFixed(1)}%</td>
            `;
            catchAllBody.appendChild(row);
        }
    }

    // Operator panel — recommended review areas.
    const ul = document.getElementById('intent-review-areas');
    if (ul) {
        ul.innerHTML = '';
        const areas = panel.recommended_review_areas || [];
        if (areas.length === 0) {
            const li = document.createElement('li');
            li.textContent = '(no flags raised by the heuristic — keep observing)';
            ul.appendChild(li);
        } else {
            for (const area of areas) {
                const li = document.createElement('li');
                li.textContent = area;
                ul.appendChild(li);
            }
        }
    }
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) {
        el.textContent = String(value ?? 0);
    }
}

function escapeHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
