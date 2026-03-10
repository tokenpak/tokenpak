# Repliflow Design System & Tokens (v1)

## 1) Color Palette

Base families: **primary, secondary, accent, success, danger, neutral**.
Each family includes 50-900 steps in `tokens.json`.

### Safe text combinations (WCAG AA)

| Foreground | Background | Ratio | AA Normal Text |
|---|---|---:|---|
| `#0f172a` (neutral-900) | `#f8fafc` (neutral-50) | 17.06:1 | ✅ |
| `#ffffff` | `#2f63f5` (primary-500) | 4.97:1 | ✅ |
| `#ffffff` | `#2149d6` (primary-600) | 7.03:1 | ✅ |
| `#ffffff` | `#199f96` (secondary-500) | 3.26:1 | ⚠️ Large text only |
| `#0f172a` | `#d9e6ff` (primary-100) | 14.21:1 | ✅ |
| `#0f172a` | `#cff4f2` (secondary-100) | 15.20:1 | ✅ |

Rules:
- Body text defaults: neutral-900 on neutral-50/white
- Interactive primary buttons: white on primary-500+
- Do not use white text on secondary-500 for normal body-size text

## 2) Typography

### Font stacks
- Display: `Sora, Manrope, Inter, sans-serif`
- Body: `Inter, Manrope, Segoe UI, sans-serif`
- Mono: `JetBrains Mono, Fira Code, SFMono-Regular, monospace`

### Type scale (tokenized)
`xs, sm, base, lg, xl, 2xl, 3xl, 4xl, 5xl, 6xl`

Weights:
- 300, 400, 500, 600, 700

Pairing:
- Hero: `display 5xl/6xl + body lg`
- Section heading: `display 2xl/3xl + body base`
- Utility text: `body sm/xs`

## 3) Spacing & Layout

Spacing scale: `0, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128px`
Mapped to `--space-1` … `--space-12`.

Breakpoints:
- mobile: 320px
- tablet: 768px
- desktop: 1024px
- wide: 1280px+

Grid:
- 12-column responsive grid
- CSS: `.grid-12`
- SCSS mixin: `@mixin grid-12`

## 4) Component Anatomy

### Buttons
- Sizes: sm, md, lg
- Variants: primary, secondary, tertiary
- States: default, hover (0.92), active (0.84), disabled (0.5)

### Input/Form
- Sizes: sm, md, lg
- Border: default neutral-300
- Focus: primary-500 ring
- Error: danger-600 border

### Card
- Radius: xl (16px)
- Padding: 24px
- Shadow: lg

## 5) Visual Effects

- Shadows: sm, md, lg, xl, 2xl
- Radius: 4, 8, 12, 16, 20, 24
- Opacity: 10%, 25%, 50%, 75%, 90%
- Motion:
  - fast: 150ms
  - normal: 300ms
  - slow: 600ms
  - easings: in, out, in-out

## 6) Accessibility Notes

- Contrast table included above
- Focus style always visible (`outline-offset: 2px`)
- Error states use color + border weight change
- Respect reduced motion in implementation phase:

```css
@media (prefers-reduced-motion: reduce) {
  * { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
```

## 7) Exports

- `tokens.json` — DTCG-compatible token object
- `tokens.css` — CSS custom properties + component primitives
- `tokens.scss` — SCSS maps + responsive/component mixins
- `figma-export.fig` — handoff placeholder + import notes

## 8) Screenshot coverage

Expected captures:
- `screenshots/color-palette.png`
- `screenshots/typography.png`
- `screenshots/components.png`

Current status: placeholders prepared; attach final exported images from Figma/Storybook in next pass.
