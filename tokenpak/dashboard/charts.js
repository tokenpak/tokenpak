// Initialize and update charts

function updateCharts() {
    updateRequestRateChart();
    updateCompressionRatioChart();
    updateTokensSavedChart();
    updateTopTypesChart();
}

// Request Rate Time Series Chart
function updateRequestRateChart() {
    const ctx = document.getElementById('requestRateChart')?.getContext('2d');
    if (!ctx) return;
    
    // Generate 60 5-minute buckets for the last hour
    const now = Date.now();
    const buckets = [];
    const labels = [];
    
    for (let i = 59; i >= 0; i--) {
        const timestamp = now - (i * 5 * 60 * 1000);
        buckets.push({ timestamp, rate: 0 });
        
        const date = new Date(timestamp);
        const hours = date.getHours().toString().padStart(2, '0');
        const minutes = date.getMinutes().toString().padStart(2, '0');
        labels.push(`${hours}:${minutes}`);
    }
    
    // Calculate rates from request history
    for (let i = 0; i < metricsState.requestHistory.length - 1; i++) {
        const curr = metricsState.requestHistory[i];
        const next = metricsState.requestHistory[i + 1];
        const timeDiff = (next.timestamp - curr.timestamp) / 1000; // seconds
        const countDiff = next.count - curr.count;
        const rate = timeDiff > 0 ? countDiff / timeDiff : 0;
        
        // Find bucket for this timestamp
        const bucket = buckets.find(b => 
            b.timestamp <= next.timestamp && 
            next.timestamp <= b.timestamp + 5 * 60 * 1000
        );
        if (bucket) bucket.rate = rate;
    }
    
    const data = buckets.map(b => b.rate);
    
    if (requestRateChartInstance) {
        requestRateChartInstance.data.labels = labels;
        requestRateChartInstance.data.datasets[0].data = data;
        requestRateChartInstance.update('none');
    } else {
        requestRateChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Requests/sec',
                    data: data,
                    borderColor: '#2563eb',
                    backgroundColor: 'rgba(37, 99, 235, 0.1)',
                    borderWidth: 2,
                    tension: 0.4,
                    fill: true,
                    pointRadius: 0,
                    pointHoverRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Requests/sec' }
                    }
                }
            }
        });
    }
}

// Compression Ratio by Document Type (Bar Chart)
function updateCompressionRatioChart() {
    const ctx = document.getElementById('compressionRatioChart')?.getContext('2d');
    if (!ctx) return;
    
    const sorted = Object.entries(metricsState.documentTypes)
        .sort((a, b) => b[1].frequency - a[1].frequency)
        .slice(0, 10);
    
    const labels = sorted.map(([type]) => shortenLabel(type, 20));
    const data = sorted.map(([, stats]) => 
        stats.frequency > 0 ? (stats.ratioSum / stats.frequency * 100) : 0
    );
    
    const colors = generateColors(data.length);
    
    if (compressionRatioChartInstance) {
        compressionRatioChartInstance.data.labels = labels;
        compressionRatioChartInstance.data.datasets[0].data = data;
        compressionRatioChartInstance.data.datasets[0].backgroundColor = colors;
        compressionRatioChartInstance.update('none');
    } else {
        compressionRatioChartInstance = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Compression Ratio %',
                    data: data,
                    backgroundColor: colors,
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100,
                        title: { display: true, text: 'Compression Ratio %' }
                    }
                }
            }
        });
    }
}

// Cumulative Tokens Saved (Area Chart)
function updateTokensSavedChart() {
    const ctx = document.getElementById('tokensSavedChart')?.getContext('2d');
    if (!ctx) return;
    
    // Generate timeline of cumulative tokens saved
    const now = Date.now();
    const buckets = [];
    const labels = [];
    let cumulativeTokens = 0;
    
    for (let i = 59; i >= 0; i--) {
        const timestamp = now - (i * 5 * 60 * 1000);
        buckets.push({ timestamp, cumulative: 0 });
        
        const date = new Date(timestamp);
        const hours = date.getHours().toString().padStart(2, '0');
        const minutes = date.getMinutes().toString().padStart(2, '0');
        labels.push(`${hours}:${minutes}`);
    }
    
    // Approximate cumulative from current total
    const avgTokensPerBucket = metricsState.tokensSaved / Math.max(1, buckets.length);
    for (let i = 0; i < buckets.length; i++) {
        buckets[i].cumulative = Math.round(avgTokensPerBucket * i);
    }
    buckets[buckets.length - 1].cumulative = metricsState.tokensSaved;
    
    const data = buckets.map(b => b.cumulative);
    
    if (tokensSavedChartInstance) {
        tokensSavedChartInstance.data.labels = labels;
        tokensSavedChartInstance.data.datasets[0].data = data;
        tokensSavedChartInstance.update('none');
    } else {
        tokensSavedChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Cumulative Tokens Saved',
                    data: data,
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    borderWidth: 2,
                    tension: 0.4,
                    fill: true,
                    pointRadius: 0,
                    pointHoverRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Cumulative Tokens' }
                    }
                }
            }
        });
    }
}

// Top Document Types (Pie Chart)
function updateTopTypesChart() {
    const ctx = document.getElementById('topTypesChart')?.getContext('2d');
    if (!ctx) return;
    
    const sorted = Object.entries(metricsState.documentTypes)
        .sort((a, b) => b[1].frequency - a[1].frequency)
        .slice(0, 8);
    
    const labels = sorted.map(([type]) => shortenLabel(type, 15));
    const data = sorted.map(([, stats]) => stats.frequency);
    const colors = generateColors(data.length, true);
    
    if (topTypesChartInstance) {
        topTypesChartInstance.data.labels = labels;
        topTypesChartInstance.data.datasets[0].data = data;
        topTypesChartInstance.data.datasets[0].backgroundColor = colors;
        topTypesChartInstance.update('none');
    } else {
        topTypesChartInstance = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: data,
                    backgroundColor: colors,
                    borderColor: '#ffffff',
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: { padding: 15 }
                    }
                }
            }
        });
    }
}

// Utility functions

function shortenLabel(text, maxLen) {
    return text.length > maxLen ? text.substring(0, maxLen - 3) + '...' : text;
}

function generateColors(count, pastel = false) {
    const colors = [];
    const hues = [210, 150, 120, 30, 0, 280, 320];
    
    for (let i = 0; i < count; i++) {
        const hue = hues[i % hues.length];
        const saturation = pastel ? 60 : 80;
        const lightness = pastel ? 70 : 50;
        colors.push(`hsl(${hue}, ${saturation}%, ${lightness}%)`);
    }
    
    return colors;
}
