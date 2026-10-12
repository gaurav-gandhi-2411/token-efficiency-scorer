# tracegauge brand system

Two layers. The **dashboard palette and type** (Color, Type and the gauge component below, the
"Calibration" system) are what `tracegauge serve` renders. The **logo** is mark "Turns": seven bars
over a baseline and a pointer, one bar (the flagged turn) in the accent colour. It replaced the
earlier dial-and-needle mark in the header, favicon and social preview; the dashboard colour tokens
were not changed.

## Why this direction

tracegauge already measures things against calibrated thresholds — a
silhouette score against a stability bar, a token count against a
self-baseline band, a cost against a plan. The identity looks like the
instrument doing that measuring, not a SaaS analytics dashboard borrowing
the word "gauge" for flavor. This is a deliberate contrast with the
cross-agent, chart-forward, comparison-forward dashboards in this space —
Calibration is instrument-forward: one honest reading at a time, its own
threshold band shown alongside it.

Checked against the three AI-generated-design defaults and rejected: not
warm-cream-serif-terracotta, not near-black-with-one-bright-pop (the ink/
paper pairing here is duller and warmer than the cliché, and the accent is
used as a *pointer*, not a hero gradient), not broadsheet hairlines.

## Color

| Token | Hex | Role |
|---|---|---|
| `ink` | `#12140F` | Body ground, panel surfaces |
| `paper` | `#F0EDE4` | Page ground, ink-panel text |
| `needle` | `#C9622B` | A live reading — neutral pointer, never a verdict |
| `calibrated` | `#4F7A5C` | In-band, verified, passed |
| `regression` | `#B01E3C` | Out-of-band, failed, regressed — never shares hue with `needle` |
| `graphite` | `#5B5D53` | Secondary ink, rule lines, ring base |
| `tick` | `#A79F8C` | Tick marks, quiet captions |

Deliberately seven tokens, each with exactly one job. `needle` and
`regression` are hue-separated on purpose (orange vs. crimson, not two
shades of red) so a screenshot full of live readings never silently reads
as a wall of alarms.

## Type

| Role | Family | Weight(s) |
|---|---|---|
| Display | Space Grotesk | 700 |
| Body | IBM Plex Sans (shipped as the renamed subset "TG Sans") | 400, 600 |
| Data / mono | IBM Plex Mono (shipped as the renamed subset "TG Mono") | 400, 600 |

**Why:** Space Grotesk carries every number that *is* a reading — gauge
values, hero metrics — and stands apart from surrounding prose on purpose,
the way a gauge's LED digits are a different material from its engraved
panel label. It never appears as body copy or a button label. IBM Plex Sans
and IBM Plex Mono share one type family (Plex, designed for IBM's own
technical products) for everything else, so the sentence explaining a
measurement and the measurement's own printed value visually belong to the
same document — a terminal capture pasted next to a paragraph of prose
reads as one system, not two unrelated typefaces glued together.

All three are open (SIL OFL / Apache), self-hostable, no CDN dependency.

## The gauge component

Not a logo shape — a parametrized component, so the same function draws the
favicon, a chart axis, and a future Wrapped slide backdrop:

```
gaugeArc(value, min, max, thresholds[], {
  majorTicks: 6, minorPerMajor: 4,
  sweep: [-135deg, 135deg],   // 270° total, standard instrument sweep
})
-> draws, in this fixed order:
   1. threshold band   (thin arc segments, colored per threshold zone)
   2. tick ring        (major + minor ticks, evenly spaced across the sweep)
   3. needle           (one line + hub, pointing at the real value)
```

Reused, not reinvented, per context:
- **Favicon / header glyph**: no longer the gauge; see "Logo assets" below.
- **`tracegauge serve` chart axes** (future): ticks + band, needle becomes the
  plotted data point instead of a fixed pointer.
- **Wrapped slide backdrops** (future): full gauge, oversized, threshold
  band at low opacity as background texture.

## Logo assets

Palette (logo only): ink `#1B2540` / `#DCE3F5`, accent `#C4204F` / `#FF7A9C`, surface `#FAFAFC` /
`#0E1322` (light / dark). Unsuffixed SVGs are adaptive (`prefers-color-scheme`); `-light` and
`-dark` are fixed colours for places that do not follow the OS scheme (the always-dark dashboard
header, the README `<picture>`).

In `assets/brand/`:

- `mark.svg` - the seven-bar mark alone.
- `lockup.svg`, `lockup-light.svg`, `lockup-dark.svg` - mark plus the lowercase wordmark.
- `favicon.svg` - the simplified five-bar mark, adaptive.
- `og-preview.svg` / `og-preview.png` - 1200x630 social-preview card, dark. GitHub serves the PNG
  from Settings, General, Social preview (RGB, no alpha); it does not render the SVG.
- `OFL-Sora.txt` - licence for the Sora letterforms in the wordmark and tagline.

In `tracegauge/web/static/brand/` (shipped in the wheel): `favicon.svg`,
`favicon-16-snapped-light.svg`, `favicon-16-snapped-dark.svg` (pixel-snapped 16px variants, 2px
bars on a 16px grid, so the bars do not anti-alias at tab size), `lockup-dark.svg` (dashboard
header) and `OFL-Sora.txt`. The dashboard links the adaptive favicon first and the snapped 16px
variants under `prefers-color-scheme` media queries; browsers that ignore `media` on icon links
use whichever icon they pick by size.

Every letter in the wordmark and tagline is a Sora outline converted to an SVG path, so no SVG
contains `<text>` and none needs a font to render. Sora is SIL OFL 1.1; attribution is in `NOTICE`.
A test (`tests/test_brand_assets.py`) checks that the files exist, contain no `<text>` or
`font-family`, and that the wheel includes the shipped ones.

Not done: PNG/ICO rasterisations of the favicon (the SVG icons cover current browsers; there is no
`/favicon.ico`).
