"""Raw AKAO SPU articulation capture through MAME 0.285's Konami GV board."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import uuid

from .native_rendering import _resolve_mame, _sha256, _trim_capture

MANIFEST_VERSION = "instrument-native-chip-render/v1"
RECEIPT_VERSION = "akao-spu-native-probe-receipt/v1"
_SCRIPT = Path(__file__).resolve().parents[1] / "psx" / "native_probe.lua"
_HEADER = struct.Struct("<4sHH6I")
_ART = struct.Struct("<IIhHHH")
_SPU_RAM_BYTES = 512 * 1024


def _read_bank(path: Path, articulation_id: int, format_version: str) -> tuple[dict, bytes]:
    if format_version not in {"3.1", "3.2"}:
        raise ValueError("format_version must explicitly identify AKAO 3.1 or 3.2")
    data = path.read_bytes()
    if len(data) < 0x40:
        raise ValueError("AKAO bank header is shorter than 0x40 bytes")
    magic, set_id, flags, unknown_08, unknown_0c, dest, declared, first, count = _HEADER.unpack_from(data)
    if magic != b"AKAO":
        raise ValueError("sample bank is not AKAO")
    if not 1 <= count <= 4096 or not first <= articulation_id < first + count:
        raise ValueError("articulation_id is outside the declared AKAO articulation range")
    payload_offset = 0x40 + count * _ART.size
    if payload_offset >= len(data):
        raise ValueError("AKAO bank ends before its sample payload")
    if dest % 8 or not 0 <= dest < _SPU_RAM_BYTES:
        raise ValueError("AKAO SPU destination must be 8-byte aligned and within SPU RAM")
    physical_payload = data[payload_offset:]
    if declared <= 0:
        raise ValueError("AKAO declared sample payload length must be positive")
    payload = physical_payload[:declared]
    if not payload or len(payload) % 16 or dest + len(payload) > _SPU_RAM_BYTES:
        raise ValueError("AKAO payload must be complete 16-byte ADPCM blocks within SPU RAM")
    index = articulation_id - first
    art_offset = 0x40 + index * _ART.size
    start, loop, fine_tune, unity, adsr1, adsr2 = _ART.unpack_from(data, art_offset)
    if unity > 0xff:
        raise ValueError("AKAO articulation unity high byte is unsupported for version 3.1/3.2")
    if start % 16 or loop % 16 or not 0 <= start < len(payload) or not 0 <= loop < len(payload):
        raise ValueError("articulation start/loop address is not a valid ADPCM block in the payload")
    other_starts = [
        _ART.unpack_from(data, 0x40 + i * _ART.size)[0]
        for i in range(count) if i != index
    ]
    next_sample = min((pos for pos in other_starts if start < pos <= len(payload)), default=len(payload))
    end = next((pos for pos in range(start, next_sample, 16) if payload[pos + 1] & 1), None)
    if end is None:
        raise ValueError("selected articulation has no ADPCM end block before file end")
    if payload[end + 1] & 2 and not start <= loop <= end:
        raise ValueError("looping articulation repeat address lies outside its selected sample")
    shortfall = max(0, declared - len(physical_payload))
    excess = max(0, len(physical_payload) - declared)
    if shortfall:
        warning = (
            f"Declared payload exceeds physical file payload by {shortfall} bytes; only the selected "
            "articulation was checked through its ADPCM end block."
        )
    elif excess:
        warning = f"Physical file contains {excess} bytes beyond declared payload; those bytes were not uploaded."
    else:
        warning = None
    metadata = {
        "format_version": format_version,
        "sample_set_id": set_id,
        "header_flags": flags,
        "header_unknown_08": unknown_08,
        "header_unknown_0c": unknown_0c,
        "spu_destination": dest,
        "declared_payload_bytes": declared,
        "actual_payload_bytes": len(payload),
        "physical_payload_bytes": len(physical_payload),
        "declared_payload_shortfall_bytes": max(0, shortfall),
        "payload_offset": payload_offset,
        "articulation_id": articulation_id,
        "articulation_index": index,
        "articulation_offset": art_offset,
        "sample_start_offset": start,
        "loop_offset": loop,
        "sample_end_block_offset": end,
        "sample_end_flags": payload[end + 1],
        "raw_fine_tune": fine_tune,
        "raw_unity_key": unity,
        "adsr1": adsr1,
        "adsr2": adsr2,
        "selected_sample_complete": True,
        "bank_payload_complete": shortfall == 0 and excess == 0,
        "truncation_warning": warning,
        "articulation_bytes_hex": data[art_offset:art_offset + _ART.size].hex(),
        "sample_first_block_hex": payload[start:start + 16].hex(),
        "sample_end_block_hex": payload[end:end + 16].hex(),
    }
    return metadata, payload


def _validate_receipt(receipt: object, instrument: dict, payload: bytes, pitch: int,
                      hold: int, tail: int) -> dict:
    if not isinstance(receipt, dict):
        raise RuntimeError("MAME receipt is not a JSON object")
    if receipt.get("schema_version") != RECEIPT_VERSION or receipt.get("status") != "ok":
        raise RuntimeError(f"MAME SPU probe failed: {receipt.get('failure', 'bad receipt')}")
    expected = {
        "payload_length": instrument["actual_payload_bytes"],
        "spu_destination": instrument["spu_destination"],
        "sample_start": instrument["sample_start_offset"],
        "loop_start": instrument["loop_offset"],
        "sample_end": instrument["sample_end_block_offset"],
        "adsr1": instrument["adsr1"],
        "adsr2": instrument["adsr2"],
        "pitch_register_raw_spu": pitch,
        "hold_frames": hold,
        "tail_frames": tail,
        "cpu_idle_pc": 0xA0010000,
        "words_written": len(payload) // 2,
        "verified_payload_bytes": len(payload),
        "dma_readback_base": 0x20000,
        "first_word": int.from_bytes(payload[:2], "little"),
        "last_word": int.from_bytes(payload[-2:], "little"),
        "voice_start_register_readback": (instrument["spu_destination"] + instrument["sample_start_offset"]) // 8,
        "voice_pitch_register_readback": pitch,
        "adsr1_readback": instrument["adsr1"],
        "adsr2_readback": instrument["adsr2"],
        "spu_control_readback": 0xC000,
        "cpu_pc_after_upload": 0xA0010000,
    }
    for field, value in expected.items():
        if type(receipt.get(field)) is not int or receipt[field] != value:
            raise RuntimeError(f"MAME receipt {field} does not match request")
    times = []
    for name in ("note_on_time_seconds", "note_off_time_seconds",
                 "capture_end_time_seconds", "frame_period_seconds"):
        value = receipt.get(name)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise RuntimeError(f"MAME receipt has invalid {name}")
        times.append(float(value))
    on, off, end, period = times
    if not 0 < period < 1 or not on < off <= end:
        raise RuntimeError("MAME receipt has inconsistent timestamps")
    if (not math.isclose(off - on, hold * period, abs_tol=1e-6)
            or not math.isclose(end - off, tail * period, abs_tol=1e-6)):
        raise RuntimeError("MAME receipt durations disagree with frame counts")
    return {
        "note_on_time_seconds": on,
        "note_off_time_seconds": off,
        "capture_end_time_seconds": end,
        "frame_period_seconds": period,
    }


def render_akao_spu_probe(
    bank_path: str | Path, *, mame_path: str | Path, rom_path: str | Path,
    output_dir: str | Path, source_record_id: str, articulation_id: int, format_version: str,
    pitch_register: int = 0x1000, hold_frames: int = 30,
    tail_frames: int = 30, sample_rate: int = 44100,

) -> dict:
    """Render one raw AKAO articulation with native PSX SPU registers.

    The pitch register is copied verbatim. No MIDI note or AKAO instrument-region
    ADSR override is inferred. Outputs publish only after receipt and WAV checks.
    """
    if not isinstance(source_record_id, str) or not source_record_id.strip():
        raise ValueError("source_record_id must be a nonempty string")
    if type(articulation_id) is not int or articulation_id < 0:
        raise ValueError("articulation_id must be a nonnegative integer")
    if type(pitch_register) is not int or not 1 <= pitch_register <= 0x3fff:
        raise ValueError("pitch_register must be a raw SPU value from 1 through 0x3fff")
    if type(hold_frames) is not int or not 1 <= hold_frames <= 3600:
        raise ValueError("hold_frames must be an integer from 1 through 3600")
    if type(tail_frames) is not int or not 0 <= tail_frames <= 3600:
        raise ValueError("tail_frames must be an integer from 0 through 3600")
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise ValueError("sample_rate must be an integer from 8000 through 192000")
    bank = Path(bank_path).expanduser().resolve(strict=True)
    if not bank.is_file():
        raise ValueError("bank_path must name a file")
    instrument, payload = _read_bank(bank, articulation_id, format_version)
    mame = _resolve_mame(mame_path)
    roms = Path(rom_path).expanduser().resolve(strict=True)
    if not roms.is_dir() or not (roms / "konamigv.zip").is_file():
        raise ValueError("rom_path must contain konamigv.zip")
    if not _SCRIPT.is_file():
        raise FileNotFoundError(f"native SPU Lua probe missing: {_SCRIPT}")
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    stem = f"akao-spu-set-{instrument['sample_set_id']:04d}-art-{articulation_id:04d}-pitch-{pitch_register:04x}"
    wav_path = output / (stem + ".wav")
    receipt_path = output / (stem + ".receipt.json")
    manifest_path = output / (stem + ".manifest.json")
    for target in (wav_path, receipt_path, manifest_path):
        if target.exists():
            raise FileExistsError(f"native SPU output already exists: {target}")

    with tempfile.TemporaryDirectory(prefix=".akao-spu-probe-") as stage_name:
        stage = Path(stage_name)
        payload_file = stage / "payload.adpcm"
        payload_file.write_bytes(payload)
        captured_wav = stage / "capture.wav"
        staged_receipt = stage / "receipt.json"
        version = subprocess.run([str(mame), "-version"], cwd=stage, shell=False,
                                 capture_output=True, text=True, timeout=15, check=False)
        if version.returncode or "0.285" not in version.stdout:
            raise RuntimeError(f"native SPU probe requires MAME 0.285; got {version.stdout!r} {version.stderr!r}")
        env = os.environ.copy()
        env.update({
            "GME_PSX_PAYLOAD": str(payload_file),
            "GME_PSX_RECEIPT": str(staged_receipt),
            "GME_PSX_DEST": str(instrument["spu_destination"]),
            "GME_PSX_PAYLOAD_LENGTH": str(len(payload)),
            "GME_PSX_START": str(instrument["sample_start_offset"]),
            "GME_PSX_LOOP": str(instrument["loop_offset"]),
            "GME_PSX_END": str(instrument["sample_end_block_offset"]),
            "GME_PSX_ADSR1": str(instrument["adsr1"]),
            "GME_PSX_ADSR2": str(instrument["adsr2"]),
            "GME_PSX_PITCH": str(pitch_register),
            "GME_PSX_HOLD": str(hold_frames),
            "GME_PSX_TAIL": str(tail_frames),
        })
        dirs = {name: stage / name for name in
                ("home", "cfg", "nvram", "input", "state", "snapshots", "diff", "comments")}
        for path in dirs.values():
            path.mkdir()
        argv = [
            str(mame), "konamigv", "-noreadconfig", "-nowriteconfig",
            "-rompath", str(roms), "-homepath", str(dirs["home"]),
            "-cfg_directory", str(dirs["cfg"]),
            "-nvram_directory", str(dirs["nvram"]),
            "-input_directory", str(dirs["input"]),
            "-state_directory", str(dirs["state"]),
            "-snapshot_directory", str(dirs["snapshots"]),
            "-diff_directory", str(dirs["diff"]),
            "-comment_directory", str(dirs["comments"]),
            "-autoboot_script", str(_SCRIPT), "-autoboot_delay", "0",
            "-video", "none", "-seconds_to_run", str(max(5, math.ceil((hold_frames + tail_frames + 120) / 55))),
            "-samplerate", str(sample_rate),
            "-wavwrite", str(captured_wav),
        ]
        timeout = max(90, math.ceil((hold_frames + tail_frames + 300) / 50) + 45)
        try:
            result = subprocess.run(argv, cwd=stage, env=env, shell=False,
                                    capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"MAME SPU probe timed out after {timeout}s") from exc
        if result.returncode:
            raise RuntimeError(f"MAME SPU probe exited {result.returncode}; stdout={result.stdout!r}; stderr={result.stderr!r}")
        if not staged_receipt.is_file():
            raise RuntimeError(f"MAME SPU probe wrote no receipt; stdout={result.stdout!r}; stderr={result.stderr!r}")
        try:
            raw_receipt = json.loads(staged_receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("MAME SPU probe receipt is unreadable") from exc
        timing = _validate_receipt(raw_receipt, instrument, payload, pitch_register, hold_frames, tail_frames)
        try:
            wav_info, alignment = _trim_capture(
                captured_wav, stage / wav_path.name, sample_rate=sample_rate,
                start_seconds=timing["note_on_time_seconds"],
                note_off_seconds=timing["note_off_time_seconds"],
                end_seconds=timing["capture_end_time_seconds"],
                frame_period_seconds=timing["frame_period_seconds"],
            )
        except RuntimeError as exc:
            raise RuntimeError(f"{exc}; receipt_timing={timing!r}; captured_wav_bytes={captured_wav.stat().st_size if captured_wav.exists() else None}; MAME stdout={result.stdout!r}; stderr={result.stderr!r}") from exc
        if alignment["peak_pcm16"] == 0:
            raise RuntimeError("native SPU probe produced silent PCM")
        upload_verification = {name: raw_receipt[name] for name in (
            "words_written", "verified_payload_bytes", "dma_readback_base",
            "first_word", "last_word",
            "voice_start_register_readback", "voice_pitch_register_readback",
            "adsr1_readback", "adsr2_readback", "spu_control_readback",
            "cpu_pc_after_upload")}
        raw_receipt["wav_alignment"] = alignment
        raw_receipt["declared_payload_bytes"] = instrument["declared_payload_bytes"]
        raw_receipt["actual_payload_bytes"] = instrument["actual_payload_bytes"]
        raw_receipt["truncation_warning"] = instrument["truncation_warning"]
        raw_receipt["format_version"] = format_version
        raw_receipt["uploaded_payload_sha256"] = hashlib.sha256(payload).hexdigest()
        staged_receipt_final = stage / receipt_path.name
        staged_receipt_final.write_text(json.dumps(raw_receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "schema_version": MANIFEST_VERSION,
            "engine": "MAME PlayStation SPU (Konami GV)",
            "source_record_id": source_record_id,
            "source": {
                "akao_bank_path": str(bank),
                "akao_bank_sha256": _sha256(bank),
                "uploaded_payload_sha256": hashlib.sha256(payload).hexdigest(),
                "mame_executable": str(mame),
                "mame_executable_sha256": _sha256(mame),
                "mame_version": version.stdout.strip(),
                "konamigv_rom_archive": str(roms / "konamigv.zip"),
                "konamigv_rom_archive_sha256": _sha256(roms / "konamigv.zip"),
                "probe_script": str(_SCRIPT),
                "probe_script_sha256": _sha256(_SCRIPT),
            },
            "instrument": instrument,
            "key": {
                "pitch_register_raw_spu": pitch_register,
                "midi_note": None,
                "velocity": None,
                "pitch_note": "Raw SPU pitch register, with no inferred MIDI note or source-region tuning.",
            },
            "conditions": {
                "requested_hold_frames": hold_frames,
                "requested_tail_frames": tail_frames,
                "note_on_time_seconds": timing["note_on_time_seconds"],
                "note_off_time_seconds": timing["note_off_time_seconds"],
                "capture_end_time_seconds": timing["capture_end_time_seconds"],
                "frame_period_seconds": timing["frame_period_seconds"],
                "sample_rate_requested": sample_rate,
                "board": {
                    "system": "konamigv",
                    "hardware": "Konami GV PlayStation-derived arcade board",
                    "spu_clock_hz": 33868800,
                    "speaker_gain": 0.75,
                    "speaker_routing": "SPU output 1 to left; output 0 to right",
                    "mame_driver_status": "imperfect",
                },
                "cpu_isolation": "Uncached KSEG1 RAM branch loop at 0xA0010000; SR interrupts masked.",
                "upload_verification": upload_verification,
                "spu_voice": "voice 0 only; raw AKAO articulation ADSR; no instrument-region override",
                "spu_reverb_fm_noise": "disabled",
                "wav_alignment": {key: alignment[key] for key in (
                    "requested_note_on_seconds", "actual_trim_start_seconds",
                    "actual_trim_end_seconds", "alignment_uncertainty_seconds",
                    "tail_shortfall_seconds", "lead_in_frames", "clock_origin_verified")},
            },
            "run_arguments": argv,
            "receipt_file": receipt_path.name,
            "wav": {"file": wav_path.name, **wav_info, "sha256": _sha256(stage / wav_path.name)},
        }
        staged_manifest = stage / manifest_path.name
        staged_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        publish = output / f".akao-spu-publish-{uuid.uuid4().hex}"
        publish.mkdir()
        try:
            for target in (wav_path, receipt_path, manifest_path):
                if target.exists():
                    raise FileExistsError(f"native SPU output appeared during render: {target}")
            for src, target in (
                (stage / wav_path.name, wav_path),
                (staged_receipt_final, receipt_path),
                (staged_manifest, manifest_path),
            ):
                destination = publish / target.name
                shutil.copyfile(src, destination)
                destination.replace(target)
        finally:
            if publish.resolve().parent != output.resolve() or not publish.name.startswith(".akao-spu-publish-"):
                raise RuntimeError("unsafe native SPU publication stage")
            shutil.rmtree(publish)
    return manifest



