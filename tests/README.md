# tests

Unit and integration tests. The ones that matter most:

- replica conversion `ceil(D_upper / C)`, including the case the original
  formula got wrong (CLAUDE.md §3.2)
- leakage guard: `max(train.ts) + H <= min(cal.ts)` (T3.3.2)
- control layer: a noisy prediction trace produces <= 1 actuation per 5 min
  during steady load (T5.2.2)
- degradation: forecaster down => arbiter stays up in reactive-only mode
