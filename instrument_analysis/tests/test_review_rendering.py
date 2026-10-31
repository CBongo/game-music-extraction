"""MIDI recipe and file publication contracts for composite auditions."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from instrument_analysis.review_rendering import make_composite_midi, render_composite_preview
from instrument_analysis.tests.test_rendering import _events, _soundfont


class CompositePreviewTests(unittest.TestCase):
    def events(self):
        return [{"midi_note": note, "start": 0, "duration": .25, "velocity": 96,
                 "residual_cents": cents, "relative_level_db": level,
                 "component_index": index}
                for index, (note, cents, level) in enumerate(((55, -25, 0), (60, 0, -6), (64, 25, -12)))]

    def test_notes_timing_bend_rpn_and_levels(self):
        midi, recipe = make_composite_midi(self.events(), program_0based=7, tail_seconds=.5)
        decoded = _events(midi)
        self.assertEqual([e for e in decoded if e[1] & 0xf0 == 0x90],
                         [(0, 0x90, b"\x37\x60"), (0, 0x91, b"\x3c\x60"), (0, 0x92, b"\x40\x60")])
        self.assertEqual([r["pitch_bend_14bit"] for r in recipe], [7168, 8192, 9216])
        self.assertEqual([r["expression_cc11"] for r in recipe], [127, 64, 32])
        for channel, row in enumerate(recipe):
            self.assertIn((0, 0xE0 | channel, bytes((row["pitch_bend_14bit"] & 127, row["pitch_bend_14bit"] >> 7))), decoded)
            for cc, value in ((101, 0), (100, 0), (6, 2), (38, 0), (11, row["expression_cc11"])):
                self.assertIn((0, 0xB0 | channel, bytes((cc, value))), decoded)
            self.assertIn((250, 0x80 | channel, bytes((row["midi_note"], 0))), decoded)
        self.assertEqual(decoded[-1], (750, 255, 47, b""))

    def test_rejects_effects_levels_range_and_timing(self):
        for change in ({"bend": []}, {"relative_level_db": None}, {"residual_cents": 51},
                       {"midi_note": 128}, {"start": .1}, {"duration": .5}):
            events = self.events()
            events[0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                make_composite_midi(events, program_0based=0, tail_seconds=0)
        midi, recipe = make_composite_midi(self.events() * 5, program_0based=0, tail_seconds=0)
        self.assertEqual(len(recipe), 15)
        self.assertNotIn(9, [r["channel_0based"] for r in recipe])

    def test_hashes_publication_and_refuses_overwrite_or_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "fixture"
            fixture.mkdir()
            components = [{"semitone_offset": note - 60, "residual_cents": cents,
                           "relative_level_db": level, "confidence": .95}
                          for note, cents, level in ((55, -25, 0), (60, 0, -6), (64, 25, -12))]
            (fixture / "analysis.json").write_text(json.dumps({"schema_version": "instrument-analysis/v1",
                "records": [{"record_id": "test-source", "analysis": {"composite": {
                    "components": components, "is_harmonic_composite": True}}}]}))
            (fixture / "diagnostics.json").write_text(json.dumps({"status": "supported",
                "source_record_id": "test-source", "components": components}))
            sf = root / "test.sf2"
            sf.write_bytes(_soundfont((0, 0, "Piano")))
            exe = root / "fluidsynth.exe"
            exe.write_bytes(b"test executable")
            def run(argv, **kwargs):
                if "-F" in argv:
                    with wave.open(argv[argv.index("-F") + 1], "wb") as stream:
                        stream.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
                        stream.writeframes(b"\x01\x00" * 33075)
                return subprocess.CompletedProcess(argv, 0, "test version", "")
            options = dict(trigger_midi_note=60, duration_seconds=.25, tail_seconds=.5,
                           program_0based=0, soundfont_path=sf, fluidsynth_path=exe,
                           output_dir=root / "out")
            with patch("instrument_analysis.review_rendering.shutil.which", return_value=str(exe)), patch("instrument_analysis.review_rendering._run", side_effect=run):
                manifest = render_composite_preview(fixture / "analysis.json", fixture / "diagnostics.json", **options)
                self.assertEqual(manifest, json.loads((root / "out/manifest.json").read_text()))
                self.assertEqual([e["midi_note"] for e in manifest["events"]], [55, 60, 64])
                for kind in ("wav", "midi"):
                    artifact = root / "out" / manifest[kind]["file"]
                    self.assertEqual(manifest[kind]["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())
                with self.assertRaises(FileExistsError):
                    render_composite_preview(fixture / "analysis.json", fixture / "diagnostics.json", **options)
                options["output_dir"] = root / "clipped"
                def clipped(argv, **kwargs):
                    result = run(argv, **kwargs)
                    if "-F" in argv:
                        with wave.open(argv[argv.index("-F") + 1], "wb") as stream:
                            stream.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
                            stream.writeframes(b"\xff\x7f" * 33075)
                    return result
                with patch("instrument_analysis.review_rendering._run", side_effect=clipped):
                    with self.assertRaisesRegex(RuntimeError, "silent or clipped"):
                        render_composite_preview(fixture / "analysis.json", fixture / "diagnostics.json", **options)
                self.assertFalse((root / "clipped").exists())
                options["trigger_midi_note"] = 0
                with self.assertRaises(ValueError):
                    render_composite_preview(fixture / "analysis.json", fixture / "diagnostics.json", **options)
                options.update(trigger_midi_note=60, output_dir=root / "mutated")
                original_run = run
                def mutate(argv, **kwargs):
                    result = original_run(argv, **kwargs)
                    if "-F" in argv:
                        (fixture / "analysis.json").write_text("{}")
                    return result
                with patch("instrument_analysis.review_rendering._run", side_effect=mutate):
                    with self.assertRaisesRegex(RuntimeError, "changed during rendering"):
                        render_composite_preview(fixture / "analysis.json", fixture / "diagnostics.json", **options)
                self.assertFalse((root / "mutated").exists())


if __name__ == "__main__":
    unittest.main()
