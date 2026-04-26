// SPDX-License-Identifier: Apache-2.0
//
// Intent Policy panel (Phase 2.2) — DRY-RUN / PREVIEW ONLY.
//
// Reads /api/intent/policy-report?window=14d. Renders eight cards
// across stat-cards + tables. The window badge in index.html
// already labels this as preview-only; this script keeps that
// label in sync with the metadata.preview_label field.

async function fetchAndUpdateIntentPolicyDashboard() {
    let payload;
    try {
        const response = await fetch('/api/intent/policy-report?window=14d');
        if (!response.ok) return;
        payload = await response.json();
    } catch (err) {
        return;
    }
    if (!payload || !payload.cards) return;

    const cards = payload.cards;
    const panel = payload.operator_panel || {};
    const meta = payload.metadata || {};

    // Window badge — keeps the dry-run label visible.
    const winBadge = document.getElementById('intent-policy-window-badge');
    if (winBadge && meta.window_days != null) {
        const label = meta.preview_label || 'DRY-RUN / PREVIEW ONLY';
        winBadge.textContent = `— last ${meta.window_days}d (${label})`;
    }

    setText('intent-policy-total', cards.total_dry_run_decisions.value);
    setText('intent-policy-budget-risk', cards.budget_risk_flags.value);

    renderActionTable(cards.top_recommended_actions.items, 'intent-policy-actions-body');
    renderSafetyTable(cards.top_safety_flags.items, 'intent-policy-safety-body');
    renderProfileTable(
        cards.suggested_compression_profiles.items,
        'intent-policy-compression-body',
        'compression_profile',
    );
    renderProfileTable(
        cards.suggested_cache_policies.items,
        'intent-policy-cache-body',
        'cache_strategy',
    );
    renderProfileTable(
        cards.suggested_delivery_policies.items,
        'intent-policy-delivery-body',
        'delivery_strategy',
    );
    renderProfileTable(
        cards.auto_routing_blocked_reasons.items,
        'intent-policy-blocked-body',
        'decision_reason',
    );

    const ul = document.getElementById('intent-policy-review-areas');
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

function renderActionTable(items, bodyId) {
    const body = document.getElementById(bodyId);
    if (!body) return;
    body.innerHTML = '';
    for (const item of items) {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${escapeHtml(item.action)}</td>
            <td>${item.count}</td>
            <td>${(item.pct ?? 0).toFixed(1)}%</td>
        `;
        body.appendChild(row);
    }
}

function renderSafetyTable(items, bodyId) {
    const body = document.getElementById(bodyId);
    if (!body) return;
    body.innerHTML = '';
    for (const item of items) {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${escapeHtml(item.safety_flag)}</td>
            <td>${item.count}</td>
            <td>${(item.pct ?? 0).toFixed(1)}%</td>
        `;
        body.appendChild(row);
    }
}

function renderProfileTable(items, bodyId, key) {
    const body = document.getElementById(bodyId);
    if (!body) return;
    body.innerHTML = '';
    for (const item of items) {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${escapeHtml(item[key] ?? '')}</td>
            <td>${item.count}</td>
        `;
        body.appendChild(row);
    }
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = String(value ?? 0);
}

function escapeHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
