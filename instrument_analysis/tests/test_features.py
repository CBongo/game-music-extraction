import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from instrument_analysis.features import extract_features


class AudioFeatureTests(unittest.TestCase):
    rate = 22050

    def write(self, path, fn, *, duration=1.0, gain=12000, stereo=False, opposite=False):
        frames = round(duration * self.rate)
        data = bytearray()
        for i in range(frames):
            t = i / self.rate
            value = max(-32768, min(32767, round(gain * fn(t))))
            if stereo:
                right = -value if opposite else value
                data += struct.pack("<hh", value, right)
            else:
                data += struct.pack("<h", value)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(2 if stereo else 1)
            wav.setsampwidth(2)
            wav.setframerate(self.rate)
            wav.writeframes(data)
        return path

    def features(self, path, **kwargs):
        return extract_features(path, note_on_seconds=kwargs.pop("note_on_seconds", 0.05),
                                note_off_seconds=kwargs.pop("note_off_seconds", 0.65),
                                end_seconds=kwargs.pop("end_seconds", 0.95), **kwargs)

    @staticmethod
    def cosine(a, b):
        return sum(x * y for x, y in zip(a, b)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))

    def test_sine_bin_and_gain_invariant_spectral_features(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            low = self.features(self.write(root / "low.wav", lambda t: math.sin(2 * math.pi * 440 * t), gain=5000))
            high = self.features(self.write(root / "high.wav", lambda t: math.sin(2 * math.pi * 440 * t), gain=16000))
            for phase in ("attack", "sustain", "release"):
                lp = low["phases"][phase]
                hp = high["phases"][phase]
                self.assertEqual(len(lp["log_spectral_bands_l2"]), 32)
                self.assertEqual(len(lp["envelope_shape"]), 64)
                self.assertGreater(self.cosine(lp["log_spectral_bands_l2"], hp["log_spectral_bands_l2"]), 0.999)
                self.assertAlmostEqual(hp["rms_dbfs"] - lp["rms_dbfs"], 20 * math.log10(16000 / 5000), delta=0.05)
            peak_band = max(range(32), key=lambda i: low["phases"]["sustain"]["log_spectral_bands_l2"][i])
            self.assertGreater(peak_band, 0)

    def test_harmonic_timbre_changes_spectrum_and_centroid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sine = self.features(self.write(root / "sine.wav", lambda t: math.sin(2 * math.pi * 220 * t)))
            rich = self.features(self.write(root / "rich.wav", lambda t: math.sin(2 * math.pi * 220 * t)
                                              + 0.7 * math.sin(2 * math.pi * 440 * t)
                                              + 0.4 * math.sin(2 * math.pi * 880 * t)))
            a, b = sine["phases"]["sustain"], rich["phases"]["sustain"]
            self.assertLess(self.cosine(a["log_spectral_bands_l2"], b["log_spectral_bands_l2"]), 0.98)
            self.assertGreater(b["spectral_centroid_hz"], a["spectral_centroid_hz"])

    def test_silence_and_opposite_phase_stereo_are_handled_without_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            silence = self.features(self.write(root / "silence.wav", lambda _t: 0, gain=0))
            for phase in silence["phases"].values():
                self.assertTrue(phase["silent"])
                self.assertEqual(phase["log_spectral_bands_l2"], [0.0] * 32)
                self.assertEqual(phase["envelope_shape"], [0.0] * 64)
            mono = self.features(self.write(root / "mono.wav", lambda t: math.sin(2 * math.pi * 330 * t)))
            anti = self.features(self.write(root / "anti.wav", lambda t: math.sin(2 * math.pi * 330 * t), stereo=True, opposite=True))
            self.assertFalse(anti["silent"])
            for phase in ("attack", "sustain", "release"):
                self.assertGreater(self.cosine(mono["phases"][phase]["log_spectral_bands_l2"],
                                               anti["phases"][phase]["log_spectral_bands_l2"]), 0.999)
                self.assertAlmostEqual(mono["phases"][phase]["rms_dbfs"],
                                       anti["phases"][phase]["rms_dbfs"], delta=0.01)

    def test_attack_sustain_release_boundaries_and_envelope_shape(self):
        def envelope(t):
            if t < 0.25:
                amp = max(0.05, t / 0.25)
            elif t < 0.65:
                amp = 1.0
            else:
                amp = max(0.0, 1.0 - (t - 0.65) / 0.3)
            return amp * math.sin(2 * math.pi * 440 * t)
        with tempfile.TemporaryDirectory() as tmp:
            result = self.features(self.write(Path(tmp) / "env.wav", envelope), attack_seconds=0.2)
            phases = result["phases"]
            self.assertAlmostEqual(phases["attack"]["start_seconds"], 0.05, places=3)
            self.assertAlmostEqual(phases["attack"]["end_seconds"], 0.25, places=3)
            self.assertAlmostEqual(phases["sustain"]["start_seconds"], 0.25, places=3)
            self.assertAlmostEqual(phases["release"]["start_seconds"], 0.65, places=3)
            attack, release = phases["attack"]["envelope_shape"], phases["release"]["envelope_shape"]
            self.assertGreater(attack[-1], attack[0])
            self.assertGreater(release[0], release[-1])


    def test_event_diagnostics_ignore_padded_audio_and_count_clipping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            padded = self.features(self.write(root / "padded.wav", lambda t: math.sin(2 * math.pi * 440 * t) if t > 1.2 else 0, duration=2.0, gain=12000))
            self.assertTrue(padded["silent"])
            self.assertEqual(padded["event_clipped_sample_count"], 0)
            clipped = self.features(self.write(root / "clipped.wav", lambda t: math.sin(2 * math.pi * 440 * t), gain=40000))
            self.assertGreater(clipped["event_clipped_sample_count"], 0)
            self.assertGreater(clipped["phases"]["sustain"]["clipped_sample_count"], 0)

    def test_hold_too_short_to_separate_attack_and_sustain_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(Path(tmp) / "short.wav", lambda t: math.sin(2 * math.pi * 440 * t))
            with self.assertRaisesRegex(ValueError, "too short"):
                extract_features(path, note_on_seconds=0.05, note_off_seconds=0.12,
                                 end_seconds=0.95, attack_seconds=0.12)
    def test_invalid_bounds_fft_and_too_short_input_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(Path(tmp) / "tone.wav", lambda t: math.sin(2 * math.pi * 440 * t))
            with self.assertRaisesRegex(ValueError, "event boundaries"):
                extract_features(path, note_on_seconds=0.5, note_off_seconds=0.4, end_seconds=0.9)
            with self.assertRaisesRegex(ValueError, "power of two"):
                self.features(path, fft_size=1000)
            with self.assertRaisesRegex(ValueError, "exceeds WAV"):
                extract_features(path, note_on_seconds=0.0, note_off_seconds=0.8, end_seconds=1.1)


if __name__ == "__main__":
    unittest.main()
