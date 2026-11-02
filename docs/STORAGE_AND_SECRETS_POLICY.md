# Storage and secrets policy

## Root and layout

All created state must resolve beneath `D:\project\moex-arbitrage-lab`:

```text
data/raw/<source>/<dataset>/<contract>/<trading_date>/
data/normalized/trades/schema_v1/<venue>/<contract>/<trading_date>/
data/aligned/<experiment_id>/<run_id>/
artifacts/manifests/
artifacts/checksums/
reports/generated/<experiment_id>/<run_id>/
cache/
tmp/
logs/
```

P0 must reject absolute paths outside the root, traversal, symlink/junction escapes, unresolved environment variables and a root on `C:`. It must not silently fall back to a user profile or system temp directory.

## Immutability and provenance

- Raw payloads are create-once and never overwritten.
- Each payload receives SHA-256, byte count, source request parameters, retrieval time, license reference and schema/source version.
- Derived artifacts identify every input checksum and deterministic transform version.
- UTC is canonical. Event, receive and local-ingest timestamps are separate nullable fields.
- Re-runs use a new run ID; they never mutate a prior evidence bundle.

## Secrets

Secrets arrive only through environment variables, are never echoed completely, and never enter manifests, command lines committed to Git, reports or exception payloads. `.env` is local and ignored; `.env.example` contains names only. Tests use obvious fake values.

## Retention

Storage permission is source-specific. If redistribution/retention rights remain unknown, mark the source `BLOCKED_LICENSE` and do not acquire it. Deletion, if later required by license, must target a manifest-defined payload and preserve a non-sensitive tombstone/audit record.

