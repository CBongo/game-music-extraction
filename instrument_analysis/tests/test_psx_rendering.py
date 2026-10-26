"""Contract tests for native AKAO/SPU capture and source-byte provenance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from instrument_analysis.psx_rendering import (
    MANIFEST_VERSION, _read_bank, render_akao_spu_probe,
)

_HEADER = struct.Struct("<4sHH6I")
_ART = struct.Struct("<IIhHHH")


def _fixture(root: Path, *, declared=32, extra=b"", end_flags=3, loop=16, start=0, unity=60):
    exe = root / "mame.exe"
    exe.write_bytes(b"mock mame")
    roms = root / "roms"
    roms.mkdir(exist_ok=True)
    (roms / "konamigv.zip").write_bytes(b"mock ROM archive")
    first = bytes([0, 4]) + bytes(range(14))
    last = bytes([0, end_flags]) + bytes(range(14, 28))
    payload = first + last + extra
    bank = root / "bank.bin"
    header = _HEADER.pack(b"AKAO", 10, 0, 0, 0, 0x1000, declared, 12, 1)
    bank.write_bytes(header.ljust(0x40, b"\0") + _ART.pack(start, loop, -100, unity, 0x35FF, 0x5FCE) + payload)
    return exe, roms, bank, payload


def _mock_run(argv, **kwargs):
    if argv[1] == "-version":
        return subprocess.CompletedProcess(argv, 0, "0.285 (mame0285)\n", "")
    assert kwargs["shell"] is False
    assert argv[1] == "konamigv"
    assert "-autoboot_script" in argv
    assert "-wavwrite" in argv
    assert argv[argv.index("-sound") + 1] == "none"
    env = kwargs["env"]
    payload = Path(env["GME_PSX_PAYLOAD"]).read_bytes()
    dest = int(env["GME_PSX_DEST"])
    start = int(env["GME_PSX_START"])
    receipt = {
        "schema_version": "akao-spu-native-probe-receipt/v1",
        "status": "ok",
        "payload_length": len(payload),
        "spu_destination": dest,
        "sample_start": start,
        "loop_start": int(env["GME_PSX_LOOP"]),
        "sample_end": int(env["GME_PSX_END"]),
        "adsr1": int(env["GME_PSX_ADSR1"]),
        "adsr2": int(env["GME_PSX_ADSR2"]),
        "pitch_register_raw_spu": int(env["GME_PSX_PITCH"]),
        "hold_frames": int(env["GME_PSX_HOLD"]),
        "tail_frames": int(env["GME_PSX_TAIL"]),
        "frame_period_seconds": 0.005,
        "note_on_time_seconds": 0.01,
        "note_off_time_seconds": 0.02,
        "capture_end_time_seconds": 0.03,
        "cpu_idle_pc": 0xA0010000,
        "words_written": len(payload) // 2,
        "verified_payload_bytes": len(payload),
        "dma_readback_base": 0x20000,
        "first_word": int.from_bytes(payload[:2], "little"),
        "last_word": int.from_bytes(payload[-2:], "little"),
        "voice_start_register_readback": (dest + start) // 8,
        "voice_pitch_register_readback": int(env["GME_PSX_PITCH"]),
        "adsr1_readback": int(env["GME_PSX_ADSR1"]),
        "adsr2_readback": int(env["GME_PSX_ADSR2"]),
        "spu_control_readback": 0xC000,
        "cpu_pc_after_upload": 0xA0010000,
    }
    if env.get("_TAMPER_READBACK"):
        receipt["verified_payload_bytes"] -= 1
    Path(env["GME_PSX_RECEIPT"]).write_text(json.dumps(receipt), encoding="utf-8")
    captured = Path(argv[argv.index("-wavwrite") + 1])
    with wave.open(str(captured), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        pcm = (b"\x10\x00\xf0\xff" if not env.get("_SILENT") else b"\0\0\0\0") * 1323
        wav.writeframes(pcm)
    return subprocess.CompletedProcess(argv, 0, "", "")


def _request(root: Path, exe: Path, roms: Path, bank: Path):
    return dict(
        bank_path=bank, mame_path=exe, rom_path=roms, output_dir=root / "out",
        source_record_id="ff8:test:art12", articulation_id=12,
        format_version="3.2", hold_frames=2, tail_frames=2,
    )


class PsxRenderingTests(unittest.TestCase):
    def test_success_records_bounded_source_bytes_and_hardware_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exe, roms, bank, payload = _fixture(root, declared=48)
            calls = []
            def runner(argv, **kwargs):
                calls.append((list(argv), kwargs))
                return _mock_run(argv, **kwargs)
            with patch("instrument_analysis.psx_rendering.subprocess.run", side_effect=runner):
                manifest = render_akao_spu_probe(**_request(root, exe, roms, bank))
            self.assertEqual(manifest["schema_version"], MANIFEST_VERSION)
            self.assertEqual(manifest["source"]["uploaded_payload_sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(manifest["instrument"]["actual_payload_bytes"], 32)
            self.assertEqual(manifest["instrument"]["physical_payload_bytes"], 32)
            self.assertEqual(manifest["instrument"]["declared_payload_shortfall_bytes"], 16)
            self.assertFalse(manifest["instrument"]["bank_payload_complete"])
            self.assertTrue(manifest["instrument"]["selected_sample_complete"])
            self.assertEqual(manifest["instrument"]["sample_end_block_offset"], 16)
            self.assertEqual(manifest["conditions"]["upload_verification"]["verified_payload_bytes"], 32)
            self.assertIsNone(manifest["key"]["midi_note"])
            self.assertEqual(manifest["key"]["pitch_register_raw_spu"], 0x1000)
            self.assertEqual(manifest["wav"]["sample_rate"], 44100)
            self.assertGreater(manifest["wav"]["frames"], 0)
            receipt = json.loads((root / "out" / manifest["receipt_file"]).read_text())
            self.assertEqual(receipt["verified_payload_bytes"], 32)
            self.assertEqual(len(calls), 2)
            for _, options in calls:
                self.assertIs(options["shell"], False)
            self.assertTrue((root / "out" / manifest["wav"]["file"]).is_file())
            with patch("instrument_analysis.psx_rendering.subprocess.run", side_effect=AssertionError("should not rerun")):
                with self.assertRaises(FileExistsError):
                    render_akao_spu_probe(**_request(root, exe, roms, bank))

    def test_trailing_bytes_are_not_uploaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, bank, payload = _fixture(root, declared=32, extra=bytes(16))
            metadata, uploaded = _read_bank(bank, 12, "3.1")
            self.assertEqual(uploaded, payload[:32])
            self.assertEqual(metadata["physical_payload_bytes"], 48)
            self.assertEqual(metadata["actual_payload_bytes"], 32)
            self.assertFalse(metadata["bank_payload_complete"])
            self.assertIn("not uploaded", metadata["truncation_warning"])

    def test_invalid_bank_version_bounds_and_end_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, bank, _ = _fixture(root)
            with self.assertRaisesRegex(ValueError, "format_version"):
                _read_bank(bank, 12, "2.0")
            with self.assertRaisesRegex(ValueError, "articulation_id"):
                _read_bank(bank, 13, "3.2")
            _, _, bank2, _ = _fixture(root, end_flags=2)
            with self.assertRaisesRegex(ValueError, "no ADPCM end"):
                _read_bank(bank2, 12, "3.2")
            _, _, bank3, _ = _fixture(root, loop=32)
            with self.assertRaisesRegex(ValueError, "loop address"):
                _read_bank(bank3, 12, "3.2")
            _, _, bank4, _ = _fixture(root, unity=256)
            with self.assertRaisesRegex(ValueError, "unity high byte"):
                _read_bank(bank4, 12, "3.2")
            _, _, one_shot, _ = _fixture(root, end_flags=1, loop=0, start=16)
            metadata, _ = _read_bank(one_shot, 12, "3.1")
            self.assertEqual(metadata["sample_end_flags"], 1)

    def test_readback_mismatch_and_silence_do_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exe, roms, bank, _ = _fixture(root)
            for flag, message in (("_TAMPER_READBACK", "verified_payload_bytes"), ("_SILENT", "silent PCM")):
                def runner(argv, **kwargs):
                    if argv[1] != "-version":
                        kwargs["env"][flag] = "1"
                    return _mock_run(argv, **kwargs)
                with patch("instrument_analysis.psx_rendering.subprocess.run", side_effect=runner):
                    with self.assertRaisesRegex(RuntimeError, message):
                        render_akao_spu_probe(**_request(root, exe, roms, bank))
                self.assertEqual(list((root / "out").glob("*.manifest.json")), [])


if __name__ == "__main__":
    unittest.main()


