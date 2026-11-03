# H-0001 v0.3 — multi-scale confirmation (COMEX → MOEX gold, October 2026)

Status: **protocol registered 2026-11-03, before any October 2026 outcome was computed or
viewed.** User authorization: 2026-11-03 ("Давай согласен, делай") for (1) v0.3 registration and
(2) a single opening of October for both the frozen v0.2 confirmation and this v0.3 test.
Config: `config/experiments/h0001_multiscale_v0_3.toml`. Holdout: HL-0003 (shares the October
window of HL-0002).

## Why v0.3

The v0.2 discovery (`h*=1 s`) is frozen and is confirmed by its own unchanged rules. The
September-only exploration (`H-0001_SEPTEMBER_AUDIT_AND_EXPLORATION.md`, §3) showed a
COMEX → MOEX next-bar dependence on 1–60 s bars and none from 120 s. v0.3 tests, once and on the
untouched October, whether that next-bar dependence holds on 5, 10, 30 and 60 s bars. The grid
was chosen from September exploration; October is the only confirmation.

## Inputs

- The frozen v0.2 one-second series `data/aligned/h0001-seconds-v0-2/p3-20261102-v1/`
  (manifest SHA-256 `dc43c416…`, built under v0.2 config `6619460e…`), loaded through
  `research.seconds_store.SecondsRun` (SHA-verified). No raw file is re-parsed.
- Day universe: the manifest's primary candidates of the month (Mon–Fri minus 2026-09-07 and
  2026-10-12). **No stale-fraction day gate** (v0.2's gate is not used): per-bar staleness
  masking replaces whole-day exclusion.
- A venue's state at second `t` (`states[t]`) is the last observation with time `< t + 1 s`
  within the same common segment, with its age measured at `t + 1 s`.

## Bars

For width Δ ∈ {5, 10, 30, 60} s, bar `u` covers seconds `[o + uΔ, o + (u+1)Δ)`, where `o` is
trading-date 00:00 UTC plus the month grid origin `lo` (minimum common-segment start offset
of the month, rounded down to a multiple of 60 s), so bars are time-of-day aligned across days.
Endpoints `e0 = o + uΔ − 1`, `e1 = o + (u+1)Δ − 1` (the state just before the bar starts and
just before it ends).

- `seg[u]` = index of the common segment containing both `e0` and `e1`, else −1.
- Bar return `r(u) = ln S(e1) − ln S(e0)`, valid iff `seg[u] ≥ 0`, both states exist and both
  ages `≤ max(5 s, Δ)`. Zero returns are kept.
- Pairing at lag `k` (bars): COMEX `r_C(u)` with MOEX `r_M(u + k)`; both valid and
  `seg[u] == seg[u+k] ≥ 0`. Forward `k = +1` (COMEX leads); reverse control `k = −1`.

## Statistics

- Per day/width/lag: Pearson `r` over the pairs, defined iff `N ≥ 30` and both variances > 0;
  `z = atanh(clamp(r, ±0.999999))`.
- Day eligibility (once for all widths, data-quality only): reconciled, common segments present,
  MOEX timestamp quantum ≤ 1 s, and `r` defined at every width for both `k = +1` and `k = −1`.
- Month statistic per width/lag: `stat = tanh(mean_d z_d)` over eligible days.
- Null: `R = 2000` deterministic whole-day derangements (seed `20261001`), the same
  permutations for every width and lag; replicate `b` mean `m_b = mean_j z(COMEX day π_b(j),
  MOEX day j)`.
- Standardisation per width/lag: `T = (mean_z − μ_null) / σ_null` (population mean/SD of
  `m_b`; undefined if `σ_null = 0`). Replicate `T_b` likewise.
- Forward family-wise p (one-sided, single-step max-T over the four widths):
  `p_fw(Δ) = (1 + #{b : max_Δ' T_b(Δ', +1) ≥ T(Δ, +1)}) / (R + 1)`.
- Reverse family-wise p (two-sided): `p_rev(Δ) = (1 + #{b : max_Δ' |T_b(Δ', −1)| ≥
  |T(Δ, −1)|}) / (R + 1)`.
- Cross-day null cells (as in the frozen v0.2 `z_matrix`): COMEX day `i` bar `u` (valid,
  `seg_i[u] ≥ 0`) with MOEX day `j` bar `u + k` under MOEX day `j`'s same-segment rule
  (`seg_j[u] == seg_j[u+k] ≥ 0`); COMEX day `i`'s own `seg_i[u+k]` is not used. The diagonal
  equals the observed day cell. Undefined cells are skipped in `m_b`; undefined replicate means
  are skipped in `μ/σ`; a replicate whose maximum is undefined counts as an exceedance; an
  undefined observed `T` gives no p value, so the width cannot pass (condition 4 can then hold
  only through the ratio rule).
- `positive_day_share(Δ)` = share of eligible days with `z(Δ, +1) > 0`;
  `strongest_day_share(Δ)` = `max_d(s·z_d) / |Σ z_d|`, `s = sign(Σ z_d)` (+inf if Σ = 0).

## Decision (frozen before October)

Data gate: `DATA_INSUFFICIENT` if fewer than 15 eligible days.

A width Δ **passes** iff all hold:

1. `stat(Δ, +1) > 0` and `p_fw(Δ) ≤ 0.05`;
2. `positive_day_share(Δ) ≥ 0.60`;
3. `strongest_day_share(Δ) < 0.35`;
4. reverse not blocking: `p_rev(Δ) > 0.05` **or** `|stat(Δ, −1)| ≤ 0.25 · stat(Δ, +1)`.

v0.3 category: `SCREEN_POSITIVE` if at least one width passes; otherwise `NO_SCREEN_SIGNAL`
(data gate passed) or `DATA_INSUFFICIENT`. A width that meets 1–3 but fails only 4 is reported
as `reverse_blocked`.

**Combined decision for H-0001 on GDZ6/GCZ6 (agreed with the user 2026-11-03):**

| Outcome | Decision |
|---|---|
| v0.2 `SCREEN_POSITIVE` or v0.3 `SCREEN_POSITIVE` | `PROCEED`: dependence confirmed; propose read-only order-book recording (still needs its own authorization) |
| Neither positive, and some v0.3 width is `reverse_blocked` | `INVESTIGATE_COMMON_MOVE_OR_CLOCK`: no order book; examine the reverse effect first |
| Neither positive, otherwise | `CLOSED_NOT_CONFIRMED`: H-0001 for this pair is closed; no order book; no post-hoc search of other scales, lags or windows on October |

v0.2's own decision rule, config and code are unchanged and evaluated separately by
`research.seconds_screen confirm`.

## Calibration on September (before freezing)

The same code runs on September (`calibrate`, month guard 2026-09, no holdout marker) and is
cross-checked by an independent implementation; numbers should be close to the exploration's
(5 s 0.292, 10 s 0.283, 30 s 0.161, 60 s 0.101 at `k=+1`, all 21 days), small differences
from the bar construction are expected and recorded. Calibration results never change the
rules above.

## Execution order

1. Code + tests + this protocol committed and pushed.
2. `calibrate` on September with the committed code (artifact records code commit, code SHA,
   config SHA, seconds manifest SHA). Append HL-0003 with these identities; commit and push.
3. October opened once: v0.2 `confirm` (frozen, unchanged), then v0.3 `confirm` (create-once
   opened marker written before any October data is read; refuses a second run or changed
   code/config). Both results are recorded whatever they are.

## Implementation interface (`src/moex_arbitrage_lab/research_v03/`)

A new package; `src/moex_arbitrage_lab/research/*.py` is imported, never edited.

- `config.py`: `MultiscaleConfig` (pydantic, frozen, `extra="forbid"`, strict) mirroring the
  TOML; `load_multiscale_config(path) -> tuple[MultiscaleConfig, str]` (config, SHA-256 of the
  exact bytes); months, widths, `familywise_alpha`, staleness floor, seed and excluded dates
  must equal the values above exactly; refuses `replicates < 2000`, `min_pairs_per_cell < 30`,
  `min_eligible_days < 15`, or looser decision thresholds than the TOML values above.
- `bars.py` (pure): `BarSeries(ret, ok, sq: list[float])`, `BarDay(trading_date, width_s,
  seg: list[int], comex: BarSeries, moex: BarSeries, counts: dict)`;
  `month_origin_s(plan) -> int` (`lo`, multiple of 60);
  `build_bar_day(day: AnalysisDay, width_s, staleness_floor_s, lo_s, n_bars) -> BarDay`;
  `n_bars_for(plan, lo_s, width_s) -> int`.
- `analysis.py` (pure): `cell_sums(bar_day, lag) -> PairSums`; `day_z(...)`;
  `analyze(days: Sequence[AnalysisDay], config) -> MultiscaleResult` (cells, eligibility,
  statistics, null, p values, per-width pass flags, category); reuses `pearson`, `fisher_z`,
  `derangements`, `strongest_day_share`, `PairSums` from `research.seconds_analysis`.
- `cli.py`: `python -m moex_arbitrage_lab.research_v03.cli {calibrate,confirm} --config
  experiments/h0001_multiscale_v0_3.toml --analysis-run-id <id> --code-commit <sha>`
  (`confirm` additionally `--v0-2-analysis-run-id <id>`: the checksum-verified v0.2
  `confirmation.json` supplies the v0.2 category for the combined decision); outputs
  under `reports/generated/h0001-multiscale-v0-3/<run_id>/` (`calibration.json`,
  `confirmation.json`, each with `.sha256`, create-once) and the opened marker
  `reports/generated/h0001-multiscale-v0-3/ledger/<calibration_sha256>.opened.json`.

Holdout mechanics (implementation, fixed before October):

- Months, widths, `α`, staleness floor, seed and excluded dates are pinned in code (an edited
  TOML cannot move the calibration onto October or shrink the max-T family).
- `calibrate` loads September under `MonthGuard("2026-09")` and is refused once any opened
  marker exists for the config **or any v0.2 opened marker exists** (October already viewed).
- `confirm` is refused if any v0.3 opened marker already exists (one October opening for v0.3
  in total, not one per calibration) and if the calibration does not predate every v0.2
  opening.
- `confirm` performs every identity check first, from manifests/artifacts only: calibration
  artifact (config SHA, code commit, research_v03 code SHA, v0.2 research code SHA, seconds
  manifest SHA), the v0.2 confirmation artifact (version, month, manifest, category), no
  existing confirmation, the day universe, and a guard pre-check of every planned date and
  COMEX interval. Only then is the create-once opened marker written, and only after it is any
  October file read.
- The confirmation guard accepts MOEX dates of October only and COMEX intervals inside
  `[2026-09-01, 2026-11-01)`: the CME trade date 2026-10-01 opens 2026-09-30 22:00 UTC, so a
  strict October-only interval guard would refuse it after the marker and waste the holdout.
- The research_v03 code SHA is over exact file bytes (LF; `.gitattributes` `eol=lf`); the code
  must not be re-saved between calibration and confirmation.
