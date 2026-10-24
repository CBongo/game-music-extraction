"""Helpers for constructing and checking instrument-analysis/v1 bundles."""

from __future__ import annotations

from typing import Any, Iterable

SCHEMA_VERSION = "instrument-analysis/v1"


def make_bundle(records: Iterable[dict[str, Any]], generator: str) -> dict[str, Any]:
    """Return a versioned bundle and enforce cross-field semantic invariants."""
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "generator": {"name": generator},
        "records": list(records),
    }
    validate_semantics(bundle)
    return bundle


def validate_semantics(bundle: dict[str, Any]) -> None:
    """Validate invariants that JSON Schema cannot express directly."""
    if bundle.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {bundle.get('schema_version')!r}")

    record_ids: set[str] = set()
    for record in bundle.get("records", []):
        record_id = record.get("record_id")
        if record_id in record_ids:
            raise ValueError(f"duplicate record_id: {record_id!r}")
        record_ids.add(record_id)

        for candidate in record.get("analysis", {}).get("routing", []):
            route = candidate.get("route", {})
            if route.get("kind") == "gm_program":
                zero_based = route.get("program_0based")
                one_based = route.get("program_1based")
                if not isinstance(zero_based, int) or one_based != zero_based + 1:
                    raise ValueError(
                        f"{record_id}: GM program numbering disagrees "
                        f"({zero_based!r} vs {one_based!r})"
                    )
