"""Native Gradius III YM2151 probe capture through MAME."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave

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
                  start_seconds: float, end_seconds: float) -> dict:
    try:
        with wave.open(str(capture), "rb") as source:
            channels, width = source.getnchannels(), source.getsampwidth()
            rate, total_frames = source.getframerate(), source.getnframes()
            if source.getcomptype() != "NONE" or width != 2 or channels not in (1, 2):
                raise RuntimeError("MAME WAV must be uncompressed mono/stereo PCM16")
            if rate != sample_rate:
                raise RuntimeError(f"MAME WAV sample rate {rate} does not match requested {sample_rate}")
            first = round(start_seconds * rate)
            last = round(end_seconds * rate)
            if first < 0 or last <= first or last > total_frames:
                raise RuntimeError("MAME WAV does not contain the receipt's complete note and tail interval")
            source.setpos(first)
            pcm = source.readframes(last - first)
            frames = last - first
            if len(pcm) != frames * channels * width or not pcm:
                raise RuntimeError("MAME WAV capture interval is empty or truncated")
    except (OSError, EOFError, wave.Error) as exc:
        raise RuntimeError(f"not a readable MAME PCM WAV: {capture}") from exc
    try:
        with wave.open(str(destination), "wb") as target:
            target.setnchannels(channels)
            target.setsampwidth(width)
            target.setframerate(rate)
            target.writeframes(pcm)
    except (OSError, wave.Error) as exc:
        raise RuntimeError(f"could not write trimmed WAV: {destination}") from exc
    return {
        "sample_rate": rate,
        "channels": channels,
        "sample_width_bytes": width,
        "frames": frames,
        "duration_seconds": frames / rate,
    }


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

    with tempfile.TemporaryDirectory(prefix=".g3-probe-", dir=output) as stage_name:
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
        argv = [
            str(mame), "gradius3", "-rompath", str(roms),
            "-autoboot_script", str(_SCRIPT), "-autoboot_delay", "0",
            "-video", "none", "-samplerate", str(sample_rate),
            "-wavwrite", str(captured_wav),
        ]
        timeout_seconds = max(60, math.ceil((hold_frames+tail_frames+300)/50)+15)
        try:
            result = subprocess.run(argv, cwd=stage, env=env, shell=False,
                                    capture_output=True, text=True, timeout=timeout_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"MAME native probe timed out after {timeout_seconds} seconds") from exc
        except OSError as exc:
            raise RuntimeError(f"MAME invocation failed: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip()[-2000:]
            raise RuntimeError(f"MAME exited {result.returncode}: {detail}")
        if not staged_receipt.is_file():
            raise RuntimeError("MAME exited without a probe receipt")
        try:
            raw_receipt = json.loads(staged_receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("MAME wrote an invalid probe receipt") from exc
        validated = _validate_receipt(raw_receipt, patch_num=patch_num,
            patch_id=str(patch_id), keycode=keycode, hold_frames=hold_frames,
            tail_frames=tail_frames, audio_rom=rom_bytes)
        wav_info = _trim_capture(captured_wav, stage / wav_path.name,
            sample_rate=sample_rate,
            start_seconds=validated["note_on_time_seconds"],
            end_seconds=validated["capture_end_time_seconds"])
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
            },
            "run_arguments": argv,
            "receipt_file": receipt_path.name,
            "wav": {"file": wav_path.name, **wav_info,
                    "sha256": _sha256(staged_wav)},
        }
        staged_manifest = stage / manifest_path.name
        staged_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n",
                                   encoding="utf-8")
        # Output paths were checked before rendering. Publish all three only on success.
        staged_wav.replace(wav_path)
        staged_receipt_final.replace(receipt_path)
        staged_manifest.replace(manifest_path)
    return manifest
