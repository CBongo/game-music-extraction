"""Metadata-first SNES BRR pitch calibration adapters."""

from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import fmean
from typing import Any, Sequence


@dataclass(frozen=True)
class SnesPitchReference:
    """One driver pitch-register observation at a known raw sequence note."""

    register_value: int
    raw_note: int
    sounding_note: float | None = None
    basis: str = "source_fact"
    method: str = "reverse-engineered driver pitch table"
    confidence: float = 1.0


def decode_snes_pitch_table(
    data: bytes,
    *,
    offset: int,
    count: int,
    reference_index: int,
    reference_note: int,
    method: str,
) -> list[SnesPitchReference]:
    """Decode a little-endian driver pitch table into note/register facts."""
    if offset < 0 or count <= 0 or not 0 <= reference_index < count:
        raise ValueError("invalid pitch-table bounds or reference index")
    if not 0 <= reference_note <= 127:
        raise ValueError("reference_note must be a MIDI note from 0 through 127")
    if not method:
        raise ValueError("pitch-table method must be non-empty")
    end = offset + count * 2
    if end > len(data):
        raise ValueError(f"pitch table ends at 0x{end:x}, beyond input size 0x{len(data):x}")
    values = [int.from_bytes(data[pos:pos + 2], "little") for pos in range(offset, end, 2)]
    if any(value == 0 for value in values):
        raise ValueError("pitch table contains a zero register value")
    references = [
        SnesPitchReference(
            register_value=value,
            raw_note=reference_note + index - reference_index,
            method=f"{method}; table byte offset 0x{offset + index * 2:x}",
        )
        for index, value in enumerate(values)
    ]
    if any(not 0 <= ref.raw_note <= 127 for ref in references):
        raise ValueError("pitch table would produce a raw note outside MIDI range")
    return references


def _split_semitones(value: float) -> tuple[int, float]:
    semitones = math.floor(value + 0.5)
    cents = (value - semitones) * 100.0
    if cents < -50.0:
        semitones -= 1
        cents += 100.0
    elif cents > 50.0:
        semitones += 1
        cents -= 100.0
    return semitones, cents


def adapt_snes_brr_instrument(
    *,
    game_or_module: str,
    instrument_id: int | str,
    sample_id: int | str,
    references: Sequence[SnesPitchReference],
    source_path: str,
    pitch_scale_reference: int = 0x1000,
    dsp_sample_rate_hz: int = 32000,
    record_id: str | None = None,
    consistency_tolerance_cents: float = 15.0,
    source_patch_command: int | str | None = None,
    song_local_slot: int | str | None = None,
) -> dict[str, Any]:
    """Create one SNES BRR observation from driver pitch facts.

    A reference with ``sounding_note`` derives both the unit-playback sample
    pitch and the observed raw-to-sounding offset. References without a known
    sounding pitch are retained but do not manufacture either conclusion.
    """
    if not references:
        raise ValueError("at least one SNES pitch reference is required")
    if not game_or_module or not source_path:
        raise ValueError("game and source_path must be non-empty")
    if record_id == "":
        raise ValueError("record_id must be non-empty when provided")
    if pitch_scale_reference <= 0 or dsp_sample_rate_hz <= 0:
        raise ValueError("pitch scale and DSP sample rate must be positive")
    if consistency_tolerance_cents < 0:
        raise ValueError("consistency tolerance cannot be negative")

    decoded: list[dict[str, Any]] = []
    valid_bases = {"source_fact", "hardware_fact", "measured", "inferred", "curated"}
    for ref in references:
        if ref.register_value <= 0:
            raise ValueError("pitch register values must be positive")
        if not 0 <= ref.raw_note <= 127:
            raise ValueError("raw_note must be a MIDI note from 0 through 127")
        if ref.sounding_note is not None and not 0 <= ref.sounding_note <= 127:
            raise ValueError("sounding_note must be a MIDI note from 0 through 127")
        if ref.basis not in valid_bases:
            raise ValueError(f"invalid epistemic basis: {ref.basis!r}")
        if not ref.method:
            raise ValueError("reference method must be non-empty")
        if not 0 <= ref.confidence <= 1:
            raise ValueError("reference confidence must be between 0 and 1")
        ratio = ref.register_value / pitch_scale_reference
        register_semitones = 12.0 * math.log2(ratio)
        item: dict[str, Any] = {
            "register_value": ref.register_value,
            "raw_note": ref.raw_note,
            "ratio": ratio,
            "register_semitones": register_semitones,
            "playback_rate_hz": dsp_sample_rate_hz * ratio,
            "basis": ref.basis,
            "method": ref.method,
            "confidence": ref.confidence,
        }
        if ref.sounding_note is not None:
            item["sounding_note"] = ref.sounding_note
            item["native_pitch_at_unity"] = ref.sounding_note - register_semitones
            item["observed_offset"] = ref.sounding_note - ref.raw_note
        decoded.append(item)

    calibrated = [item for item in decoded if "native_pitch_at_unity" in item]
    first = decoded[0]
    reference_provenance = {
        "method": first["method"],
        "location": {"path": source_path},
    }
    pitch: dict[str, Any] = {
        "pitch_register_reference": {
            "register_value": f"0x{first['register_value']:04x}",
            "note": {"number": first["raw_note"], "octave_convention": "MIDI 60 = C4"},
            "register_scale_reference": f"0x{pitch_scale_reference:04x}",
            "basis": first["basis"],
            "confidence": first["confidence"],
            "provenance": reference_provenance,
        }
    }

    confidence = min(ref.confidence for ref in references)
    consistency_note = "No sounding-pitch references were supplied; calibration was not inferred."
    spread_cents: float | None = None
    if calibrated:
        native_pitches = [item["native_pitch_at_unity"] for item in calibrated]
        native_pitch = fmean(native_pitches)
        spread_cents = (max(native_pitches) - min(native_pitches)) * 100.0
        if spread_cents > consistency_tolerance_cents:
            confidence *= max(0.0, 1.0 - spread_cents / 100.0)
            consistency_note = (
                f"conflict: pitch references span {spread_cents:.3f} cents, exceeding "
                f"the {consistency_tolerance_cents:.3f}-cent tolerance"
            )
        else:
            consistency_note = (
                f"{len(calibrated)} pitch reference(s) agree within {spread_cents:.3f} cents"
            )
        unity_key, fine_cents = _split_semitones(native_pitch)
        calibration_provenance = {
            "method": "SNES DSP pitch-register equation with sounding-note references",
            "location": {"path": source_path},
            "note": consistency_note,
        }
        pitch["source_unity_key"] = {
            "value": unity_key,
            "unit": "midi_note",
            "basis": "inferred",
            "confidence": confidence,
            "provenance": calibration_provenance,
        }
        pitch["source_fine_tune_cents"] = {
            "value": fine_cents,
            "unit": "cent",
            "basis": "inferred",
            "confidence": confidence,
            "provenance": calibration_provenance,
        }
        mean_offset = fmean(item["observed_offset"] for item in calibrated)
        offset_semitones, offset_cents = _split_semitones(mean_offset)
        pitch["observed_pitch_offset"] = {
            "semitones": offset_semitones,
            "residual_cents": offset_cents,
            "basis": "measured" if any(item["basis"] == "measured" for item in calibrated) else "inferred",
            "confidence": confidence,
            "provenance": calibration_provenance,
        }

    return {
        "record_id": record_id or f"{source_path}:instrument-{instrument_id}",
        "source": {
            "platform": "Super Nintendo",
            "game_or_module": game_or_module,
            "driver": "SPC700 custom driver",
            "source_files": [{"path": source_path}],
        },
        "identity": {
            "source_instrument_id": instrument_id,
            "source_patch_command": source_patch_command,
            "song_local_slot": song_local_slot,
            "sample_ids": [sample_id],
        },
        "observation": {
            "synthesis": {
                "engine": "sample",
                "sample": {
                    "encoding": {
                        "value": "BRR",
                        "unit": "codec",
                        "basis": "hardware_fact",
                        "provenance": {"method": "SNES DSP sample format"},
                    },
                    "sample_rate_hz": {
                        "value": dsp_sample_rate_hz,
                        "unit": "Hz",
                        "basis": "hardware_fact",
                        "provenance": {"method": "SNES DSP output rate"},
                    },
                },
                "hardware_context": [{
                    "value": {
                        "pitch_scale_reference": f"0x{pitch_scale_reference:04x}",
                        "dsp_sample_rate_hz": dsp_sample_rate_hz,
                        "references": decoded,
                        "reference_consistency_spread_cents": spread_cents,
                    },
                    "basis": "hardware_fact",
                    "confidence": confidence,
                    "provenance": {
                        "method": "SNES DSP playback equation",
                        "location": {"path": source_path},
                        "note": consistency_note,
                    },
                }],
            },
            "pitch": pitch,
        },
    }
