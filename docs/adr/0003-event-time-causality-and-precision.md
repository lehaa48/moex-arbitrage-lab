# ADR-0003: Event-time causality and honest precision

Status: accepted — 2026-11-02

## Decision

Exchange event, vendor receive/capture and local ingest times are distinct nullable fields stored as integer UTC nanoseconds without implying clock accuracy. Alignment is strict backward/half-open with staleness, session and tie policies. Horizons unsupported by actual timestamp quantum, clock semantics or density are reported unsupported.

## Consequences

No centered windows, future interpolation or unrestricted carry-forward. A local retrieval time never fills a missing event time. Subsecond evidence can be rejected as timestamp-semantics-insufficient even when fields are represented in nanoseconds.

