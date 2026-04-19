# TokenPak Dashboard — Motion Choreography & Transitions

## Overview
Complete motion design system for polished filter changes, drilldowns, and depth transitions. Makes users feel like they're "zooming into data" rather than switching pages.

## Quick Start

### HTML Attributes
```html
<!-- Animate number value -->
<div class="kpi-value" data-animate-number="true" data-value="$1,892" data-format="currency">
  $1,247
</div>

<!-- Filter that triggers animations -->
<select class="filter-item" data-affected-by-filter="section-id">
  <option>All</option>
</select>

<!-- Section affected by filter -->
<section data-affected-by-filter="section-id"><!-- content --></section>

<!-- Chart element for drilldown -->
<svg data-drilldown="true" class="chart-element"><!-- chart --></svg>

<!-- Drawer trigger -->
<button data-open-drawer="drawer-id">Open</button>
<aside id="drawer-id" class="drawer"><!-- content --></aside>
```

### JavaScript API
```javascript
// Number animations (auto-initialized)
initNumberAnimations();

// Filter changes (auto-triggered on change events)
animateFilterChange(element);

// Chart transitions
await transitionChartData(chartContainer, newData);

// Depth navigation
goToDepthLevel(2);
goBackDepthLevel();

// Drawer control
openDrawer('drawer-id');
closeDrawer('drawer-id');

// Mode switching
toggleMode('advanced');
```

## Acceptance Criteria Implementation

### ✅ 1. Number Value Transitions
- Smooth counting animation 300-500ms
- Easing: ease-out (fast start, gentle end)
- Supported formats: currency, percent, number
- Auto-triggered on `[data-animate-number="true"]` elements

### ✅ 2. Chart Transitions
- Fade out old data (150ms)
- Fade in new data (200ms)
- Smooth morph if same structure
- No layout jump

### ✅ 3. Filter Change Animation
- Subtle pulse/highlight on changed filter
- Affected sections fade + blur during load
- Skeleton pulse state
- Total transition <500ms

### ✅ 4. Drilldown "Zoom" Feel
- Clicked element brief highlight/pulse
- Dashboard "zooms" smoothly
- Breadcrumb appears with fade
- Feels like "going deeper"

### ✅ 5. Depth Level Transitions
- Slides in from right or expands
- Does NOT replace entire page
- Previous level visible (dimmed) or collapsible
- Back navigation: slides out

### ✅ 6. Drawer Transitions
- Slides in from right (300ms)
- Background dims smoothly
- Content fades in after slide (delayed)
- Close: slides out (250ms)

### ✅ 7. Hover Micro-Interactions
- Cards: subtle elevation on hover
- Charts: glow effect on elements
- Buttons: slight scale (1.02x)
- Duration: 150ms

### ✅ 8. Mode Switch Animation
- Sections slide in/out smoothly
- No jarring appearance
- Content reflows smoothly
- Duration: 300ms

### ✅ 9. Forbidden Patterns Avoided
- ✗ Bouncy/springy → All cubic-bezier easing
- ✗ Excessive scaling → Max scale(1.02)
- ✗ Animations >500ms → All ≤300ms
- ✗ Autoplay decorative → None (except loading)

### ✅ 10. Performance Targets
- Filter reaction <400ms perceived
- Chart update <500ms perceived
- All animations 60fps
- GPU-accelerated (transform + opacity)

## CSS Classes

### Duration Variables
```css
--duration-micro: 150ms;   /* Fast feedback */
--duration-short: 200ms;   /* Quick transitions */
--duration-base: 300ms;    /* Default */
--duration-long: 500ms;    /* Max allowed */
```

### Easing Variables
```css
--ease-out: cubic-bezier(.16, 1, .3, 1);  /* Primary */
--ease-in-out: cubic-bezier(.4, 0, .2, 1); /* Symmetric */
--ease-in: cubic-bezier(.4, 0, 1, 1);     /* Reverse */
```

### Animation Classes
- `.chart-fade-out` / `.chart-fade-in` — Chart transitions
- `.affected-section` / `.loading` / `.loaded` — Filter states
- `.filter-item.changed` — Filter pulse
- `.chart-element.clicked` — Drill highlight
- `.depth-level.entering` / `.exiting` — Depth slides
- `.drawer.closing` — Drawer close
- `.mode-section.hidden` / `.visible` — Mode toggle

## Performance

- All animations use GPU acceleration (`transform`, `opacity`)
- Respects `prefers-reduced-motion` OS setting
- No layout thrashing or forced reflows
- 60fps target on desktop devices

## Testing Checklist

- [x] Change a filter — smooth fade + loading state
- [x] KPI numbers count up/down (not instant)
- [x] Charts fade smoothly on data update
- [x] Drill into chart — feels like zooming
- [x] Drawer slides smoothly + background dims
- [x] Hover effects visible on cards/buttons
- [x] No janky transitions
- [x] 60fps performance
- [x] Reduced motion respected

## Documentation Files

- `motion.css` — All animation definitions
- `motion.js` — JavaScript choreography engine
- `MOTION-CHOREOGRAPHY.md` — This file
