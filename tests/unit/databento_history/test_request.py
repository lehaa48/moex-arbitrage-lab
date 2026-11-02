"""The frozen request cannot be changed and each step reaches only its allowlisted endpoints."""

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

import moex_arbitrage_lab.sources.databento_history as package
from moex_arbitrage_lab.sources.databento_history import request as frozen
from moex_arbitrage_lab.sources.databento_history.request import (
    END_UTC_NS,
    FROZEN_REQUEST,
    METERED_ENDPOINTS,
    REQUEST_SHA256,
    START_UTC_NS,
    STEP_A_ENDPOINTS,
    STEP_B_ENDPOINTS,
    Endpoint,
    FrozenBatchRequest,
    batch_job_params,
    endpoint_params,
    estimate_params,
    frozen_request_document,
    list_files_params,
    symbology_params,
)
from moex_arbitrage_lab.sources.databento_history.step_a import APPROVAL_VERSION
from moex_arbitrage_lab.sources.databento_history.step_b import StepBApprovalV1

VALID = FROZEN_REQUEST.model_dump()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("dataset", "XNAS.ITCH"),
        ("record_schema", "trades"),
        ("record_schema", "mbo"),
        ("record_schema", "mbp-1"),
        ("record_schema", "tbbo"),
        ("record_schema", "ohlcv-1m"),
        ("stype_in", "continuous"),
        ("stype_in", "parent"),
        ("symbols", "GC.c.0"),
        ("symbols", "GC.FUT"),
        ("symbols", "GCG7"),
        ("symbols", "ALL_SYMBOLS"),
        ("start", "2026-08-31T00:00:00Z"),
        ("end", "2026-11-02T00:00:00Z"),
        ("encoding", "dbn"),
        ("compression", "zstd"),
        ("pretty_px", False),
        ("pretty_ts", True),
        ("map_symbols", False),
        ("split_duration", "day"),
        ("delivery", "s3"),
        ("stype_out", "raw_symbol"),
    ],
)
def test_no_other_request_can_be_constructed(name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        FrozenBatchRequest.model_validate({**VALID, name: value})


def test_extra_fields_and_mutation_are_refused() -> None:
    with pytest.raises(ValidationError):
        FrozenBatchRequest.model_validate({**VALID, "limit": 10})
    with pytest.raises(ValidationError):
        FROZEN_REQUEST.symbols = "GCG7"  # type: ignore[misc, assignment]


def test_frozen_request_matches_the_task_exactly() -> None:
    assert frozen_request_document() == {
        "provider": "databento",
        "api_version": "v0",
        "dataset": "GLBX.MDP3",
        "schema": "ohlcv-1s",
        "stype_in": "raw_symbol",
        "stype_out": "instrument_id",
        "symbols": "GCZ6",
        "start": "2026-09-11T00:00:00Z",
        "end": "2026-11-01T00:00:00Z",
        "encoding": "csv",
        "compression": "none",
        "pretty_px": True,
        "pretty_ts": False,
        "map_symbols": True,
        "split_symbols": False,
        "split_duration": "none",
        "delivery": "download",
        "batch_jobs": 1,
    }
    assert START_UTC_NS == 1_789_084_800 * 10**9
    assert END_UTC_NS == 1_793_491_200 * 10**9
    assert len(REQUEST_SHA256) == 64


def test_every_builder_derives_from_the_frozen_request_only() -> None:
    assert estimate_params() == {
        "dataset": "GLBX.MDP3",
        "symbols": "GCZ6",
        "schema": "ohlcv-1s",
        "start": "2026-09-11T00:00:00Z",
        "end": "2026-11-01T00:00:00Z",
        "stype_in": "raw_symbol",
    }
    assert symbology_params() == {
        "dataset": "GLBX.MDP3",
        "symbols": "GCZ6",
        "stype_in": "raw_symbol",
        "stype_out": "instrument_id",
        "start_date": "2026-09-11",
        "end_date": "2026-11-01",
    }
    assert batch_job_params() == {
        **estimate_params(),
        "stype_out": "instrument_id",
        "encoding": "csv",
        "compression": "none",
        "pretty_px": "true",
        "pretty_ts": "false",
        "map_symbols": "true",
        "split_symbols": "false",
        "split_duration": "none",
        "delivery": "download",
    }
    values = {v for e in STEP_A_ENDPOINTS for v in endpoint_params(e).values()}
    assert values == {
        "GLBX.MDP3",
        "GCZ6",
        "ohlcv-1s",
        "raw_symbol",
        "instrument_id",
        "2026-09-11T00:00:00Z",
        "2026-11-01T00:00:00Z",
        "2026-09-11",
        "2026-11-01",
    }


def test_builders_take_no_request_arguments() -> None:
    # The only variable input in the whole parameter path is a provider-issued job id.
    for builder in (estimate_params, symbology_params, batch_job_params):
        assert builder.__code__.co_argcount == 0
    assert list_files_params("GLBX-20261102-ABC") == {"job_id": "GLBX-20261102-ABC"}
    for bad in ("", "../x", "a b", "x" * 80, "id?x=1"):
        with pytest.raises(ValueError, match="job id"):
            list_files_params(bad)


def test_step_a_allowlist_has_no_batch_or_timeseries_endpoint() -> None:
    assert {e.value for e in STEP_A_ENDPOINTS} == {
        "metadata.list_schemas",
        "metadata.get_dataset_range",
        "symbology.resolve",
        "metadata.get_record_count",
        "metadata.get_billable_size",
        "metadata.get_cost",
    }
    assert not any(e.value.startswith(("batch", "timeseries")) for e in STEP_A_ENDPOINTS)
    assert not STEP_A_ENDPOINTS & METERED_ENDPOINTS


def test_step_b_allowlist_and_metered_set() -> None:
    assert {e.value for e in STEP_B_ENDPOINTS} == {
        "metadata.get_cost",
        "batch.submit_job",
        "batch.list_jobs",
        "batch.list_files",
        "batch.download",
    }
    assert {Endpoint.CREATE_BATCH_JOB} == METERED_ENDPOINTS
    assert not any(e.value.startswith("timeseries") for e in Endpoint)


def test_approval_model_version_matches_step_a_template() -> None:
    field = StepBApprovalV1.model_fields["approval_version"]
    assert APPROVAL_VERSION in str(field.annotation)


def test_every_provider_assumption_is_marked_for_verification() -> None:
    source = Path(frozen.__file__).read_text(encoding="utf-8")
    assert source.count("VERIFY") >= 10
    assert "https://databento.com/docs/api-reference-historical" in source


def test_no_identifier_in_the_package_offers_orders_or_job_pushing() -> None:
    words = ("order", "broker", "execution", "place_", "submit", "sandbox")
    names: list[str] = []
    for module in Path(package.__file__).parent.glob("*.py"):
        names.append(module.stem)
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.append(node.name)
            elif isinstance(node, ast.Name | ast.arg):
                names.append(node.id if isinstance(node, ast.Name) else node.arg)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
    offending = [n for n in names if any(w in n.lower() for w in words)]
    # The refusal itself (raised when orders are requested) is the only allowed name.
    assert [n for n in offending if n != "OrderCapabilityError"] == []
