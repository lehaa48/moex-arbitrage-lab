"""Acquisition manifest v1 (technical specification v0.1 section 4, cost policy)."""

import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from moex_arbitrage_lab.contracts import (
    MANIFEST_VERSION,
    AcquisitionManifestV1,
    manifest_from_json_bytes,
    manifest_to_json_bytes,
    sha256_hex,
)

Factory = Callable[..., dict[str, object]]
FAKE_SECRET = "synthetic-secret-value-0123456789"  # noqa: S105 - obvious fake


def test_manifest_carries_the_required_provenance_fields() -> None:
    required = {
        "provider",
        "dataset",
        "request_parameters",
        "retrieval_started_utc_ns",
        "retrieval_completed_utc_ns",
        "estimated_cost",
        "actual_cost_if_known",
        "cost_decision",
        "cost_ledger_request_id",
        "license_url",
        "license_version",
        "response_status",
        "response_metadata",
        "payload_bytes",
        "payload_sha256",
        "source_format_version",
    }
    assert MANIFEST_VERSION == "acquisition_manifest_v1"
    assert required <= set(AcquisitionManifestV1.model_fields)
    assert all(info.is_required() for info in AcquisitionManifestV1.model_fields.values())


def test_valid_manifest_round_trips_byte_stably(
    make_manifest_fields: Factory, synthetic_payload: bytes
) -> None:
    manifest = AcquisitionManifestV1.model_validate(make_manifest_fields())

    data = manifest_to_json_bytes(manifest)

    assert manifest_from_json_bytes(data) == manifest
    assert manifest_to_json_bytes(manifest_from_json_bytes(data)) == data
    assert manifest.payload_sha256 == sha256_hex(synthetic_payload)


@pytest.mark.parametrize(
    ("field", "mapping"),
    [
        ("request_parameters", {"token": FAKE_SECRET}),
        ("request_parameters", {"api_key": FAKE_SECRET}),
        ("request_parameters", {"X-Api-Key": FAKE_SECRET}),
        ("request_parameters", {"password": FAKE_SECRET}),
        ("response_metadata", {"Authorization": FAKE_SECRET}),
        ("response_metadata", {"Set-Cookie": FAKE_SECRET}),
        ("response_metadata", {"x-echo": f"Bearer {FAKE_SECRET}"}),
    ],
)
def test_secrets_are_refused_and_never_echoed(
    make_manifest_fields: Factory, field: str, mapping: dict[str, str]
) -> None:
    with pytest.raises(ValidationError) as caught:
        AcquisitionManifestV1.model_validate(make_manifest_fields(**{field: mapping}))

    assert FAKE_SECRET not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cost_decision", "STOP_USER_DECISION"),
        ("estimated_cost", Decimal(-1)),
        ("estimated_cost", 0.0),
        ("actual_cost_if_known", Decimal("-0.01")),
        ("license_url", "http://insecure.invalid/license"),
        ("payload_sha256", "0" * 63),
        ("payload_path", "../outside.bin"),
        ("request_end_utc_ns", None),
        ("response_status", 99),
        ("manifest_version", "acquisition_manifest_v2"),
        ("venue", "NYMEX"),
    ],
)
def test_invalid_manifest_values_are_refused(
    make_manifest_fields: Factory, field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        AcquisitionManifestV1.model_validate(make_manifest_fields(**{field: value}))


def test_request_interval_is_half_open_and_ordered(make_manifest_fields: Factory) -> None:
    with pytest.raises(ValidationError, match="half-open"):
        AcquisitionManifestV1.model_validate(
            make_manifest_fields(request_start_utc_ns=5, request_end_utc_ns=5)
        )


def test_unknown_field_is_refused_in_json(make_manifest_fields: Factory) -> None:
    raw = json.loads(
        manifest_to_json_bytes(AcquisitionManifestV1.model_validate(make_manifest_fields()))
    )
    raw["token"] = FAKE_SECRET

    with pytest.raises(ValidationError):
        manifest_from_json_bytes(json.dumps(raw).encode())


# --- review corrections: invariants and canonical serialization ------------------------------


@pytest.mark.parametrize("completed_offset", [0, 1])
def test_retrieval_completion_at_or_after_start_is_accepted(
    make_manifest_fields: Factory, completed_offset: int
) -> None:
    started = 1_796_000_000_000_000_000
    manifest = AcquisitionManifestV1.model_validate(
        make_manifest_fields(
            retrieval_started_utc_ns=started,
            retrieval_completed_utc_ns=started + completed_offset,
        )
    )
    assert manifest.retrieval_completed_utc_ns >= manifest.retrieval_started_utc_ns


def test_retrieval_completed_before_start_is_refused(make_manifest_fields: Factory) -> None:
    with pytest.raises(ValidationError, match="retrieval_completed_utc_ns must be >="):
        AcquisitionManifestV1.model_validate(
            make_manifest_fields(retrieval_started_utc_ns=10, retrieval_completed_utc_ns=9)
        )


@pytest.mark.parametrize(
    "contract",
    [
        "GC.c.0",
        "COMEX:GC:CONT",
        "COMEX:GC:Z6",
        "COMEX:GC",
        "COMEX:GC:2026-02-30",
        "comex:gc:2026-12-29",
        "MOEX:GD:2026-12-17",
        "synthetic-contract",
    ],
)
def test_manifest_contract_is_explicit_and_agrees_with_venue(
    make_manifest_fields: Factory, contract: str
) -> None:
    with pytest.raises(ValidationError, match="canonical_contract"):
        AcquisitionManifestV1.model_validate(make_manifest_fields(canonical_contract=contract))


def test_manifest_contract_exposes_its_validated_expiry(make_manifest_fields: Factory) -> None:
    manifest = AcquisitionManifestV1.model_validate(
        make_manifest_fields(venue="MOEX", canonical_contract="MOEX:GD:2026-12-17")
    )
    assert manifest.contract_expiry == date(2026, 12, 17)


def test_serialization_is_canonical_regardless_of_mapping_insertion_order(
    make_manifest_fields: Factory,
) -> None:
    params = [("symbols", "GCZ6"), ("limit", 10), ("compressed", False), ("encoding", None)]
    metadata = [("content-type", "application/octet-stream"), ("x-request", "synthetic-1")]
    forward = AcquisitionManifestV1.model_validate(
        make_manifest_fields(request_parameters=dict(params), response_metadata=dict(metadata))
    )
    backward = AcquisitionManifestV1.model_validate(
        make_manifest_fields(
            request_parameters=dict(reversed(params)),
            response_metadata=dict(reversed(metadata)),
        )
    )

    forward_bytes = manifest_to_json_bytes(forward)
    backward_bytes = manifest_to_json_bytes(backward)

    assert forward_bytes == backward_bytes
    assert sha256_hex(forward_bytes) == sha256_hex(backward_bytes)
    raw = json.loads(forward_bytes)
    assert list(raw["request_parameters"]) == sorted(raw["request_parameters"])
    assert list(raw["response_metadata"]) == sorted(raw["response_metadata"])
    # Round trip: parsing and re-serializing reproduces the same bytes.
    assert manifest_to_json_bytes(manifest_from_json_bytes(backward_bytes)) == forward_bytes
    assert manifest_from_json_bytes(forward_bytes) == backward


def test_unsorted_json_input_is_reserialized_canonically(make_manifest_fields: Factory) -> None:
    canonical = manifest_to_json_bytes(AcquisitionManifestV1.model_validate(make_manifest_fields()))
    raw = json.loads(canonical)
    raw["request_parameters"] = dict(reversed(list(raw["request_parameters"].items())))
    shuffled = json.dumps(raw).encode()

    assert shuffled != canonical
    assert manifest_to_json_bytes(manifest_from_json_bytes(shuffled)) == canonical
