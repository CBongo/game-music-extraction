"""Matched comparison proof and rejection tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import wave

from instrument_analysis.comparisons import build_matched_comparison


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _wav(path: Path, frames: int, value: int = 1000) -> dict:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(10000)
        stream.writeframes(struct.pack("<hh", value, -value) * frames)
    return {"file": path.name, "sha256": _sha(path), "sample_rate": 10000,
            "channels": 2, "sample_width_bytes": 2, "frames": frames}


def _fixture(root: Path) -> tuple[Path, Path, Path, str]:
    source = root / "native"
    gm_dir = root / "gm"
    source.mkdir()
    gm_dir.mkdir()
    native_wav = source / "chip.wav"
    gm_wav = gm_dir / "gm.wav"
    native_wav_meta = _wav(native_wav, 500)
    gm_wav_meta = _wav(gm_wav, 500, 2000)
    native_receipt = {
        "status": "ok", "note_on_time_seconds": .01,
        "note_off_time_seconds": .03, "capture_end_time_seconds": .05,
        "frame_period_seconds": .01,
    }
    _json(source / "chip.receipt.json", native_receipt)
    native = {
        "schema_version": "instrument-native-chip-render/v1",
        "source_record_id": "source:12", "engine": "MAME PlayStation SPU (Konami GV)",
        "key": {"pitch_register_raw_spu": 4096, "midi_note": None, "velocity": None},
        "conditions": {
            "spu_reverb_fm_noise": "disabled",
            "note_on_time_seconds": .01, "note_off_time_seconds": .03,
            "capture_end_time_seconds": .05, "frame_period_seconds": .01,
            "wav_alignment": {"actual_trim_start_seconds": 0,
                              "actual_trim_end_seconds": .05,
                              "alignment_uncertainty_seconds": .01,
                              "lead_in_frames": 100, "clock_origin_verified": False},
        },
        "receipt_file": "chip.receipt.json", "wav": native_wav_meta,
    }
    native_path = source / "chip.manifest.json"
    _json(native_path, native)
    probe_id = "gm-b00000-p000-n060-v096"
    gm = {
        "schema_version": "instrument-render/v1",
        "conditions": {"dry": True, "reverb": False, "chorus": False,
                       "duration_seconds": .02, "tail_seconds": .02, "sample_rate": 10000,
                       "midi_format": 0, "midi_ticks_per_second": 1000},
        "soundfont": {"path": "source.sf2", "sha256": "a" * 64, "preset_count": 128},
        "probes": [{"probe_id": probe_id, "kind": "melodic",
                    "route": {"kind": "gm_program", "program_0based": 0, "program_1based": 1},
                    "note": 60, "bank": 0, "preset_name": "Piano",
                    "velocity": 96, "wav": gm_wav_meta}],
    }
    gm_path = gm_dir / "manifest.json"
    _json(gm_path, gm)
    calibration = {
        "schema_version": "instrument-pitch-calibration/v1",
        "basis": "acoustic_measurement", "status": "calibrated",
        "source_record_id": "source:12",
        "native_manifest_path": str(native_path.resolve()),
        "native_manifest_sha256": _sha(native_path),
        "native_wav_path": str(native_wav.resolve()),
        "native_wav_sha256": _sha(native_wav),
        "engine": native["engine"],
        "native_raw_pitch": {"key": "pitch_register_raw_spu", "value": 4096},
        "sounding_midi_note": 60.05, "f0_hz": 262.38,
        "confidence": .9, "evidence": "stable acoustic fundamental",
        "method": "autocorrelation",
        "window_estimates": [{"f0_hz": 262.3}, {"f0_hz": 262.4}],
    }
    calibration_path = root / "pitch.json"
    _json(calibration_path, calibration)
    return native_path, gm_path, calibration_path, probe_id


def _request(root: Path, native: Path, gm: Path, calibration: Path, probe_id: str) -> dict:
    return dict(native_manifest_path=native, gm_manifest_path=gm,
                pitch_calibration_path=calibration, gm_probe_id=probe_id,
                output_dir=root / "comparison", source_gain=.5, gm_gain=.25,
                gap_seconds=.01)


class ComparisonTests(unittest.TestCase):
    def test_matched_excerpts_are_raw_and_preview_gain_is_scalar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = _request(root, *_fixture(root))
            manifest = build_matched_comparison(**args)
            self.assertEqual(manifest["schema_version"], "instrument-comparison/v1")
            self.assertEqual(manifest["pitch"]["calibrated_sounding_midi_note"], 60.05)
            self.assertEqual(manifest["pitch"]["gm_note"], 60)
            self.assertAlmostEqual(manifest["pitch"]["residual_cents"], 5)
            phase = manifest["conditions"]["phase_alignment"]
            self.assertEqual((phase["matched_hold_frames"], phase["matched_tail_frames"]), (200, 200))
            self.assertEqual(phase["native_uncertainty_frames"], 100)
            files = {k: root / "comparison" / v["file"] for k, v in manifest["outputs"].items()}
            for key, path in files.items():
                self.assertEqual(_sha(path), manifest["outputs"][key]["sha256"])
            with wave.open(str(files["native_excerpt"]), "rb") as stream:
                self.assertEqual(stream.getnframes(), 400)
                self.assertEqual(struct.unpack("<hh", stream.readframes(1)), (1000, -1000))
            with wave.open(str(files["gm_excerpt"]), "rb") as stream:
                self.assertEqual(struct.unpack("<hh", stream.readframes(1)), (2000, -2000))
            with wave.open(str(files["ab_preview"]), "rb") as stream:
                self.assertEqual(stream.getnframes(), 900)
                self.assertEqual(struct.unpack("<hh", stream.readframes(1)), (500, -500))
                stream.setpos(500)
                self.assertEqual(struct.unpack("<hh", stream.readframes(1)), (500, -500))
            with self.assertRaises(FileExistsError):
                build_matched_comparison(**args)

    def test_existing_mono_fm_manifest_can_be_compared_without_resampling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = _request(root, *_fixture(root))
            path=args['native_manifest_path']; native=json.loads(path.read_text())
            wav=path.parent/native['wav']['file']
            with wave.open(str(wav),'wb') as stream:
                stream.setparams((1,2,10000,0,'NONE','not compressed'))
                stream.writeframes(struct.pack('<h',1234)*500)
            native['schema_version']='instrument-native-render/v1'
            native['engine']='MAME YM2151'
            native['key']={'keycode_raw_ym2151':66,'midi_note':None}
            native['conditions']['global_lfo_and_noise']='disabled for isolated patch probe'
            native['wav']['channels']=1; native['wav']['sha256']=_sha(wav)
            _json(path,native)
            calibration=json.loads(args['pitch_calibration_path'].read_text())
            calibration.update(native_manifest_sha256=_sha(path),native_wav_sha256=_sha(wav),
                engine=native['engine'],native_raw_pitch={'key':'keycode_raw_ym2151','value':66})
            _json(args['pitch_calibration_path'],calibration)
            result=build_matched_comparison(**args)
            with wave.open(str(root/'comparison'/result['outputs']['native_excerpt']['file']),'rb') as stream:
                self.assertEqual(stream.getframerate(),10000)
                self.assertEqual(struct.unpack('<hh',stream.readframes(1)),(1234,1234))

    def test_hash_or_pitch_identity_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = _request(root, *_fixture(root))
            native = args["native_manifest_path"]
            native_wav = native.parent / "chip.wav"
            native_wav.write_bytes(native_wav.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "WAV hash"):
                build_matched_comparison(**args)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = _request(root, *_fixture(root))
            calibration = args["pitch_calibration_path"]
            item = json.loads(calibration.read_text())
            item["native_raw_pitch"]["value"] = 123
            _json(calibration, item)
            with self.assertRaisesRegex(ValueError, "raw_pitch"):
                build_matched_comparison(**args)

    def test_unknown_pitch_abstain_low_confidence_and_distant_note_rejected(self):
        for change, message in (
            ({"status": "abstain"}, "abstained"),
            ({"basis": "raw_unity"}, "evidence"),
            ({"confidence": .79}, "confidence"),
            ({"sounding_midi_note": 61.0}, "disagrees with measured F0"),
        ):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                args = _request(root, *_fixture(root))
                calibration = args["pitch_calibration_path"]
                item = json.loads(calibration.read_text())
                item.update(change)
                _json(calibration, item)
                with self.assertRaisesRegex(ValueError, message):
                    build_matched_comparison(**args)

    def test_residual_pitch_and_preview_clipping_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = _request(root, *_fixture(root))
            gm_path = args["gm_manifest_path"]
            gm = json.loads(gm_path.read_text())
            gm["probes"][0]["note"] = 61
            _json(gm_path, gm)
            with self.assertRaisesRegex(ValueError, "differs from GM note"):
                build_matched_comparison(**args)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = _request(root, *_fixture(root))
            args["source_gain"] = 4.0
            native_wav = args["native_manifest_path"].parent / "chip.wav"
            native = json.loads(args["native_manifest_path"].read_text())
            native["wav"] = _wav(native_wav, 500, 12000)
            _json(args["native_manifest_path"], native)
            calibration = json.loads(args["pitch_calibration_path"].read_text())
            calibration["native_manifest_sha256"] = _sha(args["native_manifest_path"])
            calibration["native_wav_sha256"] = _sha(native_wav)
            _json(args["pitch_calibration_path"], calibration)
            with self.assertRaisesRegex(ValueError, "clip"):
                build_matched_comparison(**args)
            self.assertEqual(list((root / "comparison").glob("*.comparison.json")), [])

    def test_wet_reference_and_missing_timing_window_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = _request(root, *_fixture(root))
            gm = args["gm_manifest_path"]
            item = json.loads(gm.read_text())
            item["conditions"]["reverb"] = True
            _json(gm, item)
            with self.assertRaisesRegex(ValueError, "dry"):
                build_matched_comparison(**args)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = _request(root, *_fixture(root))
            native = args["native_manifest_path"]
            item = json.loads(native.read_text())
            item["conditions"]["wav_alignment"].pop("lead_in_frames")
            _json(native, item)
            with self.assertRaisesRegex(ValueError, "calibration native_manifest_sha256"):
                build_matched_comparison(**args)
            cal = args["pitch_calibration_path"]
            record = json.loads(cal.read_text())
            record["native_manifest_sha256"] = _sha(native)
            _json(cal, record)
            with self.assertRaisesRegex(ValueError, "lead-in"):
                build_matched_comparison(**args)


if __name__ == "__main__":
    unittest.main()
