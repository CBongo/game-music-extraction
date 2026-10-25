"""Mocked subprocess tests for the native SNES S-DSP renderer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave
import zlib

from instrument_analysis import snes_rendering as snes


def _fixture(root: Path):
    exe = root / "mame.exe"
    exe.write_bytes(b"fake MAME")
    ipl = root / "emulator.exe"
    ipl.write_bytes(b"not an IPL source by itself")
    spc = root / "Airship.spc"
    raw = bytearray(0x10200)
    raw[:33] = b"SNES-SPC700 Sound File Data v0.30".ljust(33, b"\0")
    raw[0x25:0x27] = (0x0800).to_bytes(2, "little")
    aram = memoryview(raw)[0x100:0x10100]
    dsp = memoryview(raw)[0x10100:0x10180]
    dsp[2:8] = bytes((0x00, 0x10, 2, 0xAF, 0x1F, 0x80))
    dsp[0x5D] = 0x50
    entry = 0x5000 + 2 * 4
    aram[entry:entry + 4] = (0x6000).to_bytes(2, "little") + (0x6009).to_bytes(2, "little")
    aram[0x6000] = 0x00
    aram[0x6009] = 0x03
    spc.write_bytes(raw)
    return exe, ipl, spc, bytes(aram), bytes(dsp)


def _good_receipt(env, aram: bytes, dsp: bytes):
    info = snes._voice_metadata(aram, dsp, int(env["GME_SNES_VOICE"]))
    pitch = int(env["GME_SNES_PITCH"])
    voice = info["voice"]
    mask = 1 << voice
    return {
        "schema_version": snes.RECEIPT_VERSION, "status": "ok",
        "source_aram_sha256": hashlib.sha256(aram).hexdigest(),
        "source_dsp_sha256": hashlib.sha256(dsp).hexdigest(),
        "aram_source_bytes": 0x10000, "aram_loaded_bytes": 0xFFF0,
        "aram_readback_verified_bytes": 0xFFF0, "aram_skipped_io_bytes": 16,
        "aram_modified_bytes": 1, "stop_opcode": 255,
        "aram_loaded_readback_ok": True,
        "voice": voice, "srcn": info["srcn"], "pitch_register": pitch,
        "adsr1": info["adsr1"], "adsr2": info["adsr2"], "gain": info["gain"],
        "sample_start_address": info["start_address"], "sample_loop_address": info["loop_address"],
        "sample_end_address_exclusive": info["end_address_exclusive"],
        "brr_frame_count": info["frame_count"], "sample_looping": info["looping"],
        "halt_pc": int(env["GME_SNES_HALT_PC"]),
        "hold_frames": int(env["GME_SNES_HOLD_FRAMES"]),
        "tail_frames": int(env["GME_SNES_TAIL_FRAMES"]),
        "startup_frames": 1, "kon_value": mask, "koff_value": mask,
        "flush_frames": 1, "capture_flush_end_time_seconds": 0.035,
        "frame_period_seconds": 0.005, "note_on_time_seconds": 0.01,
        "note_off_time_seconds": 0.02, "capture_end_time_seconds": 0.03,
        "dsp_registers": {
            "srcn": info["srcn"], "pitch_low": pitch & 0xFF, "pitch_high": pitch >> 8,
            "adsr1": info["adsr1"], "adsr2": info["adsr2"], "gain": info["gain"],
            "voice_volume_left": 127, "voice_volume_right": 127,
            "master_volume_left": 127, "master_volume_right": 127,
            "dir_register": 0x50, "echo_enable": 0, "noise_enable": 0, "pitch_mod_enable": 0,
            "echo_volume_left": 0, "echo_volume_right": 0, "flg": 0x20,
        },
    }


def _fake_run(argv, kwargs_env, aram, dsp, mutate_receipt=None):
    receipt = _good_receipt(kwargs_env, aram, dsp)
    if mutate_receipt:
        mutate_receipt(receipt)
    Path(kwargs_env["GME_SNES_RECEIPT"]).write_text(json.dumps(receipt), encoding="utf-8")
    capture = Path(argv[argv.index("-wavwrite") + 1])
    with wave.open(str(capture), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(bytes(300 * 4) + b"\x01\x00\xfe\xff" * (1543 - 300))
    return subprocess.CompletedProcess(argv, 0, "mock stdout", "mock stderr")


class SnesRenderingTests(unittest.TestCase):
    def _render(self, root, exe, ipl, spc, aram, dsp, *, mutate_receipt=None, out=None):
        def fake(argv, **kwargs):
            if "-version" in argv:
                return subprocess.CompletedProcess(argv, 0, "MAME 0.285\n", "")
            return _fake_run(argv, kwargs["env"], aram, dsp, mutate_receipt)
        with patch.object(snes.subprocess, "run", side_effect=fake):
            with patch.object(snes, "_extract_ipl", return_value=(b"I" * 64, hashlib.sha256(ipl.read_bytes()).hexdigest())):
                return snes.render_snes_spc_probe(
                    spc, mame_path=exe, output_dir=out or root / "out",
                    source_record_id="ff2-airship", voice=0, pitch_register=0x1000,
                    hold_frames=2, tail_frames=2, ipl_source_path=ipl,
                )

    def test_native_s_dsp_capture_manifest_receipt_and_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exe, ipl, spc, aram, dsp = _fixture(root)
            manifest = self._render(root, exe, ipl, spc, aram, dsp)
            self.assertEqual(manifest["schema_version"], snes.MANIFEST_VERSION)
            self.assertEqual(manifest["engine"], "MAME SNES S-DSP")
            self.assertEqual(manifest["instrument"]["srcn"], 2)
            self.assertEqual(manifest["instrument"]["dir_register_raw"], 0x50)
            self.assertEqual(manifest["source"]["mame_version"], "MAME 0.285")
            self.assertEqual(manifest["instrument"]["adsr1"], 0xAF)
            self.assertEqual(manifest["instrument"]["adsr2"], 0x1F)
            self.assertEqual(manifest["instrument"]["gain"], 0x80)
            self.assertEqual(manifest["instrument"]["sample_start_address"], 0x6000)
            self.assertEqual(manifest["instrument"]["sample_loop_address"], 0x6009)
            self.assertEqual(manifest["instrument"]["brr_frame_count"], 2)
            self.assertTrue(manifest["instrument"]["brr_looping"])
            self.assertEqual(manifest["key"]["pitch_register_raw_s_dsp"], 0x1000)
            self.assertIsNone(manifest["key"]["midi_note"])
            out = root / "out"
            wav = out / manifest["wav"]["file"]
            self.assertEqual(hashlib.sha256(wav.read_bytes()).hexdigest(), manifest["wav"]["sha256"])
            receipt = json.loads((out / manifest["receipt_file"]).read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "ok")
            self.assertEqual(receipt["aram_readback_verified_bytes"], 0xFFF0)
            self.assertEqual(manifest["conditions"]["wav_alignment"]["lead_in_frames"], 221)
            try:
                import jsonschema
            except ImportError:
                pass
            else:
                schema_path = Path(__file__).resolve().parents[2] / "schemas" / "instrument-native-chip-render-v1.schema.json"
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                jsonschema.Draft202012Validator.check_schema(schema)
                jsonschema.Draft202012Validator(schema).validate(manifest)
            self.assertFalse(manifest["conditions"]["wav_alignment"]["clock_origin_verified"])
            self.assertEqual(manifest["conditions"]["flush_frames"], 1)
            self.assertEqual(manifest["conditions"]["wav_alignment"]["tail_shortfall_seconds"], 0.0)
            argv = manifest["run_arguments"]
            self.assertIn("-cart", argv)
            self.assertIn("-video", argv)
            self.assertIn("-wavwrite", argv)
            self.assertNotIn("-sound", argv)
            self.assertTrue(Path(argv[argv.index("-cart") + 1]).name.endswith(".sfc"))

    def test_bad_signature_brr_bounds_loop_and_directory_io_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exe, ipl, spc, aram, dsp = _fixture(root)
            original = spc.read_bytes()
            bad = bytearray(original)
            bad[:32] = b"bad".ljust(32, b"\0")
            spc.write_bytes(bad)
            with self.assertRaisesRegex(ValueError, "standard SPC"):
                snes.render_snes_spc_probe(spc, mame_path=exe, output_dir=root / "out",
                    source_record_id="bad", ipl_source_path=ipl)
            spc.write_bytes(original)
            _, parsed_aram, parsed_dsp, _ = snes._read_spc(spc)
            broken = bytearray(parsed_aram)
            broken[0x6009] = 0
            with self.assertRaisesRegex(ValueError, "no end-marked"):
                snes._parse_brr(bytes(broken), 0x6000, 0x6009)
            with self.assertRaisesRegex(ValueError, "frame-aligned"):
                snes._parse_brr(parsed_aram, 0x6000, 0x600A)
            io_dsp = bytearray(parsed_dsp)
            io_dsp[0x5D] = 0
            io_dsp[4] = 0x3C  # SRCN directory entry spans $F0-$F3
            with self.assertRaisesRegex(ValueError, "overlaps skipped"):
                snes._voice_metadata(parsed_aram, bytes(io_dsp), 0)

    def test_ipl_hash_is_enforced_and_bad_receipt_never_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exe, ipl, spc, aram, dsp = _fixture(root)
            with self.assertRaisesRegex(ValueError, "required S-SMP ROM hash"):
                snes._extract_ipl(ipl, 0)
            with self.assertRaisesRegex(RuntimeError, "source_aram_sha256"):
                self._render(root, exe, ipl, spc, aram, dsp,
                             mutate_receipt=lambda r: r.update(source_aram_sha256="0" * 64))
            self.assertFalse((root / "out" / "snes-spc-ff2-airship-v0-p1000.wav").exists())

    def test_request_bounds_and_output_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exe, ipl, spc, aram, dsp = _fixture(root)
            with self.assertRaisesRegex(ValueError, "14-bit"):
                snes.render_snes_spc_probe(spc, mame_path=exe, output_dir=root / "out",
                    source_record_id="bad", pitch_register=0x4000, ipl_source_path=ipl)
            self._render(root, exe, ipl, spc, aram, dsp)
            with self.assertRaises(FileExistsError):
                self._render(root, exe, ipl, spc, aram, dsp)
