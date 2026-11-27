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

The `Arbiter Trail & Cost` card renders `pod-seconds` as the primary cost
metric and hides the derived dollar estimate unless toggled via `show $`
(which prints the `$0.0425 / pod-hour` price assumption on screen per D14).

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

## Design decisions (Design A v2 — Elite Monochrome Studio)

**Architectural white & pitch-black hierarchy.** The primary canvas uses crisp
white studio cards (`#ffffff` on `#fafafa`, `1px` hairline borders) contrasted
against a sticky pitch-black command shell and inverted SLA monolith (`#09090b`),
with a full dark mode (`#09090b` plane, `#121216` cards) toggleable in the
header.

**High-contrast semantic palette.** Monochrome ink dominates the chrome so data
channels separate immediately:

| mode | plane / card | demand | forecast | capacity |
|---|---|---|---|---|
| light (default) | `#fafafa` / `#ffffff` | `#09090b` | `#e03400` | `#059669` |
| dark | `#09090b` / `#121216` | `#fafafa` | `#ff5722` | `#10b981` |

Light studio mode is the default state unless the user toggles `Switch to dark mode`
(persisted via `cp-theme` pre-paint script) or OS preference applies when unset.

**Component-first observability.** Instead of flat summary ratios alone, the
Control Plane surfaces four focused views behind the header segmented bar
(`Control Plane`, `Pod Matrix`, `Audit Log`, `Data Table`):

- **SLA Monolith + KPI Tray (`MetricBar`)**: inverted SLA violation monolith
  paired with a 3-column hairline KPI strip (Offered Demand, Client p99 Latency,
  CPU Headroom) with smooth cubic Bezier sparklines.
- **Demand Forecast (`DemandPlot`)**: smooth measured demand curve with vertical
  gradient fill, vermilion median forecast + `90% interval` ribbon, emerald
  capacity steps, and shaded `NOW -> +H` lookahead window.
- **Pod Cluster Matrix (`PodMatrix`)**: 15-slot Kubernetes replica grid
  (`p01..p15`) distinguishing `RDY` (monolith black), `WRM` (vermilion dashed),
  and `IDLE` slots directly, plus a full-scale `ReplicaPlot` timeline in the
  `Pod Matrix` tab.
- **Latency Tail Spread (`LatencyPlot`) & `DecisionLog`**: `p50 -> p99` envelope
  chart anchored against the `100 ms` SLO threshold, paired with a compact
  arbiter trail and a mode-filterable audit log (`all`, `actuations`,
  `predictive`, `reactive`, `stabilising`, `hold`).

**Dedicated scales, single time axis.** Demand (req/s), replicas (count), and
latency (ms) live in separate plots rather than dual y-axes. `syncId` ties the
crosshair across `DemandPlot`, `ReplicaPlot`, and `LatencyPlot`.

**Geist + Geist Mono.** `Geist` handles headings, body copy, and display figures,
while `Geist_Mono` with `tabular-nums` handles timestamps, axis ticks, pod IDs,
and telemetry table columns.

**Motion is diegetic.** GSAP drives only the timeline replay playhead (`replay`,
`pause`, `Show full run`) and subtle section entrance fades, while respecting
`prefers-reduced-motion` globally.

## On component libraries

shadcn's *convention* is used — components live in `src/components/ui/` with
CVA variants (`badge.tsx`, `panel.tsx`) alongside dedicated telemetry views
(`pod-matrix.tsx`, `metric-bar.tsx`, `decision-log.tsx`, `charts/timeline.tsx`).

Aceternity UI and generic glow/beam/glass presets were deliberately avoided so
data legibility never competes with chrome.

## Accessibility

- Legend present for every multi-series plot
- Status never colour-alone — explicit text badges (`RDY`, `WRM`, `IDLE`,
  `BREACHING`, `COMPLIANT`, `predictive`, `reactive`) accompany every indicator
- `Data Table` view exposes every telemetry and forecast row in neutral
  monochrome ink with zero colour encoding
- Light and dark modes validated against their respective card surfaces
- Crosshair tooltips enhance rather than gate: all values remain inspectable in
  `Data Table`

## Verification status

- **Design A v2 Offline UI + WebSocket replay verified (2026-11-28):**
  `npm run lint` and `npm run build` pass cleanly on Next.js 16.4.6. Browser
  audits on Chromium verified Design A v2 light & dark modes, all four views
  (`Control Plane`, `Pod Matrix` with unclipped `MAX_PODS`, `Audit Log` filter
  pills, monochrome `Data Table`), GSAP Replay controls, `show $` / `hide $`
  cost-assumption toggle, leading `t + 45s` forecast band (`90% interval`), and
  live WebSocket streaming (`ws replay · ws-replay-001`) from
  `services/arbiter/ws.py --replay` with `0` console/WebSocket/network errors
  and `0` horizontal overflow (`762x621` & `1440x900`).
- **Pending on 15.6 GB cluster laptop:** live `flash` cluster run via
  `make demo` (`scripts/demo.py`) confirming live Prometheus + arbiter WS
  match against PostgreSQL `arbiter_decisions`.
