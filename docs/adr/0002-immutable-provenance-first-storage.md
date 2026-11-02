# ADR-0002: Immutable, provenance-first local storage

Status: accepted — 2026-11-02

## Decision

All project state lives under `D:\project\moex-arbitrage-lab`; project data on `C:` is rejected. Raw payloads are create-once, SHA-256 addressed in manifests, private and never overwritten. Derived artifacts form a deterministic lineage from raw checksum through schema/config/code versions.

## Consequences

P0 implements only minimal daily-file publication and path guards. The legacy streaming segment/replay subsystem is deferred. Generated market data is ignored by Git; redistribution is source-license dependent.

