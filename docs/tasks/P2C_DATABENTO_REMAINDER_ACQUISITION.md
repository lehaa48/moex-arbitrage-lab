# P2C — Databento GCZ6 remainder acquisition

Status: acquisition complete; post-run Billing screenshot pending.

## Owned paths

- `docs/tasks/P2C_DATABENTO_REMAINDER_ACQUISITION.md`
- `src/moex_arbitrage_lab/sources/databento_history/`
- `tests/unit/databento_history/`
- ignored run evidence under `artifacts/approvals/databento/`,
  `artifacts/manifests/raw/databento/`, `reports/generated/databento/`,
  `logs/databento/`, and `data/raw/databento/`

## Objective

Acquire only the missing COMEX/CME `GCZ6` one-second OHLCV interval after the completed P2B
publication. Do not request the already published `[2026-09-01, 2026-09-11)` interval and do
not request, modify, or reprocess MOEX/T-Bank data.

## Frozen request and approval

```text
dataset  = GLBX.MDP3
schema   = ohlcv-1s
stype_in = raw_symbol
symbols  = GCZ6
start    = 2026-09-11T00:00:00Z  (inclusive)
end      = 2026-11-01T00:00:00Z  (exclusive)
delivery = one batch download
```

- Exact free estimate: `USD 4.154946878552`.
- Billable size: `63,733,432` bytes.
- Expected records: `1,138,097`.
- User approval: one batch, credits only, maximum cash charge `USD 0`.
- Databento historical monthly limit required for the combined monthly usage: `USD 6`.

## Safety and acceptance

Immediately before submission, free `metadata.get_cost` must exactly match the approved
estimate. Cache and ledger must show no prior job for this exact request hash. Submit no more
than one batch; an ambiguous creation is never retried. Stop before download if provider job
cost exceeds approval or any cash-charge fact is ambiguous. Publish verified files create-once
below `data/raw/databento/glbx-mdp3/gcz6/2026-09-11--2026-11-01/`, with provider and local
SHA-256, manifests, row/time/symbol reconciliation, and an offline idempotence check.

## Completion evidence

- Step A request SHA-256: `7b184f6b3711a9f4847c3bb47b810782e896fb44e050a632ed72b6d09c939dea`.
- Step A report SHA-256: `86ddbfddf4d22988acc01ee8a639a83ef2cbef9c0d29c6750ef289988ea83168`.
- The immediately preceding provider estimate exactly matched the approved
  `USD 4.154946878552`, `63,733,432` bytes, and `1,138,097` records.
- Exactly one `batch.submit_job` ledger event created job `GLBX-20261102-DT7X3SEQBK`.
- CSV: `glbx-mdp3-20260911-20261031.ohlcv-1s.csv`, `111,649,273` local bytes,
  `1,138,097` rows, SHA-256
  `d80abd2e4c8311e8d2594ef03e6653788e9fb0c49c44f611db94d79717fdf929`.
- Reconciliation: only `GCZ6`, instrument `41271`; no timestamp ties or decreasing steps;
  all timestamps second-aligned; no zero-volume or inconsistent-OHLC rows.
- Offline repeat: exit `0`, no endpoints called, `network_used=false`,
  `job_created_now=false`; all files were already published.
- Combined with P2B: `1,385,912` CSV rows and `135,965,159` local CSV bytes; the first
  segment's maximum timestamp is strictly before this segment's minimum timestamp.
- Git HEAD: `9a28c349c71a235e2cca90873e380d9c909160dc`; Databento source/test tree SHA-256:
  `43b52e8e72a81165c22902da6b709be46de84b848170168f7745462aa30293ee`; frozen
  seconds-screen config SHA-256:
  `6619460ef629a1cd0c2e5e5a8c0d0ff01f2fd8990b5c15370b651c01c3000315`.
- Offline adapter tests: `176 passed` (exit `0`). Hygiene/secrets tests: `35 passed`
  (exit `0`). `git diff --check`: exit `0`; data, approval, manifests, reports and ledger
  are ignored by Git.

The provider job metadata did not expose an actual cost (`actual_cost_usd=null`). The run is
therefore accepted as a verified acquisition under the exact approved estimate and zero-cash
gate, while independent post-run confirmation of current balance `USD 0.00` remains pending
from the user's Databento Billing page.

The frozen v0.2 protocol originally said one two-month batch. The earlier user-imposed
`USD 1` cap required two adjacent, non-overlapping batch jobs instead. Raw observations and
the frozen interval are unchanged; this operational deviation must remain disclosed in later
evidence bundles.

## Post-run billing confirmation (appended 2026-11-03)

The user reported on 2026-11-03 that the Databento Billing page shows no charge for the
acquisition jobs ("ничего не списалось"). This closes the pending zero-cash confirmation.
The independent audit notes that the approval file was written by the operator seconds after
the Step A estimate and that `actual_cost_usd` stayed null; see
`docs/experiments/H-0001_SEPTEMBER_AUDIT_AND_EXPLORATION.md` §6.
