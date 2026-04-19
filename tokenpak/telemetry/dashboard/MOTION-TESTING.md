# TokenPak Dashboard — Motion Choreography Testing Guide

## Overview
This document serves as a QA checklist for validating the motion choreography system. All animations should feel smooth, responsive, and intentional.

**Environment**: Modern browser (Chrome, Firefox, Safari) on desktop & mobile  
**Duration**: 5-10 minutes per test suite  
**Success Criteria**: Smooth 60fps animations, <500ms perceived latency

---

## 1. Number Value Transitions

### Test 1.1: KPI Number Counting
1. Navigate to any dashboard page with KPI cards (Cost, Units, etc.)
2. Change a filter (date, region, etc.)
3. **Verify**: Numbers smoothly count from old → new value
   - ✅ Animation duration: 300-500ms (should feel snappy, not instant)
   - ✅ Easing: Fast start, gentle end (cubic-bezier ease-out)
   - ✅ No jarring jumps or flashing
4. **Check mobile**: Animations complete slightly faster on mobile

### Test 1.2: Format Handling
1. Check KPIs with different formats:
   - Currency: $1,247 → $892 (with $ and comma formatting)
   - Percentage: 42.5% → 58.3%
   - Large numbers: 1,234,567 → 987,654
2. **Verify**: All formats animate smoothly without formatting delays

---

## 2. Chart Transitions

### Test 2.1: Filter Change → Chart Fade
1. Open a dashboard with charts (Cost over time, distribution, etc.)
2. Change a filter that affects the chart
3. **Verify sequence**:
   - ✅ Chart fades out (150ms, smooth)
   - ✅ Brief loading state (skeleton pulse)
   - ✅ Chart fades in (200ms, smooth)
   - ✅ Total perceived time: <500ms
4. No layout shift or reflow (chart container stays same size)

### Test 2.2: Hover on Chart Elements
1. Hover over chart bars, lines, or points
2. **Verify**: 
   - ✅ Element glows or brightens (glow effect visible)
   - ✅ Cursor changes to pointer
   - ✅ Animation is instant (<100ms)
3. Move mouse away → glow fades smoothly

---

## 3. Filter Change Animations

### Test 3.1: Filter Highlight
1. Open filter dropdown or chips (status, compression, etc.)
2. Change a filter value
3. **Verify**:
   - ✅ Filter control briefly highlights (subtle pulse, 300ms)
   - ✅ Background color changes (rgba accent color)
   - ✅ Highlight fades after 300ms
4. If multiple filters changed, only changed one highlights

### Test 3.2: Content Fade During Filter Change
1. Change a filter that affects multiple sections
2. **Verify**:
   - ✅ Affected KPI cards fade out (150ms)
   - ✅ Charts fade out (150ms)
   - ✅ Skeleton loading appears (shimmer animation)
   - ✅ Content fades back in (200ms) when ready
   - ✅ Total time: <500ms perceived

### Test 3.3: Filter Responsiveness
1. Change filters rapidly (fast clicks)
2. **Verify**: No animation stutter or queue-up
   - Cancel in-flight animations gracefully
   - Only most recent filter change animates

---

## 4. Drilldown Zoom Feel

### Test 4.1: Clicking Chart Element
1. Find a chart with drillable elements (bar chart, pie chart)
2. Click on a chart bar/segment
3. **Verify**:
   - ✅ Clicked element pulses (scales up briefly, 300ms)
   - ✅ Feels like "zooming in" not reloading
   - ✅ Breadcrumb appears above charts with animation
   - ✅ Dashboard content smoothly refocuses
4. Click another element → animation repeats

### Test 4.2: Breadcrumb Animation
1. After drilling down, breadcrumb should appear
2. **Verify**:
   - ✅ Breadcrumb slides in from left (smooth)
   - ✅ Breadcrumb text shows current level
   - ✅ Clicking breadcrumb level goes back smoothly
   - ✅ No jarring layout changes

---

## 5. Depth Level Transitions

### Test 5.1: Navigate Deeper (L1→L2→L3)
1. Dashboard shows Level 1 data
2. Click drilldown or navigate to Level 2
3. **Verify**:
   - ✅ Level 1 dims (opacity 0.5, still visible)
   - ✅ Level 2 slides in from right (300ms, smooth)
   - ✅ Level 2 becomes active/opaque
   - ✅ Breadcrumb updates to show "Level 2"
4. Navigate to Level 3 → Level 2 dims, Level 3 slides in

### Test 5.2: Navigate Back
1. On Level 3, click back button or Level 1 breadcrumb
2. **Verify**:
   - ✅ Level 3 slides out smoothly
   - ✅ Levels 1-2 return to full opacity
   - ✅ Breadcrumb reflects current level
   - ✅ Smooth, not jarring

### Test 5.3: Depth Context
1. Navigate deep (Level 3+)
2. **Verify**: Context is never lost
   - Previous levels remain visible (dimmed)
   - Easy to see hierarchy
   - Going back is always possible

---

## 6. Drawer Transitions

### Test 6.1: Opening Drawer
1. Click a button that opens a drawer (e.g., details, settings)
2. **Verify**:
   - ✅ Overlay dims smoothly (300ms, 0→0.4 opacity)
   - ✅ Drawer slides in from right (300ms, smooth)
   - ✅ Content inside drawer fades in (after slide completes, 200ms)
   - ✅ Can interact with drawer immediately

### Test 6.2: Closing Drawer
1. With drawer open, click close button or overlay
2. **Verify**:
   - ✅ Content fades out (200ms)
   - ✅ Drawer slides out right (250ms, smooth)
   - ✅ Overlay fades (250ms)
   - ✅ Drawer fully removed from DOM when animation done

### Test 6.3: Drawer Performance
1. Open/close drawer 5 times rapidly
2. **Verify**: No animation stutter, memory leaks, or orphaned overlays
3. All overlays should be cleaned up after close

---

## 7. Hover Micro-Interactions

### Test 7.1: Card Hover
1. Hover over a KPI card, chart container, or table row
2. **Verify**:
   - ✅ Card lifts slightly (translateY -2px)
   - ✅ Shadow increases (more depth)
   - ✅ Transition is instant (150ms)
   - ✅ No cursor change needed (content is already readable)
3. Move mouse away → card returns smoothly

### Test 7.2: Button Hover
1. Hover over any `.btn` or `.btn-primary`
2. **Verify**:
   - ✅ Button scales up (1.02x)
   - ✅ .btn-primary scales more (1.03x)
   - ✅ Transition smooth (150ms)
3. Move away → returns to 1.0x

### Test 7.3: Table Row Hover
1. Hover over a row in any data table
2. **Verify**:
   - ✅ Row lifts slightly (translateY -1px)
   - ✅ Slight background highlight (rgba accent)
   - ✅ Underline appears (inset shadow)
   - ✅ All smooth (150ms)

### Test 7.4: Filter Chip Hover
1. Hover over a filter chip
2. **Verify**:
   - ✅ Chip lifts (translateY -1px)
   - ✅ Shadow appears
   - ✅ Highlight on active chip is visible
3. Click chip → state changes with no janky transitions

---

## 8. Mode Switch Animation

### Test 8.1: Toggle Advanced Mode
1. Find "Advanced Mode" or similar toggle button
2. Click to show advanced sections
3. **Verify**:
   - ✅ Advanced sections expand smoothly (300ms)
   - ✅ Content height animates (max-height)
   - ✅ Opacity fades in (0→1)
   - ✅ No layout jump
4. Click again to hide
5. **Verify**:
   - ✅ Sections collapse smoothly (300ms)
   - ✅ Opacity fades out (1→0)
   - ✅ Sections removed from flow after close

### Test 8.2: Smooth Reflow
1. Toggle advanced mode on/off several times
2. **Verify**: No layout thrashing or reflow jank
3. Page remains responsive and smooth

---

## 9. Performance Targets

### Test 9.1: Frame Rate (60fps)
1. Open Dashboard Performance Monitor (Chrome DevTools → Rendering)
2. Trigger each animation:
   - Filter change
   - Chart fade
   - Drawer open/close
   - Drill into chart
3. **Verify**: All animations hit 60fps (no dropped frames)
   - No jank or stutter
   - Green bars in FPS meter
4. **On low-end device**: Test on older phone/tablet if possible
   - Animations should still be smooth (maybe 30-45fps acceptable)

### Test 9.2: Animation Duration <500ms
1. Time each animation:
   - Number count: 300-500ms ✓
   - Chart fade: 350ms total (150 + 200) ✓
   - Filter reaction: 350ms ✓
   - Drawer: 300ms in + 250ms out ✓
   - Depth slide: 300ms ✓
   - Mode switch: 300ms ✓
2. **Verify**: None exceed 500ms

### Test 9.3: GPU Acceleration
1. Open DevTools → Layers panel (if available)
2. Trigger animations
3. **Verify**: Animated elements are in separate layers
   - Stat values: content layer
   - Chart wrappers: opacity layer
   - Drawer: transform layer
   - No repaints, only composits

---

## 10. Accessibility & Preferences

### Test 10.1: Respects prefers-reduced-motion
1. Enable OS accessibility setting: "Reduce motion"
   - macOS: System Preferences → Accessibility → Display → Reduce motion
   - Windows: Settings → Ease of Access → Display → Show animations
2. Navigate dashboard
3. **Verify**:
   - ✅ All animations play but much faster (instant or ~50ms)
   - ✅ No bouncy/springy effects
   - ✅ Functionally identical, just instant

### Test 10.2: Keyboard Navigation
1. Use Tab to navigate filters, buttons
2. Use Enter/Space to activate buttons
3. **Verify**:
   - ✅ Animations still play on keyboard interaction
   - ✅ Focus states visible
   - ✅ No animation prevents interaction

### Test 10.3: Dark Mode
1. Toggle dark mode (if available)
2. **Verify**:
   - ✅ All animations visible in both modes
   - ✅ Colors contrast properly
   - ✅ Loading skeletons visible

---

## 11. Browser Compatibility

Test on each browser:

- [x] Chrome/Chromium (latest)
- [x] Firefox (latest)
- [x] Safari (latest)
- [x] Edge (latest)
- [x] Mobile Safari (iOS)
- [x] Chrome Mobile (Android)

**Verify**:
- ✅ All animations work
- ✅ No console errors
- ✅ Easing functions render correctly
- ✅ Transform operations are smooth

---

## 12. Edge Cases

### Test 12.1: Rapid Filter Changes
1. Click filter change 10+ times quickly
2. **Verify**:
   - No stacked animations
   - Most recent animation only
   - No orphaned elements
   - Memory stable

### Test 12.2: Data Empty State
1. Filter to empty result set
2. **Verify**:
   - Animation still plays (fade out/in)
   - Empty state message appears
   - Skeleton loading completes
   - No errors

### Test 12.3: Slow Network Simulation
1. DevTools → Network → Throttle to "Slow 4G"
2. Change filter
3. **Verify**:
   - Animation starts immediately (optimistic UI)
   - Skeleton loading visible
   - No timeout or error state
   - Animation completes when data arrives

### Test 12.4: Very Large Dataset
1. Load dashboard with 1000+ rows of data
2. Scroll + animate
3. **Verify**:
   - Animations still smooth
   - No jank from rendering
   - Virtual scrolling if used

---

## Submission Checklist

Before marking complete, ensure:

- [ ] All 10 acceptance criteria verified
- [ ] 60fps achieved on desktop
- [ ] <500ms total transition time maintained
- [ ] prefers-reduced-motion respected
- [ ] No console errors
- [ ] No memory leaks (check DevTools)
- [ ] Works on mobile (<500ms reactions OK)
- [ ] Tested on Chrome, Firefox, Safari
- [ ] Dark mode works
- [ ] Keyboard accessible
- [ ] All transitions feel intentional & polished

---

## QA Sign-Off

**Tested By**: [Sue or QA person]  
**Date**: [YYYY-MM-DD]  
**Result**: ✅ PASS / ❌ FAIL

**Notes**: (Any issues found, edge cases, observations)

---

## Developer Notes

### Key Files
- `motion-choreography.js` — Main choreography system
- `motion.js` — Number animations & transitions
- `motion-integration.js` — Filter/HTMX orchestration
- `motion-choreography.css` — All animation definitions

### Performance Tuning
- Use DevTools Rendering tab to check 60fps target
- Check FPS meter during filter changes
- Look for dropped frames on low-end devices
- Profile with Chrome DevTools Performance tab

### Debugging
- Check `window.MotionChoreography` in console
- Monitor `will-change` properties (should auto-cleanup)
- Watch for animation class stacking
- Verify HTMX hooks fire (`htmx:afterSwap`)

