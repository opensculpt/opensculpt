# OpenSculpt Design System

## Identity

OpenSculpt is an **agentic OS dashboard** — a mission control center for AI agents. The design metaphor is macOS: topbar, dock, desktop surface, Spotlight-style command bar. Dark-first, information-dense but not cluttered, every pixel earns its place.

## Typography

| Role | Font | Weight | Usage |
|------|------|--------|-------|
| Display | Space Grotesk | 700-800 | Headings, brand, welcome text |
| Body | DM Sans | 400-600 | UI text, labels, descriptions |
| Mono | JetBrains Mono | 400-600 | Code, vitals, technical values |

### Type Scale (1.2 ratio, 7 sizes)

| Token | Size | Use |
|-------|------|-----|
| `--text-xs` | 10px | Badges, micro-labels, status indicators |
| `--text-sm` | 12px | Secondary text, table cells, tooltips |
| `--text-base` | 14px | Body text, input fields, card content |
| `--text-lg` | 17px | Card titles, section headers |
| `--text-xl` | 20px | Page headers, modal titles |
| `--text-2xl` | 24px | Welcome heading, hero text |
| `--text-3xl` | 29px | Brand display (rare) |

**Rule:** No fractional sizes (10.5, 11.5, 12.5). Snap to the nearest scale value.

## Color System

### Surfaces (dark-first)

| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#07080c` | Page background |
| `--bg2` | `#0e1018` | Elevated surfaces (modals, panels) |
| `--bg3` | `#161a25` | Interactive surfaces (inputs, hover) |
| `--surface` | `rgba(14,16,24,0.88)` | Cards, overlays (with blur) |
| `--surface-hover` | `rgba(22,26,37,0.95)` | Card hover state |

### Text

| Token | Value | Contrast on --bg | Usage |
|-------|-------|-----------------|-------|
| `--text` | `#e2e6ef` | 14.2:1 | Primary text |
| `--text2` | `#8b95a7` | 4.8:1 (WCAG AA) | Secondary text, labels |
| `--text-dim` | `#3d4555` | 2.1:1 | Disabled, placeholder |

**Note:** `--text2` was bumped from `#6a7486` (3.5:1, failed AA) to `#8b95a7` (4.8:1, passes AA).

### Accents

| Token | Value | Semantic |
|-------|-------|----------|
| `--accent` | `#e8a44a` | Primary action, brand, warm |
| `--accent2` | `#f0c674` | Accent hover, lighter variant |
| `--purple` | `#9b7aed` | Active state, focus ring |
| `--blue` | `#60a5fa` | Information, links |
| `--green` | `#4ade80` | Success, running, healthy |
| `--red` | `#f87171` | Error, failed, danger |
| `--yellow` | `#fbbf24` | Warning, caution |
| `--cyan` | `#67e8f9` | Operating state |

### Semantic Tokens

| Token | Value | Usage |
|-------|-------|-------|
| `--glow-blue` | `rgba(96,165,250,0.25)` | Info focus glow |
| `--glow-green` | `rgba(74,222,128,0.25)` | Success focus glow |
| `--card-bg` | `var(--surface)` | Card backgrounds |
| `--card-border` | `var(--border)` | Card borders |
| `--border` | `rgba(255,255,255,0.06)` | Subtle borders |
| `--border-focus` | `rgba(255,255,255,0.14)` | Focus/hover borders |

## Spacing Scale (4px base)

| Token | Value | Usage |
|-------|-------|-------|
| `--space-1` | 4px | Tight gaps, icon margins |
| `--space-2` | 8px | Small padding, compact gaps |
| `--space-3` | 12px | Standard padding, card inner |
| `--space-4` | 16px | Card padding, section gaps |
| `--space-5` | 20px | Large padding, section margins |
| `--space-6` | 24px | Desktop padding, major sections |
| `--space-8` | 32px | Modal padding, wizard content |
| `--space-10` | 40px | Large section spacing |
| `--space-12` | 48px | Dock/topbar height |

**Rule:** All spacing uses this scale. No arbitrary pixel values.

## Border Radius

| Token | Value | Usage |
|-------|-------|-------|
| `--radius-sm` | 4px | Small elements (topbar buttons, badges) |
| `--radius-md` | 8px | Inputs, dock items, small cards |
| `--radius-lg` | 14px | Goal cards, special cards |
| `--radius-xl` | 16px | Command bar, modals, chat overlay |
| `--radius-full` | 50% | Dots, avatar circles, status indicators |

## Animation

| Token | Duration | Easing | Usage |
|-------|----------|--------|-------|
| `--duration-fast` | 150ms | ease | Hover, focus, micro-interactions |
| `--duration-normal` | 300ms | ease | Card transitions, overlays |
| `--duration-slow` | 500ms | cubic-bezier(0.16,1,0.3,1) | Page transitions, celebration |
| `--spring` | 300ms | cubic-bezier(0.34,1.56,0.64,1) | Card hover lift, button press |

**Rule:** `prefers-reduced-motion` must disable all animations.

## Z-Index Layers

| Layer | Value | Components |
|-------|-------|------------|
| Background | 0 | body::before atmosphere |
| Desktop | 1 | Desktop grid, goal cards |
| Card controls | 5 | Expand buttons on cards |
| Chat backdrop | 48 | Semi-transparent overlay |
| Chat panel | 49 | Chat overlay panel |
| Command bar | 50 | Spotlight command bar, status line |
| Evolution nudge | 99 | Nudge banner below topbar |
| Chrome | 100 | Topbar, dock |
| Notifications | 200 | Toast stack, popups, connection lost |
| Modal | 250 | Detail modal |
| Settings | 300 | Settings modal |
| Wizard | 10000 | Setup wizard (covers everything) |

## Layout

- **Desktop grid:** `max-width: 1440px; margin: 0 auto`
- **Grid columns:** `repeat(auto-fill, minmax(320px, 1fr))`
- **Active goal cards:** `grid-column: span 2` (priority placement)
- **Completed goals:** Collapsed to summary row
- **Topbar:** 40px fixed, full width
- **Status strip:** 44px fixed, below topbar, shows active goal status
- **Command bar:** 700px max, centered, fixed bottom 48px
- **Dock:** 48px fixed, full width, bottom

## Interaction States

Every component must specify these states:

| State | Visual treatment |
|-------|-----------------|
| **Loading** | Skeleton shimmer (use `.skeleton` class) or pulsing dots |
| **Empty** | Warm message + primary action ("No goals yet. Try asking...") |
| **Error** | Red border + message + retry action |
| **Success** | Green flash (2s) + toast with result |
| **Sending** | Input disabled, send button becomes spinner |
| **Thinking** | Pulsing dots indicator in chat |

## Accessibility

- All interactive elements: `aria-label` or visible text
- Main landmarks: `<main>`, `<nav>`, `role="search"`
- Touch targets: 44px minimum
- Color contrast: WCAG AA (4.5:1 for text, 3:1 for large text)
- Status: never communicated by color alone (add text/icon)
- Focus visible: 2px solid `var(--purple)` outline
- `prefers-reduced-motion`: disable all animations

## Anti-Patterns (Do NOT use)

1. `border-left: Npx solid <color>` on cards (AI slop pattern)
2. Emoji as UI icons (use SVG or consistent Unicode symbols)
3. Inline `style=` attributes in JS-generated HTML (use CSS classes)
4. Arbitrary font sizes not on the type scale
5. Arbitrary spacing not on the 4px scale
6. Purple/violet gradient backgrounds
7. 3-column icon-title-description feature grids
