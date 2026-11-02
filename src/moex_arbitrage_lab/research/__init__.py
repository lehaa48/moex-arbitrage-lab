"""H-0001 v0.2 seconds screen (research only, offline).

Modules:

- ``seconds_config``: frozen protocol config (TOML) and its SHA-256 identity;
- ``seconds_series``: deterministic causal one-second series per venue (MOEX from trades,
  COMEX from Databento ``ohlcv-1s`` CSV), sessions and common segments;
- ``seconds_analysis``: returns, lagged per-day correlations, equal-day Fisher-z aggregation,
  September discovery, October confirmation, controls, data gate and decision rule;
- ``seconds_store``: create-once outputs, manifests and month-guarded loading;
- ``seconds_screen``: offline CLI ``build-seconds``, ``discover``, ``confirm``.

This is a trades/bar-based predictive screen. Nothing here estimates tradability or costs.
"""
