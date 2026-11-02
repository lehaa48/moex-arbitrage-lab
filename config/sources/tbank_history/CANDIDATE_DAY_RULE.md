# P1 candidate-day rule (preregistered)

Written by the main agent on 2026-11-02, before any T‑Bank archive request, before any GDZ6 or GCZ6 trade data was seen, and without any lead-lag result (none exists). The rule uses only official calendars, the contract specification and the holdout plan. It must not be changed after the probe; a change creates a new, separately approved rule version.

## Rule v1

1. Universe: calendar days 2026‑10‑01 … 2026‑10‑31.
2. Keep MOEX derivatives-market trading days that are Tuesday, Wednesday or Thursday with the regular schedule (no holiday, shortened, extended or otherwise modified session in official MOEX calendars/notices).
3. Keep only days that are also ordinary COMEX GC trading days (no CME holiday schedule or early close), so the same day can later serve P2/P3.
4. Exclude the first and the last MOEX trading day of October 2026.
5. Keep only days within the first 60 % (rounded down) of October 2026 MOEX trading days in chronological sequence. H‑0001 suggests a 60/20/20 chronological train/validation/holdout split by complete days (HL‑0001, still unassigned and UNOPENED); this keeps the one-day plumbing slice out of any future validation/holdout region.
6. Not near expiry: the contract's last trading day must be at least 30 calendar days after the candidate day.
7. Sort the remaining candidates chronologically and select the **first**. Only that day is probed. If the probe does not return exactly the expected response, stop with `BLOCKED`: the next candidate is probed only after review approval.

No trade data, price, volume, liquidity figure or lead-lag outcome is used by this rule. Liquidity and archive completeness are measured afterwards and reported, not used to re-select.

## Application

Filled in after the official calendar evidence (workstream A) is cross-checked: candidates, rejections with reasons, and the selected day are recorded in the P1 evidence and in `gdz6.toml`.
