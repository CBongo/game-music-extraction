"""Native SNES S-DSP voice capture through MAME's BRR/ADSR renderer."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
import zlib

from instrument_analysis.native_rendering import _finite_seconds, _resolve_mame, _sha256, _trim_capture

MANIFEST_VERSION = "instrument-native-chip-render/v1"
RECEIPT_VERSION = "snes-native-probe-receipt/v1"
IPL_SHA1 = "97e352553e94242ae823547cd853eecda55c20f0"
IPL_CRC32 = 0x44BB3A40
SPC_SIGNATURE = b"SNES-SPC700 Sound File Data v"
SPC_HEADER_SIZE, ARAM_SIZE, DSP_SIZE = 0x100, 0x10000, 0x80
DSP_OFFSET = SPC_HEADER_SIZE + ARAM_SIZE
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "snes" / "native_probe.lua"


def _read_spc(path: Path):
    raw = path.read_bytes()
    if len(raw) < DSP_OFFSET + DSP_SIZE or not raw.startswith(SPC_SIGNATURE):
        raise ValueError("spc_path must be a standard SPC snapshot with complete RAM and DSP blocks")
    aram = raw[SPC_HEADER_SIZE:DSP_OFFSET]
    dsp = raw[DSP_OFFSET:DSP_OFFSET + DSP_SIZE]
    return raw, aram, dsp, int.from_bytes(raw[0x25:0x27], "little")


def _parse_brr(aram: bytes, start: int, loop: int) -> dict:
    if not 0 <= start < ARAM_SIZE or not 0 <= loop < ARAM_SIZE:
        raise ValueError("BRR directory address is outside SPC RAM")
    position, headers = start, []
    while position + 9 <= ARAM_SIZE:
        header = aram[position]
        headers.append(header)
        position += 9
        if header & 1:
            break
    if not headers or not headers[-1] & 1:
        raise ValueError("BRR sample has no end-marked frame inside SPC RAM")
    looping = bool(headers[-1] & 2)
    if looping and (not start <= loop < position or (loop - start) % 9):
        raise ValueError("BRR loop address is outside the sample or not frame-aligned")
    end_exclusive = position
    if start < 0x100 and end_exclusive > 0xF0:
        raise ValueError("BRR sample overlaps the skipped SPC I/O page")
    return {"start_address": start, "loop_address": loop,
            "end_address_exclusive": end_exclusive, "frame_count": len(headers),
            "looping": looping, "end_header": headers[-1]}


def _voice_metadata(aram: bytes, dsp: bytes, voice: int) -> dict:
    base = voice * 0x10
    srcn = dsp[base + 4]
    directory_base = dsp[0x5D] << 8
    directory_address = directory_base + srcn * 4
    if directory_address + 4 > ARAM_SIZE:
        raise ValueError("DSP sample directory entry lies outside SPC RAM")
    if directory_address < 0x100 and directory_address + 4 > 0xF0:
        raise ValueError("selected sample directory entry overlaps skipped SPC I/O")
    start, loop = (int.from_bytes(aram[directory_address + n:directory_address + n + 2], "little")
                   for n in (0, 2))
    return {"voice": voice, "srcn": srcn, "directory_base": directory_base,
            "directory_address": directory_address, "adsr1": dsp[base + 5],
            "adsr2": dsp[base + 6], "gain": dsp[base + 7],
            "original_pitch_register": dsp[base + 2] | ((dsp[base + 3] & 0x3F) << 8),
            "voice_register_bytes_hex": dsp[base:base + 8].hex(),
            **_parse_brr(aram, start, loop)}


def _choose_halt_pc(aram: bytes, dsp: bytes, selected: dict) -> int:
    directory_base = dsp[0x5D] << 8
    protected = [(selected["start_address"], selected["end_address_exclusive"])]
    for channel in range(8):
        srcn = dsp[channel * 0x10 + 4]
        entry = directory_base + srcn * 4
        start = int.from_bytes(aram[entry:entry + 2], "little")
        loop = int.from_bytes(aram[entry + 2:entry + 4], "little")
        if start == 0:
            continue
        try:
            sample = _parse_brr(aram, start, loop)
        except ValueError:
            continue
        protected.append((sample["start_address"], sample["end_address_exclusive"]))
    for pc in range(0x0200, 0xFFF0):
        if 0xF0 <= (pc & 0xFFFF) <= 0xFF:
            continue
        if directory_base <= pc < directory_base + 0x400:
            continue
        if not any(start <= pc < end for start, end in protected):
            return pc
    raise ValueError("no safe SPC RAM byte is available for the STOP instruction")


def _extract_ipl(source: Path, offset: int) -> tuple[bytes, str]:
    if type(offset) is not int or offset < 0:
        raise ValueError("ipl_source_offset must be a nonnegative byte offset")
    with source.open("rb") as stream:
        stream.seek(offset)
        data = stream.read(64)
    if len(data) != 64 or hashlib.sha1(data).hexdigest() != IPL_SHA1 or zlib.crc32(data) != IPL_CRC32:
        raise ValueError("IPL source block does not match MAME's required S-SMP ROM hash")
    return data, _sha256(source)


def _make_test_lorom(path: Path) -> str:
    """Build a private 32 KiB LoROM that stops the main 5A22 CPU at reset."""
    rom = bytearray(b"\xff" * 0x8000)
    rom[0:3] = b"\xdb\x80\xfe"
    h = 0x7FC0
    rom[h:h + 21] = b"GME NATIVE DSP TEST".ljust(21, b" ")
    rom[h + 0x15:h + 0x1C] = bytes((0x20, 0, 5, 0, 1, 0x33, 0))
    rom[0x7FFC:0x7FFE] = (0x8000).to_bytes(2, "little")
    rom[h + 0x1C:h + 0x20] = b"\0\0\0\0"
    checksum = (sum(rom) + 0x1FE) & 0xFFFF
    rom[h + 0x1C:h + 0x1E] = (checksum ^ 0xFFFF).to_bytes(2, "little")
    rom[h + 0x1E:h + 0x20] = checksum.to_bytes(2, "little")
    path.write_bytes(rom)
    return hashlib.sha256(rom).hexdigest()


def _validate_receipt(receipt: object, *, request: dict, aram: bytes, dsp: bytes,
                      voice_info: dict, hold_frames: int, tail_frames: int) -> dict:
    if not isinstance(receipt, dict) or receipt.get("schema_version") != RECEIPT_VERSION:
        raise RuntimeError("MAME did not write a valid SNES probe receipt")
    if receipt.get("status") != "ok":
        raise RuntimeError(f"SNES MAME probe failed: {receipt.get('failure', 'unknown Lua error')}")
    expected = {
        "source_aram_sha256": hashlib.sha256(aram).hexdigest(),
        "source_dsp_sha256": hashlib.sha256(dsp).hexdigest(),
        "voice": voice_info["voice"], "srcn": voice_info["srcn"],
        "pitch_register": request["pitch_register"], "adsr1": voice_info["adsr1"],
        "adsr2": voice_info["adsr2"], "gain": voice_info["gain"],
        "sample_start_address": voice_info["start_address"],
        "sample_loop_address": voice_info["loop_address"],
        "sample_end_address_exclusive": voice_info["end_address_exclusive"],
        "brr_frame_count": voice_info["frame_count"], "sample_looping": voice_info["looping"],
        "aram_source_bytes": ARAM_SIZE, "aram_loaded_bytes": ARAM_SIZE - 16,
        "aram_readback_verified_bytes": ARAM_SIZE - 16, "aram_skipped_io_bytes": 16,
        "aram_modified_bytes": 1, "stop_opcode": 255,
        "kon_value": 1 << voice_info["voice"], "koff_value": 1 << voice_info["voice"],
        "flush_frames": 1, "hold_frames": hold_frames, "tail_frames": tail_frames,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise RuntimeError(f"SNES receipt {key} does not match the supplied SPC snapshot")
    halt_pc = receipt.get("halt_pc")
    if type(halt_pc) is not int or halt_pc != request["halt_pc"]:
        raise RuntimeError("SNES receipt has invalid SPC stop PC")
    if (voice_info["start_address"] <= halt_pc < voice_info["end_address_exclusive"]
            or voice_info["directory_base"] <= halt_pc < voice_info["directory_base"] + 0x400
            or 0xF0 <= halt_pc < 0x100):
        raise RuntimeError("SPC stop PC overlaps sample data, directory, or skipped I/O")
    if receipt.get("aram_loaded_readback_ok") is not True:
        raise RuntimeError("SNES ARAM readback verification failed")
    on = _finite_seconds(receipt.get("note_on_time_seconds"), "note_on_time_seconds")
    off = _finite_seconds(receipt.get("note_off_time_seconds"), "note_off_time_seconds")
    end = _finite_seconds(receipt.get("capture_end_time_seconds"), "capture_end_time_seconds")
    period = _finite_seconds(receipt.get("frame_period_seconds"), "frame_period_seconds")
    if not on < off <= end or not 0 < period < 1:
        raise RuntimeError("SNES receipt has inconsistent timing")
    if (not math.isclose(off - on, hold_frames * period, abs_tol=1e-6)
            or not math.isclose(end - off, tail_frames * period, abs_tol=1e-6)):
        raise RuntimeError("SNES receipt timing disagrees with requested frame counts")
    flush_end = _finite_seconds(receipt.get("capture_flush_end_time_seconds"), "capture_flush_end_time_seconds")
    if not math.isclose(flush_end - end, period, abs_tol=1e-6):
        raise RuntimeError("SNES receipt did not flush exactly one frame after the requested tail")
    regs = receipt.get("dsp_registers")
    want = {"srcn": voice_info["srcn"], "pitch_low": request["pitch_register"] & 0xFF,
            "pitch_high": (request["pitch_register"] >> 8) & 0x3F,
            "adsr1": voice_info["adsr1"], "adsr2": voice_info["adsr2"], "gain": voice_info["gain"],
            "voice_volume_left": 127, "voice_volume_right": 127,
            "master_volume_left": 127, "master_volume_right": 127,
            "dir_register": dsp[0x5D], "echo_enable": 0, "noise_enable": 0, "pitch_mod_enable": 0,
            "echo_volume_left": 0, "echo_volume_right": 0, "flg": 0x20}
    if regs != want:
        raise RuntimeError("SNES receipt DSP register readback does not match the programmed patch")
    return {"note_on_time_seconds": on, "note_off_time_seconds": off,
            "capture_end_time_seconds": end, "capture_flush_end_time_seconds": flush_end,
            "frame_period_seconds": period, "halt_pc": halt_pc,
            "flush_frames": 1, "startup_frames": receipt.get("startup_frames"),
            "dsp_registers": regs}


def render_snes_spc_probe(
    spc_path: str | Path, *, mame_path: str | Path = "mame",
    output_dir: str | Path, source_record_id: str, voice: int = 0,
    pitch_register: int = 0x1000, hold_frames: int = 30, tail_frames: int = 30,
    sample_rate: int = 44100, ipl_source_path: str | Path,
    ipl_source_offset: int = 0x89878,
) -> dict:
    """Render one SPC snapshot voice with native S-DSP processing.

    Pitch is a raw 14-bit S-DSP register; no MIDI conversion is attempted.
    """
    if not isinstance(source_record_id, str) or not source_record_id.strip():
        raise ValueError("source_record_id must be a nonempty string")
    if type(voice) is not int or not 0 <= voice <= 7:
        raise ValueError("voice must be an integer from 0 through 7")
    if type(pitch_register) is not int or not 0 <= pitch_register <= 0x3FFF:
        raise ValueError("pitch_register must be a raw 14-bit S-DSP value")
    if type(hold_frames) is not int or not 1 <= hold_frames <= 3600:
        raise ValueError("hold_frames must be an integer from 1 through 3600")
    if type(tail_frames) is not int or not 0 <= tail_frames <= 3600:
        raise ValueError("tail_frames must be an integer from 0 through 3600")
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise ValueError("sample_rate must be an integer from 8000 through 192000")

    mame = _resolve_mame(mame_path)
    spc = Path(spc_path).expanduser().resolve(strict=True)
    ipl_source = Path(ipl_source_path).expanduser().resolve(strict=True)
    _, aram, dsp, source_pc = _read_spc(spc)
    voice_info = _voice_metadata(aram, dsp, voice)
    ipl, ipl_file_sha256 = _extract_ipl(ipl_source, ipl_source_offset)
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", source_record_id.strip()).strip("-.") or "source"
    stem = f"snes-spc-{safe_id}-v{voice}-p{pitch_register:04x}"
    wav_path = output / f"{stem}.wav"
    receipt_path = output / f"{stem}.receipt.json"
    manifest_path = output / f"{stem}.manifest.json"
    for path in (wav_path, receipt_path, manifest_path):
        if path.exists():
            raise FileExistsError(f"native render output already exists: {path}")

    halt_pc = _choose_halt_pc(aram, dsp, voice_info)
    request = {"pitch_register": pitch_register, "halt_pc": halt_pc}
    with tempfile.TemporaryDirectory(prefix=".snes-native-") as scratch_name:
        scratch = Path(scratch_name)
        rom_root = scratch / "roms"
        snes_roms = rom_root / "snes"
        snes_roms.mkdir(parents=True)
        (snes_roms / "spc700.rom").write_bytes(ipl)
        aram_file = scratch / "aram.bin"
        dsp_file = scratch / "dsp.bin"
        aram_file.write_bytes(aram)
        dsp_file.write_bytes(dsp)
        cart = scratch / "gme_native_test.sfc"
        cart_sha256 = _make_test_lorom(cart)
        staged_receipt = scratch / "receipt.json"
        capture = scratch / "capture.wav"
        env = os.environ.copy()
        env.update({
            "GME_SNES_RECEIPT": str(staged_receipt),
            "GME_SNES_ARAM_PATH": str(aram_file),
            "GME_SNES_DSP_PATH": str(dsp_file),
            "GME_SNES_ARAM_SHA256": hashlib.sha256(aram).hexdigest(),
            "GME_SNES_DSP_SHA256": hashlib.sha256(dsp).hexdigest(),
            "GME_SNES_VOICE": str(voice), "GME_SNES_PITCH": str(pitch_register),
            "GME_SNES_HOLD_FRAMES": str(hold_frames), "GME_SNES_TAIL_FRAMES": str(tail_frames),
            "GME_SNES_HALT_PC": str(halt_pc),
        })
        isolated = {name: scratch / name for name in
                    ("home", "cfg", "nvram", "input", "state", "snapshots", "diff", "comments")}
        for directory in isolated.values():
            directory.mkdir()
        try:
            version_result = subprocess.run([str(mame), "-version", "-noreadconfig", "-nowriteconfig"],
                cwd=scratch, shell=False, capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"could not query MAME version: {exc}") from exc
        if version_result.returncode:
            raise RuntimeError(f"MAME version query failed: stdout={version_result.stdout!r}; stderr={version_result.stderr!r}")
        version_lines = (version_result.stdout or version_result.stderr).strip().splitlines()
        if not version_lines:
            raise RuntimeError("MAME version query returned no version text")
        mame_version = version_lines[0].strip()
        argv = [
            str(mame), "snes", "-noreadconfig", "-nowriteconfig",
            "-rompath", str(rom_root), "-homepath", str(isolated["home"]),
            "-cfg_directory", str(isolated["cfg"]), "-nvram_directory", str(isolated["nvram"]),
            "-input_directory", str(isolated["input"]), "-state_directory", str(isolated["state"]),
            "-snapshot_directory", str(isolated["snapshots"]), "-diff_directory", str(isolated["diff"]),
            "-comment_directory", str(isolated["comments"]), "-cart", str(cart),
            "-autoboot_script", str(_SCRIPT), "-autoboot_delay", "0", "-video", "none",
            "-samplerate", str(sample_rate), "-wavwrite", str(capture),
        ]
        timeout_seconds = max(60, math.ceil((hold_frames + tail_frames + 300) / 50) + 15)
        try:
            result = subprocess.run(argv, cwd=scratch, env=env, shell=False,
                                    capture_output=True, text=True, timeout=timeout_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"MAME SNES probe timed out; stdout={exc.stdout!r}; stderr={exc.stderr!r}") from exc
        except OSError as exc:
            raise RuntimeError(f"MAME SNES invocation failed: {exc}") from exc
        if result.returncode:
            raise RuntimeError(f"MAME exited {result.returncode}; stdout={result.stdout!r}; stderr={result.stderr!r}")
        if not staged_receipt.is_file():
            raise RuntimeError(f"MAME exited without a probe receipt; stdout={result.stdout!r}; stderr={result.stderr!r}")
        try:
            raw_receipt = json.loads(staged_receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"MAME wrote an invalid SNES receipt; stdout={result.stdout!r}; stderr={result.stderr!r}") from exc
        try:
            validated = _validate_receipt(raw_receipt, request=request, aram=aram, dsp=dsp,
                voice_info=voice_info, hold_frames=hold_frames, tail_frames=tail_frames)
            wav_info, wav_alignment = _trim_capture(capture, scratch / wav_path.name,
                sample_rate=sample_rate,
                start_seconds=validated["note_on_time_seconds"],
                note_off_seconds=validated["note_off_time_seconds"],
                end_seconds=validated["capture_end_time_seconds"],
                frame_period_seconds=validated["frame_period_seconds"])
        except RuntimeError as exc:
            raise RuntimeError(f"{exc}; MAME stdout={result.stdout!r}; stderr={result.stderr!r}") from exc
        raw_receipt["wav_alignment"] = wav_alignment
        alignment_fields = (
            "requested_note_on_seconds", "actual_trim_start_seconds", "actual_trim_end_seconds",
            "alignment_uncertainty_seconds", "lead_in_frames", "tail_shortfall_seconds", "clock_origin_verified")
        manifest = {
            "schema_version": MANIFEST_VERSION, "engine": "MAME SNES S-DSP",
            "source_record_id": source_record_id,
            "source": {
                "spc_path": str(spc), "spc_sha256": _sha256(spc),
                "aram_sha256": hashlib.sha256(aram).hexdigest(),
                "dsp_registers_sha256": hashlib.sha256(dsp).hexdigest(),
                "mame_executable": str(mame), "mame_executable_sha256": _sha256(mame),
                "mame_version": mame_version,
                "probe_script": str(_SCRIPT), "probe_script_sha256": _sha256(_SCRIPT),
                "ipl_source_path": str(ipl_source), "ipl_source_sha256": ipl_file_sha256,
                "ipl_source_offset": ipl_source_offset, "ipl_block_sha1": IPL_SHA1,
                "test_cartridge_sha256": cart_sha256,
            },
            "instrument": {
                "voice": voice_info["voice"], "srcn": voice_info["srcn"],
                "sample_directory_base": voice_info["directory_base"],
                "dir_register_raw": dsp[0x5D],
                "sample_directory_address": voice_info["directory_address"],
                "sample_start_address": voice_info["start_address"],
                "sample_loop_address": voice_info["loop_address"],
                "sample_end_address_exclusive": voice_info["end_address_exclusive"],
                "brr_frame_count": voice_info["frame_count"], "brr_looping": voice_info["looping"],
                "brr_end_header": voice_info["end_header"], "adsr1": voice_info["adsr1"],
                "adsr2": voice_info["adsr2"], "gain": voice_info["gain"],
                "original_pitch_register": voice_info["original_pitch_register"],
                "voice_register_bytes_hex": voice_info["voice_register_bytes_hex"],
            },
            "key": {"pitch_register_raw_s_dsp": pitch_register, "midi_note": None,
                    "velocity": None, "velocity_note": "The S-DSP probe fixes voice volume; velocity is unsupported."},
            "conditions": {
                "requested_hold_frames": hold_frames, "requested_tail_frames": tail_frames,
                "startup_frames": validated["startup_frames"],
                "note_on_time_seconds": validated["note_on_time_seconds"],
                "note_off_time_seconds": validated["note_off_time_seconds"],
                "capture_end_time_seconds": validated["capture_end_time_seconds"],
                "capture_flush_end_time_seconds": validated["capture_flush_end_time_seconds"],
                "flush_frames": 1,
                "frame_period_seconds": validated["frame_period_seconds"], "sample_rate_requested": sample_rate,
                "source_spc_pc": source_pc, "spc_stop_pc": validated["halt_pc"],
                "aram_load": {"source_bytes": ARAM_SIZE, "loaded_bytes": ARAM_SIZE - 16,
                              "skipped_io_bytes": 16, "readback_verified_bytes": ARAM_SIZE - 16,
                              "modified_bytes": 1, "stop_opcode": 255},
                "dsp_registers": validated["dsp_registers"],
                "wav_alignment": {field: wav_alignment[field] for field in alignment_fields},
            },
            "run_arguments": argv, "receipt_file": receipt_path.name,
            "wav": {"file": wav_path.name, **wav_info, "sha256": _sha256(scratch / wav_path.name)},
        }
        receipt_final = scratch / receipt_path.name
        receipt_final.write_text(json.dumps(raw_receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest_tmp = scratch / manifest_path.name
        manifest_tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        publish = output / f".snes-publish-{uuid.uuid4().hex}"
        publish.mkdir()
        try:
            files = ((scratch / wav_path.name, publish / wav_path.name, wav_path),
                     (receipt_final, publish / receipt_path.name, receipt_path),
                     (manifest_tmp, publish / manifest_path.name, manifest_path))
            for source, staged, _ in files:
                shutil.copyfile(source, staged)
            if any(final.exists() for _, _, final in files):
                raise FileExistsError("native render output appeared during publication")
            for _, staged, final in files:
                staged.replace(final)
        finally:
            if publish.resolve().parent != output.resolve() or not publish.name.startswith(".snes-publish-"):
                raise RuntimeError("unsafe SNES native render publication staging path")
            shutil.rmtree(publish)
    return manifest
