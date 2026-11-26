# cloudpulse_core

Shared Python package imported by **both** `ml/` (training) and `services/`
(serving). Feature engineering is defined here exactly once.

Why it exists: if training and serving compute features separately they drift
apart, the model silently degrades, and nothing fails loudly. See CLAUDE.md §2
rule 6.

Planned contents: `features.py` (T3.3.1), `seeds.py` (T0.2.3), `config.py`.
