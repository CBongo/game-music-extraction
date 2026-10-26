"""Hash, calibration, route, and gain-invariance tests for GM ranking."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
import wave

from instrument_analysis.ranking import (
    build_gm_reference_index, prepared_reference_index, rank_native_source, rank_wav_source,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def audio(path: Path, *, harmonics=(1.0,), scale=1000, frames=10000):
    pcm = bytearray()
    for i in range(frames):
        t = i / 10000
        decay = 1 if i < 5000 else math.exp(-(i - 5000) / 2500)
        sample = sum(weight * math.sin(2 * math.pi * 261.625565 * (h + 1) * t)
                     for h, weight in enumerate(harmonics))
        value = round(scale * sample * decay)
        pcm += struct.pack("<hh", value, -value)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(10000)
        stream.writeframes(pcm)
    return {"file": path.name, "sha256": sha(path), "sample_rate": 10000,
            "channels": 2, "sample_width_bytes": 2, "frames": frames}


def fixture(root: Path):
    gm_dir = root / "gm"
    gm_dir.mkdir()
    p0 = gm_dir / "p0.wav"
    p0v = gm_dir / "p0v.wav"
    p1 = gm_dir / "p1.wav"
    drum = gm_dir / "drum.wav"
    wav0 = audio(p0, scale=1000)
    wav0v = audio(p0v, scale=600)
    wav1 = audio(p1, harmonics=(.1, 1.0, .7), scale=1000)
    wavd = audio(drum, harmonics=(1.0, .2, .4), scale=1000)
    probes = []
    for pid, p, vel, meta in (
        ("gm-b00000-p000-n060-v096", 0, 96, wav0),
        ("gm-b00000-p000-n060-v080", 0, 80, wav0v),
        ("gm-b00000-p001-n060-v096", 1, 96, wav1),
    ):
        probes.append({
            "probe_id": pid, "kind": "melodic", "channel_0based": 0,
            "bank": 0, "route": {"kind": "gm_program", "program_0based": p,
                                 "program_1based": p + 1},
            "program_0based": p, "program_1based": p + 1,
            "note": 60, "velocity": vel, "preset_name": f"Preset {p}", "wav": meta,
        })
    probes.append({
        "probe_id": "gm-drums-p000-n035-v096", "kind": "percussion",
        "channel_0based": 9, "bank": 128, "route": {"kind": "gm_percussion", "note": 35},
        "program_0based": 0, "program_1based": 1,
        "note": 35, "velocity": 96, "preset_name": "Standard 1", "wav": wavd,
    })
    gm = {
        "schema_version": "instrument-render/v1",
        "conditions": {"dry": True, "reverb": False, "chorus": False,
                       "midi_format": 0, "midi_ticks_per_second": 1000,
                       "duration_seconds": .5, "tail_seconds": .5, "sample_rate": 10000},
        "soundfont": {"path": "fixture.sf2", "sha256": "a" * 64, "preset_count": 3},
        "probes": probes,
    }
    manifest = gm_dir / "manifest.json"
    dump(manifest, gm)
    native_dir = root / "native"
    native_dir.mkdir()
    source_wav = native_dir / "native.wav"
    source_meta = audio(source_wav, scale=2000)
    receipt = {"status": "ok", "note_on_time_seconds": 0,
               "note_off_time_seconds": .5, "capture_end_time_seconds": 1.0}
    dump(native_dir / "native.receipt.json", receipt)
    source = {
        "schema_version": "instrument-native-chip-render/v1",
        "source_record_id": "fixture:native:0",
        "engine": "MAME PlayStation SPU (Konami GV)",
        "key": {"pitch_register_raw_spu": 4096, "midi_note": None},
        "conditions": {
            "spu_reverb_fm_noise": "disabled",
            "note_on_time_seconds": 0, "note_off_time_seconds": .5,
            "capture_end_time_seconds": 1.0,
            "wav_alignment": {"actual_trim_start_seconds": 0},
        },
        "wav": source_meta, "receipt_file": "native.receipt.json",
    }
    source_manifest = native_dir / "native.manifest.json"
    dump(source_manifest, source)
    cal = {
        "schema_version": "instrument-pitch-calibration/v1",
        "basis": "acoustic_measurement", "status": "calibrated",
        "source_record_id": "fixture:native:0",
        "native_manifest_path": str(source_manifest.resolve()),
        "native_manifest_sha256": sha(source_manifest),
        "native_wav_path": str(source_wav.resolve()),
        "native_wav_sha256": sha(source_wav),
        "engine": source["engine"],
        "native_raw_pitch": {"key": "pitch_register_raw_spu", "value": 4096},
        "sounding_midi_note": 60.0, "f0_hz": 261.625565, "confidence": .99,
        "evidence": "fixture acoustic fundamental", "method": "test",
        "window_estimates": [{"f0_hz": 261.6}, {"f0_hz": 261.7}],
    }
    cal_path = root / "calibration.json"
    dump(cal_path, cal)
    return manifest, source_manifest, cal_path, source_wav, p0, drum


class RankingTests(unittest.TestCase):
    def test_index_and_native_rank_gain_invariant_grouped_by_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gm, native, cal, _, _, _ = fixture(root)
            index_path = root / "index.json"
            index = build_gm_reference_index([gm], index_path)
            self.assertEqual(len(index["references"]), 4)
            self.assertEqual(index["references"][0]["features"]["schema_version"],
                             "instrument-audio-features/v1")
            bundle, diagnostics = rank_native_source(
                native, cal, index_path, distance_threshold=.5, margin_threshold=.001)
            self.assertEqual(bundle["records"][0]["analysis"]["routing"][0]["route"],
                             {"kind": "gm_program", "program_0based": 0, "program_1based": 1})
            self.assertEqual(diagnostics["status"], "ranked")
            self.assertEqual(diagnostics["candidates"][0]["distinct_soundfont_count"], 1)
            self.assertEqual(len(diagnostics["candidates"]), 2)
            self.assertEqual(diagnostics["consensus"]["distinct_soundfont_count"], 1)
            self.assertEqual(diagnostics["excluded_counts"]["route"], 1)
            self.assertLess(diagnostics["candidates"][0]["distance"], .02)
            self.assertEqual(bundle["records"][0]["analysis"]["routing"][0]
                             ["candidate_audition_octave_adjustment"], 0)

    def test_prepared_index_reuses_verified_inputs_and_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gm, native, cal, source_wav, _, _ = fixture(root)
            index_path = root / "index.json"
            build_gm_reference_index([gm], index_path)
            with prepared_reference_index(index_path) as prepared:
                first, _ = rank_native_source(native, cal, index_path,
                    prepared_index=prepared, distance_threshold=.5, margin_threshold=.001)
                second, _ = rank_wav_source(source_wav, index_path,
                    record_id="neutral:unpitched", note_on_seconds=0,
                    note_off_seconds=.5, end_seconds=1, prepared_index=prepared)
                self.assertEqual(first["records"][0]["analysis"]["routing"][0]["route"]["kind"], "gm_program")
                self.assertEqual(second["records"][0]["analysis"]["routing"][0]["route"]["kind"], "abstain")
            with self.assertRaisesRegex(ValueError, "closed"):
                rank_native_source(native, cal, index_path, prepared_index=prepared)

    def test_prepared_index_detects_mid_batch_reference_tamper_at_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gm, native, cal, _, p0, _ = fixture(root)
            index_path = root / "index.json"
            build_gm_reference_index([gm], index_path)
            with self.assertRaisesRegex(ValueError, "WAV hash"):
                with prepared_reference_index(index_path) as prepared:
                    rank_native_source(native, cal, index_path, prepared_index=prepared,
                                       distance_threshold=.5, margin_threshold=.001)
                    p0.write_bytes(p0.read_bytes() + b"tamper")

    def test_tamper_wet_and_abstain_without_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gm, native, cal, _, p0, _ = fixture(root)
            index_path = root / "index.json"
            build_gm_reference_index([gm], index_path)
            p0.write_bytes(p0.read_bytes() + b"x")
            with self.assertRaisesRegex(ValueError, "WAV hash"):
                rank_native_source(native, cal, index_path)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gm, _, _, source_wav, _, _ = fixture(root)
            item = json.loads(gm.read_text())
            item["conditions"]["reverb"] = True
            dump(gm, item)
            with self.assertRaisesRegex(ValueError, "dry"):
                build_gm_reference_index([gm], root / "index.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gm, _, _, source_wav, _, _ = fixture(root)
            index_path = root / "index.json"
            build_gm_reference_index([gm], index_path)
            bundle, diag = rank_wav_source(
                source_wav, index_path, record_id="neutral:0001",
                note_on_seconds=0, note_off_seconds=.5, end_seconds=1,
                calibration=None)
            self.assertEqual(diag["status"], "abstain")
            self.assertEqual(bundle["records"][0]["analysis"]["routing"][0]["route"]["kind"], "abstain")

    def test_neutral_query_hash_pitch_and_percussion_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gm, _, _, source_wav, _, drum = fixture(root)
            index_path = root / "index.json"
            build_gm_reference_index([gm], index_path)
            calibration = {
                "schema_version": "instrument-query-pitch-calibration/v1",
                "basis": "verified_driver_reference", "status": "calibrated",
                "wav_sha256": sha(source_wav), "wav_path": str(source_wav.resolve()),
                "event_boundaries_seconds": {"note_on": 0, "note_off": .5, "end": 1},
                "source_record_id": "neutral:0001",
                "sounding_midi_note": 60, "confidence": 1,
                "evidence": "verified fixture MIDI render recipe",
                "provenance": {"method": "fixture render MIDI identity",
                               "locator": "probe gm-b00000-p000-n060-v096",
                               "source_file_path": str(gm.resolve()),
                               "source_file_sha256": sha(gm)},
            }
            bundle, diag = rank_wav_source(
                source_wav, index_path, record_id="neutral:0001",
                note_on_seconds=0, note_off_seconds=.5, end_seconds=1,
                calibration=calibration, distance_threshold=.5, margin_threshold=.001)
            self.assertEqual(diag["status"], "ranked")
            self.assertEqual(bundle["records"][0]["analysis"]["routing"][0]["route"]["kind"], "gm_program")
            calibration["wav_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "hash-bound"):
                rank_wav_source(source_wav, index_path, record_id="neutral:0001",
                                note_on_seconds=0, note_off_seconds=.5, end_seconds=1,
                                calibration=calibration)
            calibration["wav_sha256"] = sha(drum)
            calibration["wav_path"] = str(drum.resolve())
            facts = {
                "source": {"platform": "synthetic", "game_or_module": "query", "driver": "known drum"},
                "identity": {"source_instrument_id": "neutral:drum"},
                "observation": {},
                "source_record_id": "neutral:drum", "wav_sha256": sha(drum),
                "classification": {"kind": "gm_percussion_trigger", "basis": "source_fact",
                                   "gm_channel_0based": 9,
                                   "evidence": "known channel-10 route",
                                   "provenance": {"method": "verified render recipe"}},
            }
            calibration["source_record_id"] = "neutral:drum"
            bundle, diag = rank_wav_source(
                drum, index_path, record_id="neutral:drum",
                note_on_seconds=0, note_off_seconds=.5, end_seconds=1,
                calibration=calibration, source_facts=facts,
                distance_threshold=.5, margin_threshold=.001)
            self.assertEqual(bundle["records"][0]["analysis"]["routing"][0]["route"],
                             {"kind": "gm_percussion", "note": 35})
            self.assertEqual(diag["excluded_counts"]["route"], 3)

    def test_source_fact_handoff_and_bank_variant_grouping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gm, native, cal, source_wav, _, _ = fixture(root)
            item = json.loads(gm.read_text())
            variant = dict(item["probes"][0])
            variant["probe_id"] = "gm-b00008-p000-n060-v096"
            variant["bank"] = 8
            item["probes"].append(variant)
            dump(gm, item)
            index_path = root / "index.json"
            build_gm_reference_index([gm], index_path)
            _, diag = rank_native_source(native, cal, index_path,
                                         distance_threshold=.5, margin_threshold=.001)
            self.assertEqual(len(diag["candidates"]), 2)
            self.assertEqual(diag["candidates"][0]["variant_count"], 2)
            self.assertEqual(diag["candidates"][0]["distinct_soundfont_count"], 1)
            facts = {
                "source_record_id": "neutral:sfx", "wav_sha256": sha(source_wav),
                "classification": {"kind": "sfx", "basis": "source_fact",
                    "evidence": "driver trigger is an effect", "provenance": {"method": "source opcode"}},
            }
            bundle, status = rank_wav_source(
                source_wav, index_path, record_id="neutral:sfx",
                note_on_seconds=0, note_off_seconds=.5, end_seconds=1,
                source_facts=facts)
            self.assertEqual(status["status"], "handoff")
            self.assertEqual(bundle["records"][0]["analysis"]["routing"][0]["route"]["kind"], "sfx")
            facts["classification"]["basis"] = "curated"
            with self.assertRaisesRegex(ValueError, "classification"):
                rank_wav_source(source_wav, index_path, record_id="neutral:sfx",
                                note_on_seconds=0, note_off_seconds=.5,
                                end_seconds=1, source_facts=facts)

    def test_native_grounded_source_facts_are_hash_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gm, native, cal, source_wav, _, _ = fixture(root)
            index_path = root / "index.json"
            build_gm_reference_index([gm], index_path)
            facts_path = root / "facts.json"
            facts = {
                "source_record_id": "fixture:native:0",
                "native_manifest_sha256": sha(native),
                "wav_sha256": sha(source_wav),
                "classification": {"kind": "composite", "basis": "source_fact",
                    "evidence": "explicit layered source command",
                    "provenance": {"method": "driver command inspection",
                                   "source_file_path": str(source_wav.resolve()),
                                   "source_file_sha256": sha(source_wav)}},
            }
            dump(facts_path, facts)
            bundle, diag = rank_native_source(native, cal, index_path,
                                               source_facts_path=facts_path)
            self.assertEqual(diag["status"], "handoff")
            self.assertEqual(bundle["records"][0]["analysis"]["routing"][0]["route"]["kind"], "composite")
            self.assertEqual(diag["query"]["source_facts"]["sha256"], sha(facts_path))
            facts["wav_sha256"] = "0" * 64
            dump(facts_path, facts)
            with self.assertRaisesRegex(ValueError, "hash-bound"):
                rank_native_source(native, cal, index_path, source_facts_path=facts_path)

    def test_usage_coverage_is_separate_and_mapping_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gm, native, cal, _, _, _ = fixture(root)
            index_path = root / "index.json"
            build_gm_reference_index([gm], index_path)
            original = json.loads(native.read_text())
            record_id = original["source_record_id"]
            canonical = {"schema_version": "instrument-analysis/v1", "records": [{
                "record_id": record_id,
                "source": {"platform": "Sony PlayStation", "game_or_module": "fixture", "driver": "AKAO PSX"},
                "identity": {"source_instrument_id": 12},
                "observation": {
                    "usage": {"provenance": {"method": "fixture driver state"}, "sequences": [{"sequence_id": 1, "note_occurrences": [
                        {"raw_sequence_note":60,"sounding_midi_note":60,"pitch_context_complete":True},
                        {"raw_sequence_note":60,"sounding_midi_note":60,"pitch_context_complete":True}]}]},
                    "existing_mapping": {"route": {"kind": "gm_program", "program_0based": 127,
                                                   "program_1based": 128},
                                         "basis": "curated", "provenance": {"method": "legacy"}},
                },
            }]}
            canonical_path = root / "canonical.json"
            dump(canonical_path, canonical)
            bundle, diag = rank_native_source(native, cal, index_path,
                                               canonical_record_path=canonical_path,
                                               distance_threshold=.5, margin_threshold=.001)
            self.assertEqual(diag["functional"]["status"], "coverage_only")
            self.assertEqual(diag["functional"]["blend_weight"], 0)
            self.assertEqual(diag["functional"]["scores"][0]["indexed_probe_note_coverage"], 1)
            self.assertNotIn("existing_mapping", bundle["records"][0]["observation"])
            for note in canonical['records'][0]['observation']['usage']['sequences'][0]['note_occurrences']:
                note['pitch_context_complete']=False
            dump(canonical_path,canonical)
            _, unknown=rank_native_source(native,cal,index_path,canonical_record_path=canonical_path)
            self.assertEqual(unknown['functional']['status'],'not_scored')
            self.assertNotEqual(bundle["records"][0]["analysis"]["routing"][0]["route"].get("program_0based"), 127)

    def test_index_cannot_relabel_pitch_or_bank_after_building(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gm,native,cal,*_ = fixture(root)
            path=root/'index.json';original=build_gm_reference_index([gm],path)
            for field,value in [('note',61),('velocity',1),('bank',2),('preset_name','relabeled')]:
                with self.subTest(field=field):
                    tampered=json.loads(json.dumps(original));tampered['references'][0][field]=value
                    dump(path,tampered)
                    with self.assertRaisesRegex(ValueError,'identity changed'):
                        rank_native_source(native,cal,path)

    def test_missing_wav_and_curated_mapping_do_not_create_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gm, _, _, _, _, _ = fixture(root)
            index_path = root / "index.json"
            build_gm_reference_index([gm], index_path)
            bundle, diag = rank_wav_source(
                root / "neutral-missing.wav", index_path, record_id="neutral:missing",
                note_on_seconds=0, note_off_seconds=.5, end_seconds=1)
            self.assertEqual(diag["status"], "abstain")
            self.assertEqual(bundle["records"][0]["analysis"]["routing"][0]["route"]["kind"], "abstain")


if __name__ == "__main__":
    unittest.main()
