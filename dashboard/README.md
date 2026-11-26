# dashboard

The CloudPulse control plane. Next.js 16 · React 19 · Tailwind 4 · Recharts 3.

```bash
npm install
npm run dev        # http://localhost:3000
```

Runs on the **host**, not in the cluster (CLAUDE.md §3.7.1).

## Status: simulated

There is no arbiter yet, so the dashboard renders a seeded simulation of a
`flash` run and says so in the header at all times. A dashboard that cannot
tell you whether its numbers are real is worse than no dashboard.

Swapping in live data is one change: replace `simulateRun()` in
`src/app/page.tsx` with the WebSocket feed. Nothing else moves.

## It pins the WebSocket contract

`src/lib/telemetry.ts` defines `TelemetryFrame`, `ForecastFrame`, `Decision`
and `RunMeta`. **These are the message shapes T6.1 must emit.** Building the UI
first turned an open question into a specification.

Field semantics follow `deploy/queries.md`: `rps` is the replica-independent
demand signal (never CPU percent — CLAUDE.md §3.2), and latency quantiles are
client-side k6 values, not server histograms (§3.5).

## Design decisions

**Colour is reserved for data.** Every rule, axis, border and label is
greyscale. No gradients, no glow, no glass. The charts read at a glance because
nothing else on screen competes with them.

**The palette was computed, not chosen.** Values come from the validated
data-viz reference palette and were checked with its validator:

| mode | result |
|---|---|
| light (`#ffffff`) | all pass · worst all-pairs CVD ΔE 12.6, normal-vision 23.0 |
| dark (`#191919`) | all pass · all slots ≥ 3:1 |

Light mode flags orange (2.88:1) and aqua (2.54:1) below 3:1, which triggers
the **relief rule** — so every series carries a direct legend entry and a table
view exists. Dark mode is a *selected* set of steps for the dark surface, not
an automatic inversion.

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

## Not done

Visual inspection. The build compiles and the page returns HTTP 200, but no
one has looked at the rendered output yet — open it and check for label
collisions, geometry and overflow before treating it as finished.
