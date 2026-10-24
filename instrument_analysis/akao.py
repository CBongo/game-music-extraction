"""Metadata-first adapters for PlayStation AKAO sample collections."""

from __future__ import annotations

import math
import struct
from typing import Any

_HEADER = struct.Struct("<4sHH6I")
_ARTICULATION = struct.Struct("<IIhHHH")
_REGION = struct.Struct("<8B")


def _provenance(method: str, path: str, offset: int, note: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"method": method, "location": {"path": path, "offset": offset}}
    if note:
        value["note"] = note
    return value


def _datum(value: Any, unit: str, provenance: dict[str, Any], basis: str = "source_fact") -> dict[str, Any]:
    return {"value": value, "unit": unit, "basis": basis, "provenance": provenance}


def decode_akao_v31_fine_tune(raw_fine_tune: int, raw_unity_key: int) -> tuple[int, int, float]:
    """Decode AKAO v3.1 fixed-point tuning using vgmtrans-compatible math."""
    if not -0x8000 <= raw_fine_tune <= 0x7FFF:
        raise ValueError("raw_fine_tune must fit in a signed 16-bit integer")
    if not 0 <= raw_unity_key <= 0xFF:
        raise ValueError("raw_unity_key must fit in one byte")
    multiplier = (
        1.0 + raw_fine_tune / 32768.0
        if raw_fine_tune >= 0
        else (raw_fine_tune & 0xFFFF) / 65536.0
    )
    if multiplier <= 0:
        raise ValueError("fine-tune value produces a non-positive frequency multiplier")
    total_cents = 1200.0 * math.log2(multiplier)
    coarse = math.trunc(total_cents / 100.0)
    residual = math.trunc(total_cents) - coarse * 100
    return raw_unity_key - coarse, residual, total_cents


def _decode_sample_set(
    data: bytes, source_path: str, source_base_offset: int
) -> tuple[dict[str, int], dict[int, dict[str, Any]]]:
    if len(data) < 0x40:
        raise ValueError("AKAO sample-set header is shorter than 0x40 bytes")
    fields = _HEADER.unpack_from(data)
    magic, sample_set_id, header_flags = fields[:3]
    unknown_08, unknown_0c, spu_address, sample_length, first_art_id, art_count = fields[3:]
    if magic != b"AKAO":
        raise ValueError(f"not an AKAO sample set: magic={magic!r}")
    expected_size = 0x40 + art_count * _ARTICULATION.size
    if len(data) < expected_size:
        raise ValueError(
            f"AKAO sample set declares {art_count} articulations but ends at "
            f"0x{len(data):x} (need at least 0x{expected_size:x})"
        )
    header = {
        "sample_set_id": sample_set_id,
        "header_flags": header_flags,
        "unknown_08": unknown_08,
        "unknown_0c": unknown_0c,
        "spu_address": spu_address,
        "sample_length": sample_length,
    }
    articulations: dict[int, dict[str, Any]] = {}
    for index in range(art_count):
        offset = 0x40 + index * _ARTICULATION.size
        sample_start, loop_address, raw_tune, raw_unity, adsr1, adsr2 = _ARTICULATION.unpack_from(data, offset)
        if raw_unity > 0xFF:
            raise ValueError(f"articulation {index} has unsupported unity-key high byte: 0x{raw_unity:04x}")
        if loop_address < sample_start:
            raise ValueError(
                f"articulation {index} loop address 0x{loop_address:x} precedes "
                f"sample address 0x{sample_start:x}"
            )
        art_id = first_art_id + index
        adjusted_unity, fine_cents, total_cents = decode_akao_v31_fine_tune(raw_tune, raw_unity)
        articulations[art_id] = {
            "offset": offset,
            "sample_start": sample_start,
            "loop_address": loop_address,
            "unity_key": adjusted_unity,
            "fine_cents": fine_cents,
            "adsr1": adsr1,
            "adsr2": adsr2,
            "provenance": _provenance(
                "decoded AKAO v3.1 articulation",
                source_path,
                source_base_offset + offset,
                f"raw tune={raw_tune}; total tuning={total_cents:.6f} cents",
            ),
        }
    return header, articulations


def adapt_akao_v31_sample_set(
    data: bytes,
    *,
    instrument_data: bytes,
    game_or_module: str,
    source_path: str,
    instrument_source_path: str,
    format_version: str,
    record_prefix: str | None = None,
    region_or_version: str | None = None,
    sample_base_offset: int = 0,
    instrument_base_offset: int = 0,
) -> list[dict[str, Any]]:
    """Join a v3.1+ sample set with its instrument regions.

    A sample collection contains articulations, not instruments. Canonical
    records are emitted only after the instrument regions reference those
    articulations and provide true program identity and key splits.
    """
    if format_version not in {"3.1", "3.2"}:
        raise ValueError("format_version must explicitly confirm AKAO 3.1 or 3.2")
    if not game_or_module or not source_path or not instrument_source_path:
        raise ValueError("game and source paths must be non-empty")
    if record_prefix == "":
        raise ValueError("record_prefix must be non-empty when provided")
    if sample_base_offset < 0 or instrument_base_offset < 0:
        raise ValueError("source base offsets cannot be negative")
    if len(instrument_data) < 0x20:
        raise ValueError("AKAO instrument pointer table is shorter than 0x20 bytes")

    header, articulations = _decode_sample_set(data, source_path, sample_base_offset)
    namespace = record_prefix or f"{source_path}:set-{header['sample_set_id']:04x}"
    source: dict[str, Any] = {
        "platform": "Sony PlayStation",
        "game_or_module": game_or_module,
        "driver": "AKAO PSX",
        "container": f"sample-set 0x{header['sample_set_id']:04x}",
        "source_files": [
            {"path": source_path, "offset": sample_base_offset, "length": len(data)},
            {"path": instrument_source_path, "offset": instrument_base_offset, "length": len(instrument_data)},
        ],
    }
    if region_or_version:
        source["region_or_version"] = region_or_version

    records: list[dict[str, Any]] = []
    for instrument_id in range(16):
        pointer = struct.unpack_from("<H", instrument_data, instrument_id * 2)[0]
        if pointer == 0xFFFF or (pointer == 0 and instrument_id != 0):
            continue
        instrument_offset = 0x20 + pointer
        if instrument_offset >= len(instrument_data):
            raise ValueError(f"instrument {instrument_id} points beyond instrument data: 0x{instrument_offset:x}")

        zones: list[dict[str, Any]] = []
        sample_ids: list[str] = []
        region_offset = instrument_offset
        while region_offset + _REGION.size <= len(instrument_data):
            region_bytes = instrument_data[region_offset:region_offset + _REGION.size]
            if region_bytes[:4] == b"\0\0\0\0":
                break
            art_id, key_low, key_high, attack, sustain_rate, sustain_mode, release, volume = _REGION.unpack(region_bytes)
            if key_low > key_high:
                raise ValueError(f"instrument {instrument_id} region at 0x{region_offset:x} has reversed key range")
            if art_id not in articulations:
                raise ValueError(f"instrument {instrument_id} references missing articulation {art_id}")
            art = articulations[art_id]
            sample_id = f"{namespace}#spu-offset-0x{art['sample_start']:08x}"
            if sample_id not in sample_ids:
                sample_ids.append(sample_id)
            region_provenance = _provenance(
                "decoded AKAO v3.1 instrument region",
                instrument_source_path,
                instrument_base_offset + region_offset,
                f"articulation {art_id}; raw key range {key_low}-{key_high}",
            )
            pitch = {
                "source_unity_key": _datum(art["unity_key"], "midi_note", art["provenance"]),
                "source_fine_tune_cents": _datum(art["fine_cents"], "cent", art["provenance"]),
            }
            envelope = {
                "kind": "amplitude",
                "raw": {
                    "articulation_adsr1": art["adsr1"],
                    "articulation_adsr2": art["adsr2"],
                    "region_attack_rate": attack,
                    "region_sustain_rate": sustain_rate,
                    "region_sustain_mode": sustain_mode,
                    "region_release_rate": release,
                    "region_volume": volume,
                    "raw_key_low": key_low,
                    "raw_key_high": key_high,
                },
                "basis": "source_fact",
                "confidence": 1,
                "provenance": region_provenance,
            }
            zones.append({
                "zone_id": len(zones),
                "sample_id": sample_id,
                "key_range": {"minimum": min(key_low, 127), "maximum": min(key_high, 127)},
                "pitch": pitch,
                "envelopes": [envelope],
                "provenance": region_provenance,
            })
            region_offset += _REGION.size

        if not zones:
            continue
        zones[0]["key_range"]["minimum"] = 0
        zones[-1]["key_range"]["maximum"] = 127
        for previous, current in zip(zones, zones[1:]):
            previous_high = previous["key_range"]["maximum"]
            if current["key_range"]["minimum"] > previous_high + 1:
                current["key_range"]["minimum"] = previous_high + 1
        context_provenance = articulations[_REGION.unpack_from(instrument_data, instrument_offset)[0]]["provenance"]
        records.append({
            "record_id": f"{namespace}:instrument-{instrument_id}",
            "source": dict(source),
            "identity": {
                "source_instrument_id": f"sample-set-{header['sample_set_id']:04x}:slot-{instrument_id}",
                "source_patch_command": instrument_id,
                "song_local_slot": instrument_id,
                "sample_ids": sample_ids,
            },
            "observation": {
                "synthesis": {
                    "engine": "sample",
                    "sample": {"encoding": _datum("SPU ADPCM", "codec", context_provenance, "hardware_fact")},
                    "hardware_context": [_datum({
                        "sample_set_spu_address": header["spu_address"],
                        "sample_section_length": header["sample_length"],
                        "header_flags": header["header_flags"],
                        "header_unknown_08": header["unknown_08"],
                        "header_unknown_0c": header["unknown_0c"],
                    }, "raw AKAO fields", context_provenance)],
                },
                "zones": zones,
            },
        })
    return records
