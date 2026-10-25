"""Hash-bound, phase-matched native chip versus GM reference previews."""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import math
import re
from pathlib import Path
import shutil
import sys
import uuid
import wave

VERSION = "instrument-comparison/v1"
CALIBRATION_VERSION = "instrument-pitch-calibration/v1"
_RAW_FIELDS = {"keycode_raw_ym2151", "pitch_register_raw_s_dsp", "pitch_register_raw_spu"}


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not readable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _number(value: object, label: str, *, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    n = float(value)
    if minimum is not None and n < minimum or maximum is not None and n > maximum:
        raise ValueError(f"{label} is outside the allowed range")
    return n


def _local_file(manifest_path: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute() or Path(value).name != value:
        raise ValueError(f"{label} must be a file name alongside its manifest")
    path = manifest_path.parent / value
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    return path.resolve()


def _verified_wav(manifest_path: Path, meta: object, label: str) -> tuple[Path, dict, str]:
    if not isinstance(meta, dict):
        raise ValueError(f"{label} lacks WAV metadata")
    path = _local_file(manifest_path, meta.get("file"), f"{label} WAV")
    actual_hash = _hash(path)
    if actual_hash != meta.get("sha256"):
        raise ValueError(f"{label} WAV hash differs from manifest")
    with wave.open(str(path), "rb") as stream:
        rate, channels, width, frames = (stream.getframerate(), stream.getnchannels(),
                                          stream.getsampwidth(), stream.getnframes())
        if stream.getcomptype() != "NONE" or frames < 1:
            raise ValueError(f"{label} WAV must contain uncompressed PCM frames")
    expected = {"sample_rate": rate, "channels": channels,
                "sample_width_bytes": width, "frames": frames}
    if any(meta.get(k) != v for k, v in expected.items()):
        raise ValueError(f"{label} WAV header differs from manifest")
    if channels not in (1, 2) or width != 2:
        raise ValueError("comparison requires mono/stereo PCM16 input")
    return path, expected, actual_hash


def _native_dry(manifest: dict) -> None:
    engine = manifest.get("engine")
    conditions = manifest.get("conditions")
    if not isinstance(conditions, dict):
        raise ValueError("native render has no conditions")
    if engine == "MAME YM2151":
        if conditions.get("global_lfo_and_noise") != "disabled for isolated patch probe":
            raise ValueError("native YM2151 effects isolation is unknown")
    elif engine == "MAME SNES S-DSP":
        regs = conditions.get("dsp_registers", {})
        if not isinstance(regs, dict) or any(regs.get(k) != 0 for k in (
                "echo_enable", "echo_volume_left", "echo_volume_right")):
            raise ValueError("native S-DSP echo is enabled or unknown")
    elif engine == "MAME PlayStation SPU (Konami GV)":
        if conditions.get("spu_reverb_fm_noise") != "disabled":
            raise ValueError("native SPU effects isolation is unknown")
    else:
        raise ValueError(f"unsupported native engine {engine!r}")


def _native_key(manifest: dict) -> dict:
    key = manifest.get("key")
    if not isinstance(key, dict) or key.get("midi_note") is not None:
        raise ValueError("native manifest must expose an unconverted raw pitch")
    values = [(name, key[name]) for name in _RAW_FIELDS if name in key]
    if len(values) != 1 or type(values[0][1]) is not int:
        raise ValueError("native manifest must expose exactly one integer raw pitch")
    return {"key": values[0][0], "value": values[0][1]}


def _calibration(calibration: dict, calibration_path: Path, native_path: Path,
                 native_hash: str, native_wav: Path, native_wav_hash: str,
                 native: dict, raw_pitch: dict) -> tuple[float, dict]:
    if calibration.get("schema_version") != CALIBRATION_VERSION:
        raise ValueError("unknown pitch calibration record")
    if calibration.get("status") != "calibrated":
        raise ValueError("pitch calibration abstained or is not calibrated")
    basis = calibration.get("basis")
    if basis not in ("acoustic_measurement", "verified_driver_reference"):
        raise ValueError("pitch calibration needs acoustic or verified driver evidence")
    confidence = _number(calibration.get("confidence"), "calibration confidence", minimum=0, maximum=1)
    if confidence < 0.8:
        raise ValueError("pitch calibration confidence is below 0.8")
    evidence = calibration.get("evidence")
    method = calibration.get("method")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("pitch calibration lacks evidence")
    if basis == "acoustic_measurement" and (
            not isinstance(method, str) or not method.strip()):
        raise ValueError("acoustic pitch calibration lacks method")
    expected = {
        "native_manifest_path": str(native_path),
        "native_manifest_sha256": native_hash,
        "native_wav_path": str(native_wav),
        "native_wav_sha256": native_wav_hash,
        "source_record_id": native.get("source_record_id"),
        "engine": native.get("engine"),
        "native_raw_pitch": raw_pitch,
    }
    for name, value in expected.items():
        if calibration.get(name) != value:
            raise ValueError(f"pitch calibration {name} disagrees with native source")
    note = _number(calibration.get("sounding_midi_note"), "calibrated sounding MIDI note",
                   minimum=0, maximum=127)
    if basis == "acoustic_measurement":
        f0 = _number(calibration.get("f0_hz"), "measured fundamental", minimum=1)
        expected_note = 69 + 12 * math.log2(f0 / 440)
        if abs(note - expected_note) > 0.001:
            raise ValueError("acoustic calibration MIDI note disagrees with measured F0")
        windows = calibration.get("window_estimates")
        if not isinstance(windows, list):
            raise ValueError("acoustic calibration lacks sustain windows")
        valid_windows = [w for w in windows if isinstance(w, dict) and
                         w.get("f0_hz") is not None and
                         isinstance(w.get("f0_hz"), (int, float)) and
                         not isinstance(w.get("f0_hz"), bool) and
                         math.isfinite(w["f0_hz"]) and w["f0_hz"] > 0]
        if len(valid_windows) < 2:
            raise ValueError("acoustic calibration lacks two measured sustain windows")
    return note, {"path": str(calibration_path), "sha256": _hash(calibration_path)}


def _phase(native: dict, native_receipt: dict, native_frames: int,
           gm: dict, gm_frames: int, rate: int) -> dict:
    conditions = native.get("conditions")
    alignment = conditions.get("wav_alignment") if isinstance(conditions, dict) else None
    if not isinstance(alignment, dict):
        raise ValueError("native WAV alignment is missing")
    times = {}
    for name in ("note_on_time_seconds", "note_off_time_seconds", "capture_end_time_seconds",
                 "frame_period_seconds"):
        value = _number(conditions.get(name), f"native {name}", minimum=0)
        receipt_value = _number(native_receipt.get(name), f"native receipt {name}", minimum=0)
        if abs(value - receipt_value) > 1e-6:
            raise ValueError(f"native receipt disagrees about {name}")
        times[name] = value
    if not times["note_on_time_seconds"] < times["note_off_time_seconds"] < times["capture_end_time_seconds"]:
        raise ValueError("native event window is invalid")
    period = times["frame_period_seconds"]
    if not 0 < period < 1:
        raise ValueError("native frame period is invalid")
    start = _number(alignment.get("actual_trim_start_seconds"), "native trim start", minimum=0)
    end = _number(alignment.get("actual_trim_end_seconds"), "native trim end", minimum=start)
    uncertainty = _number(alignment.get("alignment_uncertainty_seconds"), "native alignment uncertainty",
                          minimum=0)
    if uncertainty > period + 1 / rate:
        raise ValueError("native alignment exceeds one frame of uncertainty")
    lead = alignment.get("lead_in_frames")
    if type(lead) is not int or lead < 0 or lead >= native_frames:
        raise ValueError("native note-on lead-in is invalid")
    note_on_frame = lead
    calculated_on = round((times["note_on_time_seconds"] - start) * rate)
    if abs(calculated_on - lead) > 1:
        raise ValueError("native note-on timestamp differs from lead-in frames")
    note_off_frame = round((times["note_off_time_seconds"] - start) * rate)
    if not note_on_frame < note_off_frame < native_frames:
        raise ValueError("native hold/tail window is missing")
    if abs(native_frames / rate + start - end) > 2 / rate:
        raise ValueError("native trim end differs from WAV duration")
    if times["capture_end_time_seconds"] - end > period + 2 / rate:
        raise ValueError("native capture tail is shorter than one frame uncertainty")
    gm_conditions = gm.get("conditions")
    if not isinstance(gm_conditions, dict):
        raise ValueError("GM render conditions are missing")
    gm_hold = round(_number(gm_conditions.get("duration_seconds"), "GM hold", minimum=0) * rate)
    gm_tail = round(_number(gm_conditions.get("tail_seconds"), "GM tail", minimum=0) * rate)
    if gm_hold < 1 or gm_tail < 1 or gm_frames < gm_hold + gm_tail:
        raise ValueError("GM note-on/off/tail window is missing")
    source_hold = note_off_frame - note_on_frame
    source_tail = native_frames - note_off_frame
    matched_hold = min(source_hold, gm_hold)
    matched_tail = min(source_tail, gm_tail)
    if matched_hold < 1 or matched_tail < 1:
        raise ValueError("comparable hold/tail window is missing")
    return {
        "native_note_on_frame": note_on_frame,
        "native_note_off_frame": note_off_frame,
        "gm_note_on_frame": 0,
        "gm_note_off_frame": gm_hold,
        "matched_hold_frames": matched_hold,
        "matched_tail_frames": matched_tail,
        "native_hold_available_frames": source_hold,
        "native_tail_available_frames": source_tail,
        "gm_hold_available_frames": gm_hold,
        "gm_tail_available_frames": gm_tail,
        "native_phase_jump_frames": source_hold - matched_hold,
        "gm_phase_jump_frames": gm_hold - matched_hold,
        "native_uncertainty_frames": math.ceil(uncertainty * rate),
        "native_clock_origin_verified": alignment.get("clock_origin_verified") is True,
        "alignment_method": "MIDI and emulator note-on/off event frames; acoustic onset was not inferred",
    }


def _read_pcm(path: Path) -> tuple[int, bytes]:
    with wave.open(str(path), "rb") as stream:
        frames = stream.getnframes()
        data = stream.readframes(frames)
        if len(data) != frames * stream.getnchannels() * stream.getsampwidth():
            raise ValueError(f"truncated PCM data: {path}")
        if stream.getnchannels() == 1:
            data = b"".join(data[i:i + 2] * 2 for i in range(0, len(data), 2))
        return frames, data


def _write_pcm(path: Path, pcm: bytes, rate: int) -> dict:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(pcm)
    return {"file": path.name, "sha256": _hash(path), "frames": len(pcm) // 4,
            "sample_rate": rate, "channels": 2, "sample_width_bytes": 2}


def _scaled(pcm: bytes, gain: float) -> bytes:
    if gain == 1:
        return pcm
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    out = array("h")
    for sample in samples:
        value = round(sample * gain)
        if value < -32768 or value > 32767:
            raise ValueError("preview scalar gain would clip PCM16")
        out.append(value)
    if sys.byteorder != "little":
        out.byteswap()
    return out.tobytes()


def build_matched_comparison(
    native_manifest_path: str | Path, gm_manifest_path: str | Path, *,
    gm_probe_id: str, pitch_calibration_path: str | Path, output_dir: str | Path,
    residual_cents_tolerance: float = 25.0, source_gain: float = 1.0,
    gm_gain: float = 1.0, gap_seconds: float = 0.25,
) -> dict:
    """Write provenance-bound dry excerpts and a sequential A/B preview.

    Only explicit calibrated pitch records can select a GM MIDI note. The
    excerpt WAVs retain original PCM amplitude; scalar gain applies only to
    the preview and is recorded in the comparison manifest.
    """
    tolerance = _number(residual_cents_tolerance, "residual_cents_tolerance", minimum=0, maximum=50)
    source_gain = _number(source_gain, "source_gain", minimum=0, maximum=64)
    gm_gain = _number(gm_gain, "gm_gain", minimum=0, maximum=64)
    gap_seconds = _number(gap_seconds, "gap_seconds", minimum=0, maximum=5)
    if source_gain == 0 or gm_gain == 0:
        raise ValueError("preview gains must be positive")
    native_path = Path(native_manifest_path).expanduser().resolve(strict=True)
    gm_path = Path(gm_manifest_path).expanduser().resolve(strict=True)
    calibration_path = Path(pitch_calibration_path).expanduser().resolve(strict=True)
    native, gm, calibration = (_read_json(native_path, "native manifest"),
                               _read_json(gm_path, "GM manifest"),
                               _read_json(calibration_path, "pitch calibration"))
    if native.get("schema_version") not in {"instrument-native-chip-render/v1", "instrument-native-render/v1"}:
        raise ValueError("unknown native manifest schema")
    if gm.get("schema_version") != "instrument-render/v1":
        raise ValueError("unknown GM manifest schema")
    _native_dry(native)
    if not isinstance(native.get("source_record_id"), str) or not native["source_record_id"].strip():
        raise ValueError("native source_record_id is missing")
    native_hash, gm_hash = _hash(native_path), _hash(gm_path)
    native_wav, native_info, native_wav_hash = _verified_wav(native_path, native.get("wav"), "native")
    raw_pitch = _native_key(native)
    calibrated_note, calibration_ref = _calibration(
        calibration, calibration_path, native_path, native_hash, native_wav,
        native_wav_hash, native, raw_pitch)
    gm_conditions = gm.get("conditions")
    if not isinstance(gm_conditions, dict) or gm_conditions.get("dry") is not True or any(
            gm_conditions.get(k) is not False for k in ("reverb", "chorus")):
        raise ValueError("GM reference must be explicitly dry with reverb and chorus disabled")
    if gm_conditions.get("midi_format") != 0 or gm_conditions.get("midi_ticks_per_second") != 1000:
        raise ValueError("GM MIDI event timing format is unsupported")
    soundfont = gm.get("soundfont")
    if (not isinstance(soundfont, dict) or not isinstance(soundfont.get("path"), str) or
            not isinstance(soundfont.get("sha256"), str) or len(soundfont["sha256"]) != 64):
        raise ValueError("GM reference lacks SoundFont provenance")
    probes = gm.get("probes")
    if not isinstance(gm_probe_id, str) or not gm_probe_id or not isinstance(probes, list):
        raise ValueError("GM probe ID or probe list is missing")
    matches = [p for p in probes if isinstance(p, dict) and p.get("probe_id") == gm_probe_id]
    if len(matches) != 1:
        raise ValueError("GM probe ID must identify exactly one reference")
    probe = matches[0]
    route = probe.get("route")
    if probe.get("kind") != "melodic" or not isinstance(route, dict) or route.get("kind") != "gm_program":
        raise ValueError("pitch-calibrated comparison requires a melodic GM route")
    program = route.get("program_0based")
    if (type(program) is not int or not 0 <= program <= 127 or
            route.get("program_1based") != program + 1 or
            probe.get("program_0based", program) != program or
            probe.get("program_1based", program + 1) != program + 1 or
            type(probe.get("bank")) is not int or not 0 <= probe["bank"] <= 16383 or
            type(probe.get("velocity")) is not int or not 1 <= probe["velocity"] <= 127 or
            not isinstance(probe.get("preset_name"), str) or not probe["preset_name"]):
        raise ValueError("GM program route or preset is invalid")
    gm_note = probe.get("note")
    if type(gm_note) is not int or not 0 <= gm_note <= 127:
        raise ValueError("GM MIDI note is invalid")
    residual = 100.0 * (calibrated_note - gm_note)
    if abs(residual) > tolerance:
        raise ValueError(f"calibrated pitch differs from GM note by {residual:.2f} cents")
    gm_wav, gm_info, gm_wav_hash = _verified_wav(gm_path, probe.get("wav"), "GM")
    rate = native_info["sample_rate"]
    if rate != gm_info["sample_rate"] or rate != gm_conditions.get("sample_rate"):
        raise ValueError("native and GM WAV sample rates differ; resampling is unsupported")
    receipt_path = _local_file(native_path, native.get("receipt_file"), "native receipt")
    receipt = _read_json(receipt_path, "native receipt")
    if receipt.get("status") != "ok":
        raise ValueError("native receipt does not report success")
    phase = _phase(native, receipt, native_info["frames"], gm, gm_info["frames"], rate)
    nframes, native_pcm = _read_pcm(native_wav)
    gframes, gm_pcm = _read_pcm(gm_wav)
    if nframes != native_info["frames"] or gframes != gm_info["frames"]:
        raise ValueError("WAV changed while comparison was being assembled")
    count_hold, count_tail = phase["matched_hold_frames"], phase["matched_tail_frames"]
    n_on, n_off, g_off = (phase[k] for k in (
        "native_note_on_frame", "native_note_off_frame", "gm_note_off_frame"))
    native_excerpt = (native_pcm[n_on * 4:(n_on + count_hold) * 4]
                      + native_pcm[n_off * 4:(n_off + count_tail) * 4])
    gm_excerpt = (gm_pcm[:count_hold * 4]
                  + gm_pcm[g_off * 4:(g_off + count_tail) * 4])
    expected_size = (count_hold + count_tail) * 4
    if len(native_excerpt) != expected_size or len(gm_excerpt) != expected_size:
        raise ValueError("matched PCM window exceeds source WAV")
    gap_frames = round(gap_seconds * rate)
    preview_pcm = (_scaled(native_excerpt, source_gain) + bytes(gap_frames * 4)
                   + _scaled(gm_excerpt, gm_gain))
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    stem = f"{re.sub(r'[^a-z0-9]+', '-', native['engine'].lower()).strip('-')}-{gm_probe_id}"
    if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in stem):
        raise ValueError("comparison output stem contains unsafe characters")
    names = {
        "native_excerpt": stem + ".native.wav",
        "gm_excerpt": stem + ".gm.wav",
        "ab_preview": stem + ".ab.wav",
        "manifest": stem + ".comparison.json",
    }
    targets = {name: output / filename for name, filename in names.items()}
    for target in targets.values():
        if target.exists():
            raise FileExistsError(f"comparison output already exists: {target}")
    input_hashes = {
        native_path: native_hash, native_wav: native_wav_hash,
        receipt_path: _hash(receipt_path), gm_path: gm_hash,
        gm_wav: gm_wav_hash, calibration_path: calibration_ref["sha256"],
    }
    publish = output / f".comparison-publish-{uuid.uuid4().hex}"
    publish.mkdir()
    try:
        outputs = {
            "native_excerpt": _write_pcm(publish / names["native_excerpt"], native_excerpt, rate),
            "gm_excerpt": _write_pcm(publish / names["gm_excerpt"], gm_excerpt, rate),
            "ab_preview": _write_pcm(publish / names["ab_preview"], preview_pcm, rate),
        }
        result = {
            "schema_version": VERSION,
            "source_record_id": native["source_record_id"],
            "engine": native["engine"],
            "input": {
                "native_manifest": {"path": str(native_path), "sha256": native_hash},
                "native_wav": {"path": str(native_wav), "sha256": native_wav_hash},
                "native_receipt": {"path": str(receipt_path), "sha256": _hash(receipt_path)},
                "gm_manifest": {"path": str(gm_path), "sha256": gm_hash},
                "gm_wav": {"path": str(gm_wav), "sha256": gm_wav_hash},
                "pitch_calibration": calibration_ref,
                "soundfont": gm.get("soundfont"),
            },
            "pitch": {
                "calibration_basis": calibration["basis"],
                "calibration_status": calibration["status"],
                "calibration_confidence": calibration["confidence"],
                "calibrated_sounding_midi_note": calibrated_note,
                "gm_note": gm_note,
                "residual_cents": residual,
                "tolerance_cents": tolerance,
                "native_raw_pitch": raw_pitch,
                "evidence": calibration["evidence"],
            },
            "route": {
                "gm_probe_id": gm_probe_id,
                "kind": route["kind"],
                "program_0based": route["program_0based"],
                "program_1based": route["program_1based"],
                "bank": probe["bank"],
                "preset_name": probe["preset_name"],
                "velocity": probe["velocity"],
            },
            "conditions": {
                "sample_rate": rate,
                "channels": 2,
                "sample_width_bytes": 2,
                "dry": True,
                "source_gain": source_gain,
                "gm_gain": gm_gain,
                "gap_seconds": gap_seconds,
                "gap_frames": gap_frames,
                "gain_scope": "preview only; source and GM excerpts retain original PCM amplitude",
                "source_fixed_volume_vs_midi_velocity_note": "Native fixed voice volume and GM MIDI velocity are separate controls; the scalar gains are presentation levels only.",
                "phase_alignment": phase,
            },
            "outputs": outputs,
        }
        (publish / names["manifest"]).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for path, expected_hash in input_hashes.items():
            if _hash(path) != expected_hash:
                raise ValueError(f"comparison input changed during assembly: {path}")
        for target in targets.values():
            if target.exists():
                raise FileExistsError(f"comparison output appeared during assembly: {target}")
        for key in ("native_excerpt", "gm_excerpt", "ab_preview", "manifest"):
            (publish / names[key]).replace(targets[key])
    finally:
        if publish.resolve().parent != output.resolve() or not publish.name.startswith(".comparison-publish-"):
            raise RuntimeError("unsafe comparison publication stage")
        shutil.rmtree(publish)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-manifest", required=True)
    parser.add_argument("--gm-manifest", required=True)
    parser.add_argument("--gm-probe-id", required=True)
    parser.add_argument("--pitch-calibration", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--residual-cents-tolerance", type=float, default=25.0)
    parser.add_argument("--source-gain", type=float, default=1.0)
    parser.add_argument("--gm-gain", type=float, default=1.0)
    parser.add_argument("--gap-seconds", type=float, default=0.25)
    args = parser.parse_args(argv)
    manifest = build_matched_comparison(
        args.native_manifest, args.gm_manifest, gm_probe_id=args.gm_probe_id,
        pitch_calibration_path=args.pitch_calibration, output_dir=args.output,
        residual_cents_tolerance=args.residual_cents_tolerance,
        source_gain=args.source_gain, gm_gain=args.gm_gain,
        gap_seconds=args.gap_seconds)
    print(json.dumps({"schema_version": manifest["schema_version"],
                      "source_record_id": manifest["source_record_id"],
                      "gm_probe_id": manifest["route"]["gm_probe_id"],
                      "output_dir": str(Path(args.output).resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
