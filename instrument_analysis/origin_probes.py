"""Construct explicitly isolated hardware probes from bound ROM sample bytes."""
from __future__ import annotations

import json
from pathlib import Path

from .ff6_samples import extract_ff6_sample
from .rendering import _sha256, _staging_directory


def build_rom_sample_probe(metadata_path, *, output_dir, pitch_register=4096):
    """Build an SPC input, without rendering or claiming original performance.

    This builder independently repeats the version-pinned extraction and checks
    the exact ROM, descriptor, BRR and ADSR byte bindings in supplied metadata.
    """
    if type(pitch_register) is not int or not 1 <= pitch_register <= 0x3FFF:
        raise ValueError("pitch_register must be a nonzero 14-bit S-DSP integer")
    metadata_path = Path(metadata_path).expanduser().resolve(strict=True)
    metadata_hash = _sha256(metadata_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if (metadata.get("schema_version") != "instrument-rom-sample/v1"
            or metadata.get("game") != "Final Fantasy VI"
            or metadata.get("rom", {}).get("version_approved") is not True):
        raise ValueError("only version-approved FF6 ROM sample metadata is supported")
    rom_ref = metadata["rom"]
    rom_path = Path(rom_ref["path"])
    if not rom_path.is_absolute():
        rom_path = metadata_path.parent / rom_path
    rom_path = rom_path.resolve(strict=True)
    rom_hash = _sha256(rom_path)
    if rom_hash != rom_ref["sha256"]:
        raise ValueError("ROM hash differs from approved metadata")
    actual, brr = extract_ff6_sample(rom_path, metadata.get("instrument", {}).get("global_sample_id"))
    if (actual["rom"]["payload_sha256"] != rom_ref.get("payload_sha256")
            or actual["rom"]["size"] != rom_ref.get("size")
            or actual["rom"]["header_bytes"] != rom_ref.get("header_bytes")
            or actual["rom"]["sha256"] != rom_hash
            or actual["record_id"] != metadata.get("record_id")):
        raise ValueError("ROM/sample identity differs from metadata")
    for section in ("instrument", "brr"):
        if any(metadata.get(section, {}).get(key) != value for key, value in actual[section].items()):
            raise ValueError(f"{section} descriptor or bytes differ from ROM metadata")
    adsr = bytes.fromhex(actual["instrument"]["adsr_entry"]["bytes_hex"])
    start, directory, srcn, voice = 0x3000, 0x1E00, 0x40, 0
    loop = start + actual["brr"]["loop_relative_bytes"]
    if start + len(brr) > 0x10000 or loop >= 0x10000:
        raise ValueError("relocated BRR does not fit SPC RAM")
    spc = bytearray(0x10200)
    spc[:33] = b"SNES-SPC700 Sound File Data v0.30".ljust(33, b"\x00")
    spc[0x21:0x25] = bytes((0x1A, 0x1A, 0x1B, 30))
    spc[0x25:0x27] = (0x0200).to_bytes(2, "little")
    aram = memoryview(spc)[0x100:0x10100]
    dsp = memoryview(spc)[0x10100:0x10180]
    aram[start:start + len(brr)] = brr
    address = directory + srcn * 4
    aram[address:address + 4] = start.to_bytes(2, "little") + loop.to_bytes(2, "little")
    dsp[:8] = bytes((127, 127, pitch_register & 255, pitch_register >> 8, srcn, adsr[0], adsr[1], 0))
    dsp[0x0C] = dsp[0x1C] = 127
    dsp[0x5D], dsp[0x6C] = directory >> 8, 0x20
    manifest = {"schema_version": "instrument-rom-sample-probe/v1",
        "source_record_id": metadata["record_id"],
        "generator": {"name": "instrument_analysis.origin_probes", "label": "constructed_isolated_rom_sample_probe"},
        "scope": "constructed_isolated_rom_sample_probe_not_original_game_performance",
        "input": {"sample_metadata": {"path": str(metadata_path), "sha256": metadata_hash},
                  "rom": {"path": str(rom_path), "sha256": rom_hash,
                          "payload_sha256": rom_ref["payload_sha256"]}},
        "source_sample": {"brr_sha256": actual["brr"]["sha256"],
            "rom_file_offset": actual["brr"]["file_offset"], "length_bytes": len(brr),
            "descriptor_sha256": actual["instrument"]["descriptor_sha256"],
            "global_sample_id": actual["instrument"]["global_sample_id"]},
        "conditions": {"aram_initialization": "zero-filled; only BRR payload and selected directory entry populated",
            "spc_file_size": len(spc), "spc_header_bytes": 256, "aram_bytes": 65536,
            "dsp_bytes": 128, "initial_pc": 512, "voice": voice, "srcn": srcn,
            "directory_base": directory, "directory_entry_address": address,
            "sample_start_address": start, "loop_address": loop,
            "loop_relative_bytes": actual["brr"]["loop_relative_bytes"],
            "sample_looping": actual["brr"]["looping"], "pitch_register": pitch_register,
            "pitch_basis": "explicit raw hardware register; no sounding-note or source sampled-note inference",
            "adsr1": adsr[0], "adsr2": adsr[1], "gain": 0,
            "adsr_origin": actual["instrument"]["adsr_entry"],
            "voice_volume_left": 127, "voice_volume_right": 127,
            "master_volume_left": 127, "master_volume_right": 127,
            "echo_enable": 0, "noise_enable": 0, "pitch_mod_enable": 0,
            "echo_volume_left": 0, "echo_volume_right": 0, "flg": 32,
            "clock_test": "SPC is an input layout only; native emulator capture and clock/readback validation are separate"}}
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise FileExistsError("ROM sample probe output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with _staging_directory(output.parent) as workspace:
        stage = workspace / "publish"
        stage.mkdir()
        spc_path = stage / "sample.spc"
        spc_path.write_bytes(spc)
        if _sha256(metadata_path) != metadata_hash or _sha256(rom_path) != rom_hash:
            raise RuntimeError("ROM sample probe input changed during construction")
        manifest["spc"] = {"file": "sample.spc", "sha256": _sha256(spc_path)}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        stage.rename(output)
    return manifest
