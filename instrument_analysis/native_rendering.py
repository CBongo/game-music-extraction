"""Native Gradius III YM2151 probe capture through MAME."""
from __future__ import annotations

import array
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave
import uuid

MANIFEST_VERSION = "instrument-native-render/v1"
RECEIPT_VERSION = "gradius3-native-probe-receipt/v1"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "arcade" / "gradius3" / "g3_probe.lua"
_COMMON = _REPO_ROOT / "arcade" / "gradius3" / "g3_common.lua"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite_seconds(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RuntimeError(f"invalid {label} in MAME receipt")
    if value < 0:
        raise RuntimeError(f"negative {label} in MAME receipt")
    return float(value)


def _validate_receipt(receipt: object, *, patch_num: int, patch_id: str,
                      keycode: int, hold_frames: int, tail_frames: int,
                      audio_rom: bytes) -> dict:
    if not isinstance(receipt, dict):
        raise RuntimeError("MAME receipt is not a JSON object")
    if receipt.get("schema_version") != RECEIPT_VERSION or receipt.get("status") != "ok":
        reason = receipt.get("failure", "receipt is not successful")
        raise RuntimeError(f"MAME probe failed: {reason}")
    expected = {
        "patch_num": patch_num,
        "keycode_raw_ym2151": keycode,
        "hold_frames": hold_frames,
        "tail_frames": tail_frames,
    }
    for field, value in expected.items():
        if type(receipt.get(field)) is not int or receipt[field] != value:
            raise RuntimeError(f"MAME receipt {field} does not match request")
    if str(receipt.get("patch_id")) != str(patch_num):
        raise RuntimeError("MAME receipt patch_id does not match patch number")
    pointer = receipt.get("patch_pointer")
    if type(pointer) is not int or pointer < 0 or pointer + 25 > len(audio_rom):
        raise RuntimeError("MAME receipt patch pointer is outside the supplied audio ROM")
    pointer_table = 0x27CD + patch_num * 2
    expected_pointer = int.from_bytes(audio_rom[pointer_table:pointer_table + 2], "little")
    if pointer != expected_pointer:
        raise RuntimeError("MAME receipt patch pointer disagrees with supplied audio ROM")
    actual_bytes = audio_rom[pointer:pointer + 25].hex()
    if receipt.get("patch_bytes_hex") != actual_bytes:
        raise RuntimeError("MAME patch bytes do not match supplied audio ROM")
    on = _finite_seconds(receipt.get("note_on_time_seconds"), "note_on_time_seconds")
    off = _finite_seconds(receipt.get("note_off_time_seconds"), "note_off_time_seconds")
    end = _finite_seconds(receipt.get("capture_end_time_seconds"), "capture_end_time_seconds")
    if not on < off <= end:
        raise RuntimeError("MAME receipt has inconsistent note/capture times")
    period = _finite_seconds(receipt.get('frame_period_seconds'), 'frame_period_seconds')
    if not 0 < period < 1:
        raise RuntimeError('invalid frame period in MAME receipt')
    if (not math.isclose(off-on, hold_frames*period, abs_tol=1e-6)
            or not math.isclose(end-off, tail_frames*period, abs_tol=1e-6)):
        raise RuntimeError('MAME receipt durations disagree with requested frame counts')
    startup_frames = receipt.get("startup_frames")
    if type(startup_frames) is not int or not 1 <= startup_frames <= 300:
        raise RuntimeError("MAME receipt has invalid startup frame count")
    return {
        "patch_pointer": pointer,
        "patch_bytes_hex": actual_bytes,
        "note_on_time_seconds": on,
        "note_off_time_seconds": off,
        "capture_end_time_seconds": end,
        "startup_frames": startup_frames,
        "frame_period_seconds": period,
    }


def _trim_capture(capture: Path, destination: Path, *, sample_rate: int,
                  start_seconds: float, note_off_seconds: float,
                  end_seconds: float, frame_period_seconds: float) -> tuple[dict, dict]:
    try:
        with wave.open(str(capture), "rb") as source:
            channels, width = source.getnchannels(), source.getsampwidth()
            rate, total_frames = source.getframerate(), source.getnframes()
            if source.getcomptype() != "NONE" or width != 2 or channels not in (1, 2):
                raise RuntimeError("MAME WAV must be uncompressed mono/stereo PCM16")
            if rate != sample_rate:
                raise RuntimeError(f"MAME WAV sample rate {rate} does not match requested {sample_rate}")
            pcm = source.readframes(total_frames)
            if len(pcm) != total_frames * channels * width:
                raise RuntimeError("MAME WAV capture is truncated")
    except (OSError, EOFError, wave.Error) as exc:
        raise RuntimeError(f"not a readable MAME PCM WAV: {capture}") from exc
    recorded_seconds = total_frames / rate
    end_shortfall = max(0.0, end_seconds - recorded_seconds)
    end_overrun = max(0.0, recorded_seconds - end_seconds)
    if max(end_shortfall, end_overrun) > frame_period_seconds:
        raise RuntimeError("MAME WAV end differs from the receipt by more than one video frame")

    # Keep one full video frame before the key-on timestamp to cover mixer-clock
    # alignment and slow instrument attacks. The first nonzero sample is diagnostic only.
    requested_start = max(0.0, start_seconds - frame_period_seconds)
    first_frame = round(requested_start * rate)
    last_frame = min(round(end_seconds * rate), total_frames)
    if last_frame <= first_frame:
        raise RuntimeError("MAME WAV does not contain a usable note/tail interval")
    frame_bytes = channels * width
    raw_pcm = pcm
    pcm = raw_pcm[first_frame * frame_bytes:last_frame * frame_bytes]
    frames = last_frame - first_frame
    if not pcm or len(pcm) != frames * frame_bytes:
        raise RuntimeError("MAME WAV capture interval is empty or truncated")
    actual_start = first_frame / rate
    actual_end = last_frame / rate
    expected_capture_seconds = end_seconds - requested_start
    actual_capture_seconds = frames / rate
    if abs(actual_capture_seconds - expected_capture_seconds) > frame_period_seconds:
        raise RuntimeError("MAME WAV note/tail duration differs by more than one video frame")

    # PCM16 signal checks are stored in the receipt metadata, not the strict manifest schema.
    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max((abs(value) for value in samples), default=0)
    rms = math.sqrt(sum(value * value for value in samples) / len(samples)) if samples else 0.0
    first_nonzero_byte = next((i for i, value in enumerate(raw_pcm) if value), None)
    first_nonzero_seconds = (first_nonzero_byte // frame_bytes) / rate if first_nonzero_byte is not None else None
    try:
        with wave.open(str(destination), "wb") as target:
            target.setnchannels(channels)
            target.setsampwidth(width)
            target.setframerate(rate)
            target.writeframes(pcm)
    except (OSError, wave.Error) as exc:
        raise RuntimeError(f"could not write trimmed WAV: {destination}") from exc
    wav_info = {
        "sample_rate": rate,
        "channels": channels,
        "sample_width_bytes": width,
        "frames": frames,
        "duration_seconds": actual_capture_seconds,
    }
    alignment = {
        "requested_note_on_seconds": start_seconds,
        "requested_note_off_seconds": note_off_seconds,
        "receipt_capture_end_seconds": end_seconds,
        "actual_trim_start_seconds": actual_start,
        "actual_trim_end_seconds": actual_end,
        "alignment_uncertainty_seconds": frame_period_seconds,
        "lead_in_frames": max(0, round(start_seconds * rate) - first_frame),
        "tail_shortfall_seconds": max(0.0, end_seconds - actual_end),
        "capture_end_overrun_seconds": max(0.0, actual_end - end_seconds),
        "clock_origin_verified": False,
        "first_nonzero_sample_seconds_diagnostic": first_nonzero_seconds,
        "recorded_wav_duration_seconds": recorded_seconds,
        "peak_pcm16": peak,
        "rms_pcm16": rms,
        "clipped_samples_pcm16": sum(1 for value in samples if abs(value) >= 32767),
    }
    return wav_info, alignment


def _resolve_mame(mame_path: str | Path) -> Path:
    raw = os.fspath(mame_path)
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return candidate.resolve(strict=True)
    found = shutil.which(raw)
    if found:
        return Path(found).resolve(strict=True)
    raise FileNotFoundError(f"MAME executable not found: {mame_path}")


def render_gradius3_probe(
    *, mame_path: str | Path = "mame", rompath: str | Path,
    audio_rom_path: str | Path, output_dir: str | Path,
    source_record_id: str, patch_id: str | int, keycode: int,
    hold_frames: int = 30, tail_frames: int = 30, sample_rate: int = 44100,
) -> dict:
    """Capture one raw YM2151 keycode using MAME and validate a JSON receipt.

    keycode is copied verbatim to YM2151 register 0x28. It is not a MIDI note.
    Output is staged and published only after MAME, receipt, ROM-byte, and WAV checks pass.
    """
    if not isinstance(source_record_id, str) or not source_record_id.strip():
        raise ValueError("source_record_id must be a nonempty string")
    if not isinstance(patch_id, (str, int)) or isinstance(patch_id, bool) or not str(patch_id).strip():
        raise ValueError("patch_id must be a nonempty string or integer")
    if type(keycode) is not int or not 0 <= keycode <= 127:
        raise ValueError("keycode must be a raw YM2151 keycode byte from 0 through 127")
    if type(hold_frames) is not int or not 1 <= hold_frames <= 3600:
        raise ValueError("hold_frames must be an integer from 1 through 3600")
    if type(tail_frames) is not int or not 0 <= tail_frames <= 3600:
        raise ValueError("tail_frames must be an integer from 0 through 3600")
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise ValueError("sample_rate must be an integer from 8000 through 192000")
    try:
        patch_num = int(patch_id, 0) if isinstance(patch_id, str) else patch_id
    except ValueError as exc:
        raise ValueError("patch_id must identify the numeric FM patch 0 through 177") from exc
    if type(patch_num) is not int or not 0 <= patch_num <= 177:
        raise ValueError("patch_id must identify the numeric FM patch 0 through 177")

    mame = _resolve_mame(mame_path)
    roms = Path(rompath).expanduser().resolve(strict=True)
    audio_rom = Path(audio_rom_path).expanduser().resolve(strict=True)
    if not roms.is_dir():
        raise ValueError("rompath must be a directory")
    if not audio_rom.is_file():
        raise ValueError("audio_rom_path must name a file")
    rom_bytes = audio_rom.read_bytes()
    if len(rom_bytes) < 0x27CD + patch_num * 2 + 2:
        raise ValueError("audio ROM is too short for the Gradius III FM patch pointer table")
    pointer = int.from_bytes(rom_bytes[0x27CD + patch_num * 2:0x27CD + patch_num * 2 + 2], "little")
    if pointer + 25 > len(rom_bytes):
        raise ValueError("audio ROM FM patch pointer is outside the supplied ROM")

    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    stem = f"gradius3-fm-{patch_num:03d}-kc-{keycode:03d}"
    wav_path = output / f"{stem}.wav"
    manifest_path = output / f"{stem}.manifest.json"
    receipt_path = output / f"{stem}.receipt.json"
    collisions = [p for p in (wav_path, manifest_path, receipt_path) if p.exists()]
    if collisions:
        raise FileExistsError(f"native render output already exists: {collisions[0]}")

    with tempfile.TemporaryDirectory(prefix=".g3-probe-") as stage_name:
        stage = Path(stage_name)
        staged_receipt = stage / "receipt.json"
        captured_wav = stage / "capture.wav"
        env = os.environ.copy()
        env.update({
            "GME_G3_PATCH": str(patch_num),
            "GME_G3_KEYCODE": str(keycode),
            "GME_G3_HOLD_FRAMES": str(hold_frames),
            "GME_G3_TAIL_FRAMES": str(tail_frames),
            "GME_G3_RECEIPT": str(staged_receipt),
        })
        output_dirs = {name: stage / name for name in ("home", "cfg", "nvram", "input", "state", "snapshots", "diff", "comments")}
        for path in output_dirs.values():
            path.mkdir()
        argv = [
            str(mame), "gradius3", "-noreadconfig", "-nowriteconfig",
            "-rompath", str(roms), "-homepath", str(output_dirs["home"]),
            "-cfg_directory", str(output_dirs["cfg"]),
            "-nvram_directory", str(output_dirs["nvram"]),
            "-input_directory", str(output_dirs["input"]),
            "-state_directory", str(output_dirs["state"]),
            "-snapshot_directory", str(output_dirs["snapshots"]),
            "-diff_directory", str(output_dirs["diff"]),
            "-comment_directory", str(output_dirs["comments"]),
            "-autoboot_script", str(_SCRIPT), "-autoboot_delay", "0",
            "-video", "none", "-samplerate", str(sample_rate),
            "-wavwrite", str(captured_wav),
        ]
        timeout_seconds = max(60, math.ceil((hold_frames+tail_frames+300)/50)+15)
        try:
            result = subprocess.run(argv, cwd=stage, env=env, shell=False,
                                    capture_output=True, text=True, timeout=timeout_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"MAME native probe timed out after {timeout_seconds} seconds; stdout={exc.stdout!r}; stderr={exc.stderr!r}") from exc
        except OSError as exc:
            raise RuntimeError(f"MAME invocation failed: {exc}") from exc
        if result.returncode:
            raise RuntimeError(f"MAME exited {result.returncode}; stdout={result.stdout!r}; stderr={result.stderr!r}")
        if not staged_receipt.is_file():
            raise RuntimeError(f"MAME exited without a probe receipt; stdout={result.stdout!r}; stderr={result.stderr!r}")
        try:
            raw_receipt = json.loads(staged_receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"MAME wrote an invalid probe receipt; stdout={result.stdout!r}; stderr={result.stderr!r}") from exc
        try:
            validated = _validate_receipt(raw_receipt, patch_num=patch_num,
                patch_id=str(patch_id), keycode=keycode, hold_frames=hold_frames,
                tail_frames=tail_frames, audio_rom=rom_bytes)
            wav_info, wav_alignment = _trim_capture(captured_wav, stage / wav_path.name,
                sample_rate=sample_rate,
                start_seconds=validated["note_on_time_seconds"],
                note_off_seconds=validated["note_off_time_seconds"],
                end_seconds=validated["capture_end_time_seconds"],
                frame_period_seconds=validated["frame_period_seconds"])
        except RuntimeError as exc:
            raise RuntimeError(f"{exc}; MAME stdout={result.stdout!r}; stderr={result.stderr!r}") from exc
        raw_receipt["wav_alignment"] = wav_alignment
        manifest_alignment = {key: wav_alignment[key] for key in (
            "requested_note_on_seconds", "actual_trim_start_seconds",
            "actual_trim_end_seconds", "alignment_uncertainty_seconds",
            "tail_shortfall_seconds", "lead_in_frames", "clock_origin_verified")}
        staged_wav = stage / wav_path.name
        staged_receipt_final = stage / receipt_path.name
        staged_receipt_final.write_text(json.dumps(raw_receipt, indent=2, sort_keys=True)+"\n",
                                        encoding="utf-8")
        manifest = {
            "schema_version": MANIFEST_VERSION,
            "engine": "MAME YM2151",
            "source_record_id": source_record_id,
            "source": {
                "audio_rom_path": str(audio_rom),
                "audio_rom_sha256": _sha256(audio_rom),
                "mame_executable": str(mame),
                "mame_executable_sha256": _sha256(mame),
                "probe_script": str(_SCRIPT),
                "probe_script_sha256": _sha256(_SCRIPT),
                "common_script_sha256": _sha256(_COMMON),
            },
            "instrument": {
                "patch_id": str(patch_id),
                "patch_num": patch_num,
                "patch_pointer": validated["patch_pointer"],
                "patch_bytes_hex": validated["patch_bytes_hex"],
            },
            "key": {
                "keycode_raw_ym2151": keycode,
                "midi_note": None,
                "velocity": None,
                "velocity_note": "Native driver has fixed operator levels; velocity is unsupported.",
            },
            "conditions": {
                "requested_hold_frames": hold_frames,
                "requested_tail_frames": tail_frames,
                "startup_frames": validated["startup_frames"],
                "note_on_time_seconds": validated["note_on_time_seconds"],
                "note_off_time_seconds": validated["note_off_time_seconds"],
                "capture_end_time_seconds": validated["capture_end_time_seconds"],
                "sample_rate_requested": sample_rate,
                "frame_period_seconds": validated['frame_period_seconds'],
                "global_lfo_and_noise": "disabled for isolated patch probe",
                "wav_alignment": manifest_alignment,
            },
            "run_arguments": argv,
            "receipt_file": receipt_path.name,
            "wav": {"file": wav_path.name, **wav_info,
                    "sha256": _sha256(staged_wav)},
        }
        staged_manifest = stage / manifest_path.name
        staged_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n",
                                   encoding="utf-8")
        # Copy to an output-local stage, then publish same-volume replacements.
        # The MAME working stage may be on the system temp volume.
        publish = output / f".g3-publish-{uuid.uuid4().hex}"
        publish.mkdir()
        try:
            publish_wav = publish / wav_path.name
            publish_receipt = publish / receipt_path.name
            publish_manifest = publish / manifest_path.name
            shutil.copyfile(staged_wav, publish_wav)
            shutil.copyfile(staged_receipt_final, publish_receipt)
            shutil.copyfile(staged_manifest, publish_manifest)
            publish_wav.replace(wav_path)
            publish_receipt.replace(receipt_path)
            publish_manifest.replace(manifest_path)
        finally:
            if publish.resolve().parent != output.resolve() or not publish.name.startswith('.g3-publish-'):
                raise RuntimeError('unsafe native render publication staging path')
            shutil.rmtree(publish)
    return manifest
