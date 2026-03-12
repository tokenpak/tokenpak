// Global state for metrics
let metricsState = {
    totalEvents: 0,
    avgRatio: 0,
    tokensSaved: 0,
    requestRate: 0,
    latencyP50: 0,
    latencyP95: 0,
    uptime: 0,
    documentTypes: {},
    requestHistory: [],
    lastUpdate: null
};

// Chart instances (global)
let requestRateChartInstance = null;
let compressionRatioChartInstance = null;
let tokensSavedChartInstance = null;
let topTypesChartInstance = null;

// Fetch metrics from /metrics endpoint (Prometheus format)
async function fetchMetricsEndpoint() {
    try {
        const response = await fetch('/metrics');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.text();
    } catch (error) {
        console.error('Failed to fetch /metrics:', error);
        updateConnectionStatus(false);
        return null;
    }
}

// Fetch health data from /health endpoint
async function fetchHealthEndpoint() {
    try {
        const response = await fetch('/health');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
    } catch (error) {
        console.error('Failed to fetch /health:', error);
        return null;
    }
}

// Parse Prometheus format metrics
function parsePrometheusMetrics(text) {
    if (!text) return {};
    
    const metrics = {};
    const lines = text.split('\n');
    
    for (const line of lines) {
        if (line.startsWith('#') || !line.trim()) continue;
        
        const match = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)\{?([^}]*)\}?\s+([\d.e+\-]+)$/);
        if (!match) continue;
        
        const [, name, labels, value] = match;
        const numValue = parseFloat(value);
        
        if (!metrics[name]) metrics[name] = [];
        metrics[name].push({
            labels: parseLabels(labels),
            value: numValue
        });
    }
    
    return metrics;
}

// Parse Prometheus labels
function parseLabels(labelStr) {
    const labels = {};
    if (!labelStr) return labels;
    
    const matches = labelStr.matchAll(/([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"/g);
    for (const match of matches) {
        labels[match[1]] = match[2];
    }
    return labels;
}

// Extract metrics from parsed data
function extractMetrics(promMetrics, healthData) {
    const metrics = { ...metricsState };
    
    // Total compression events (count of compression_events_total)
    if (promMetrics.tokenpak_compression_events_total) {
        metrics.totalEvents = promMetrics.tokenpak_compression_events_total
            .reduce((sum, m) => sum + m.value, 0);
    }
    
    // Average compression ratio
    if (promMetrics.tokenpak_compression_ratio_avg) {
        const ratioSum = promMetrics.tokenpak_compression_ratio_avg
            .reduce((sum, m) => sum + m.value, 0);
        metrics.avgRatio = promMetrics.tokenpak_compression_ratio_avg.length > 0
            ? (ratioSum / promMetrics.tokenpak_compression_ratio_avg.length * 100)
            : 0;
    }
    
    // Tokens saved (sum of reduction)
    if (promMetrics.tokenpak_tokens_saved_total) {
        metrics.tokensSaved = promMetrics.tokenpak_tokens_saved_total
            .reduce((sum, m) => sum + m.value, 0);
    }
    
    // Compression latency percentiles
    if (promMetrics.tokenpak_compression_latency_ms) {
        const p50 = promMetrics.tokenpak_compression_latency_ms
            .find(m => m.labels.quantile === '0.5');
        const p95 = promMetrics.tokenpak_compression_latency_ms
            .find(m => m.labels.quantile === '0.95');
        
        if (p50) metrics.latencyP50 = Math.round(p50.value);
        if (p95) metrics.latencyP95 = Math.round(p95.value);
    }
    
    // Document types stats
    if (promMetrics.tokenpak_document_type_compression) {
        const docTypes = {};
        
        for (const m of promMetrics.tokenpak_document_type_compression) {
            const docType = m.labels.type || 'unknown';
            if (!docTypes[docType]) {
                docTypes[docType] = { frequency: 0, ratioSum: 0, tokensSaved: 0 };
            }
            docTypes[docType].frequency += 1;
            docTypes[docType].ratioSum += m.value;
        }
        
        // Add tokens saved per type
        if (promMetrics.tokenpak_document_type_tokens_saved) {
            for (const m of promMetrics.tokenpak_document_type_tokens_saved) {
                const docType = m.labels.type || 'unknown';
                if (docTypes[docType]) {
                    docTypes[docType].tokensSaved += m.value;
                }
            }
        }
        
        metrics.documentTypes = docTypes;
    }
    
    // Health data
    if (healthData) {
        if (healthData.uptime_seconds !== undefined) {
            metrics.uptime = healthData.uptime_seconds;
        }
        if (healthData.requests_total !== undefined) {
            // Calculate request rate from history
            const now = Date.now();
            if (!metricsState.lastUpdate) {
                metricsState.requestHistory = [{ timestamp: now, count: healthData.requests_total }];
            } else {
                const timeDiff = (now - metricsState.lastUpdate) / 1000; // seconds
                const countDiff = healthData.requests_total - 
                    (metricsState.requestHistory[metricsState.requestHistory.length - 1]?.count || 0);
                metrics.requestRate = timeDiff > 0 ? countDiff / timeDiff : 0;
                
                metricsState.requestHistory.push({ timestamp: now, count: healthData.requests_total });
                
                // Keep only last hour of history
                const oneHourAgo = now - 3600000;
                metricsState.requestHistory = metricsState.requestHistory
                    .filter(h => h.timestamp > oneHourAgo);
            }
        }
    }
    
    metrics.lastUpdate = new Date().toLocaleTimeString();
    return metrics;
}

// Fetch and update all metrics
async function fetchAndUpdateMetrics() {
    const [promText, healthData] = await Promise.all([
        fetchMetricsEndpoint(),
        fetchHealthEndpoint()
    ]);
    
    if (!promText || !healthData) {
        updateConnectionStatus(false);
        return;
    }
    
    const promMetrics = parsePrometheusMetrics(promText);
    metricsState = extractMetrics(promMetrics, healthData);
    
    updateConnectionStatus(true);
    updateStatsCards();
    updateCharts();
    updateDocumentTypesTable();
}

// Update connection status indicator
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
    
    if (metricsState.lastUpdate) {
        document.getElementById('last-update').textContent = `Last update: ${metricsState.lastUpdate}`;
    }
}

// Update stat cards with current metrics
function updateStatsCards() {
    document.getElementById('total-events').textContent = 
        metricsState.totalEvents.toLocaleString();
    document.getElementById('avg-ratio').textContent = 
        metricsState.avgRatio.toFixed(1) + '%';
    document.getElementById('tokens-saved').textContent = 
        metricsState.tokensSaved.toLocaleString();
    document.getElementById('request-rate').textContent = 
        metricsState.requestRate.toFixed(2);
    document.getElementById('latency-p50').textContent = 
        metricsState.latencyP50 + 'ms';
    document.getElementById('latency-p95').textContent = 
        metricsState.latencyP95 + 'ms';
    
    // Format uptime
    const hours = Math.floor(metricsState.uptime / 3600);
    const minutes = Math.floor((metricsState.uptime % 3600) / 60);
    document.getElementById('uptime').textContent = 
        hours + 'h ' + minutes + 'm';
    document.getElementById('health-status').textContent = 
        metricsState.uptime > 0 ? 'OK' : 'UNKNOWN';
}

// Update document types table
function updateDocumentTypesTable() {
    const tbody = document.getElementById('documentTypesBody');
    tbody.innerHTML = '';
    
    // Sort by frequency
    const sorted = Object.entries(metricsState.documentTypes)
        .sort((a, b) => b[1].frequency - a[1].frequency)
        .slice(0, 10);
    
    for (const [type, stats] of sorted) {
        const row = document.createElement('tr');
        const avgCompression = stats.frequency > 0 
            ? (stats.ratioSum / stats.frequency * 100)
            : 0;
        
        row.innerHTML = `
            <td>${escapeHtml(type)}</td>
            <td>${stats.frequency}</td>
            <td>${avgCompression.toFixed(1)}%</td>
            <td>${stats.tokensSaved.toLocaleString()}</td>
        `;
        tbody.appendChild(row);
    }
}

// Utility to escape HTML
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
