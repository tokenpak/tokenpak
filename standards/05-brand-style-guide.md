---
title: TokenPak Brand Style Guide
type: standard
status: draft
depends_on: [00-product-constitution.md]
---

# TokenPak Brand Style Guide

How TokenPak looks and sounds across every surface: CLI, dashboard, docs, README, demos, social posts, slides.

---

## 1. Name

- The product is **TokenPak**. One word. Capital T, capital P. Always written as one word.
- Never "Token Pak", "Token-Pak", "tokenpak" in prose, or "TOKENPAK" except as a bash-prefixed env var.
- In code and CLI: `tokenpak` (lowercase). In docs: **TokenPak**.
- Env vars: `TOKENPAK_*`.
- Domain: `tokenpak.ai`. Email: `hello@tokenpak.ai`.

## 2. Tagline

**Primary:** "Cut your LLM token spend by 30–50%, zero config."

**Short:** "Local proxy. Lower token bills."

**One-sentence description (README hero, press):** "TokenPak is a local proxy that compresses your LLM context before it hits the API — fewer tokens, lower cost, same results."

Don't write new taglines for individual surfaces. Pick one of these.

## 3. Tone of Voice

From Constitution §8:

- **Direct.** Cut qualifiers. "Fast" not "very fast."
- **Specific.** Numbers before adjectives. "32.8% saved on a 747-token prompt" beats "massive savings."
- **Confident, not cocky.** Describe what TokenPak does; let the numbers carry the weight.
- **Developer-to-developer.** We assume readers know what an LLM, a proxy, and a token are.
- **Honest about limits.** Savings depend on workload; say so.

**Never:**
- "Revolutionary," "game-changing," "cutting-edge," "industry-leading," "next-gen."
- "Simply," "just," "easily" — they patronize and erase effort.
- Emoji fireworks. 🚀✨🔥 in product copy is a no.
- "We're excited to announce..." — get to what changed.

## 4. Logo and Mark

TODO: logo file does not yet exist in the repo. When it does, add:
- Primary logo (horizontal, light background)
- Primary logo (horizontal, dark background)
- Icon mark (square, for favicons and avatars)
- Clear space rules
- Minimum size

Until then, **the wordmark is the logo.** Set in the primary typeface (§6) at a weight and size appropriate to the surface.

## 5. Color

### 5.1 Brand palette

TokenPak's brand palette is small on purpose. More colors = less consistency.

| Token | Hex | Role |
|---|---|---|
| `tp-ink` | `#0B0F14` | Primary text, hero UI |
| `tp-paper` | `#F6F7F9` | Page background (light mode) |
| `tp-charcoal` | `#1A1F26` | Page background (dark mode) |
| `tp-accent` | `#00C389` | Savings, success, CTAs |
| `tp-warn` | `#F5A623` | Warnings, degraded states |
| `tp-danger` | `#E5484D` | Errors |
| `tp-mute` | `#6B7280` | Secondary text, gridlines |
| `tp-rule` | `#E4E7EB` | Dividers, borders (light); adjust for dark |

TODO: validate these hexes against the site CSS (`site/assets/style.css`) and reconcile.

### 5.2 Color rules

- **`tp-accent` is a signal color.** Use it for savings numbers, success states, primary CTAs — nowhere else. Decorative accent color defeats the signal.
- **Never use red for decoration.** Red means something failed. Keeping it scarce makes errors visible.
- **Gray does most of the work.** UI chrome is gray, not colored.
- **No gradients in product UI.** Marketing pages may use one subtle ink→charcoal gradient, sparingly.

## 6. Typography

- **Primary typeface:** system font stack — `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`. Fast, familiar, no web font load.
- **Monospace:** system mono stack — `ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace`. Used for code, CLI output, hashes, request IDs.
- **Type scale** (rem, 16px base):
  - Display: 2.5 (hero)
  - H1: 1.875
  - H2: 1.5
  - H3: 1.25
  - Body: 1.0
  - Small: 0.875
  - Caption: 0.75
- **Line height:** 1.5 for body, 1.2 for headings, 1.4 for code blocks.
- **Weight:** 400 body, 600 emphasis, 700 headings and numbers. No 300 ("light"). No italic for emphasis; use weight or color.

## 7. Spacing

- **4px grid.** All spacing is a multiple of 4.
- **Vertical rhythm:** headings have space above equal to their line height; body has space below equal to half.
- **Panels** (CLI and dashboard boxes) have internal padding of 16px (dashboard) / two spaces (CLI).
- **Don't use visual separation as decoration.** A divider means "different kinds of content follow." A blank line can often replace it.

## 8. CLI Styling

See 03 §4. Summarized brand rules:

- **Box drawing:** Unicode light box characters (`┌─┐├┤└┘│`), never ASCII (`+-+`).
- **Text colors:** only for state (see 03 §4.1).
- **Bold** for numbers inside panels and for command names in help text. Never for decoration.
- **No ANSI effects** beyond foreground color and bold. No blink, no underline (it confuses with hyperlinks).

## 9. Dashboard Styling

See 04. Summarized brand rules:

- **Borders** are 1px `tp-rule`. No drop shadows in chrome.
- **Corners:** 8px radius on cards, 4px on inputs. No sharp corners.
- **Icons:** line-style, 1.5px stroke, 20×20 default. Pulled from a single set (Lucide), not mixed.
- **Charts:** `tp-accent` for primary series, `tp-mute` for reference lines. Two-series charts use `tp-accent` + `tp-ink`; add color only when data demands it.

## 10. Docs and Web Styling

- **Max content width:** 720px for prose, 960px for reference pages with wide code samples.
- **Code blocks:** `tp-charcoal` background, `tp-paper` text, light syntax highlighting. Same colors in light and dark mode.
- **Callouts** have four flavors — `note`, `tip`, `warning`, `danger` — each a single line of color on the left rule, not a filled box.
- **Tables:** row stripe of `tp-paper` at 50% in light mode, `tp-ink` at 8% in dark. No borders between cells.

## 11. Emoji and Icon Rules

**Emoji:**
- Only in status lines and only the five listed in 03 §4.1 (✅ ⚠️ ❌ 📦 🚀).
- Never in headings, body prose, or commit messages.
- Never to replace a word. "✅ done" is fine in a status line; "✅ Implemented" in a heading is not.

**Icons:**
- Lucide set, line style, consistent stroke.
- Icon + label wherever the label fits. Icon-only only when the context makes the meaning unambiguous (e.g., play/pause in a running demo).
- Never use icons from two different sets in the same view.

## 12. Label and Status Microcopy

| Good | Avoid |
|---|---|
| Saved 14.9k tokens | Reduced context by an estimated 14,920 tokens |
| Proxy up · 127.0.0.1:8766 | Service running normally |
| Port 8766 in use | An error occurred while starting |
| Credentials: 2 ok, 1 expiring | Multiple credential statuses detected |
| No requests in the last 5 min | Awaiting activity |
| Apply changes? [y/N] | Are you sure you want to continue? |

Short, present-tense, specific.

## 13. Assets

Logos, swatches, fonts, and other binary assets live in `standards/assets/` once we have them. Do not commit assets to `docs/assets/` by default — those are for documentation visuals, not the brand system.
