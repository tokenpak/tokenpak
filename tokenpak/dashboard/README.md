# TokenPak Metrics Dashboard

Web-based UI for monitoring TokenPak proxy observability metrics in real-time.

## Overview

The Metrics Dashboard provides operators with a visual, human-readable view of TokenPak proxy performance and compression effectiveness. It fetches live metrics from the proxy's `/metrics` and `/health` endpoints and displays them in real-time charts and stats.

## Access

The dashboard is served at:

```
http://localhost:8766/dashboard
```

Or customize the port if running TokenPak on a different port:

```
http://<proxy-host>:<proxy-port>/dashboard
```

## Features

### Real-Time Metrics

- **Total Compression Events**: Count of all compression operations since proxy startup
- **Average Compression Ratio**: Percentage of reduction (e.g., 75% = 25% of original size)
- **Total Tokens Saved**: Cumulative token reduction across all requests
- **Request Rate**: Requests per second (calculated from rolling window)
- **Compression Latency**: P50 and P95 percentiles in milliseconds
- **Uptime**: How long the proxy has been running
- **Proxy Health**: Connection and responsiveness status

### Charts & Visualization

1. **Request Rate Time Series** (Line Chart)
   - Last 1 hour of data
   - 5-minute resolution buckets
   - Shows traffic patterns over time

2. **Compression Ratio by Document Type** (Bar Chart)
   - Top 10 document types
   - Compression effectiveness per type
   - Color-coded for easy identification

3. **Cumulative Tokens Saved** (Area Chart)
   - Running total of tokens saved
   - Linear interpolation of historical data
   - Shows cumulative benefit over time

4. **Top Document Types by Frequency** (Doughnut Chart)
   - Pie distribution of request types
   - Top 8 types displayed
   - Helpful for understanding workload composition

### Document Types Table

Detailed breakdown of top 10 document types:

| Column | Description |
|--------|-------------|
| Document Type | The media/format type (e.g., `application/json`, `text/plain`) |
| Frequency | Number of times this type was compressed |
| Avg Compression | Average compression ratio for this type |
| Tokens Saved | Total tokens saved across all requests of this type |

## Technical Details

### Data Sources

- **`/metrics`** (Prometheus format)
  - `tokenpak_compression_events_total`: Total compression events
  - `tokenpak_compression_ratio_avg`: Average ratio per type
  - `tokenpak_tokens_saved_total`: Cumulative tokens saved
  - `tokenpak_compression_latency_ms`: Latency percentiles
  - `tokenpak_document_type_compression`: Per-type metrics
  - `tokenpak_document_type_tokens_saved`: Per-type savings

- **`/health`** (JSON)
  - `uptime_seconds`: Proxy uptime
  - `requests_total`: Total requests processed
  - Connection status

### Auto-Refresh

The dashboard automatically fetches metrics every **5 seconds** and updates all charts and stats without requiring a manual refresh.

### Browser Compatibility

- Chrome/Edge (90+)
- Firefox (88+)
- Safari (14+)
- Mobile browsers (responsive design)

## File Structure

```
tokenpak/dashboard/
├── index.html          # Main HTML structure and layout
├── metrics.js          # Prometheus metrics parsing and state management
├── charts.js           # Chart.js initialization and updates
├── styles.css          # Professional styling and responsive design
├── README.md           # This file
└── __init__.py         # Python integration module
```

### index.html
- Contains the layout (header, stats cards, charts, table)
- Responsive grid system with Flexbox
- Auto-refresh timer (5 seconds)

### metrics.js
- Fetches `/metrics` and `/health` endpoints
- Parses Prometheus text format
- Maintains global state (metricsState)
- Updates stat cards and table

### charts.js
- Uses Chart.js (v4.4.1) from CDN
- Initializes and updates 4 charts:
  - Request rate (line)
  - Compression ratio (bar)
  - Tokens saved (area)
  - Top types (doughnut)
- Handles responsive resizing

### styles.css
- Mobile-first responsive design
- Professional blue/gray color scheme
- Grid layout for stat cards and charts
- Smooth animations and hover effects

## Customization

### Theme Colors

Edit `styles.css` to change colors:

```css
/* Header gradient */
header {
    background: linear-gradient(135deg, #2563eb 0%, #1e40af 100%);
}

/* Charts use generateColors() in charts.js */
const hues = [210, 150, 120, 30, 0, 280, 320];  // Blue, green, orange, red, purple
```

### Refresh Interval

Edit `index.html` to change the 5-second auto-refresh:

```javascript
setInterval(() => {
    fetchAndUpdateMetrics();
}, 5000);  // Change 5000 to desired milliseconds
```

### Chart Types

Modify `charts.js` to swap chart types:

```javascript
// Example: Change compression ratio from bar to horizontal bar
type: 'horizontalBar',  // 'line', 'bar', 'doughnut', 'pie', etc.
```

## Deployment

The dashboard is embedded in the TokenPak proxy:

1. No separate server or installation required
2. Served directly from `/dashboard` by the proxy
3. Client-side only — no backend processing
4. Works offline if metrics API is reachable

### Production Use

- Dashboard is read-only (no write operations)
- Safe for public/internal use
- Suitable for monitoring dashboards and status pages
- Can be embedded in iframes on other dashboards

## Troubleshooting

### Dashboard Won't Load

1. Verify TokenPak proxy is running: `curl http://localhost:8766/health`
2. Check CORS is enabled (proxy sends `Access-Control-Allow-Origin: *`)
3. Verify `/metrics` endpoint: `curl http://localhost:8766/metrics`
4. Check browser console for errors (F12 → Console tab)

### Charts Not Updating

1. Confirm metrics are being collected: `curl http://localhost:8766/metrics | head -10`
2. Check network tab (F12 → Network) for 200 responses from `/metrics` and `/health`
3. Verify browser has JavaScript enabled
4. Check proxy logs for errors

### Missing Data in Charts

- Charts populate as data arrives; may be empty on first load
- Request some data through the proxy to generate metrics
- Wait 10+ seconds for initial data to appear

## Future Enhancements

- Drill-down into request logs by type/time range
- Cache hit/miss statistics
- Provider-specific metrics (Anthropic, OpenAI, Google)
- Alert thresholds and notifications
- Export metrics as PNG/PDF
- Custom date range filtering
- WebSocket updates for real-time push

## License

Part of TokenPak. See main repository for license.
