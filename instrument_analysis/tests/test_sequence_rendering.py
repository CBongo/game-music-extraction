"""Sequence MIDI scheduling, curve preservation, and render provenance."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from instrument_analysis.sequence_rendering import make_sequence_midi, render_sequence_preview
from instrument_analysis.tests.test_rendering import _events, _soundfont


def recipe():
    return {"schema_version": "instrument-sequence-recipe/v1", "source_record_id": "test",
        "source": {"path": "source.json", "sha256": "0" * 64},
        "provenance": {"context": "authored test", "basis": "test", "timing_basis": "test seconds",
                       "pitch_basis": "test notes", "effects_complete": True},
        "events": [{"event_id": "a", "midi_note": 60, "start_seconds": .1,
                    "duration_seconds": .4, "velocity": 90, "residual_cents": 10,
                    "relative_level_db": 0,
                    "pitch_curve": [{"time_seconds": .2, "cents": 50}],
                    "expression_curve": [{"time_seconds": .15, "value_0_127": 70}]}],
        "end_seconds": .75}


class SequenceRenderingTests(unittest.TestCase):
    def test_curves_offsets_and_octave_preserve_timing(self):
        midi, events = make_sequence_midi(recipe(), 3, -1, [
            {"semitone_offset": 0, "residual_cents": 0, "relative_level_db": 0},
            {"semitone_offset": 4, "residual_cents": -5, "relative_level_db": -6}])
        decoded = _events(midi)
        self.assertEqual([e["midi_note"] for e in events], [48, 52])
        self.assertEqual([e["channel_0based"] for e in events], [0, 1])
        self.assertEqual(events[0]["quantized_pitch_curve"][1]["tick"], 300)
        self.assertEqual(events[0]["quantized_expression_curve"][1], {"tick": 250, "expression_cc11": 70})
        self.assertEqual(events[1]["quantized_expression_curve"][1]["expression_cc11"], 35)
        self.assertIn((100, 0x90, bytes((48, 90))), decoded)
        self.assertIn((500, 0x80, bytes((48, 0))), decoded)
        self.assertEqual(decoded[-1], (750, 255, 47, b""))

    def test_channel_reuse_resets_bend_before_same_tick_noteon(self):
        data = recipe()
        data["provenance"]["release_guard_seconds"] = 0
        second = deepcopy(data["events"][0])
        second.update(event_id="b", start_seconds=.5, duration_seconds=.1, residual_cents=0,
                      pitch_curve=[], expression_curve=[])
        data["events"].append(second)
        midi, events = make_sequence_midi(data, 0)
        self.assertEqual([e["channel_0based"] for e in events], [0, 0])
        decoded = _events(midi)
        off = decoded.index((500, 0x80, bytes((60, 0))))
        reset = decoded.index((500, 0xE0, b"\x00\x40"))
        cut = decoded.index((500, 0xB0, bytes((120, 0))))
        on = decoded.index((500, 0x90, bytes((60, 90))))
        self.assertLess(off, reset)
        self.assertLess(off, cut)
        self.assertLess(cut, reset)
        self.assertLess(reset, on)
        self.assertTrue(events[1]["reuse_cut"])

    def test_quantized_boundary_reuse_and_fifteen_channels(self):
        data = recipe()
        data["provenance"]["release_guard_seconds"] = 0
        data["events"] = [{**deepcopy(data["events"][0]), "event_id": str(i)} for i in range(15)]
        _, events = make_sequence_midi(data, 0)
        self.assertEqual(len({e["channel_0based"] for e in events}), 15)
        self.assertNotIn(9, {e["channel_0based"] for e in events})
        data = recipe()
        data["provenance"]["release_guard_seconds"] = 0
        data["events"][0].update(start_seconds=.1004, duration_seconds=.3992,
                                  pitch_curve=[{"time_seconds": .3992, "cents": -10}])
        data["events"].append({**deepcopy(data["events"][0]), "event_id": "b",
            "start_seconds": .5001, "duration_seconds": .1, "pitch_curve": [], "expression_curve": []})
        midi, events = make_sequence_midi(data, 0)
        self.assertEqual(events[0]["note_off_tick"], events[1]["start_tick"])
        decoded = _events(midi)
        self.assertLess(decoded.index((500, 0x80, bytes((60, 0)))),
                        decoded.index((500, 0x90, bytes((60, 90)))))

    def test_rejects_unsupported_unknown_and_bad_curves(self):
        for change in ({"duration_seconds": True}, {"raw_effects": []},
                       {"pitch_curve": [{"time_seconds": .5, "cents": 0}]},
                       {"pitch_curve": [{"time_seconds": .1, "cents": 201}]},
                       {"expression_curve": [{"time_seconds": .1, "value_0_127": True}]}):
            data = recipe()
            data["events"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                make_sequence_midi(data, 0)
        data = recipe()
        data["provenance"]["effects_complete"] = False
        with self.assertRaises(ValueError):
            make_sequence_midi(data, 0)
        data = recipe()
        data["events"] = [{**deepcopy(data["events"][0]), "event_id": str(i)} for i in range(16)]
        with self.assertRaisesRegex(ValueError, "15 melodic"):
            make_sequence_midi(data, 0)

    def test_default_reserves_release_channels_and_explicit_guard_allows_cut_reuse(self):
        data = recipe()
        data["end_seconds"] = 2
        event = deepcopy(data["events"][0])
        event.update(start_seconds=0, duration_seconds=.1, pitch_curve=[], expression_curve=[])
        data["events"] = [{**event, "event_id": str(i), "start_seconds": i * .1} for i in range(16)]
        with self.assertRaisesRegex(ValueError, "15 melodic"):
            make_sequence_midi(data, 0)
        data["events"] = data["events"][:2]
        midi, events = make_sequence_midi(data, 0)
        self.assertEqual([e["channel_0based"] for e in events], [0, 1])
        self.assertFalse(any(e["reuse_cut"] for e in events))
        self.assertFalse(any(e[1] & 0xf0 == 0xb0 and e[2][0] == 120 for e in _events(midi)))
        data["events"] = [{**event, "event_id": str(i), "start_seconds": i * .1} for i in range(16)]
        data["provenance"]["release_guard_seconds"] = 0
        _, events = make_sequence_midi(data, 0)
        self.assertEqual({e["channel_0based"] for e in events}, {0})
        self.assertEqual(sum(e["reuse_cut"] for e in events), 15)
        data["provenance"]["release_guard_seconds"] = True
        with self.assertRaises(ValueError):
            make_sequence_midi(data, 0)

    def test_file_publication_hashes_and_source_binding(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source.json"
            source.write_text(json.dumps({"source_record_id": "test"}))
            data = recipe()
            data["source"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
            path = root / "recipe.json"
            path.write_text(json.dumps(data))
            font, exe = root / "test.sf2", root / "test.exe"
            font.write_bytes(_soundfont((0, 0, "Piano")))
            exe.write_bytes(b"test")
            def run(argv, **kwargs):
                if "-F" in argv:
                    with wave.open(argv[argv.index("-F") + 1], "wb") as stream:
                        stream.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
                        stream.writeframes(b"\x01\x00\x02\x00" * 40000)
                return subprocess.CompletedProcess(argv, 0, "test", "")
            options = dict(program_0based=0, soundfont_path=font, fluidsynth_path=exe, output_dir=root / "out")
            with patch("instrument_analysis.sequence_rendering.shutil.which", return_value=str(exe)), patch("instrument_analysis.sequence_rendering._run", side_effect=run):
                manifest = render_sequence_preview(path, **options)
                self.assertEqual(manifest["wav"]["frames"], 33075)
                for field in ("midi", "wav"):
                    self.assertEqual(manifest[field]["sha256"], hashlib.sha256((root / "out" / manifest[field]["file"]).read_bytes()).hexdigest())
                with self.assertRaises(FileExistsError):
                    render_sequence_preview(path, **options)
                source.write_text("{}")
                with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                    render_sequence_preview(path, **options)
                audio = root / "native.wav"
                audio.write_bytes(b"bound audio bytes")
                audio_hash = hashlib.sha256(audio.read_bytes()).hexdigest()
                context = {"source_audio_sha256": audio_hash, "capture_context_sha256": "1" * 64}
                source.write_text(json.dumps({"source_record_id": "test", "source_context": context}))
                data["source"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
                data["provenance"].update(source_context=deepcopy(context),
                    source_sequence_audio={"path": "native.wav", "sha256": audio_hash})
                options["output_dir"] = root / "bound-out"
                path.write_text(json.dumps(data))
                render_sequence_preview(path, **options)
                data["provenance"]["source_context"]["capture_context_sha256"] = "2" * 64
                path.write_text(json.dumps(data))
                options["output_dir"] = root / "mismatch"
                with self.assertRaisesRegex(ValueError, "capture context mismatch"):
                    render_sequence_preview(path, **options)
                data["provenance"]["source_context"] = deepcopy(context)
                path.write_text(json.dumps(data))
                def mutate(argv, **kwargs):
                    result = run(argv, **kwargs)
                    if "-F" in argv:
                        audio.write_bytes(b"changed source audio")
                    return result
                with patch("instrument_analysis.sequence_rendering._run", side_effect=mutate):
                    with self.assertRaisesRegex(RuntimeError, "input changed during rendering"):
                        render_sequence_preview(path, **options)
                self.assertFalse((root / "mismatch").exists())


if __name__ == "__main__":
    unittest.main()
