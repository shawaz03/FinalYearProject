# dashboard

The CloudPulse control plane. Next.js 16 · React 19 · Tailwind 4 · Recharts 3.

```bash
npm install
npm run dev        # http://localhost:3000
```

Runs on the **host**, not in the cluster (CLAUDE.md §3.7.1).

## Status: simulated fallback + live WebSocket feed (`T6.1`–`T6.2`)

The dashboard renders the seeded `simulateRun()` fallback by default (`simulated`
badge in the header) and connects via `src/lib/use-cloudpulse-stream.ts`
(`ws://127.0.0.1:8002/ws/stream` by default, or `NEXT_PUBLIC_WS_URL`) when
`connect ws` is clicked. The header badge reflects `Meta.live` (`live` for
cluster runs, `ws replay` for `services/arbiter/ws.py --replay`, `simulated`
for client-side simulation) so the operator always sees data provenance.

Panel `03 Cost` renders `pod-seconds` as the primary cost metric and hides the
derived dollar estimate unless toggled via `show $` (which prints the
`$0.0425 / pod-hour` price assumption on screen per D14).

```bash
# Offline WebSocket replay + UI check (no cluster required)
python -m services.arbiter.ws --replay --host 127.0.0.1 --port 8002
npm run dev   # http://localhost:3000 -> click "connect ws"
```

## WebSocket contract

`src/lib/telemetry.ts` defines `TelemetryFrame`, `ForecastFrame`, `Decision`
and `RunMeta`. `services/arbiter/ws.py` (`T6.1`) emits `snapshot`,
`telemetry`, `forecast`, and `decision` JSON frames matching these exact shapes.

Field semantics follow `deploy/queries.md`: `rps` is the replica-independent
demand signal (never CPU percent — CLAUDE.md §3.2), and latency quantiles are
client-side k6 values, not server histograms (§3.5).

## Design decisions

**Colour is reserved for data.** Every rule, axis, border and label is
greyscale. No gradients, no glow, no glass. The charts read at a glance because
nothing else on screen competes with them.

**Warm ground, flat panels.** The plane carries a slow three-layer gradient
(two wide low-contrast radials plus a long diagonal). Panels sit on a **flat**
surface on top of it — a chart drawn over a gradient has no single background
colour to validate contrast against, so the ground never runs under the data.

Three layers rather than one ramp because a single two-stop gradient across a
whole page bands visibly on 8-bit displays. Close stop values and large radii
are what keep a large flat field smooth.

**The palette was computed, not chosen.** No violet anywhere — the earlier
periwinkle slot read as purple. Candidates were run through the data-viz
validator and the failures discarded:

| candidate | result |
|---|---|
| violet / amber / lime | FAIL — lime vs amber CVD ΔE **3.4** (deuteranopia) |
| blue / rust / dark teal | FAIL — teal chroma 0.088, reads grey |
| **blue / rust / teal-green** | **PASS** both modes |

The lime/amber pair is the lesson: obviously distinct to normal vision,
near-identical to roughly 6% of men. Only the validator finds that.

| mode | surface | series | result |
|---|---|---|---|
| sand (default) | `#fbf8f1` | `#2060c8` `#cc5a1a` `#009384` | CVD ΔE 13.7 · normal 20.2 · all ≥ 3:1 |
| espresso | `#1c1815` | `#5a9bea` `#eb7b33` `#13ad97` | CVD ΔE 12.7 · normal 15.6 · all ≥ 3:1 |

Sand is the default state, not a light variant of a dark theme — the OS
preference does not override it. Espresso is a selected set of steps for the
warm dark surface.

**Character lives in the chrome, not the chart colours.** The first version
read generic because it was flat cards on `#191919` — the most common dashboard
surface there is. What changed:

- tinted blue-black and warm-paper surfaces instead of neutral grey
- a surveyor's **dot grid** on the plane and static SVG **film grain**, for
  material quality without a single gradient
- **corner registration ticks** and a 1px lit top edge on every plate, so
  panels read as instrument plates rather than cards
- **section indices** (01, 02, 03) on panel headers
- hard typographic scale contrast: a `clamp(56px, 8vw, 96px)` hero against
  10px wide-tracked eyebrow labels
- a 2px status spine on the hero plate that turns critical when breaching
- 2px panel radius — near-square, mechanical

**Three plots, one time axis, never two y-axes.** Demand (req/s), replicas
(count) and latency (ms) are different scales. A dual-axis chart invents a
correlation that is not in the data — the single most misleading chart mistake.
Stacked small multiples sharing an x domain give the same comparison honestly.
`syncId` ties the crosshair across all three.

**IBM Plex, not Inter.** Plex was drawn for technical documentation; its
slightly mechanical letterforms read as instrumentation rather than as another
SaaS landing page. Mono is used only for code-like tokens — ids, timestamps,
log columns — never for display figures. Display numbers use proportional
figures; `tabular-nums` is reserved for columns that align vertically.

**Motion is diegetic.** The only animation touching data is the replay
playhead, driven by GSAP. It earns its place: watching the forecast band
advance ahead of the measured line is the clearest demonstration of what the
system does. `prefers-reduced-motion` is honoured globally.

## On component libraries

shadcn's *convention* is used — components are code in this repo
(`components/ui/`), built on Radix primitives with CVA variants, not an opaque
dependency.

Aceternity UI and React Bits were considered and deliberately not used.
Their signature look — spotlight cards, beams, meteors, gradient borders — is
precisely the generic AI-startup aesthetic this interface avoids, and it would
fight the data for attention. GSAP is used directly where motion is warranted.

## Accessibility

- Legend present for every multi-series plot; text never wears the data colour
- Status never colour-alone — a text label accompanies every state dot
- Table view toggle exposes every value with no colour encoding
- Light and dark both validated against their own surface
- Crosshair tooltips enhance, never gate: values are reachable in the table

## Verification status

- **Offline UI + WebSocket replay verified (2026-11-27):** `npm run lint` and
  `npm run build` pass cleanly on Next.js 16.4.6. Browser audit on Chromium
  verified Sand & Espresso themes, Table View, GSAP Replay, `show $` / `hide $`
  cost-assumption toggle, leading `t + H` forecast band (`90% interval` legend
  swatch), and live WebSocket streaming from `services/arbiter/ws.py --replay`
  with 0 console errors and 0 horizontal overflow (`762x621` & `1440x900`).
- **Pending on 15.6 GB cluster laptop:** live `flash` cluster run via
  `make demo` (`scripts/demo.py`) confirming live Prometheus + arbiter WS
  match against PostgreSQL `arbiter_decisions`.
