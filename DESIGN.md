# Design System — Curious Robot

## Product Context

- **What this is:** An agentic robotics research workspace that shows how a Franka robot discovers unknown physical properties, updates a belief simulation, selects an action, and verifies the result.
- **Who it is for:** Robotics researchers, simulation engineers, hiring reviewers, and technically curious viewers who need to audit the evidence behind an autonomous decision.
- **Space:** GPU robotics simulation, system identification, scientific observability, and agent-user interaction.
- **Project type:** Data-dense local web application with a live R3F visualization, evidence timeline, telemetry, replay, and generated portfolio media.
- **Memorable thing:** The instant a physical measurement changes what the robot believes and therefore what it does next.

## Aesthetic Direction

- **Direction:** Precision Lab Instrument.
- **Decoration level:** Intentional and restrained.
- **Mood:** Calm, exact, and alive. It should feel like calibrated scientific equipment rather than a generic SaaS dashboard, game HUD, or neon AI demo.
- **Primary visual anchor:** The Newton-driven R3F scene. UI chrome supports the simulation and never competes with it.
- **Category-safe choices:** Familiar run controls, explicit status, stable region layout, readable evidence chronology, conventional scientific units.
- **Deliberate risks:** A nearly black graphite live workspace, one cyan signal color, unusually explicit provenance, and belief changes treated as the dominant UI event.

## Typography

- **Primary UI / Korean:** IBM Plex Sans KR — technical without feeling sterile, broad Korean coverage, strong hierarchy at compact density.
- **Data / IDs / units / code:** IBM Plex Mono — distinguish measurements and provenance from prose; enable tabular numerals.
- **Loading:** Self-host pinned WOFF2 files under `src/web/src/assets/fonts/`; never depend on a font CDN in production, replay, or capture.
- **Fallback:** `sans-serif` or `monospace` only after the bundled family, never as the intended appearance.

### Type scale

| Token | Size / line-height | Use |
| --- | --- | --- |
| `--text-xs` | 0.875rem / 1.25rem | nonessential metadata with compliant contrast |
| `--text-sm` | 1rem / 1.45rem | buttons, labels, compact evidence details |
| `--text-md` | 1.0625rem / 1.6rem | primary body and rationale |
| `--text-lg` | 1.25rem / 1.6rem | region title and active-stage detail |
| `--text-xl` | 1.5rem / 1.8rem | workspace/page title |
| `--text-2xl` | 2rem / 2.4rem | New Run mission statement only |

Use sentence case. Uppercase is reserved for short status categories such as `LIVE`, `FAILED`, or `EVALUATOR TRUTH`, never paragraphs.

## Color

All implementation tokens use OKLCH. Hex values below are review references, not the CSS source of truth.

### Dark live/capture theme

| Semantic role | OKLCH | Reference hex | Usage |
| --- | --- | --- | --- |
| `--canvas` | `oklch(0.155 0.015 245)` | `#0A0E13` | application and viewport surround |
| `--surface-1` | `oklch(0.205 0.018 245)` | `#111820` | main panels |
| `--surface-2` | `oklch(0.255 0.022 245)` | `#17212B` | controls and selected evidence |
| `--border` | `oklch(0.36 0.025 245)` | `#2A3642` | hairline separation |
| `--text-primary` | `oklch(0.96 0.008 230)` | `#F1F5F7` | primary content |
| `--text-secondary` | `oklch(0.76 0.025 235)` | `#A7B4BF` | secondary content |
| `--text-muted` | `oklch(0.61 0.03 235)` | `#748391` | nonessential metadata only |
| `--signal` | `oklch(0.79 0.13 205)` | `#39D0DF` | active phase, primary run action, synchronized cursor |
| `--signal-fg` | `oklch(0.22 0.045 205)` | `#052C31` | text on signal fill |
| `--success` | `oklch(0.76 0.13 150)` | `#67C98A` | verified predicate |
| `--warning` | `oklch(0.80 0.14 78)` | `#F2B84B` | degraded/partial state |
| `--danger` | `oklch(0.70 0.17 25)` | `#F36C6C` | safety and terminal failure |
| `--truth-reveal` | `oklch(0.81 0.10 330)` | `#E6A0CA` | post-terminal evaluator-only reveal |

### Light replay/review theme

| Semantic role | OKLCH | Reference hex |
| --- | --- | --- |
| `--canvas` | `oklch(0.94 0.012 240)` | `#E9EDF0` |
| `--surface-1` | `oklch(0.985 0.005 240)` | `#F8FAFB` |
| `--surface-2` | `oklch(0.955 0.01 240)` | `#EEF2F4` |
| `--border` | `oklch(0.82 0.02 240)` | `#C5CFD6` |
| `--text-primary` | `oklch(0.23 0.02 245)` | `#111820` |
| `--text-secondary` | `oklch(0.43 0.035 240)` | `#465563` |
| `--text-muted` | `oklch(0.53 0.03 240)` | `#657480` |
| `--signal` | `oklch(0.53 0.12 205)` | `#007D89` |
| `--success` | `oklch(0.50 0.13 150)` | `#1E7A46` |
| `--warning` | `oklch(0.53 0.13 75)` | `#9A6500` |
| `--danger` | `oklch(0.49 0.17 25)` | `#B8343C` |

### R3F and chart encodings

Color is paired with geometry, dash, marker, and text:

- measured force: cyan solid arrow;
- commanded/desired force: amber dashed arrow;
- observed trajectory: light solid path with circular samples;
- predicted trajectory: cyan dashed path with diamond samples;
- target region: green double ring plus label;
- contact point: red cross/normal vector;
- belief simulation: cyan outline watermark and `BELIEF` label;
- evaluator truth reveal: rose hatch and explicit `EVALUATOR ONLY` label.

Never introduce purple/indigo gradients. Never use green alone to signal success or red alone to signal failure.

## Spacing and Density

- **Base unit:** 4 px.
- **Density:** Compact but readable. The viewport gets space; evidence and data use disciplined density.
- **Scale:** `2xs=2`, `xs=4`, `sm=8`, `md=12`, `lg=16`, `xl=24`, `2xl=32`, `3xl=48` px.
- Evidence card internal padding: 12–16 px.
- Region gutters: 16 px desktop, 12 px tablet, 8–12 px mobile.
- Permanent regions use hairline borders, not card containers or shadows.

## Layout

- **Approach:** Grid-disciplined scientific workspace.
- **Desktop ≥1280 px:** header, six-phase stepper, 65/35 resizable viewport/evidence split, 180–240 px telemetry dock.
- **Tablet 768–1279 px:** 58/42 viewport/evidence, telemetry in bottom Sheet.
- **Mobile <768 px:** live companion and replay; active phase, 42 svh viewport, latest evidence, sticky safety controls, Sheets for telemetry/history.
- **Radius:** `sm=2px`, `md=4px`, `lg=6px`, `full=9999px` only for status badges or circular geometry.
- **Shadows:** menus/dialogs only. Permanent workspace regions use value and border separation.
- **Resizable panels:** keyboard accessible; canonical capture layout ignores personal panel settings.

## Component Rules

- Use shadcn components only from the approved allowlist in the Agent Lab design document.
- Cards exist only for discrete evidence/tool/verification events. Never build a dashboard-card mosaic.
- Tabs are only for Live and Replay. The discovery process uses the macro stepper.
- The primary button is `Run discovery demo`; `Configure run` is secondary disclosure.
- Safe stop always includes text and is never hidden in an overflow menu.
- Empty states state what will appear, why it is empty, and one relevant action.
- Loading states name actual work such as GPU initialization or asset mapping. Never show an unexplained spinner after evidence exists.

## Motion

- **Approach:** Minimal-functional and deterministic.
- **Easing:** enter `cubic-bezier(0.16,1,0.3,1)`, exit `cubic-bezier(0.7,0,0.84,0)`, move `cubic-bezier(0.45,0,0.55,1)`.
- **Durations:** micro 80 ms, short 160 ms, medium 280 ms, status pulse 1.5 s.
- Evidence enters once. No ambient motion, decorative particles, auto-orbit camera, or continuous card animation.
- Manual camera control locks out stage-guided camera changes until `Return to guided`.
- `prefers-reduced-motion` removes pulse, translation, and camera interpolation.
- Capture/replay uses a fixed clock, disables nondeterministic animation, and renders R3F on demand.

## Accessibility

- Body contrast ≥4.5:1 and large text/UI graphics ≥3:1 in both themes.
- Body copy is at least 16 px; metadata below 16 px is nonessential and still meets contrast.
- Touch targets are at least 44×44 px.
- Every canvas interaction has a DOM equivalent and synchronized semantic scene summary.
- Stage changes use polite live announcements; safety/terminal events use assertive announcements.
- Focus order follows header, phase stepper, viewport controls, evidence, telemetry.
- Color is never the only state or series encoding.

## Media Rules

- Canonical media viewport: 1920×1080 and 1280×720 verification capture.
- Canonical panel split: 65/35 with 220 px telemetry.
- Dark Precision Lab theme is used for portfolio MP4/GIF.
- Fonts, GLB assets, and icons are bundled; no network requests are permitted during capture.
- Each output frame waits for reducer, chart, and R3F frame acknowledgments.
- README embeds an optimized GIF and links the MP4/Release asset.

## Anti-Patterns

- Purple/blue AI gradient, neon glow, or game HUD styling.
- Repeated large-radius cards and permanent panel shadows.
- Generic three-column feature grids, ornamental icon circles, or centered-everything layouts.
- Small low-contrast metadata used for critical status.
- Raw chain-of-thought, fabricated reasoning, or animation not driven by events.
- Browser physics or visually plausible interpolation presented as recorded truth.

## Decisions Log

| Date | Decision | Rationale |
| --- | --- | --- |
| 2026-10-20 | Precision Lab Instrument direction | Keeps Newton physics visually primary and makes evidence feel calibrated rather than theatrical |
| 2026-10-20 | IBM Plex Sans KR + IBM Plex Mono | Supports Korean UI, scientific prose, tabular values, IDs, and offline capture coherently |
| 2026-10-20 | Graphite + one cyan signal color | Creates strong current-state hierarchy without generic AI gradients or excessive status color |
| 2026-10-20 | Dark live/capture, light replay/review | Live benefits from visual focus; long evidence review benefits from light reading surfaces |
| 2026-10-20 | 4 px compact spacing and hairline regions | Supports high information density without a dashboard-card mosaic |
| 2026-10-20 | Minimal deterministic motion | Preserves causality, reduced-motion access, and repeatable media capture |
