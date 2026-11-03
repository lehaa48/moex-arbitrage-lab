"""H-0001 v0.3 multi-scale confirmation (research only, offline).

Protocol: docs/experiments/H-0001_MULTISCALE_V0_3.md; config:
config/experiments/h0001_multiscale_v0_3.toml. The frozen v0.2 package
``moex_arbitrage_lab.research`` is imported, never edited.

Modules:

- ``config``: frozen protocol config (TOML), its SHA-256 identity and the protocol floor;
- ``bars``: time-of-day aligned 5/10/30/60 s bar returns from the frozen v0.2 one-second
  states (backward-only, explicit staleness cap, no carry across segments);
- ``analysis``: lagged per-day correlations, eligibility, equal-day Fisher-z statistics,
  shared day-mismatch derangement null, family-wise max-T p values, per-width pass flags,
  category and the combined H-0001 decision;
- ``cli``: offline ``calibrate`` (September) and ``confirm`` (October, once, behind a
  create-once opened marker).

This is a trades/bar-based predictive screen. Nothing here estimates tradability or costs.
"""
