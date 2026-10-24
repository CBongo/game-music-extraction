"""Explicit, bounded join of raw usage and grounded metadata pitch."""

from __future__ import annotations

from copy import deepcopy
from math import isclose, isfinite
from typing import Any

from .schema import validate_semantics

# Only known decoder/adapter spellings. Other driver names must match exactly.
_PLATFORMS = {"psx": "Sony PlayStation", "snes": "Super Nintendo"}


def _index(bundle: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    validate_semantics(bundle)
    records = bundle.get("records")
    if not isinstance(records, list):
        raise ValueError(f"{label} records must be an array")
    indexed = {}
    for record in records:
        rid = record.get("record_id")
        if not isinstance(rid, str) or not rid or rid in indexed:
            raise ValueError(f"{label} has missing, invalid, or duplicate record_id {rid!r}")
        indexed[rid] = record
    return indexed


def _check_sources(usage: dict[str, Any], metadata: dict[str, Any]) -> None:
    a, b = usage["source"], metadata["source"]
    for source in (a, b):
        if not all(isinstance(source.get(key), str) and source[key]
                   for key in ("platform", "game_or_module", "driver")):
            raise ValueError("source lacks platform, game_or_module, or driver")
    pa = _PLATFORMS.get(a["platform"], a["platform"])
    pb = _PLATFORMS.get(b["platform"], b["platform"])
    if pa != pb or a["game_or_module"] != b["game_or_module"]:
        raise ValueError(f"incompatible source for {usage['record_id']!r}")
    if a["driver"] != b["driver"] and not (
        pa == "Sony PlayStation" and
        {a["driver"], b["driver"]} == {"AKAO", "AKAO PSX"}
    ):
        raise ValueError(f"incompatible driver for {usage['record_id']!r}")
    for field in ("source_patch_command", "song_local_slot"):
        x, y = usage["identity"].get(field), metadata["identity"].get(field)
        if x is not None and y is not None and (type(x) is not type(y) or x != y):
            raise ValueError(f"incompatible {field} for {usage['record_id']!r}")


def _detail(note: dict[str, Any], metadata: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    detail: dict[str, Any] = {"metadata_record_id": metadata["record_id"]}
    if note.get("pitch_context_complete") is not True:
        return None, {**detail, "status": "skipped", "reason": "pitch_context_incomplete"}
    key = note["raw_sequence_note"]
    for field in ("global_transpose_semitones", "track_transpose_semitones", "ramp_transpose_semitones"):
        delta = note.get(field, 0)
        if type(delta) is not int:
            raise ValueError(f"invalid {field}")
        key += delta
    detail["driver_transposed_note"] = key
    observation = metadata["observation"]
    zones = observation.get("zones", [])
    if zones:
        matches = [z for z in zones if
                   z.get("key_range", {}).get("minimum", 0) <= key <=
                   z.get("key_range", {}).get("maximum", 127)]
        if len(matches) != 1:
            return None, {**detail, "status": "skipped",
                          "reason": "ambiguous_zone" if matches else "no_matching_zone"}
        zone = matches[0]
        detail["zone_id"] = zone["zone_id"]
        velocity = zone.get("velocity_range")
        if velocity and velocity != {"minimum": 0, "maximum": 127}:
            return None, {**detail, "status": "skipped", "reason": "velocity_context_unknown"}
        pitch = zone.get("pitch", {})
    else:
        pitch = observation.get("pitch", {})
    offset = pitch.get("observed_pitch_offset")
    if not offset:
        return None, {**detail, "status": "skipped", "reason": "missing_observed_pitch_offset"}
    basis = offset.get("basis")
    if basis not in {"source_fact", "hardware_fact", "measured", "inferred", "curated"}:
        raise ValueError("invalid observed_pitch_offset basis")
    if basis == "curated":
        return None, {**detail, "status": "skipped", "reason": "curated_pitch_offset"}
    semitones, cents = offset["semitones"], offset["residual_cents"]
    if (type(semitones) is not int or isinstance(cents, bool)
            or not isinstance(cents, (int, float)) or not isfinite(cents)
            or not -50 <= cents <= 50):
        raise ValueError("invalid observed_pitch_offset")
    confidence = offset.get("confidence")
    if confidence is not None:
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError("invalid observed_pitch_offset confidence")
        if confidence == 0:
            return None, {**detail, "status": "skipped", "reason": "zero_confidence_offset"}
    if str(offset.get("provenance", {}).get("note", "")).startswith("conflict:"):
        return None, {**detail, "status": "skipped", "reason": "conflicting_pitch_references"}
    detail.update(status="calibrated", offset_semitones=semitones,
                  residual_cents=cents, offset_basis=offset["basis"],
                  offset_provenance=deepcopy(offset["provenance"]))
    return offset, detail


def _verify_set(note: dict[str, Any], key: str, value: int | float) -> None:
    old = note.get(key)
    if old is not None and (isinstance(old, bool) or not isinstance(old, (int, float))
                            or not isclose(old, value, rel_tol=0, abs_tol=1e-9)):
        raise ValueError(f"conflicting preexisting {key}")
    note[key] = value


def calibrate_usage(
    usage_bundle: dict[str, Any], metadata_bundle: dict[str, Any],
    *, record_links: dict[str, str],
) -> dict[str, Any]:
    """Return a deep-copied usage bundle with defensible sounding notes.

    Explicit links may cover a subset of usage records, and metadata may be
    shared across records. Unlinked records remain unchanged. Missing pitch
    context, missing observed offsets, and ambiguous zones are
    recorded as diagnostics under note provenance parameters.
    """
    usage_records = _index(usage_bundle, "usage")
    metadata_records = _index(metadata_bundle, "metadata")
    if not isinstance(record_links, dict) or any(
        not isinstance(k, str) or k not in usage_records for k in record_links
    ):
        raise ValueError("unknown usage record_id")
    targets = list(record_links.values())
    if any(not isinstance(v, str) or v not in metadata_records for v in targets):
        raise ValueError("unknown metadata record_id")
    for uid, mid in record_links.items():
        _check_sources(usage_records[uid], metadata_records[mid])

    result = deepcopy(usage_bundle)
    for record in result["records"]:
        if record["record_id"] not in record_links:
            continue
        metadata = metadata_records[record_links[record["record_id"]]]
        usage = record.get("observation", {}).get("usage")
        if usage is None:
            continue
        overall: list[int] = []
        observed_occurrences = False
        for sequence in usage.get("sequences", []):
            notes = sequence.get("note_occurrences", [])
            if not notes:
                continue
            observed_occurrences = True
            integral: set[int] = set()
            for note in notes:
                offset, detail = _detail(note, metadata)
                if offset is not None:
                    tuning = offset["semitones"] + offset["residual_cents"] / 100
                    _verify_set(note, "sample_tuning_semitones", tuning)
                    sounding = detail["driver_transposed_note"] + tuning
                    if 0 <= sounding <= 127:
                        _verify_set(note, "sounding_midi_note", sounding)
                    else:
                        if "sounding_midi_note" in note:
                            raise ValueError("conflicting preexisting out-of-range sounding_midi_note")
                        detail.update(status="out_of_range", reason="sounding_note_outside_midi_range",
                                      calculated_sounding_note=sounding)
                sounding = note.get("sounding_midi_note")
                if sounding is not None:
                    if (isinstance(sounding, bool) or not isinstance(sounding, (int, float))
                            or not isfinite(sounding) or not 0 <= sounding <= 127):
                        raise ValueError("invalid preexisting sounding_midi_note")
                    if float(sounding).is_integer():
                        integral.add(int(sounding))
                    else:
                        detail["aggregate_reason"] = "fractional_sounding_note"
                provenance = note.setdefault("provenance", {"method": "raw usage observation"})
                parameters = provenance.setdefault("parameters", {})
                old_detail = parameters.get("pitch_calibration")
                if old_detail is not None and old_detail != detail:
                    raise ValueError("conflicting preexisting pitch_calibration provenance")
                parameters["pitch_calibration"] = detail
            values = sorted(integral)
            old_values = sequence.get("sounding_midi_notes")
            if old_values is not None and sorted(set(old_values)) != values:
                raise ValueError("conflicting preexisting sounding_midi_notes")
            if values:
                sequence["sounding_midi_notes"] = values
            else:
                sequence.pop("sounding_midi_notes", None)
            overall.extend(values)
        if overall:
            expected = {"minimum": min(overall), "maximum": max(overall)}
            old_range = usage.get("overall_sounding_note_range")
            if old_range is not None and old_range != expected:
                raise ValueError("conflicting preexisting overall_sounding_note_range")
            usage["overall_sounding_note_range"] = expected
        elif observed_occurrences and "overall_sounding_note_range" in usage:
            raise ValueError("conflicting preexisting overall_sounding_note_range")
    validate_semantics(result)
    return result
