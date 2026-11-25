# Doc Intelligence Studio — Design system (v4.1 · portal 1.1)

Applies to every screen. Source of truth for tokens is the `:root` block in `app/index.html` (prototype) and `portal/src/styles/app.css` (portal); this page explains them.

## Principles

1. **The document is the centre.** The builder's run panel and the review workspace show the real page with what the platform found (text blocks, tables, fields, redactions) drawn on it.
2. **Familiar building, own identity.** The builder uses the drag-and-drop canvas pattern users know (palette, wires, ports), with our own visual language, vocabulary and AI-component model.
3. **Enterprise, not "AI-app".** Navy shell, cool greys, one cobalt brand colour. AI is marked with a single solid teal, never gradients or neon. Colour carries meaning only (status, stage, field identity, AI).
4. **Enterprise defaults.** Versioning with release notes, draft-change tracking, policies shown where they apply, write-only secrets, keyboard access (⌘K, Del, Ctrl+Z/D), WCAG-AA contrast, reduced-motion support.
5. **Canvas first (portal 1.1).** Builder side panels stay out of the way until asked for: the step list opens from +, settings open for the clicked step, and both close on an outside click.

## Typography

| Role | Font | Size / line | Weight |
|---|---|---|---|
| Page title | IBM Plex Sans | 24/32 | 600 |
| Section / dialog title | IBM Plex Sans | 18/28 · 15/22 | 600 |
| Body, controls | IBM Plex Sans | 14/20 | 400–600 |
| Secondary, hints, table headers | IBM Plex Sans | 12.5–13/18 | 400–500 |
| IDs, field names, code, endpoints | IBM Plex Mono | 12.5–13 | 400 |

Both families are open source (SIL OFL). Tabular numbers (`.num`) for all figures. Self-host in production.

## Colour tokens (light · dark via `light-dark()`)

| Token | Light | Use |
|---|---|---|
| `--shell` | #0f1b2d (navy) | Sidebar / bottom navigation; text #c6d0dd, muted #8392a8, accent #5b8def (badges only) |
| `--bg` | #f3f5f8 | App and canvas background |
| `--surface` / `-2` / `-3` | #fff / #f5f7fa / #e8ecf1 | Cards, panels, wells |
| `--border` / `-strong` | #dde2e9 / #c5cdd8 | Dividers / inputs, step cards |
| `--text` / `-2` / `-3` | #121a26 / #3e4a5b / #64707f | Primary / secondary / tertiary |
| `--brand` | **#1652c8 cobalt** (hover #1043a8, soft #e9f0fc, ink #123f9c; dark #6f9bff) | Primary actions, selection, focus ring, live run state |
| `--ai` | **#0e7490 teal** (soft #e6f3f6, ink #0b5d73) | AI only: AI step edge + "AI" tag + icon tile, AI components, slots, AI wires, AI badges |
| `--success` `--warning` `--danger` `--info` | #1e7a46 · #a15c07 · #c0362c · #1d5fa8 | Status only, each with a `-soft` background |

**Stage tints** (step icon tile only, muted): Intake #b5541c · Prepare #8c4a8f · Read #1f6aa5 · Understand #0e7490 · Validate #9a6a12 · Decide #4659a8 · Deliver #4a5668.

**Field identity colours**: #1652c8, #0e7490, #8c4a8f, #b5541c, #9a6a12, #2f7d4f, #4659a8, #a43b5c, #4a5668, #127a6b.

## Navigation

Active item: white text on a 12 % white background highlight, radius 6. **No left accent bar** (removed in portal 1.1). Hover: 6 % white background.

## Builder visual language

| Element | Spec |
|---|---|
| Step card | 248×68, radius 12, 1 px `--border-strong`, `--sh-sm`; 40 px icon tile tinted by stage; name 13.5/600, summary 12/400; status icon right (running cobalt, done green, error red). |
| AI step | Same card with a 4 px teal left edge, teal icon tile and a small "AI" tag on the top edge. Slots: 13 px teal diamonds on the bottom edge with labels and + buttons; required marked \*. |
| AI component | 208×48 pill, dashed teal border, round teal icon; top diamond "plug". |
| Ports | 14 px circles; input left, outputs right (labelled when more than one; error output red). |
| Wires | 2 px grey cubic curves leaving/entering ports horizontally; 10 px drawn arrowhead at the input port; selected/hovered/live in cobalt with a midpoint × delete; AI wires dashed teal, no arrowhead. |
| Canvas | `--bg` with 20 px dot grid; floating toolbar (radius 10, `--sh-md`) starting with a cobalt **+** (add steps); minimap 184×116 bottom right, hidden when the canvas is empty. |
| Empty canvas | Centred 56 px **+** tile (dashed `--border-strong`, radius 14, cobalt icon; hover: brand border + `--brand-soft`), title "Add your first step", hint "Start with an intake such as Email inbox or Upload portal." |
| Step list | 300 px overlay sliding in from the left of the canvas, `--sh-xl`; search + close ×; grouped by stage. Hidden by default. |
| Settings panel | 384 px overlay sliding in from the right, `--sh-xl`. Shown while a step/connection is selected or pipeline settings are open. |
| Selection | 3 px cobalt focus ring + brand border; hover toolbar above a step (test · duplicate · configure · delete). |

## Shape, spacing, elevation

Radius 6 / 8 / 12 · 4 px spacing grid · shadows `--sh-xs … --sh-xl` · focus ring 3 px cobalt at 22 % · panel slide 180 ms ease (none with reduced motion).

## Icons

IBM Carbon Design System icons (Apache 2.0), 32-grid filled glyphs rendered at 16 px in controls and 18–20 px in navigation and step tiles. Product-branded icons (Watson, watsonx) are deliberately not used. The portal uses `@carbon/icons-react`.

## Brand mark

Custom logo: cobalt rounded square with a white folded-corner document and two content bars (teal, cobalt). No icon-library glyph.

## Components

Button (primary · secondary · ghost · danger; 36/32/28) · Input / Select / Textarea · Range · Switch · Checkbox · Badge (neutral, success, warning, danger, info, brand, ai) · Kbd · Card · Tabs · Segmented control · Table (stacked on mobile) · Tile (incl. `ai`) · Meter · Collapsible section · Menu (searchable, grouped, shortcuts) · Dialog · Drawer · Command palette · Toast · Alert · Empty state · Avatar · Step card · AI component · Port · Slot · Minimap.

Naming note: run-log rows use `.logrow` (renamed from `.lg`, which collided with `.tile.lg`).

## Layout

- Shell: 240 px navy sidebar (Operate · Build · Govern) + 52 px white top bar. Sidebar collapses to a 64 px rail in the builder and review workspaces and below 1100 px; bottom tab bar below 760 px.
- Builder (portal 1.1): full-width canvas + run panel. Step list (left, 300 px) and settings panel (right, 384 px) are overlays, hidden by default. Open: + / click a step / header **Settings**. Close: outside click, Escape, ×, or after adding a step (step list). Menus, dialogs and toasts don't count as outside.

## Vocabulary

Pipeline · step · stage · connection · output · AI component · slot. Stages: Intake, Prepare, Read, Understand, Validate, Decide, Deliver. Slots: Model, Memory, Tools, Knowledge, Output.
