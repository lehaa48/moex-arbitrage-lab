# P2B — Databento GCZ6 one-second acquisition under USD 1

Status: **downloaded and locally verified** on 2026-11-02; provider billing display still needs
the user's portal check because `batch.list_jobs` exposes only `id`, `state`, and `ts_received`.

## Objective

Acquire only COMEX/CME `GCZ6` one-second OHLCV data from Databento. Do not request, modify, or reprocess MOEX/T-Bank data.

## Frozen request

```text
dataset  = GLBX.MDP3
schema   = ohlcv-1s
stype_in = raw_symbol
symbols  = GCZ6
start    = 2026-09-01T00:00:00Z  (inclusive)
end      = 2026-09-11T00:00:00Z  (exclusive)
delivery = one batch download
```

Free Databento metadata estimates obtained on 2026-11-02:

- exact estimate: `USD 0.904719159007`;
- billable size: `13,877,640` bytes;
- expected records: `247,815`;
- extending the end to `2026-09-12T00:00:00Z` costs `USD 1.024559810758` and is forbidden by the USD 1 limit.

## Safety gates

1. Databento historical monthly limit is USD 1.
2. User screenshot showed USD 125 remaining credits and USD 0 current balance.
3. Batch submission requires explicit approval of exactly `USD 0.904719159007` with `max_cash_charge_usd = 0`.
4. Immediately before submission, call free `metadata.get_cost` again. It must equal the approved estimate and remain below USD 1.
5. Cache and request ledger must show no prior batch for this exact request.
6. Submit at most one batch job. Never stream and never retry an ambiguous job creation.
7. If the provider reports job cost above the approved estimate or any cash-charge ambiguity, stop before download.

## Storage and verification

Store immutable raw output only below:

```text
D:\project\moex-arbitrage-lab\data\raw\databento\glbx-mdp3\gcz6\2026-09-01--2026-09-11\
```

Keep data ignored by Git. Record the request, provider job ID, file list, bytes, provider checksum, local SHA-256, row count, minimum/maximum timestamps, unique symbol/instrument IDs, and raw publication manifest. Reject HTML, unexpected formats, timestamps outside the frozen half-open range, symbol mismatches, partial files, or overwrite attempts.

After verified publication, disable `Enable pay as you go`. No analysis outcome is part of this acquisition task.

## Completion evidence

- Explicit user approval: `USD 0.904719159007`, credits only, cash charge `USD 0`.
- Databento job: `GLBX-20261102-D47PTAWTAD`, terminal state `done`; exactly one
  `batch.submit_job` ledger event.
- Raw directory: `data/raw/databento/glbx-mdp3/gcz6/2026-09-01--2026-09-11/`.
- Main payload: `glbx-mdp3-20260901-20260910.ohlcv-1s.csv`, `24,315,886` bytes,
  SHA-256 `489b0597e4401e5f3aefba88d36b77ffc5e41cc6162168ab1fce0c95e4fd9c77`.
- Validated data rows: `247,815`; symbol `GCZ6`; instrument ID `41271`; zero timestamp ties,
  decreasing timestamp steps, non-second-aligned records, inconsistent OHLC rows, and
  zero-volume rows.
- Provider auxiliary files and SHA-256: `condition.json`
  `d69d759c5fc35e6ff60325722cea80704139b8a01df37a095a0445f8508fd000`, `manifest.json`
  `25db929bb38c3e8da943cf75693618de3cedab7ff706612ccbb3fda56d65ee48`, `metadata.json`
  `f1dd8dd3021ba8f83be8c0196a05fb140239bcd2bbd0c9cad94dd0753c764c4a`.
- Offline idempotence verification returned all files `already_published=true`, no endpoints
  called, and `network_used=false`.
- Databento targeted tests after the provider-compatibility fixes: `176 passed`.
- Ledger: `logs/databento/requests.jsonl`; final decisions `step_b_complete` and
  `FREE_CONFIRMED` with `max_cash_charge_usd=0`.

## Post-run billing confirmation (appended 2026-11-03)

The user reported on 2026-11-03 that the Databento Billing page shows no charge for the
acquisition jobs ("ничего не списалось"). This closes the pending zero-cash confirmation.
The independent audit notes that the approval file was written by the operator seconds after
the Step A estimate and that `actual_cost_usd` stayed null; see
`docs/experiments/H-0001_SEPTEMBER_AUDIT_AND_EXPLORATION.md` §6.
