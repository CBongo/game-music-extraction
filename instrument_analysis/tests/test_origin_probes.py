"""Constructed SPC probes use bound ROM bytes, never surrogate presets."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from instrument_analysis.ff6_samples import parse_ff6_sample
from instrument_analysis.origin_probes import build_rom_sample_probe
from instrument_analysis.snes_rendering import _read_spc, _voice_metadata
from instrument_analysis.tests.test_ff6_samples import synthetic


class OriginProbeTests(unittest.TestCase):
    def fixture(self, root):
        rom = root / "synthetic.rom"
        data = synthetic()
        # Explicit synthetic envelope bytes stand in for a parser-approved table.
        data[0x53E18 + 12:0x53E18 + 14] = b"\xff\xe0"
        rom.write_bytes(data)
        metadata, brr = parse_ff6_sample(bytes(data), 7)
        metadata["rom"].update(path=str(rom), version_approved=True)
        path = root / "metadata.json"
        path.write_text(json.dumps(metadata))
        self.approval_patcher = patch("instrument_analysis.origin_probes.extract_ff6_sample", return_value=(deepcopy(metadata), brr))
        self.approval_mock = self.approval_patcher.start()
        self.addCleanup(self.approval_patcher.stop)
        return rom, path, metadata, brr

    def test_exact_brr_adsr_loop_and_isolated_layout(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _, path, metadata, brr = self.fixture(root)
            manifest = build_rom_sample_probe(path, output_dir=root / "out")
            self.approval_mock.assert_called_once_with(root / "synthetic.rom", 7)
            raw, aram, dsp, pc = _read_spc(root / "out/sample.spc")
            info = _voice_metadata(aram, dsp, 0)
            self.assertEqual(len(raw), 0x10200)
            self.assertEqual(pc, 0x200)
            self.assertEqual(info["start_address"], 0x3000)
            self.assertEqual(info["loop_address"], 0x3009)
            self.assertEqual(info["directory_base"], 0x1E00)
            self.assertEqual(info["srcn"], 0x40)
            self.assertEqual((info["adsr1"], info["adsr2"]), (255, 224))
            self.assertEqual(aram[0x3000:0x3000 + len(brr)], brr)
            self.assertEqual(manifest["spc"]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(manifest["source_sample"]["brr_sha256"], metadata["brr"]["sha256"])
            self.assertEqual(dsp[0x2D], 0)
            self.assertEqual(dsp[0x3D], 0)
            self.assertEqual(dsp[0x4D], 0)
            self.assertEqual(dsp[0x0C], 127)
            self.assertEqual(dsp[0x1C], 127)
            self.assertIn("not_original_game_performance", manifest["scope"])
            with self.assertRaises(FileExistsError):
                build_rom_sample_probe(path, output_dir=root / "out")

    def test_rejects_metadata_hash_adsr_loop_and_platform_tampering(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            rom, path, original, _ = self.fixture(root)
            for section, key, value in (("rom", "version_approved", False),
                ("rom", "sha256", "0" * 64), ("brr", "sha256", "0" * 64),
                ("brr", "loop_relative_bytes", 1), ("brr", "length_bytes", 999),
                ("brr", "file_offset", 0)):
                metadata = deepcopy(original)
                metadata[section][key] = value
                path.write_text(json.dumps(metadata))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    build_rom_sample_probe(path, output_dir=root / "rejected")
            metadata = deepcopy(original)
            metadata["instrument"]["adsr_entry"]["bytes_hex"] = "0000"
            path.write_text(json.dumps(metadata))
            with self.assertRaises(ValueError):
                build_rom_sample_probe(path, output_dir=root / "rejected")
            metadata = deepcopy(original)
            metadata["game"] = "Final Fantasy IV"
            path.write_text(json.dumps(metadata))
            with self.assertRaises(ValueError):
                build_rom_sample_probe(path, output_dir=root / "rejected")
            path.write_text(json.dumps(original))
            for pitch in (0, 16384, True):
                with self.assertRaises(ValueError):
                    build_rom_sample_probe(path, output_dir=root / "rejected", pitch_register=pitch)
            rom.write_bytes(b"changed ROM")
            with self.assertRaisesRegex(ValueError, "ROM hash"):
                build_rom_sample_probe(path, output_dir=root / "rejected")
            self.assertFalse((root / "rejected").exists())

    def test_forged_approval_cannot_authorize_unknown_production_rom(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _, path, _, _ = self.fixture(root)
            self.approval_patcher.stop()
            with self.assertRaisesRegex(ValueError, "unsupported FF6 ROM"):
                build_rom_sample_probe(path, output_dir=root / "rejected")
            self.assertFalse((root / "rejected").exists())


if __name__ == "__main__":
    unittest.main()
