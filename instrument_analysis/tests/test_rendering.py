"""Contract tests for reproducible FluidSynth reference rendering."""
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import tempfile
import unittest
from unittest.mock import patch
import wave

from instrument_analysis.rendering import (
    MANIFEST_VERSION, make_probe_midi, plan_gm_reference_probes,
    render_gm_reference_set, read_soundfont_presets,
)


def _chunk(tag, payload):
    return tag + len(payload).to_bytes(4, "little") + payload + (b"\x00" if len(payload) & 1 else b"")


def _soundfont(*presets):
    headers = b"".join(struct.pack("<20sHHHIII", name.encode("ascii"), program, bank,
                                    0, 0, 0, 0)
                       for bank, program, name in presets)
    headers += struct.pack("<20sHHHIII", b"EOP", 0, 0, 0, 0, 0, 0)
    body = b"sfbk" + _chunk(b"LIST", b"pdta" + _chunk(b"phdr", headers))
    return b"RIFF" + len(body).to_bytes(4, "little") + body

def _events(data):
    """Decode the subset of SMF events emitted by the renderer."""
    assert data[:4] == b"MThd"
    assert int.from_bytes(data[8:10], "big") == 0
    assert int.from_bytes(data[12:14], "big") == 1000
    assert data[14:18] == b"MTrk"
    track = data[22:]
    assert len(track) == int.from_bytes(data[18:22], "big")
    events = []
    i = 0
    tick = 0
    while i < len(track):
        delta = 0
        while True:
            byte = track[i]
            i += 1
            delta = (delta << 7) | (byte & 127)
            if not byte & 128:
                break
        tick += delta
        status = track[i]
        i += 1
        if status == 255:
            kind = track[i]
            length = track[i + 1]
            payload = track[i + 2:i + 2 + length]
            i += 2 + length
            events.append((tick, status, kind, bytes(payload)))
        else:
            length = 1 if status & 0xF0 == 0xC0 else 2
            payload = bytes(track[i:i + length])
            i += length
            events.append((tick, status, payload))
    return events


class RenderingTests(unittest.TestCase):
    def test_default_plan_covers_all_gm_programs_and_standard_drums(self):
        probes = plan_gm_reference_probes()
        self.assertEqual(len(probes), 128 * 3 + 47)
        self.assertEqual(probes[0]["program_0based"], 0)
        self.assertEqual(probes[0]["program_1based"], 1)
        self.assertEqual(probes[0]["route"],
                         {"kind": "gm_program", "program_0based": 0,
                          "program_1based": 1})
        self.assertEqual(probes[383]["program_0based"], 127)
        self.assertEqual(probes[383]["program_1based"], 128)
        self.assertEqual(probes[384]["channel_0based"], 9)
        self.assertEqual(probes[384]["note"], 35)
        self.assertEqual(probes[384]["route"], {"kind": "gm_percussion", "note": 35})
        self.assertEqual(probes[-1]["note"], 81)
        self.assertEqual(len({p["probe_id"] for p in probes}), len(probes))

    def test_midi_timing_bank_and_channel_encoding(self):
        melody, drum = plan_gm_reference_probes(
            notes=(60,), velocities=(96,), programs=(5,),
            percussion_keys=(38,), banks=(129,))
        self.assertEqual(_events(make_probe_midi(
            melody, duration_seconds=.25, tail_seconds=.75)), [
            (0, 255, 81, b"\x0f\x42\x40"),
            (0, 0xB0, b"\x00\x01"),
            (0, 0xB0, b"\x20\x01"),
            (0, 0xC0, b"\x05"),
            (0, 0x90, b"\x3c\x60"),
            (250, 0x80, b"\x3c\x00"),
            (1000, 255, 47, b""),
        ])
        self.assertEqual(_events(make_probe_midi(drum)), [
            (0, 255, 81, b"\x0f\x42\x40"),
            (0, 0xC9, b"\x00"),
            (0, 0x99, b"\x26\x60"),
            (500, 0x89, b"\x26\x00"),
            (1000, 255, 47, b""),
        ])
        self.assertIsNone(drum["bank_select_msb"])
        self.assertEqual(drum["bank"], 128)

    def test_render_manifest_and_no_shell_with_mock_synth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sf = root / "bank.sf2"
            exe = root / "fluidsynth.exe"
            sf.write_bytes(_soundfont((0, 0, "Piano"), (128, 0, "Drums")))
            exe.write_bytes(b"executable")
            calls = []

            def fake_run(argv, **kwargs):
                calls.append((list(argv), kwargs))
                if "--version" in argv:
                    return subprocess.CompletedProcess(argv, 0, "FluidSynth 2.5.4\n", "")
                wav_path = Path(next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--fast-render=")))
                midi_path = Path(argv[-1])
                self.assertTrue(midi_path.is_file())
                with wave.open(str(wav_path), "wb") as stream:
                    stream.setnchannels(2)
                    stream.setsampwidth(2)
                    stream.setframerate(44100)
                    stream.writeframes(b"\x01\x00\xfe\xff" * 44100)
                return subprocess.CompletedProcess(argv, 0, "", "")

            with patch("instrument_analysis.rendering.shutil.which", return_value=str(exe)), \
                 patch("instrument_analysis.rendering.subprocess.run", side_effect=fake_run):
                manifest = render_gm_reference_set(sf, root / "out",
                    fluidsynth_path=exe, notes=(60,), velocities=(96,),
                    programs=(0,), percussion_keys=(35,))
            self.assertEqual(manifest["schema_version"], MANIFEST_VERSION)
            self.assertEqual(manifest["generator"]["version"], "FluidSynth 2.5.4")
            self.assertEqual(manifest["soundfont"]["sha256"], hashlib.sha256(sf.read_bytes()).hexdigest())
            self.assertEqual(len(manifest["probes"]), 2)
            self.assertEqual({p["preset_name"] for p in manifest["probes"]},
                             {"Piano", "Drums"})
            self.assertEqual(manifest["soundfont"]["preset_count"], 2)
            for entry in manifest["probes"]:
                self.assertIsNone(entry["source_record_id"])
                self.assertIn(entry["route"]["kind"], {"gm_program", "gm_percussion"})
                self.assertEqual(entry["wav"]["frames"], 44100)
                self.assertEqual(entry["wav"]["peak_pcm16"], 2)
                wav_file = root / "out" / entry["wav"]["file"]
                self.assertEqual(entry["wav"]["sha256"], hashlib.sha256(wav_file.read_bytes()).hexdigest())
            disk = json.loads((root / "out" / "manifest.json").read_text())
            self.assertEqual(disk, manifest)
            try:
                from jsonschema import Draft202012Validator
                from referencing import Registry,Resource
            except ImportError:
                pass
            else:
                repo=Path(__file__).resolve().parents[2]
                definition=json.loads((repo/'schemas/instrument-render-v1.schema.json').read_text())
                observation=json.loads((repo/'schemas/instrument-analysis-v1.schema.json').read_text())
                registry=Registry().with_resource(observation['$id'],Resource.from_contents(observation))
                Draft202012Validator.check_schema(definition)
                Draft202012Validator(definition,registry=registry).validate(manifest)

            self.assertEqual(len(calls), 3)
            for argv, kwargs in calls:
                self.assertIs(kwargs["shell"], False)
                self.assertIsNotNone(kwargs["timeout"])
            render_calls = [argv for argv, _ in calls[1:]]
            self.assertNotEqual(render_calls[0][-1], render_calls[1][-1])
            for argv in render_calls:
                self.assertIn("--no-shell", argv)
                self.assertIn("--no-midi-in", argv)
                self.assertIn("synth.midi-bank-select=mma", argv)
                self.assertIn("--chorus=0", argv)
                self.assertIn("--reverb=0", argv)
            with patch("instrument_analysis.rendering.shutil.which", return_value=str(exe)):
                with self.assertRaises(FileExistsError):
                    render_gm_reference_set(sf, root / "out",
                        fluidsynth_path=exe, notes=(60,), programs=(0,),
                        percussion_keys=(35,))

    def test_failed_render_does_not_publish_manifest_or_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sf = root / "bank.sf2"
            exe = root / "fluidsynth.exe"
            sf.write_bytes(_soundfont((0, 0, "Piano"), (128, 0, "Drums")))
            exe.write_bytes(b"executable")

            def failing_run(argv, **kwargs):
                if "--version" in argv:
                    return subprocess.CompletedProcess(argv, 0, "FluidSynth test", "")
                return subprocess.CompletedProcess(argv, 2, "", "bad soundfont")

            with patch("instrument_analysis.rendering.shutil.which", return_value=str(exe)), \
                 patch("instrument_analysis.rendering.subprocess.run", side_effect=failing_run):
                with self.assertRaisesRegex(RuntimeError, "probe .*bad soundfont"):
                    render_gm_reference_set(sf, root / "out", fluidsynth_path=exe,
                        notes=(60,), programs=(0,), percussion_keys=())
            self.assertFalse((root / "out" / "manifest.json").exists())
            self.assertEqual(list((root / "out").glob("*.wav")), [])


    def test_zero_velocity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "velocity 0"):
            plan_gm_reference_probes(notes=(60,), velocities=(0,),
                                     programs=(0,), percussion_keys=())

        probe = dict(plan_gm_reference_probes(notes=(60,), programs=(0,),
                                              percussion_keys=())[0])
        probe["velocity"] = 0
        with self.assertRaises(ValueError):
            make_probe_midi(probe)

    def test_soundfont_preset_coverage_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sf = root / "partial.sf3"
            exe = root / "fluidsynth.exe"
            sf.write_bytes(_soundfont((0, 0, "Piano")))
            exe.write_bytes(b"executable")
            self.assertEqual(read_soundfont_presets(sf), {(0, 0): "Piano"})
            with patch("instrument_analysis.rendering.shutil.which", return_value=str(exe)), \
                 patch("instrument_analysis.rendering.subprocess.run") as runner:
                with self.assertRaisesRegex(ValueError, "bank=0 program=1"):
                    render_gm_reference_set(sf, root / "out", fluidsynth_path=exe,
                        notes=(60,), programs=(0, 1), percussion_keys=())
                with self.assertRaisesRegex(ValueError, "bank=128 program=0"):
                    render_gm_reference_set(sf, root / "out", fluidsynth_path=exe,
                        notes=(60,), programs=(0,), percussion_keys=(35,))
                runner.assert_not_called()
            self.assertFalse((root / "out" / "manifest.json").exists())
            truncated = root / "truncated.sf2"
            truncated.write_bytes(sf.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError, "truncated SoundFont RIFF"):
                read_soundfont_presets(truncated)

    def test_soundfont_parser_rejects_bad_format_duplicate_and_chunk_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.sf2"
            path.write_bytes(b"not-a-soundfont")
            with self.assertRaisesRegex(ValueError, "RIFF sfbk"):
                read_soundfont_presets(path)
            path.write_bytes(_soundfont((0, 0, "A"), (0, 0, "B")))
            with self.assertRaisesRegex(ValueError, "duplicate bank 0, program 0"):
                read_soundfont_presets(path)
            malformed = bytearray(_soundfont((0, 0, "A")))
            offset = malformed.index(b"phdr") + 4
            malformed[offset:offset + 4] = (9999).to_bytes(4, "little")
            path.write_bytes(malformed)
            with self.assertRaisesRegex(ValueError, "truncated SoundFont pdta chunk"):
                read_soundfont_presets(path)


    def test_short_wav_is_rejected_without_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sf = root / "bank.sf2"
            exe = root / "fluidsynth.exe"
            sf.write_bytes(_soundfont((0, 0, "Piano")))
            exe.write_bytes(b"executable")

            def short_run(argv, **kwargs):
                if "--version" in argv:
                    return subprocess.CompletedProcess(argv, 0, "FluidSynth test", "")
                wav_path = Path(next(arg.split("=", 1)[1]
                                     for arg in argv if arg.startswith("--fast-render=")))
                with wave.open(str(wav_path), "wb") as stream:
                    stream.setnchannels(2)
                    stream.setsampwidth(2)
                    stream.setframerate(44100)
                    stream.writeframes(b"\x00\x00\x00\x00" * 100)
                return subprocess.CompletedProcess(argv, 0, "", "")

            with patch("instrument_analysis.rendering.shutil.which", return_value=str(exe)), \
                 patch("instrument_analysis.rendering.subprocess.run", side_effect=short_run):
                with self.assertRaisesRegex(RuntimeError, "invalid WAV properties"):
                    render_gm_reference_set(sf, root / "out", fluidsynth_path=exe,
                        notes=(60,), programs=(0,), percussion_keys=())
            self.assertFalse((root / "out" / "manifest.json").exists())
            self.assertEqual(list((root / "out").glob("*.wav")), [])

if __name__ == "__main__":
    unittest.main()
