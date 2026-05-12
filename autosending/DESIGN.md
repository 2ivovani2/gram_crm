# Design System — AutoSending

## Color

Strategy: **Restrained** — tinted near-black + one blue-violet accent used at ≤10%.
All colors in OKLCH. Neutrals tinted toward hue 255 (blue-violet).

### Base scale
| Token | Value | Use |
|---|---|---|
| `--bg` | `oklch(0.10 0.006 255)` | Page background |
| `--surface` | `oklch(0.13 0.006 255)` | Card, panel |
| `--raised` | `oklch(0.16 0.007 255)` | Hover state, elevated |
| `--sunken` | `oklch(0.08 0.004 255)` | Input background, code |

### Border
| Token | Value |
|---|---|
| `--border` | `oklch(0.20 0.008 255)` |
| `--border-2` | `oklch(0.26 0.010 255)` |

### Text
| Token | Value | Use |
|---|---|---|
| `--text-1` | `oklch(0.92 0.004 255)` | Headings, primary |
| `--text-2` | `oklch(0.60 0.007 255)` | Body, secondary |
| `--text-3` | `oklch(0.38 0.005 255)` | Muted, placeholders |
| `--text-4` | `oklch(0.24 0.004 255)` | Disabled |

### Accent (blue-violet)
| Token | Value |
|---|---|
| `--accent` | `oklch(0.62 0.18 258)` |
| `--accent-hi` | `oklch(0.72 0.14 258)` |
| `--accent-bg` | `oklch(0.14 0.025 258)` |
| `--accent-border` | `oklch(0.22 0.045 258)` |

### Semantic
| Name | Value | Use |
|---|---|---|
| `--green` | `oklch(0.68 0.165 145)` | Success, online |
| `--green-bg` | `oklch(0.13 0.022 145)` | Success bg |
| `--red` | `oklch(0.62 0.195 22)` | Error, danger |
| `--red-bg` | `oklch(0.13 0.022 22)` | Error bg |
| `--amber` | `oklch(0.72 0.14 80)` | Warning |
| `--amber-bg` | `oklch(0.13 0.022 80)` | Warning bg |
| `--cyan` | `oklch(0.72 0.12 200)` | Info |

## Typography

Font: system stack (`-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif`).
Base: 14px / 1.5. Mono: `"SF Mono", "Fira Code", ui-monospace, monospace`.

Scale (min 1.25 ratio):
- `xs`: 11px
- `sm`: 13px
- `base`: 14px
- `md`: 16px
- `lg`: 20px
- `xl`: 24px
- `2xl`: 30px
- `3xl`: 38px

## Spacing

4px base unit. Scale: 4, 8, 12, 16, 24, 32, 48, 64, 96.

## Border radius

- `--r-sm`: 6px (inputs, small chips)
- `--r`: 8px (buttons, tags)
- `--r-lg`: 12px (cards)
- `--r-xl`: 16px (modals, panels)

## Elevation

No `box-shadow` for decoration. Shadows only for focus states and modals:
- Focus ring: `0 0 0 2px var(--accent-bg), 0 0 0 4px var(--accent)`
- Modal: `0 24px 48px oklch(0.04 0.002 255 / 0.8)`

## Motion

Duration: 120ms for micro, 200ms for transitions, 300ms for entrances.
Easing: `cubic-bezier(0.16, 1, 0.3, 1)` (expo out). No bounce, no elastic.

## Components

### Sidebar
Width: 240px. Background: `var(--surface)`. Right border: `var(--border)`.
No box-shadow.

### Cards
Background: `var(--surface)`. Border: `var(--border)`. Radius: `var(--r-lg)`.
No blur, no gradients on background.

### Inputs
Background: `var(--sunken)`. Border: `var(--border)`. Height: 38px. Radius: `var(--r-sm)`.
Focus: accent border + focus ring.

### Buttons
- Primary: `var(--accent)` bg, white text, `var(--r)` radius
- Ghost: transparent bg, `var(--border)` border, `var(--text-2)` text
- Danger: `var(--red)` bg, white text

## Bans

- No `backdrop-filter: blur()` for decoration
- No `background-clip: text` (gradient text)
- No `border-left` colored accents > 1px
- No big-number hero metrics with gradient accents
- No identical icon + heading + text card grids
- No modals as first UX choice
