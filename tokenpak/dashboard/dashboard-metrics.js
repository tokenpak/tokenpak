/**
 * TokenPak Metrics Dashboard v2 — Real-Time Performance Visualization
 * Consumes /metrics/dashboard JSON endpoint with 8 key metrics
 */

let dashboardState = {
    lastUpdate: null,
    metrics: {},
    history: [],
    maxHistorySize: 60  // Keep 60 samples (5min @ 5sec intervals)
};

/**
 * Fetch dashboard metrics from /metrics/dashboard endpoint
 */
async function fetchDashboardMetrics() {
    try {
        const response = await fetch('/metrics/dashboard');
        if (!response.ok) {
            console.error(`HTTP ${response.status}`);
            updateConnectionStatus(false);
            return null;
        }
        const data = await response.json();
        updateConnectionStatus(true);
        return data;
    } catch (error) {
        console.error('Failed to fetch /metrics/dashboard:', error);
        updateConnectionStatus(false);
        return null;
    }
}

/**
 * Update connection status indicator
 */
function updateConnectionStatus(isConnected) {
    const dot = document.getElementById('connection-status');
    const text = document.getElementById('status-text');

    if (isConnected) {
        dot.classList.remove('status-loading', 'status-error');
        dot.classList.add('status-ok');
        text.textContent = 'Connected';
    } else {
        dot.classList.remove('status-ok');
        dot.classList.add('status-error');
        text.textContent = 'Disconnected';
    }

    if (dashboardState.lastUpdate) {
        document.getElementById('last-update').textContent = `Last update: ${dashboardState.lastUpdate}`;
    }
}

/**
 * Format large numbers for display
 */
function formatNumber(num) {
    if (num >= 1e9) return (num / 1e9).toFixed(2) + 'B';
    if (num >= 1e6) return (num / 1e6).toFixed(2) + 'M';
    if (num >= 1e3) return (num / 1e3).toFixed(2) + 'K';
    return num.toFixed(0);
}

/**
 * Format duration (seconds to HH:MM:SS)
 */
function formatDuration(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}

/**
 * Format percentage
 */
function formatPercent(ratio) {
    return (ratio * 100).toFixed(2) + '%';
}

/**
 * Update all dashboard metric cards
 */
function updateDashboard(metrics) {
    dashboardState.metrics = metrics;
    dashboardState.lastUpdate = new Date().toLocaleTimeString();
    dashboardState.history.push({
        timestamp: Date.now(),
        metrics: JSON.parse(JSON.stringify(metrics))
    });

    // Trim history
    if (dashboardState.history.length > dashboardState.maxHistorySize) {
        dashboardState.history = dashboardState.history.slice(-dashboardState.maxHistorySize);
    }

    // Update all metric displays
    updateMetric1(metrics);      // Request count + throughput
    updateMetric2(metrics);      // Latency histogram
    updateMetric3(metrics);      // Model distribution
    updateMetric4(metrics);      // Routing decisions
    updateMetric5(metrics);      // Cache hit ratio
    updateMetric6(metrics);      // Error rate
    updateMetric7(metrics);      // Streaming requests
    updateMetric8(metrics);      // 24h window
    updateModelsTable(metrics);  // Model breakdown table
}

/**
 * KEY METRIC 1: Request Count + Throughput
 */
function updateMetric1(metrics) {
    const requests = metrics.requests || {};
    document.getElementById('metric1-requests').textContent =
        formatNumber(requests.total || 0);
    document.getElementById('metric1-throughput').textContent =
        (requests.throughput_req_per_sec || 0).toFixed(3);
}

/**
 * KEY METRIC 2: Latency Histogram (p50, p95, p99)
 */
function updateMetric2(metrics) {
    const latency = metrics.latency || {};
    document.getElementById('metric2-p50').textContent =
        (latency.p50_ms || 0).toFixed(0) + 'ms';
    document.getElementById('metric2-p95').textContent =
        (latency.p95_ms || 0).toFixed(0) + 'ms';
    document.getElementById('metric2-p99').textContent =
        (latency.p99_ms || 0).toFixed(0) + 'ms';
    document.getElementById('metric2-avg').textContent =
        (latency.avg_ms || 0).toFixed(0) + 'ms';
}

/**
 * KEY METRIC 3: Model Provider Distribution
 */
function updateMetric3(metrics) {
    document.getElementById('metric3-model-count').textContent =
        metrics.model_count || 0;

    // Find top cost model
    let topCost = 0;
    if (metrics.models) {
        for (const [model, data] of Object.entries(metrics.models)) {
            if (data.cost > topCost) {
                topCost = data.cost;
            }
        }
    }
    document.getElementById('metric3-top-cost').textContent =
        '$' + topCost.toFixed(2);
}

/**
 * KEY METRIC 4: Routing Decisions
 */
function updateMetric4(metrics) {
    const routing = metrics.routing || {};
    document.getElementById('metric4-routing').textContent =
        formatPercent(routing.smart_routing_hit_rate || 0);
}

/**
 * KEY METRIC 5: Cache Hit Ratio
 */
function updateMetric5(metrics) {
    const cache = metrics.cache || {};
    document.getElementById('metric5-cache-ratio').textContent =
        formatPercent(cache.hit_ratio || 0);
    document.getElementById('metric5-cache-read').textContent =
        formatNumber(cache.read_tokens || 0);
}

/**
 * KEY METRIC 6: Error Rate + Top Failures
 */
function updateMetric6(metrics) {
    const errors = metrics.errors || {};
    document.getElementById('metric6-error-rate').textContent =
        (errors.error_rate * 100).toFixed(2) + '%';
    document.getElementById('metric6-error-count').textContent =
        errors.error_count || 0;

    // Top failure type
    let topFailure = 'None';
    if (errors.top_failures && Object.keys(errors.top_failures).length > 0) {
        const topCode = Object.keys(errors.top_failures)[0];
        const topCount = errors.top_failures[topCode];
        topFailure = `${topCode} (${topCount}x)`;
    }
    document.getElementById('metric6-top-failure').textContent = topFailure;
}

/**
 * KEY METRIC 7: Streaming Request Count
 */
function updateMetric7(metrics) {
    const streaming = metrics.streaming || {};
    document.getElementById('metric7-streaming').textContent =
        streaming.count || 0;
    document.getElementById('metric7-uptime').textContent =
        formatDuration(metrics.uptime_seconds || 0);
}

/**
 * KEY METRIC 8: 24-Hour Rolling Window
 */
function updateMetric8(metrics) {
    const window24h = metrics.window_24h || {};
    document.getElementById('metric8-input').textContent =
        formatNumber(window24h.input_tokens || 0);
    document.getElementById('metric8-output').textContent =
        formatNumber(window24h.output_tokens || 0);
    document.getElementById('metric8-cost').textContent =
        '$' + (window24h.total_cost || 0).toFixed(2);
    document.getElementById('metric8-protected').textContent =
        formatNumber(window24h.protected_tokens || 0);
}

/**
 * Update models breakdown table
 */
function updateModelsTable(metrics) {
    const tbody = document.getElementById('modelsBody');
    if (!tbody) return;  // Element might not exist in simplified view

    tbody.innerHTML = '';
    const models = metrics.models || {};

    // Sort by cost (descending)
    const sorted = Object.entries(models)
        .sort((a, b) => (b[1].cost || 0) - (a[1].cost || 0));

    for (const [modelName, data] of sorted) {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td><strong>${escapeHtml(modelName)}</strong></td>
            <td>${data.requests || 0}</td>
            <td>${formatNumber(data.input_tokens || 0)}</td>
            <td>$${(data.cost || 0).toFixed(2)}</td>
        `;
        tbody.appendChild(row);
    }
}

/**
 * Escape HTML special characters
 */
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
}

/**
 * Fetch and update metrics
 */
async function fetchAndUpdateMetrics() {
    const metrics = await fetchDashboardMetrics();
    if (metrics) {
        updateDashboard(metrics);
    }
}

// Auto-refresh every 5 seconds
setInterval(() => {
    fetchAndUpdateMetrics();
}, 5000);

// Initial load
window.addEventListener('DOMContentLoaded', () => {
    fetchAndUpdateMetrics();
});

// Fallback if DOMContentLoaded already fired
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', fetchAndUpdateMetrics);
} else {
    fetchAndUpdateMetrics();
}
