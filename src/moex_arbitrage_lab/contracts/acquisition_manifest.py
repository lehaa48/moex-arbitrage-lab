"""Raw acquisition manifest, version 1 (technical specification v0.1, section 4).

One manifest per immutable raw payload: provider, exact request parameters, local retrieval
times, cost facts and the zero-charge decision (docs/COST_AND_PURCHASE_POLICY.md), license
reference, API result metadata without secrets, byte count and SHA-256.

Secrets never enter a manifest: parameter and metadata keys that look like credentials
(token, key, secret, auth, ...) and ``Bearer`` values are refused. Retrieval times are local
wall-clock observations and are never event times; completion is never before start.
``canonical_contract`` uses the shared validated ``<VENUE>:<ROOT>:<YYYY-MM-DD>`` identifier and
must agree with ``venue``. Serialization is canonical (mapping keys sorted).
"""

import re
from datetime import date
from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

from moex_arbitrage_lab.contracts.types import (
    CanonicalContract,
    CurrencyCode,
    ExactDecimal,
    Identifier,
    NonNegativeInt64,
    RelativePosixPath,
    Sha256Hex,
    SourceText,
    UtcNanos,
    parse_canonical_contract,
)
from moex_arbitrage_lab.settings.secrets import is_secret_name

MANIFEST_VERSION: Final = "acquisition_manifest_v1"

ParameterValue = Identifier | NonNegativeInt64 | bool | None
HttpsUrl = Annotated[str, StringConstraints(max_length=2048, pattern=r"^https://[^\s]+$")]
_SECRET_VALUE: Final = re.compile(r"^\s*(bearer|basic|token)\s", re.IGNORECASE)


def _check_no_secrets(mapping: dict[str, object], what: str) -> None:
    for key, value in mapping.items():
        if is_secret_name(key):
            # The key is reported, the value never.
            raise ValueError(f"{what} key {key!r} looks like a credential; secrets are refused")
        if isinstance(value, str) and _SECRET_VALUE.match(value):
            raise ValueError(f"{what} value for {key!r} looks like a credential; refused")


class AcquisitionManifestV1(BaseModel):
    """Provenance of one raw payload; immutable once constructed."""

    # Rejected inputs are never echoed in errors: a refused value may be a secret.
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    manifest_version: Literal["acquisition_manifest_v1"]
    acquisition_id: Identifier
    provider: Identifier
    dataset: Identifier
    source_schema: Identifier
    source_format_version: Identifier | None
    venue: Literal["MOEX", "COMEX"]
    # <VENUE>:<ROOT>:<YYYY-MM-DD expiry>; the expiry is part of the validated identifier.
    canonical_contract: CanonicalContract
    trading_date: date
    request_parameters: dict[Identifier, ParameterValue]
    request_start_utc_ns: UtcNanos | None
    request_end_utc_ns: UtcNanos | None
    # Local wall clock of this machine: provenance only, never an event time.
    retrieval_started_utc_ns: UtcNanos
    retrieval_completed_utc_ns: UtcNanos
    cost_ledger_request_id: Identifier
    estimated_cost: ExactDecimal = Field(ge=0)
    estimated_cost_currency: CurrencyCode
    actual_cost_if_known: ExactDecimal | None = Field(ge=0)
    # Only FREE_CONFIRMED permits a request; a manifest cannot exist without it.
    cost_decision: Literal["FREE_CONFIRMED"]
    license_url: HttpsUrl
    license_version: Identifier
    response_status: Annotated[int, Field(ge=100, le=599)] | None
    response_metadata: dict[Identifier, SourceText]
    payload_path: RelativePosixPath
    payload_bytes: NonNegativeInt64
    payload_sha256: Sha256Hex
    publisher_version: Identifier

    @field_validator("request_parameters", "response_metadata")
    @classmethod
    def _no_secrets_and_sorted_keys(cls, value: dict[str, object]) -> dict[str, object]:
        _check_no_secrets(value, "manifest")
        return dict(sorted(value.items()))

    @field_serializer("request_parameters", "response_metadata")
    def _serialize_sorted(self, value: dict[str, object]) -> dict[str, object]:
        # Canonical form: mapping entries by key (code-point order), whatever the input order.
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def _contract_agrees_with_venue(self) -> Self:
        if parse_canonical_contract(self.canonical_contract).venue != self.venue:
            raise ValueError("canonical_contract must agree with venue")
        return self

    @model_validator(mode="after")
    def _request_interval_is_increasing(self) -> Self:
        start, end = self.request_start_utc_ns, self.request_end_utc_ns
        if (start is None) != (end is None):
            raise ValueError("request_start_utc_ns and request_end_utc_ns are set together")
        if start is not None and end is not None and end <= start:
            raise ValueError("request interval must be half-open [start, end) with end > start")
        return self

    @model_validator(mode="after")
    def _retrieval_completes_after_it_starts(self) -> Self:
        if self.retrieval_completed_utc_ns < self.retrieval_started_utc_ns:
            raise ValueError("retrieval_completed_utc_ns must be >= retrieval_started_utc_ns")
        return self

    @property
    def contract_expiry(self) -> date:
        """Expiry carried by ``canonical_contract`` (validated calendar date)."""
        return parse_canonical_contract(self.canonical_contract).expiry


def manifest_to_json_bytes(manifest: AcquisitionManifestV1) -> bytes:
    """Canonical JSON: fixed field order, mapping entries sorted by key, UTF-8, no whitespace.

    Two manifests with the same content serialize to the same bytes (and SHA-256) regardless of
    the insertion order of ``request_parameters``/``response_metadata``."""
    return manifest.model_dump_json().encode("utf-8")


def manifest_from_json_bytes(data: bytes) -> AcquisitionManifestV1:
    """Parse and fully validate a manifest produced by ``manifest_to_json_bytes``."""
    return AcquisitionManifestV1.model_validate_json(data)
