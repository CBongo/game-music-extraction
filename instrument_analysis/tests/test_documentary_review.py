"""Combined branch reviews preserve independent ranks and documentary evidence."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from instrument_analysis.documentary_review import build_documentary_review
from instrument_analysis.documentary_shortlist import _rule


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def ref(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class DocumentaryReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sid = "source-test"
        self.catalog = self.root / "catalog.json"
        write(self.catalog, {})
        self.rom, self.brr = self.root / "rom", self.root / "brr"
        self.rom.write_bytes(b"rom"); self.brr.write_bytes(b"brr")
        self.binding = {"record_id": self.sid, "lead_key": "flute-test", "status": "bound",
            "origin": {"authority": "preferred_documentary", "external_report_state": "reported",
                       "claim": {"raw_cells": ["Flute"]}},
            "sample": {"rom": ref(self.rom), "brr": {"sha256": ref(self.brr)["sha256"]}},
            "artifact": {"file": "flute-test/sample.brr", "sha256": ref(self.brr)["sha256"]}}
        self.bindings = self.root / "bindings.json"
        write(self.bindings, {"bindings": [self.binding]})
        write(self.root / "flute-test/binding.json", self.binding)
        record = {"record_id": self.sid, "observation": {"synthesis": {"sample": {"sha256": {"value": ref(self.brr)["sha256"]}}}}}
        write(self.root / "flute-test/record.json", record)
        write(self.root / "records.json", {"records": [record]})
        self.native, self.cal = self.root / "native.json", self.root / "cal.json"
        write(self.native, {"source_record_id": self.sid})
        write(self.cal, {"source_record_id": self.sid})
        self.gm = self.root / "gm.json"; write(self.gm, {})
        self.index_path = self.root / "index.json"; write(self.index_path, {})
        self.refs = [{"probe_id": f"p{p}", "soundfont_sha256": "f" * 64,
            "manifest_sha256": ref(self.gm)["sha256"], "bank": 0,
            "note": 60, "conditions": {"hold_seconds": .5, "tail_seconds": .5},
            "features": {"valid": True},
            "route": {"kind": "gm_program", "program_0based": p, "program_1based": p + 1}}
            for p in (73, 80, 81, 72, 74, 75)]
        self.index = {"references": self.refs, "manifests": [ref(self.gm)]}
        self.baseline = {"schema_version": "instrument-ranking-diagnostics/v1", "status": "abstain",
            "query": {"source_record_id": self.sid, "native_manifest": ref(self.native), "pitch_calibration": ref(self.cal),
                      "event_boundaries_seconds": {"note_on": 0, "note_off": .5, "end": 1}},
            "functional": {"status": "not_scored"},
            "candidates": [{**deepcopy(r), "rank": i + 1, "distance": .1 * (i + 1)} for i, r in enumerate(self.refs)]}
        self.audio = self.root / "audio.json"; write(self.audio, self.baseline)
        self.gate = {"status": "eligible", "calibrated_midi_note": 60}
        self.shortlist = {"schema_version": "instrument-documentary-shortlist/v1", "source_record_id": self.sid,
            "inputs": {"bindings": ref(self.bindings), "catalog": ref(self.catalog), "index": ref(self.index_path),
                       "source_rom": ref(self.rom), "source_brr": ref(self.brr), "audio_diagnostics": ref(self.audio)},
            "origin": deepcopy(self.binding["origin"]), "rule": _rule(self.binding), "audition_gate": self.gate,
            "candidates": [{"route": self.refs[i]["route"], "reference_variants": [self.refs[i]], "documentary_priority": 1 if i == 0 else 2} for i in (0, 3, 4, 5)]}
        self.path = self.root / "shortlist.json"; write(self.path, self.shortlist)

    def build(self, imported=False):
        @contextmanager
        def prepared(path):
            yield object()
        def comparison(native, gm, *, output_dir, **kwargs):
            write(Path(output_dir) / "candidate.comparison.json", {"source_record_id": self.sid})
        def review(spec_path, output):
            spec = json.loads(Path(spec_path).read_text())
            self.assertFalse(spec["blind"])
            value = {"package_id": "a" * 64}
            write(Path(output) / "manifest.json", value)
            return value
        origin_target = ("instrument_analysis.imported_bindings.validate_imported_binding" if imported
                         else "instrument_analysis.documentary_review._origin")
        with patch(origin_target, return_value=(self.binding, self.catalog)), \
             patch("instrument_analysis.documentary_review._baseline", return_value=(self.baseline, self.gate)), \
             patch("instrument_analysis.documentary_review.prepared_reference_index", side_effect=prepared), \
             patch("instrument_analysis.documentary_review._index_for_query", return_value=self.index), \
             patch("instrument_analysis.documentary_review.build_matched_comparison", side_effect=comparison) as compare, \
             patch("instrument_analysis.documentary_review.build_review_package", side_effect=review) as package:
            result = build_documentary_review(self.path, output_dir=self.root / "review-output")
            return result, compare.call_count, package.call_count

    def test_imported_binding_review_revalidates_generic_origin(self):
        write(self.bindings, {"schema_version": "instrument-imported-origin-bindings/v1", "bindings": [self.binding]})
        self.shortlist["inputs"]["bindings"] = ref(self.bindings)
        write(self.path, self.shortlist)
        result, comparisons, packages = self.build(imported=True)
        self.assertEqual((result["candidate_count"], comparisons, packages), (6, 6, 1))
        report = json.loads((self.root / "review-output/report.json").read_text())
        self.assertEqual(report["inputs"]["catalog"], ref(self.catalog))
        self.assertEqual(report["origin"], self.binding["origin"])
        self.assertTrue(report["no_fused_winner"])
        self.assertEqual(report["human_label_count"], 0)

    def test_union_deduplicates_and_preserves_abstention_and_labels(self):
        other_phase = deepcopy(self.refs[0])
        other_phase["conditions"] = {"hold_seconds": .92, "tail_seconds": .08}
        self.index["references"] = self.refs + [other_phase]
        result, comparisons, packages = self.build()
        self.assertEqual((result["candidate_count"], comparisons, packages), (6, 6, 1))
        report = json.loads((self.root / "review-output/report.json").read_text())
        row = next(c for c in report["candidates"] if c["route"]["program_0based"] == 73)
        self.assertEqual(row["evidence"]["membership"], ["documentary", "audio_only"])
        self.assertEqual(row["evidence"]["documentary_priority"], 1)
        self.assertEqual(row["evidence"]["acoustic"]["rank"], 1)
        self.assertEqual(report["independent_audio_baseline"]["status"], "abstain")
        self.assertEqual(report["human_label_count"], 0)
        self.assertTrue(report["no_fused_winner"])
        with self.assertRaises(FileExistsError):
            self.build()

    def test_metadata_gate_does_not_build_audio(self):
        self.gate = {"status": "metadata_only", "reason": "Unknown acoustic pitch"}
        self.shortlist["audition_gate"] = self.gate
        write(self.path, self.shortlist)
        result, comparisons, packages = self.build()
        self.assertEqual(result["status"], "metadata_only")
        self.assertEqual((comparisons, packages), (0, 0))
        self.assertIsNone(result["review"])

    def test_tamper_and_source_mismatch_rejected(self):
        self.rom.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "stale/missing"):
            self.build()
        self.rom.write_bytes(b"rom")
        self.baseline["query"]["source_record_id"] = "wrong"
        with self.assertRaisesRegex(ValueError, "baseline source mismatch"):
            self.build()
        self.assertFalse((self.root / "review-output").exists())

    def test_changed_documentary_rule_or_reference_rejected(self):
        self.shortlist["candidates"][0]["reference_variants"][0]["bank"] = 3
        write(self.path, self.shortlist)
        with self.assertRaises(ValueError):
            self.build()


if __name__ == "__main__":
    unittest.main()
