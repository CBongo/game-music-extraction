"""Adversarial and cross-trigger tests for isolated baked-chord inference."""
from __future__ import annotations
import hashlib
import math
from pathlib import Path
import random
import struct
import tempfile
import unittest
import wave

from instrument_analysis.composites import analyze_baked_chord, expand_composite_events


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _probes(root: Path, *, offsets=(), harmonics=(1.0, .3, .15),
            frequency_mode="equal_tempered", noise=False, silence=False,
            short=False, triggers=(60, 64, 67)):
    identity = hashlib.sha256((str(offsets) + str(harmonics) + frequency_mode).encode()).hexdigest()
    probes = []
    for index, trigger in enumerate(triggers):
        path = root / f"neutral-{index}.wav"
        rate, frames = 22050, 26460
        rng = random.Random(71 + index)
        values = []
        for frame in range(frames):
            t = frame / rate
            envelope = min(1.0, t / .015, max(0, (1.2 - t) / .2))
            if silence:
                sample = 0.0
            elif noise:
                sample = rng.uniform(-1, 1) * envelope * .2
            elif frequency_mode == "just_456":
                base = 440 * 2 ** ((trigger - 24 - 69) / 12)
                sample = sum(math.sin(2 * math.pi * base * h * t) for h in (4, 5, 6)) * envelope / 3
            elif frequency_mode == "missing_234":
                base = 440 * 2 ** ((trigger - 12 - 69) / 12)
                sample = sum(math.sin(2 * math.pi * base * h * t) for h in (2, 3, 4)) * envelope / 3
            elif frequency_mode == "inharmonic":
                base = 440 * 2 ** ((trigger - 69) / 12)
                sample = sum(math.sin(2 * math.pi * base * r * t)
                             for r in (1, math.sqrt(2), math.pi / 2)) * envelope / 3
            else:
                sample = sum(
                    sum(weight * math.sin(2 * math.pi *
                        (440 * 2 ** ((trigger + offset - 69) / 12)) * (h + 1) * t)
                        for h, weight in enumerate(harmonics))
                    for offset in offsets) * envelope / max(1, len(offsets))
            values.append(max(-32767, min(32767, round(sample * 7000))))
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            stream.writeframes(struct.pack("<" + "h" * frames, *values))
        probes.append({
            "wav_path": str(path), "wav_sha256": _sha(path),
            "trigger_midi_note": trigger,
            "note_on_seconds": 0, "note_off_seconds": .22 if short else 1.0,
            "analysis_end_seconds": .18 if short else .95,
            "end_seconds": 1.2,
            "render_conditions": {
                "source_identity_sha256": identity,
                "source_identity_kind": "controlled_recipe",
            },
        })
    return probes


class CompositeTests(unittest.TestCase):
    def test_major_minor_suspended_and_inversion_cross_trigger(self):
        cases = [
            ((-5, 0, 4), "major"),
            ((0, 3, 7), "minor"),
            ((0, 2, 7), "sus2"),
            ((0, 5, 7), "sus4"),
            ((-5, 4, 12), "major"),
        ]
        for offsets, quality in cases:
            with self.subTest(offsets=offsets), tempfile.TemporaryDirectory() as directory:
                probes = _probes(Path(directory), offsets=offsets)
                bundle, diag = analyze_baked_chord(probes, source_record_id="neutral")
                self.assertEqual(diag["status"], "supported", diag["reason"])
                self.assertEqual(diag["quality"], quality)
                self.assertEqual(bundle['records'][0]['source']['platform'],'synthetic audio')
                self.assertIsNone(diag['input']['source_sample_sha256'])
                self.assertEqual([c["semitone_offset"] for c in diag["components"]], list(offsets))
                if quality in ("sus2", "sus4"):
                    self.assertEqual(diag["quality_alternatives"][0]["root_pc_relative_trigger"], 0)
                    self.assertEqual({a["quality"] for a in diag["quality_alternatives"]},
                                     {"sus2", "sus4"})
                self.assertTrue(bundle["records"][0]["analysis"]["composite"]["is_harmonic_composite"])

    def test_wide_dyad_has_competing_observed_major_triad(self):
        # A lower root's third harmonic coincides with an independently
        # observed upper note. The two- and three-component source models
        # cannot be separated by these magnitude spectra alone.
        with tempfile.TemporaryDirectory() as directory:
            bundle, diag = analyze_baked_chord(_probes(Path(directory),
                offsets=(-12, 4, 7), harmonics=(1, .3, .15)),
                source_record_id="neutral")
            self.assertEqual(diag["status"], "abstain", diag)
            competing = diag["model"]["competing_three_note_fit"]
            self.assertIsNotNone(competing)
            self.assertEqual(competing["offsets"], [-12, 4, 7])
            self.assertNotIn("composite", bundle["records"][0]["analysis"])
            self.assertEqual(bundle["records"][0]["analysis"]["routing"][0]["route"]["kind"],
                             "abstain")

    def test_single_rich_and_missing_fundamental_alternative(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, diag = analyze_baked_chord(_probes(Path(directory), offsets=(0,),
                harmonics=(1, .7, .5, .3, .2)), source_record_id="neutral")
            self.assertEqual(diag["status"], "supported")
            self.assertEqual([c["semitone_offset"] for c in diag["components"]], [0])
            self.assertFalse(bundle["records"][0]["analysis"]["composite"]["is_harmonic_composite"])
        for mode in ("just_456", "missing_234"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                bundle, diag = analyze_baked_chord(_probes(Path(directory),
                    frequency_mode=mode), source_record_id="neutral")
                self.assertEqual(diag["status"], "abstain", diag)
                self.assertIsNotNone(diag["model"]["single_missing_fundamental_alternative"])
                self.assertNotIn("composite", bundle["records"][0]["analysis"])

    def test_power_octave_noise_silence_and_inharmonic_abstain(self):
        for mode, options in (
            ("power", {"offsets": (0, 7, 12)}),
            ("noise", {"noise": True}),
            ("silence", {"silence": True}),
            ("inharmonic", {"frequency_mode": "inharmonic"}),
            ("short", {"offsets": (0, 4, 7), "short": True}),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                _, diag = analyze_baked_chord(_probes(Path(directory), **options),
                                               source_record_id="neutral")
                self.assertEqual(diag["status"], "abstain", diag)

    def test_single_trigger_is_not_confirmed_and_hash_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            probes = _probes(Path(directory), offsets=(0, 4, 7), triggers=(60,))
            _, diag = analyze_baked_chord(probes, source_record_id="neutral")
            self.assertIn(diag["status"], ("tentative", "abstain"))
            path = Path(probes[0]["wav_path"])
            path.write_bytes(path.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                analyze_baked_chord(probes, source_record_id="neutral")

    def test_expansion_is_all_or_nothing_and_preserves_common_modulation(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, diag = analyze_baked_chord(_probes(Path(directory), offsets=(-5, 0, 4)),
                                                source_record_id="neutral")
            event = {"midi_note": 60, "start": 1.25, "duration": .75, "velocity": 99,
                     "pitch_bend": [0, 100, -50], "vibrato": {"depth_cents": 18}}
            expanded = expand_composite_events([event], bundle, diag)
            self.assertEqual([e["midi_note"] for e in expanded], [55, 60, 64])
            self.assertTrue(all(e["start"] == 1.25 and e["duration"] == .75 and
                                e["velocity"] == 99 and e["pitch_bend"] == event["pitch_bend"] and
                                e["vibrato"] == event["vibrato"] for e in expanded))
            self.assertTrue(all("residual_cents" in e and "relative_level_db" in e for e in expanded))
            with self.assertRaisesRegex(ValueError,'source_record_id'):
                expand_composite_events([{**event,'source_record_id':'different'}],bundle,diag)
            from copy import deepcopy
            bad_bundle,bad_diag=deepcopy(bundle),deepcopy(diag)
            bad_bundle['records'][0]['analysis']['composite']['components'][0]['residual_cents']=True
            bad_diag['components'][0]['residual_cents']=True
            with self.assertRaisesRegex(ValueError,'offset/cents'):
                expand_composite_events([event],bad_bundle,bad_diag)
            with self.assertRaisesRegex(ValueError, "outside MIDI range"):
                expand_composite_events([event, {**event, "midi_note": 0}], bundle, diag)
            with self.assertRaisesRegex(ValueError, "only supported"):
                expand_composite_events([event], bundle, {**diag, "status": "abstain"})


if __name__ == "__main__":
    unittest.main()
