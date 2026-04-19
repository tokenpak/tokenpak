# TokenPak Dashboard — Web Component Embed

## Quick Start

```html
<!DOCTYPE html>
<html>
<head>
  <script src="/dashboard/static/embed/tokenpak-dashboard.js"></script>
</head>
<body>
  <tokenpak-dashboard 
    data-source="http://localhost:17888"
    view="finops"
    date-range="7d">
  </tokenpak-dashboard>
</body>
</html>
```

## Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `data-source` | URL | *required* | TokenPak collector API URL |
| `view` | string | `finops` | View: `finops`, `engineering`, `audit` |
| `theme` | string | `dark` | Theme: `dark`, `light`, `auto` |
| `date-range` | string | `7d` | Range: `1d`, `7d`, `30d`, `90d` |
| `provider` | string | — | Filter to provider (e.g. `anthropic`) |
| `model` | string | — | Filter to model (e.g. `claude-sonnet-4-5`) |
| `compact` | boolean | `false` | Compact mode (minimal chrome) |
| `auth-token` | string | — | API authentication token |

## JavaScript API

```javascript
const dashboard = document.querySelector('tokenpak-dashboard');

// Methods
dashboard.setFilters({ provider: 'anthropic', model: 'claude-sonnet-4-5' });
dashboard.refresh();
dashboard.setView('engineering');
const state = dashboard.getState();

// Events
dashboard.addEventListener('tp-ready', e => console.log(e.detail));
dashboard.addEventListener('tp-filter-change', e => console.log(e.detail));
dashboard.addEventListener('tp-trace-click', e => console.log(e.detail));
dashboard.addEventListener('tp-error', e => console.error(e.detail));
```

## Theme Customization

```css
tokenpak-dashboard {
  --tp-bg-primary: #0f172a;
  --tp-bg-card: #1e293b;
  --tp-text-primary: #e2e8f0;
  --tp-text-muted: #94a3b8;
  --tp-accent: #6366f1;
  --tp-positive: #22c55e;
  --tp-danger: #ef4444;
}
```

## iframe Fallback

```html
<iframe 
  src="http://localhost:17888/dashboard/finops?embed=1&days=7"
  width="100%" 
  height="600px" 
  frameborder="0">
</iframe>
```

## CORS Configuration

For cross-origin embeds, configure CORS in `server.py`:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yoursite.com"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
```

## Browser Support

- Chrome/Edge 79+
- Firefox 63+
- Safari 13+
- All modern browsers with Custom Elements v1 support

## Files

- `tokenpak-dashboard.js` — Web component (UMD bundle, ~4KB minified)
- `example.html` — Full integration example
- `README.md` — This file
