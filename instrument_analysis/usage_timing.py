"""Audit converter timing without promoting it to native playback evidence."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path

from .comparisons import _hash
from .usage import query_usage


def _number(value, label, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or positive and value == 0:
        raise ValueError(f"invalid {label}")
    return value


def audit_usage_timing(bundle_path, *, source_record_id, sequence_id, track_id=None):
    """Return a traceable converter timeline; unknown sound/effects stay blocked."""
    path = Path(bundle_path).resolve(strict=True)
    digest = _hash(path)
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if bundle.get("schema_version") != "instrument-analysis/v1":
        raise ValueError("unsupported usage bundle")
    records = [r for r in bundle.get("records", []) if r.get("record_id") == source_record_id]
    if len(records) != 1:
        raise ValueError("source record must identify exactly one record")
    record = records[0]
    contexts = record.get("observation", {}).get("usage", {}).get("provenance", {}).get("parameters", {}).get("sequence_render_contexts", [])
    contexts = [c for c in contexts if type(c.get("sequence_id")) is type(sequence_id) and c["sequence_id"] == sequence_id]
    if len(contexts) != 1:
        raise ValueError("missing or ambiguous sequence render context")
    context = contexts[0]
    timebase = _number(context.get("ticks_per_quarter"), "timebase", True)
    default = _number(context.get("default_microseconds_per_quarter"), "default tempo", True)
    tempos = {}
    for event in context.get("tempo_events", []):
        tick = _number(event.get("tick"), "tempo tick")
        value = _number(event.get("microseconds_per_quarter"), "tempo", True)
        if tick in tempos and tempos[tick] != value:
            raise ValueError("conflicting conductor tempos at one tick")
        tempos[tick] = value
    ordered = sorted(tempos.items())
    def seconds(tick):
        tick = _number(tick, "note tick")
        previous, tempo, total = 0, default, 0.0
        for boundary, value in ordered:
            if boundary > tick:
                break
            total += (boundary - previous) * tempo / timebase / 1e6
            previous, tempo = boundary, value
        return total + (tick - previous) * tempo / timebase / 1e6
    rows = query_usage({**bundle, "records": [record]}, sequence_id=sequence_id, track_id=track_id)
    if len(rows) != 1 or not rows[0]["note_occurrences"]:
        raise ValueError("no matching note occurrences")
    reasons = set()
    if context.get("effects_complete") is not True:
        reasons.add("source_effects_not_fully_normalized")
    if context.get("basis") == "legacy_ff3_midi_conversion":
        reasons.add("timing_is_converter_approximation_not_native_driver_receipt")
    events = []
    for index, note in enumerate(rows[0]["note_occurrences"]):
        time = note.get("time", {})
        if time.get("unit") != "midi_tick" or time.get("timebase") != timebase:
            raise ValueError("note timing unit or timebase mismatch")
        start = _number(time.get("value"), "note start")
        render = note.get("provenance", {}).get("parameters", {}).get("render_context", {})
        gate = render.get("gate_duration_ticks")
        velocity = render.get("velocity_0_127")
        if gate is None or velocity is None:
            raise ValueError("note lacks converter gate or velocity context")
        gate = _number(gate, "gate duration", True)
        if type(velocity) is not int or not 1 <= velocity <= 127:
            raise ValueError("invalid converter velocity")
        if render.get("effects_complete") is not True:
            reasons.add("note_effects_not_fully_normalized")
        sounding = note.get("sounding_midi_note")
        if sounding is None:
            reasons.add("sounding_pitch_not_grounded")
        else:
            _number(sounding, "sounding MIDI note")
            if sounding > 127:
                raise ValueError("sounding note outside MIDI range")
        if note.get("pitch_context_complete") is not True:
            reasons.add("pitch_context_incomplete")
        events.append({"event_id": f"occurrence-{index:05d}", "track_id": note.get("track_id"),
                       "source_event_index": note.get("event_index"), "raw_sequence_note": note["raw_sequence_note"],
                       "sounding_midi_note": sounding, "start_tick": start,
                       "source_step_duration_ticks": note.get("duration"), "converter_gate_ticks": gate,
                       "start_seconds": seconds(start), "duration_seconds": seconds(start + gate) - seconds(start),
                       "converter_velocity": velocity, "source_occurrence": deepcopy(note)})
    if _hash(path) != digest:
        raise ValueError("usage input changed during audit")
    return {"schema_version": "instrument-usage-timing-audit/v1", "source_record_id": source_record_id,
            "input": {"path": str(path), "sha256": digest}, "sequence_id": sequence_id,
            "timing_context": deepcopy(context), "status": "blocked" if reasons else "timeline_available_requires_source_audio_binding",
            "blocking_reasons": sorted(reasons), "events": events,
            "note": "Converter timing is retained as evidence. This report is not a native sequence render or an effects-complete recipe."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("usage", type=Path)
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--sequence-id", required=True, help="JSON integer or string")
    parser.add_argument("--track-id", help="JSON integer or string")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    result = audit_usage_timing(args.usage, source_record_id=args.record_id,
        sequence_id=json.loads(args.sequence_id), track_id=json.loads(args.track_id) if args.track_id else None)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
