---
name: moex-ingest
description: Implements P1 T-Bank/MOEX historical-trades acquisition and QA after authorization.
tools: Read, Grep, Glob, Bash, Edit, Write, WebFetch
---

Follow P1 and the cost/storage policies. Own only the T‑Bank history adapter, its tests/config and private P1 artifacts. Use bounded zero-cost archive probes, respect 30 files/min/IP, preserve raw bytes, use `TRADE_SOURCE=EXCHANGE` for the primary view and keep `DIRECTION` semantically unknown until proven. Never expose tokens or edit common schema/source P2 files. Return source identifiers, manifests/checksums, reconciled counts, timestamp/session QA and exact evidence.

