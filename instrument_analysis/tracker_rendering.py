"""Isolated sample probes from strict four-channel ProTracker M.K. modules."""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import struct
import subprocess
import tempfile
import uuid
import wave
from pathlib import Path
from typing import Any, Sequence

VERSION = "instrument-tracker-sample-probes/v1"
_PAL_CLOCK_HZ = 7_093_789.2
_MOD_HEADER_SIZE = 1084
_PATTERN_SIZE = 1024
_NOTE_OFF_ROW = 8
_BPM = 130
_SPEED = 6
_NOTE_OFF_SECONDS = _NOTE_OFF_ROW * 2.5 * _SPEED / _BPM
_END_SECONDS = 1.0
_ANALYSIS_GUARD_SECONDS = 0.04


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_mod(data: bytes, sample_number: int) -> tuple[dict[str, Any], bytes, bytes]:
    if len(data) < _MOD_HEADER_SIZE + _PATTERN_SIZE:
        raise ValueError("module is too short for a ProTracker M.K. header and one pattern")
    if data[1080:1084] != b"M.K.":
        raise ValueError("only strict 4-channel ProTracker M.K. modules are supported")
    if type(sample_number) is not int or not 1 <= sample_number <= 31:
        raise ValueError("sample_number must be an integer from 1 through 31")
    song_length = data[950]
    if not 1 <= song_length <= 128:
        raise ValueError("invalid ProTracker song length")
    orders = data[952:952 + song_length]
    if not orders:
        raise ValueError("module has no pattern orders")
    pattern_count = max(orders) + 1
    patterns_size = pattern_count * _PATTERN_SIZE
    samples_start = _MOD_HEADER_SIZE + patterns_size
    if samples_start > len(data):
        raise ValueError("pattern table points beyond end of module")
    headers = []
    total_samples = 0
    for index in range(31):
        offset = 20 + index * 30
        header = data[offset:offset + 30]
        length = int.from_bytes(header[22:24], "big") * 2
        if header[24] & 0xF0:
            raise ValueError(f"sample {index + 1} has unsupported nonzero finetune high nibble")
        finetune_raw = header[24] & 0x0F
        finetune_signed = finetune_raw if finetune_raw < 8 else finetune_raw - 16
        volume = header[25]
        loop_start = int.from_bytes(header[26:28], "big") * 2
        loop_length = int.from_bytes(header[28:30], "big") * 2
        if volume > 64:
            raise ValueError(f"sample {index + 1} volume exceeds ProTracker range")
        if length and loop_length > 2 and loop_start + loop_length > length:
            raise ValueError(f"sample {index + 1} loop exceeds sample data")
        headers.append({"raw_header": header, "length": length, "finetune_raw": finetune_raw,
                        "finetune_signed": finetune_signed, "volume": volume,
                        "loop_start": loop_start, "loop_length": loop_length,
                        "loop_enabled": length > 0 and loop_length > 2})
        total_samples += length
    if samples_start + total_samples != len(data):
        raise ValueError("module sample data size does not exactly match its M.K. sample headers")
    chosen = headers[sample_number - 1]
    if chosen["length"] < 64:
        raise ValueError("selected sample is empty or too short")
    if chosen["loop_enabled"] and chosen["loop_start"] + chosen["loop_length"] > chosen["length"]:
        raise ValueError("selected sample loop is invalid")
    if chosen["raw_header"][25] == 0:
        raise ValueError("selected sample has zero volume")
    sample_data_start = samples_start + sum(h["length"] for h in headers[:sample_number - 1])
    sample_data = data[sample_data_start:sample_data_start + chosen["length"]]
    meta = {
        "module_format": "ProTracker M.K. (strict 4-channel)" ,
        "module_title": data[:20].split(b"\0", 1)[0].decode("latin-1", "replace"),
        "pattern_count": pattern_count,
        "song_length_patterns": song_length,
        "sample_number": sample_number,
        "sample_header_hex": chosen["raw_header"].hex(),
        "sample_length_bytes": chosen["length"],
        "sample_sha256": _sha256(sample_data),
        "finetune_raw_nibble": chosen["finetune_raw"],
        "finetune_signed_eighth_semitones": chosen["finetune_signed"],
        "volume_raw_0_64": chosen["volume"],
        "loop_header_start_bytes": chosen["loop_start"],
        "loop_header_length_bytes": chosen["loop_length"],
        "loop_enabled": chosen["loop_enabled"],
        "effective_loop_start_bytes": chosen["loop_start"] if chosen["loop_enabled"] else None,
        "effective_loop_length_bytes": chosen["loop_length"] if chosen["loop_enabled"] else 0,
        "sample_offset_unit_bytes": 256,
    }
    return meta, sample_data, data


def _set_event(pattern: bytearray, row: int, channel: int, sample: int = 0,
               period: int = 0, effect: int = 0, parameter: int = 0) -> None:
    if not 0 <= row < 64 or not 0 <= channel < 4:
        raise ValueError("pattern event coordinates are out of range")
    base = row * 16 + channel * 4
    pattern[base] = (sample & 0xF0) | ((period >> 8) & 0x0F)
    pattern[base + 1] = period & 0xFF
    pattern[base + 2] = ((sample & 0x0F) << 4) | (effect & 0x0F)
    pattern[base + 3] = parameter & 0xFF


def _probe_module(data: bytes, sample_number: int, period: int, sample_offset: int) -> bytes:
    meta, _, _ = _parse_mod(data, sample_number)
    if type(period) is not int or not 1 <= period <= 0x0FFF:
        raise ValueError("period must be a raw 12-bit ProTracker period")
    if type(sample_offset) is not int or not 0 <= sample_offset <= 255:
        raise ValueError("sample_offset must be an integer in 256-byte ProTracker effect units (0..255)")
    start_bytes = sample_offset * 256
    if start_bytes >= meta["sample_length_bytes"]:
        raise ValueError("sample offset begins at or beyond selected sample end")
    if meta["loop_enabled"] and sample_offset:
        raise ValueError("nonzero sample offset is unsupported for looped samples")
    source_samples = sum(int.from_bytes(data[20 + i * 30 + 22:20 + i * 30 + 24], "big") * 2 for i in range(31))
    original_patterns = meta["pattern_count"] * _PATTERN_SIZE
    samples_start = _MOD_HEADER_SIZE + original_patterns
    sample_blob = data[samples_start:samples_start + source_samples]
    header = bytearray(data[:_MOD_HEADER_SIZE])
    header[950] = 1
    header[951] = 0
    header[952:1080] = bytes(128)
    header[952] = 0
    pattern = bytearray(_PATTERN_SIZE)
    _set_event(pattern, 0, 0, sample=sample_number, period=period,
               effect=9 if sample_offset else 0, parameter=sample_offset)
    # Set a known tick rate in channel four, then cut channel one's sample at row 8.
    _set_event(pattern, 0, 3, effect=0xF, parameter=_BPM)
    _set_event(pattern, _NOTE_OFF_ROW, 0, effect=0xE, parameter=0xC0)
    return bytes(header) + bytes(pattern) + sample_blob


def _validate_trigger_list(triggers: Sequence[dict[str, int]]) -> list[dict[str, int]]:
    if not isinstance(triggers, Sequence) or isinstance(triggers, (str, bytes)):
        raise ValueError("triggers must be a sequence of explicit note/period records")
    clean = []
    for item in triggers:
        if not isinstance(item, dict):
            raise ValueError("each trigger must be a mapping")
        note, period = item.get("trigger_midi_note"), item.get("period")
        if type(note) is not int or not 0 <= note <= 127:
            raise ValueError("trigger_midi_note must be explicit MIDI 0..127")
        if type(period) is not int or not 1 <= period <= 0x0FFF:
            raise ValueError("period must be a raw 12-bit ProTracker period")
        clean.append({"trigger_midi_note": note, "period": period})
    if len(clean) < 3 or len({t["trigger_midi_note"] for t in clean}) < 3:
        raise ValueError("provide at least three distinct grounded trigger pitches")
    notes = [t["trigger_midi_note"] for t in clean]
    if max(notes) - min(notes) < 4:
        raise ValueError("trigger notes must span at least four semitones")
    if len({t["period"] for t in clean}) != len(clean):
        raise ValueError("each trigger must use a distinct explicit period")
    return clean


def _wav_metadata(path: Path, sample_rate: int) -> dict[str, Any]:
    try:
        with wave.open(str(path), "rb") as wav:
            channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
            if wav.getcomptype() != "NONE" or channels != 2 or width != 2 or rate != sample_rate:
                raise RuntimeError("openmpt123 output must be stereo PCM16 at requested sample rate")
            if abs(frames / rate - _END_SECONDS) > 1.0 / rate:
                raise RuntimeError("openmpt123 output duration differs from requested probe window")
            pcm = wav.readframes(frames)
            if len(pcm) != frames * channels * width:
                raise RuntimeError("openmpt123 wrote a truncated WAV")
    except (OSError, wave.Error) as exc:
        raise RuntimeError(f"openmpt123 did not write a readable PCM16 WAV: {path}") from exc
    return {"channels": channels, "sample_width_bytes": width, "sample_rate": rate,
            "frames": frames, "duration_seconds": frames / rate, "sha256": _file_hash(path)}


def _estimated_sample_end_seconds(instrument: dict[str, Any], period: int, sample_offset: int) -> float | None:
    if instrument["loop_enabled"]:
        return None
    raw_sample_rate = _PAL_CLOCK_HZ / (2.0 * period)
    finetune_factor = 2.0 ** (instrument["finetune_signed_eighth_semitones"] / 96.0)
    remaining_bytes = instrument["sample_length_bytes"] - sample_offset * 256
    return max(0.0, remaining_bytes / (raw_sample_rate * finetune_factor))


def render_tracker_sample_probes(
    module_path: str | Path, *, sample_number: int, triggers: Sequence[dict[str, int]],
    output_dir: str | Path, source_record_id: str, openmpt123_path: str | Path,
    sample_offset: int = 0, filter_taps: int = 8, sample_rate: int = 44100,
    amiga_resampler: bool = False,
) -> dict[str, Any]:
    """Render one selected source sample at explicit periods in isolated test MODs.

    Only ProTracker M.K. is accepted. Source song patterns/effects are replaced by
    one selected sample trigger, explicit tempo, and a row-8 note cut. The chosen
    sample header and original sample bytes are retained verbatim.
    """
    src = Path(module_path).resolve()
    renderer = Path(openmpt123_path).resolve()
    if not src.is_file() or not renderer.is_file():
        raise ValueError("module_path and openmpt123_path must name existing files")
    if not isinstance(source_record_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", source_record_id):
        raise ValueError("source_record_id must be a safe neutral identifier")
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise ValueError("sample_rate must be from 8000 through 192000")
    if type(filter_taps) is not int or filter_taps not in (1, 2, 4, 8):
        raise ValueError("filter_taps must be 1, 2, 4, or 8")
    if type(amiga_resampler) is not bool:
        raise ValueError("amiga_resampler must be a bool")
    triggers = _validate_trigger_list(triggers)
    source_bytes = src.read_bytes()
    source_hash = _sha256(source_bytes)
    instrument, sample_data, _ = _parse_mod(source_bytes, sample_number)
    if type(sample_offset) is not int or not 0 <= sample_offset <= 255:
        raise ValueError("sample_offset must be an integer from 0 through 255")
    if sample_offset * 256 >= instrument["sample_length_bytes"]:
        raise ValueError("sample offset begins at or beyond selected sample end")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    final_dir = output / source_record_id
    if final_dir.exists():
        raise FileExistsError(f"tracker probe output already exists: {final_dir}")
    stage = output / f".tracker-probe-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        renderer_hash = _file_hash(renderer)
        version_result = subprocess.run([str(renderer), "--version"], cwd=stage, shell=False,
                                        capture_output=True, text=True, timeout=15, check=False)
        if version_result.returncode:
            raise RuntimeError(f"openmpt123 version query failed: {version_result.stderr!r}")
        version = (version_result.stdout or version_result.stderr).strip().splitlines()[0]
        results = []
        for index, trigger in enumerate(triggers):
            probe_mod = _probe_module(source_bytes, sample_number, trigger["period"], sample_offset)
            mod_name = f"probe-{index:02d}.mod"
            wav_name = f"probe-{index:02d}.wav"
            mod_path, wav_path = stage / mod_name, stage / wav_name
            with mod_path.open("xb") as stream:
                stream.write(probe_mod)
            argv = [
                str(renderer), "--batch", "--quiet", "--no-progress", "--no-meters", "--no-details",
                "--samplerate", str(sample_rate), "--channels", "2", "--no-float", "--filter", str(filter_taps),
                "--ramping", "0", "--dither", "0", "--gain", "0", "--stereo", "100",
                "--repeat", "0", "--end-time", str(_END_SECONDS),
                "--ctl", f"render.resampler.emulate_amiga={int(amiga_resampler)}",
                "--ctl", "render.resampler.emulate_amiga_type=a500",
                "--output", str(wav_path), "--", str(mod_path),
            ]
            try:
                result = subprocess.run(argv, cwd=stage, shell=False, capture_output=True,
                                        text=True, timeout=60, check=False)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"openmpt123 timed out for trigger {index}; stdout={exc.stdout!r}; stderr={exc.stderr!r}") from exc
            if result.returncode:
                raise RuntimeError(f"openmpt123 failed for trigger {index}: stdout={result.stdout!r}; stderr={result.stderr!r}")
            wav_meta = _wav_metadata(wav_path, sample_rate)
            period = trigger["period"]
            raw_sample_rate = _PAL_CLOCK_HZ / (2.0 * period)
            finetune_factor = 2.0 ** (instrument["finetune_signed_eighth_semitones"] / 96.0)
            tuned_sample_rate = raw_sample_rate * finetune_factor
            expected_sample_end = _estimated_sample_end_seconds(instrument, period, sample_offset)
            audible_end = _NOTE_OFF_SECONDS if expected_sample_end is None else min(_NOTE_OFF_SECONDS, expected_sample_end)
            analysis_end = audible_end - _ANALYSIS_GUARD_SECONDS
            if analysis_end < 0.57:
                raise ValueError("selected sample is too short for a guarded multi-window pitch analysis")
            results.append({
                "probe_module_file": mod_name,
                "probe_module_path": str(final_dir / mod_name),
                "probe_module_sha256": _sha256(probe_mod),
                "wav_file": wav_name,
                "wav_path": str(final_dir / wav_name),
                "wav_sha256": wav_meta["sha256"],
                "trigger_midi_note": trigger["trigger_midi_note"],
                "note_on_seconds": 0.0,
                "note_off_seconds": audible_end,
                "note_cut_time_seconds": _NOTE_OFF_SECONDS,
                "expected_sample_end_seconds": expected_sample_end,
                "analysis_end_seconds": analysis_end,
                "analysis_guard_seconds": _ANALYSIS_GUARD_SECONDS,
                "end_seconds": _END_SECONDS,
                "render_conditions": {
                    "period_raw_protracker_12bit": period,
                    "source_sample_sha256": instrument["sample_sha256"],
                    "period_basis": "explicit caller-supplied tracker period; nominal sample rate = 7093789.2/(2*period) Hz; no note-to-period inference",
                    "nominal_sample_rate_hz": raw_sample_rate,
                    "sample_finetune_signed_eighth_semitones": instrument["finetune_signed_eighth_semitones"],
                    "finetune_rate_factor": finetune_factor,
                    "sample_rate_after_finetune_hz": tuned_sample_rate,
                    "sample_length_bytes": instrument["sample_length_bytes"],
                    "expected_sample_end_seconds": expected_sample_end,
                    "analysis_end_seconds": analysis_end,
                    "analysis_guard_seconds": _ANALYSIS_GUARD_SECONDS,
                    "sample_offset_effect_units": sample_offset,
                    "sample_offset_bytes": sample_offset * 256,
                    "note_cut": {"row": _NOTE_OFF_ROW, "extended_effect": "EC0", "time_seconds": _NOTE_OFF_SECONDS},
                    "audible_end_basis": "min(pattern note-cut time, expected one-shot sample exhaustion); loops end at pattern note cut",
                    "sample_end_estimation_method": "remaining sample bytes divided by PAL clock/(2*period) and 2^(signed finetune eighth-semitones/96); advisory bound, confirmed against PCM where audible",
                    "bpm": _BPM, "speed_ticks_per_row": _SPEED,
                    "duration_seconds_requested": _END_SECONDS,
                    "sample_rate": sample_rate, "channels": 2, "sample_width_bytes": 2,
                    "filter_taps": filter_taps, "amiga_resampler_enabled": amiga_resampler,
                    "amiga_resampler_type_control": "a500",
                    "resampler_profile": "amiga-a500" if amiga_resampler else f"libopenmpt-filter-{filter_taps}-tap",
                    "ramping": 0, "dither": 0,
                    "gain_db": 0, "stereo_separation_percent": 100, "repeat_count": 0,
                    "source_song_patterns_and_effects": "replaced by a single selected-sample probe pattern",
                    "selected_source_sample_header_and_data": "preserved verbatim in probe MOD",
                    "openmpt123_version": version,
                },
                "wav": wav_meta,
            })
        manifest = {
            "schema_version": VERSION,
            "source_record_id": source_record_id,
            "source": {
                "module_sha256": source_hash,
                "module_format": instrument["module_format"],
                "sample_number": sample_number,
                "sample_header_hex": instrument["sample_header_hex"],
                "sample_bytes_sha256": instrument["sample_sha256"],
                "sample_length_bytes": instrument["sample_length_bytes"],
                "finetune_raw_nibble": instrument["finetune_raw_nibble"],
                "finetune_signed_eighth_semitones": instrument["finetune_signed_eighth_semitones"],
                "volume_raw_0_64": instrument["volume_raw_0_64"],
                "loop_header_start_bytes": instrument["loop_header_start_bytes"],
                "loop_header_length_bytes": instrument["loop_header_length_bytes"],
                "loop_enabled": instrument["loop_enabled"],
                "effective_loop_start_bytes": instrument["effective_loop_start_bytes"],
                "effective_loop_length_bytes": instrument["effective_loop_length_bytes"],
            },
            "renderer": {"path": str(renderer), "sha256": renderer_hash, "version": version},
            "conditions": {"filter_taps": filter_taps, "amiga_resampler_enabled": amiga_resampler,
                           "amiga_resampler_type_control": "a500",
                           "resampler_profile": "amiga-a500" if amiga_resampler else f"libopenmpt-filter-{filter_taps}-tap",
                           "sample_offset_effect_units": sample_offset,
                           "note_cut_time_seconds": _NOTE_OFF_SECONDS, "analysis_guard_seconds": _ANALYSIS_GUARD_SECONDS, "end_seconds": _END_SECONDS},
            "probes": results,
        }
        manifest_path = stage / "probes.json"
        with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, indent=2, sort_keys=True)
            stream.write("\n")
        if _file_hash(src) != source_hash or _file_hash(renderer) != renderer_hash:
            raise RuntimeError("tracker module or renderer changed during probe render")
        if final_dir.exists():
            raise FileExistsError(f"tracker probe output appeared during render: {final_dir}")
        stage.replace(final_dir)
        manifest["manifest_path"] = str(final_dir / "probes.json")
        return manifest
    except Exception:
        if stage.exists():
            if stage.resolve().parent != output.resolve() or not stage.name.startswith('.tracker-probe-'):
                raise RuntimeError('unsafe tracker probe staging cleanup path')
            shutil.rmtree(stage)
        raise
