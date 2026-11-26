# load

Traffic generation for every experiment.

**k6 with the `constant-arrival-rate` executor — open loop.** Locust is
closed-loop by default: virtual users wait for a response before sending the
next request, so under overload the offered load quietly drops and the latency
damage we exist to measure disappears (coordinated omission). CLAUDE.md §3.5.

- `profiles/` — RPS schedules as config: `gradual`, `flash`, `diurnal`

Latency is recorded **client-side**, here, not from server metrics.
Phase 2 (T2.1). Owner: M3.
