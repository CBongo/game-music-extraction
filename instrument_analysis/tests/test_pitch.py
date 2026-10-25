import hashlib
import json
import math
import random
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from instrument_analysis.pitch import calibrate_manifest_pitch


class PitchCalibrationTests(unittest.TestCase):
    def make_capture(self, root, signal, *, rate=22050, duration=1.0):
        wav_path = root / "tone.wav"
        samples = [max(-32768, min(32767, round(signal(i / rate)))) for i in range(round(rate * duration))]
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(struct.pack("<" + "h" * len(samples), *samples))
        manifest = {
            "schema_version": "instrument-native-chip-render/v1",
            "source_record_id": "synthetic-fixture",
            "engine": "test native chip",
            "key": {"pitch_register_raw_test": 4096, "midi_note": None},
            "conditions": {"note_on_time_seconds": 0.02, "note_off_time_seconds": 0.95,
                           "wav_alignment": {"actual_trim_start_seconds": 0.0}},
            "wav": {"file": wav_path.name, "sha256": hashlib.sha256(wav_path.read_bytes()).hexdigest()},
        }
        manifest_path = root / "tone.manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path

    def test_sine_and_harmonic_rich_tones_calibrate_acoustically(self):
        for fn, expected in [
            (lambda t: 10000 * math.sin(2 * math.pi * 440.0 * t), 440.0),
            (lambda t: 8000 * math.sin(2 * math.pi * 220.0 * t)
             + 4500 * math.sin(2 * math.pi * 440.0 * t)
             + 2500 * math.sin(2 * math.pi * 660.0 * t), 220.0),
        ]:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = calibrate_manifest_pitch(self.make_capture(root, fn))
                self.assertEqual(result["status"], "calibrated")
                self.assertAlmostEqual(result["f0_hz"], expected, delta=1.5)
                self.assertEqual(result["basis"], "acoustic_measurement")
                self.assertIsNone(result["native_raw_pitch"].get("midi_note"))
                self.assertGreaterEqual(result["confidence"], 0.8)

    def test_silence_noise_chord_and_close_competitors_abstain(self):
        rng = random.Random(19)
        silence = lambda _t: 0
        noise = lambda _t: rng.randint(-12000, 12000)
        chord = lambda t: 7000 * math.sin(2 * math.pi * 440 * t) + 7000 * math.sin(2 * math.pi * 554.37 * t)
        close = lambda t: 7000 * math.sin(2 * math.pi * 440 * t) + 7000 * math.sin(2 * math.pi * 445 * t)
        for fn in (silence, noise, chord, close):
            with self.subTest(signal=fn), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = calibrate_manifest_pitch(self.make_capture(root, fn))
                self.assertEqual(result["status"], "abstain")
                self.assertIsNone(result["sounding_midi_note"])

    def test_manifest_and_wav_hashes_are_bound_and_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = self.make_capture(root, lambda t: 10000 * math.sin(2 * math.pi * 330 * t))
            result = calibrate_manifest_pitch(manifest_path)
            self.assertEqual(result["native_manifest_sha256"], hashlib.sha256(manifest_path.read_bytes()).hexdigest())
            wav_path = root / "tone.wav"
            wav_path.write_bytes(wav_path.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "WAV hash"):
                calibrate_manifest_pitch(manifest_path)

    def test_trim_clock_is_aligned_to_wav_and_invalid_bounds_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = self.make_capture(root, lambda t: 10000 * math.sin(2 * math.pi * 440 * t))
            data = json.loads(manifest_path.read_text())
            data["conditions"]["note_on_time_seconds"] = 1.02
            data["conditions"]["note_off_time_seconds"] = 1.95
            data["conditions"]["wav_alignment"]["actual_trim_start_seconds"] = 1.0
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            result = calibrate_manifest_pitch(manifest_path)
            self.assertEqual(result["status"], "calibrated")
            self.assertGreater(result["window_estimates"][0]["start_seconds"], 0)
            data["conditions"]["wav_alignment"]["actual_trim_start_seconds"] = 4.0
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "trim bounds"):
                calibrate_manifest_pitch(manifest_path)


if __name__ == "__main__":
    unittest.main()
