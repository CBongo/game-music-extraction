"""Mocked subprocess contract tests for the native Gradius III renderer."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from instrument_analysis.native_rendering import (
    MANIFEST_VERSION, render_gradius3_probe,
)


def _fixture(root: Path):
    exe = root / "mame.exe"
    exe.write_bytes(b"fake MAME executable")
    romdir = root / "roms"
    romdir.mkdir()
    rom = bytearray(0x10000)
    patch_num, pointer = 3, 0x500
    rom[0x27CD + patch_num * 2:0x27CD + patch_num * 2 + 2] = pointer.to_bytes(2, "little")
    raw_patch = bytes(range(25))
    rom[pointer:pointer + 25] = raw_patch
    audio = root / "audio.bin"
    audio.write_bytes(rom)
    return exe, romdir, audio, patch_num, pointer, raw_patch


def _receipt(env, patch_num, pointer, raw_patch, *, status="ok"):
    data = {
        "schema_version": "gradius3-native-probe-receipt/v1",
        "status": status,
        "patch_id": str(patch_num),
        "patch_num": patch_num,
        "keycode_raw_ym2151": 66,
        "hold_frames": 2,
        "tail_frames": 2,
        "startup_frames": 8,
        "frame_period_seconds": 0.005,
        "patch_pointer": pointer,
        "patch_bytes_hex": raw_patch.hex(),
        "note_on_time_seconds": 0.01,
        "note_off_time_seconds": 0.02,
        "capture_end_time_seconds": 0.03,
    }
    if status != "ok":
        data["failure"] = "Z80 startup timeout"
    Path(env["GME_G3_RECEIPT"]).write_text(json.dumps(data), encoding="utf-8")


def _capture(argv, env, *, receipt=True, status="ok"):
    wav_path = Path(argv[argv.index("-wavwrite") + 1])
    with wave.open(str(wav_path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(b"\x01\x00\xfe\xff" * 4410)
    if receipt:
        rom = Path(env["_TEST_AUDIO_ROM"]).read_bytes()
        patch_num = int(env["GME_G3_PATCH"])
        pointer = int.from_bytes(rom[0x27CD + patch_num * 2:0x27CD + patch_num * 2 + 2], "little")
        _receipt(env, patch_num, pointer, rom[pointer:pointer + 25], status=status)
    return subprocess.CompletedProcess(argv, 0, "", "")


class NativeRenderingTests(unittest.TestCase):
    def test_successful_native_capture_is_receipt_validated_trimmed_and_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exe, romdir, audio, patch_num, pointer, raw_patch = _fixture(root)
            calls = []

            def fake_run(argv, **kwargs):
                calls.append((list(argv), kwargs))
                kwargs["env"]["_TEST_AUDIO_ROM"] = str(audio)
                return _capture(argv, kwargs["env"])

            with patch("instrument_analysis.native_rendering.subprocess.run", side_effect=fake_run):
                manifest = render_gradius3_probe(mame_path=exe, rompath=romdir,
                    audio_rom_path=audio, output_dir=root / "out",
                    source_record_id="source-7", patch_id=patch_num, keycode=66,
                    hold_frames=2, tail_frames=2)
            self.assertEqual(manifest["schema_version"], MANIFEST_VERSION)
            self.assertEqual(manifest["engine"], "MAME YM2151")
            self.assertEqual(manifest["source_record_id"], "source-7")
            self.assertEqual(manifest["instrument"]["patch_pointer"], pointer)
            self.assertEqual(manifest["instrument"]["patch_bytes_hex"], raw_patch.hex())
            self.assertEqual(manifest["key"]["keycode_raw_ym2151"], 66)
            self.assertIsNone(manifest["key"]["midi_note"])
            self.assertIsNone(manifest["key"]["velocity"])
            self.assertEqual(manifest["wav"]["frames"], 882)
            self.assertEqual(manifest["wav"]["duration_seconds"], 882 / 44100)
            out = root / "out"
            wav_path = out / manifest["wav"]["file"]
            self.assertEqual(manifest["wav"]["sha256"], hashlib.sha256(wav_path.read_bytes()).hexdigest())
            receipt = json.loads((out / manifest["receipt_file"]).read_text())
            self.assertEqual(receipt["status"], "ok")
            disk_manifest = json.loads((out / f"gradius3-fm-{patch_num:03d}-kc-066.manifest.json").read_text())
            self.assertEqual(disk_manifest, manifest)
            try:
                from jsonschema import Draft202012Validator
                from referencing import Registry,Resource
            except ImportError:
                pass
            else:
                repo=Path(__file__).resolve().parents[2]
                definition=json.loads((repo/'schemas/instrument-native-render-v1.schema.json').read_text())
                observation=json.loads((repo/'schemas/instrument-analysis-v1.schema.json').read_text())
                registry=Registry().with_resource(observation['$id'],Resource.from_contents(observation))
                Draft202012Validator.check_schema(definition)
                Draft202012Validator(definition,registry=registry).validate(manifest)

            self.assertEqual(len(calls), 1)
            argv, kwargs = calls[0]
            self.assertIs(kwargs["shell"], False)
            self.assertEqual(kwargs["timeout"], 60)
            self.assertEqual(kwargs["cwd"], Path(kwargs["cwd"]))
            self.assertIn("-wavwrite", argv)
            self.assertIn("-samplerate", argv)
            self.assertNotIn("-sound", argv)
            self.assertEqual(kwargs["env"]["GME_G3_KEYCODE"], "66")
            self.assertEqual(kwargs["env"]["GME_G3_PATCH"], str(patch_num))

    def test_timeout_nonzero_and_missing_receipt_do_not_publish(self):
        for mode in ("timeout", "nonzero", "missing"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                exe, romdir, audio, patch_num, _, _ = _fixture(root)

                def fake_run(argv, **kwargs):
                    kwargs["env"]["_TEST_AUDIO_ROM"] = str(audio)
                    if mode == "timeout":
                        raise subprocess.TimeoutExpired(argv, 60)
                    if mode == "nonzero":
                        return subprocess.CompletedProcess(argv, 2, "", "boot failure")
                    return _capture(argv, kwargs["env"], receipt=False)

                with patch("instrument_analysis.native_rendering.subprocess.run", side_effect=fake_run):
                    with self.assertRaises(RuntimeError):
                        render_gradius3_probe(mame_path=exe, rompath=romdir,
                            audio_rom_path=audio, output_dir=root / "out",
                            source_record_id="source-7", patch_id=patch_num, keycode=66,
                            hold_frames=2, tail_frames=2)
                self.assertEqual(list((root / "out").glob("gradius3-*")), [])

    def test_failed_or_rom_mismatched_receipt_is_rejected(self):
        for mode in ("failed", "bytes", "duration"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                exe, romdir, audio, patch_num, pointer, raw_patch = _fixture(root)

                def fake_run(argv, **kwargs):
                    env = kwargs["env"]
                    env["_TEST_AUDIO_ROM"] = str(audio)
                    if mode == "failed":
                        return _capture(argv, env, status="failed")
                    result = _capture(argv, env)
                    receipt_path = Path(env["GME_G3_RECEIPT"])
                    data = json.loads(receipt_path.read_text())
                    if mode == 'bytes':
                        data["patch_bytes_hex"] = "00" * 25
                    else:
                        data['frame_period_seconds'] = 0.01
                    receipt_path.write_text(json.dumps(data))
                    return result

                with patch("instrument_analysis.native_rendering.subprocess.run", side_effect=fake_run):
                    with self.assertRaises(RuntimeError):
                        render_gradius3_probe(mame_path=exe, rompath=romdir,
                            audio_rom_path=audio, output_dir=root / "out",
                            source_record_id="source-7", patch_id=patch_num, keycode=66,
                            hold_frames=2, tail_frames=2)
                self.assertEqual(list((root / "out").glob("gradius3-*")), [])

    def test_rejects_invalid_inputs_and_existing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exe, romdir, audio, patch_num, _, _ = _fixture(root)
            args = dict(mame_path=exe, rompath=romdir, audio_rom_path=audio,
                output_dir=root / "out", source_record_id="source-7",
                patch_id=patch_num, keycode=66, hold_frames=2, tail_frames=2)
            with self.assertRaises(ValueError):
                render_gradius3_probe(**{**args, "source_record_id": ""})
            with patch("instrument_analysis.native_rendering.subprocess.run") as run:
                with self.assertRaises(ValueError):
                    render_gradius3_probe(**{**args, "keycode": 128})
                run.assert_not_called()
            out = root / "out"
            out.mkdir()
            (out / "gradius3-fm-003-kc-066.wav").write_bytes(b"occupied")
            with self.assertRaises(FileExistsError):
                render_gradius3_probe(**args)


if __name__ == "__main__":
    unittest.main()
