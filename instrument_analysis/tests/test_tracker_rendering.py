import json
import math
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from instrument_analysis.tracker_rendering import (
    _NOTE_OFF_SECONDS, _estimated_sample_end_seconds, _parse_mod, _probe_module,
    render_tracker_sample_probes,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "instruments" / "generated" / "fixtures"
TRIGGERS = [
    {"trigger_midi_note": 60, "period": 428},
    {"trigger_midi_note": 62, "period": 381},
    {"trigger_midi_note": 64, "period": 339},
]


def fake_run(argv, **kwargs):
    if argv[1] == "--version":
        return subprocess.CompletedProcess(argv, 0, "openmpt123 v0.8.9 test\n", "")
    assert kwargs["shell"] is False
    assert "--batch" in argv and "--output" in argv and "--" in argv
    output = Path(argv[argv.index("--output") + 1])
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        frames = 44100
        data = []
        for i in range(frames):
            sample = int(8000 * math.sin(2 * math.pi * 261.6256 * i / 44100))
            data.extend((sample, sample))
        wav.writeframes(struct.pack("<" + "h" * len(data), *data))
    return subprocess.CompletedProcess(argv, 0, "", "")


class TrackerRenderingTests(unittest.TestCase):
    def setup_input(self, root):
        renderer = root / "openmpt123.exe"
        renderer.write_bytes(b"mock renderer")
        module = FIXTURE_DIR / "single-rich-tone.mod"
        return renderer, module

    def test_probe_module_preserves_source_sample_and_encodes_period_cut_and_tempo(self):
        source = (FIXTURE_DIR / "single-rich-tone.mod").read_bytes()
        meta, sample, _ = _parse_mod(source, 1)
        probe = _probe_module(source, 1, 381, 0)
        self.assertEqual(probe[20:50], source[20:50])
        self.assertEqual(probe[1084 + 1024:], sample)
        self.assertEqual(probe[1080:1084], b"M.K.")
        self.assertEqual(probe[950], 1)
        self.assertEqual(meta["finetune_signed_eighth_semitones"], 0)
        pattern = probe[1084:1084 + 1024]
        note = pattern[:4]
        self.assertEqual(note[0] & 0xF0, 0)
        self.assertEqual((((note[0] & 0x0F) << 8) | note[1]), 381)
        self.assertEqual(pattern[3 * 4 + 3], 130)  # F82 tempo on channel four
        cut = pattern[8 * 16:8 * 16 + 4]
        self.assertEqual(cut[2] & 0x0F, 0xE)
        self.assertEqual(cut[3], 0xC0)
        self.assertAlmostEqual(_NOTE_OFF_SECONDS, 8 * (2.5 * 6 / 130))

    def test_renders_hash_bound_three_pitch_probes_with_audio_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            renderer, module = self.setup_input(root)
            calls = []
            def run(argv, **kwargs):
                calls.append((list(argv), kwargs))
                return fake_run(argv, **kwargs)
            with patch("instrument_analysis.tracker_rendering.subprocess.run", side_effect=run):
                manifest = render_tracker_sample_probes(
                    module, sample_number=1, triggers=TRIGGERS, output_dir=root / "out",
                    source_record_id="neutral-001", openmpt123_path=renderer)
            self.assertEqual(len(calls), 4)  # version + three probes
            self.assertEqual(manifest["schema_version"], "instrument-tracker-sample-probes/v1")
            self.assertEqual([p["trigger_midi_note"] for p in manifest["probes"]], [60, 62, 64])
            self.assertEqual(manifest["source"]["sample_bytes_sha256"], manifest["probes"][0]["render_conditions"]["source_sample_sha256"])
            self.assertNotIn("module_path", manifest["source"])
            self.assertEqual(manifest["source"]["sample_header_hex"], (module.read_bytes()[20:50]).hex())
            for index, (probe, (argv, kwargs)) in enumerate(zip(manifest["probes"], calls[1:])):
                self.assertEqual(kwargs["shell"], False)
                self.assertEqual(probe["note_on_seconds"], 0.0)
                self.assertLessEqual(probe["note_off_seconds"], probe["note_cut_time_seconds"])
                self.assertAlmostEqual(probe["note_cut_time_seconds"], _NOTE_OFF_SECONDS)
                self.assertLess(probe["analysis_end_seconds"], probe["note_off_seconds"])
                self.assertAlmostEqual(probe["note_off_seconds"] - probe["analysis_end_seconds"], 0.04)
                self.assertEqual(probe["end_seconds"], 1.0)
                self.assertEqual(probe["wav"]["frames"], 44100)
                self.assertEqual(probe["wav_sha256"], probe["wav"]["sha256"])
                self.assertEqual(argv[argv.index("--filter") + 1], "8")
                ctl_values = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "--ctl"]
                self.assertIn("render.resampler.emulate_amiga=0", ctl_values)
                self.assertIn("render.resampler.emulate_amiga_type=a500", ctl_values)
                self.assertFalse(probe["render_conditions"]["amiga_resampler_enabled"])
                self.assertEqual(probe["render_conditions"]["amiga_resampler_type_control"], "a500")
                self.assertEqual(probe["render_conditions"]["resampler_profile"], "libopenmpt-filter-8-tap")
                self.assertEqual(argv[argv.index("--dither") + 1], "0")
                self.assertEqual(argv[argv.index("--ramping") + 1], "0")
                self.assertEqual(probe["render_conditions"]["period_raw_protracker_12bit"], TRIGGERS[index]["period"])
            saved = Path(manifest["manifest_path"])
            try:
                from jsonschema import Draft202012Validator
            except ImportError:
                pass
            else:
                schema=json.loads((Path(__file__).resolve().parents[2]/'schemas/instrument-tracker-probes-v1.schema.json').read_text())
                Draft202012Validator(schema).validate(manifest)
                Draft202012Validator(schema).validate(json.loads(saved.read_text()))
            self.assertTrue(saved.is_file())
            self.assertEqual(json.loads(saved.read_text())["source_record_id"], "neutral-001")
            with self.assertRaises(FileExistsError):
                render_tracker_sample_probes(module, sample_number=1, triggers=TRIGGERS,
                                             output_dir=root / "out", source_record_id="neutral-001",
                                             openmpt123_path=renderer)

    def test_rejects_non_mk_bad_sample_and_unanchored_trigger_lists(self):
        source = bytearray((FIXTURE_DIR / "single-rich-tone.mod").read_bytes())
        source[1080:1084] = b"XM01"
        with self.assertRaisesRegex(ValueError, "strict 4-channel"):
            _parse_mod(bytes(source), 1)
        original = (FIXTURE_DIR / "single-rich-tone.mod").read_bytes()
        with self.assertRaisesRegex(ValueError, "1 through 31"):
            _parse_mod(original, 32)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            renderer, module = self.setup_input(root)
            with self.assertRaisesRegex(ValueError, "three distinct"):
                render_tracker_sample_probes(module, sample_number=1,
                                             triggers=TRIGGERS[:2], output_dir=root / "out",
                                             source_record_id="neutral-short", openmpt123_path=renderer)
            with self.assertRaisesRegex(ValueError, "sample offset begins"):
                render_tracker_sample_probes(module, sample_number=1, triggers=TRIGGERS,
                                             output_dir=root / "out", source_record_id="neutral-offset",
                                             openmpt123_path=renderer, sample_offset=32)

    def test_four_byte_loop_is_valid_two_byte_repeat_marker_is_disabled(self):
        data = bytearray((FIXTURE_DIR / "single-rich-tone.mod").read_bytes())
        header = 20
        data[header + 26:header + 28] = (128).to_bytes(2, "big")
        data[header + 28:header + 30] = (2).to_bytes(2, "big")
        meta, _, _ = _parse_mod(bytes(data), 1)
        self.assertTrue(meta["loop_enabled"])
        self.assertEqual(meta["loop_header_length_bytes"], 4)
        self.assertIsNone(_estimated_sample_end_seconds(meta, 214, 0))
        with self.assertRaisesRegex(ValueError, "offset is unsupported for looped"):
            _probe_module(bytes(data), 1, 214, 1)

        data[header + 28:header + 30] = (1).to_bytes(2, "big")
        marker, _, _ = _parse_mod(bytes(data), 1)
        self.assertFalse(marker["loop_enabled"])

    def test_finetune_sign_controls_one_shot_end_bound_and_high_nibble_is_rejected(self):
        data = bytearray((FIXTURE_DIR / "single-rich-tone.mod").read_bytes())
        header = 20
        base, _, _ = _parse_mod(bytes(data), 1)
        base_end = _estimated_sample_end_seconds(base, 339, 0)
        data[header + 24] = 1
        positive, _, _ = _parse_mod(bytes(data), 1)
        positive_end = _estimated_sample_end_seconds(positive, 339, 0)
        data[header + 24] = 0x0F
        negative, _, _ = _parse_mod(bytes(data), 1)
        negative_end = _estimated_sample_end_seconds(negative, 339, 0)
        self.assertLess(positive_end, base_end)
        self.assertGreater(negative_end, base_end)
        data[header + 24] = 0x10
        with self.assertRaisesRegex(ValueError, "finetune high nibble"):
            _parse_mod(bytes(data), 1)

    def test_amiga_resampler_profile_is_explicit_and_bool_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            renderer, module = self.setup_input(root)
            seen = []
            def run(argv, **kwargs):
                seen.append(list(argv))
                return fake_run(argv, **kwargs)
            with patch("instrument_analysis.tracker_rendering.subprocess.run", side_effect=run):
                manifest = render_tracker_sample_probes(
                    module, sample_number=1, triggers=TRIGGERS, output_dir=root / "enabled",
                    source_record_id="amiga-enabled", openmpt123_path=renderer,
                    amiga_resampler=True)
            controls = [seen[1][i + 1] for i, value in enumerate(seen[1][:-1]) if value == "--ctl"]
            self.assertIn("render.resampler.emulate_amiga=1", controls)
            self.assertIn("render.resampler.emulate_amiga_type=a500", controls)
            self.assertEqual(manifest["conditions"]["resampler_profile"], "amiga-a500")
            self.assertTrue(manifest["probes"][0]["render_conditions"]["amiga_resampler_enabled"])
            with self.assertRaisesRegex(ValueError, "must be a bool"):
                render_tracker_sample_probes(
                    module, sample_number=1, triggers=TRIGGERS, output_dir=root / "invalid",
                    source_record_id="amiga-invalid", openmpt123_path=renderer,
                    amiga_resampler=1)

    def test_rejects_corrupt_sample_loop_extent(self):
        data = bytearray((FIXTURE_DIR / "single-rich-tone.mod").read_bytes())
        # Loop starts at 8,000 bytes and requests 1,000 more, beyond the 8,192-byte sample.
        header = 20
        data[header + 26:header + 28] = (4000).to_bytes(2, "big")
        data[header + 28:header + 30] = (500).to_bytes(2, "big")
        with self.assertRaisesRegex(ValueError, "loop exceeds"):
            _parse_mod(bytes(data), 1)


if __name__ == "__main__":
    unittest.main()
