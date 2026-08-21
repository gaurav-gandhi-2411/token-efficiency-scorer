# tracegauge brand system — Calibration

Status: proposed, **for review** — not wired into `tes serve` or any shipped
template yet. Full interactive review (color, type, the gauge component, all
four favicon sizes, badge, OG preview) at the artifact linked in PR #21.

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
| Body | IBM Plex Sans | 400, 600 |
| Data / mono | IBM Plex Mono | 400, 600 |

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
- **Favicon / wordmark glyph**: needle + arc only — ticks and the threshold
  band are dropped entirely, since neither reads below ~64px. This is the
  whole reason the favicon and the full gauge are visually related but not
  the same asset — proven at 16/32/64/512px in the review artifact.
- **`tes serve` chart axes** (future): ticks + band, needle becomes the
  plotted data point instead of a fixed pointer.
- **Wrapped slide backdrops** (future): full gauge, oversized, threshold
  band at low opacity as background texture.

## Assets in this directory

- `favicon.svg` — the minimal mark (arc + needle, no ticks/band), dark
  ground, legible from 16px up.
- `wordmark.svg` — header lockup: mark + "tracegauge" with the dial-glyph
  standing in for the 'a' in "gauge" (the literal instrument word).
  First-pass kerning — a candidate for a manual pass once approved.
- `badge.svg` — compact horizontal README badge, 168×28, self-contained
  ground so it holds next to shields.io badges on any README background.
- `og-preview.svg` — 1200×630 link-unfurl preview (GitHub/Slack/social).
- `og-preview.png` — 1280×640 PNG export of the above, uploaded via repo
  Settings → General → Social preview (GitHub serves this PNG directly;
  it doesn't render the SVG). `og-preview-conservative.png` is a
  maximally-stripped re-encode (RGB, 8-bit, no alpha/interlace/ICC
  profile, IHDR+IDAT+IEND only) kept as a fallback upload if the primary
  PNG's social-preview asset ever 404s again — see AU1 in git history for
  the diagnosis.

None of these are referenced by any shipped template yet.

## Not done here (explicitly out of scope for this review)

- Wiring any asset into `tes serve`'s HTML templates.
- PNG/ICO rasterization of `favicon.svg` (real browsers want a rasterized
  favicon in most contexts; SVG-only favicons have inconsistent support —
  rasterize once the direction itself is approved, not before).
- Final kerning pass on `wordmark.svg`.
- Applying the palette/type tokens to `tes serve`'s existing dashboard CSS.
