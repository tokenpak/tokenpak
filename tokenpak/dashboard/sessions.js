// SPDX-License-Identifier: Apache-2.0
//
// Phase 2 (CCG-13) — Top Sessions dashboard card.
//
// Reads /metrics/dashboard (JSON) and renders the `sessions` array as a
// sortable table. No prompt content is exposed — session IDs only.

function _escSess(str) {
    if (str == null) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

async function fetchAndUpdateSessionsDashboard() {
    let payload;
    try {
        const response = await fetch('/metrics/dashboard');
        if (!response.ok) {
            return;
        }
        payload = await response.json();
    } catch (err) {
        return;
    }

    const sessions = (payload && Array.isArray(payload.sessions)) ? payload.sessions : [];
    const tbody = document.getElementById('sessionsBody');
    if (!tbody) return;

    if (sessions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" style="opacity: 0.5;">No session data yet.</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    for (const s of sessions) {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td style="font-family: monospace; font-size: 0.85em;">${_escSess(s.session_id)}</td>
            <td>${s.request_count ?? 0}</td>
            <td>${(s.input_tokens ?? 0).toLocaleString()}</td>
            <td>${(s.output_tokens ?? 0).toLocaleString()}</td>
            <td>${(s.cache_read_input_tokens ?? 0).toLocaleString()}</td>
            <td>${(s.cache_creation_input_tokens ?? 0).toLocaleString()}</td>
            <td>$${(s.cost ?? 0).toFixed(4)}</td>
            <td>${s.latency_p50 ?? 0} ms</td>
            <td>${_escSess(s.platform)}</td>
        `;
        tbody.appendChild(row);
    }
}

window.fetchAndUpdateSessionsDashboard = fetchAndUpdateSessionsDashboard;
