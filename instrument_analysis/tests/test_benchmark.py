"""Behavioral tests for the explicit-label benchmark evaluator."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from instrument_analysis.benchmark import evaluate_benchmark


def gm(program, percussion=False):
    return {"kind": "gm_program", "program_0based": program,
            "program_1based": program + 1, "melodic_percussion": percussion}


def candidate(rank, route, offset=None):
    result = {"rank": rank, "route": route}
    if offset is not None:
        result["observed_pitch_offset"] = {
            "semitones": offset[0], "residual_cents": offset[1]}
    return result


def record(record_id, routing=(), *, observed=None, components=None):
    result = {"record_id": record_id, "analysis": {"routing": list(routing)}}
    if observed is not None:
        result["observation"] = {"pitch": {"observed_pitch_offset": {
            "semitones": observed[0], "residual_cents": observed[1]}}}
    if components is not None:
        result["analysis"]["composite"] = {"components": [
            {"semitone_offset": n, "residual_cents": 0} for n in components]}
    return result


def manifest(*cases):
    return {"schema_version": "instrument-benchmark/v1", "cases": list(cases)}


def bundle(*records):
    return {"schema_version": "instrument-analysis/v1", "records": list(records)}


def case(identifier, labels=None, split="test"):
    result = {"case_id": identifier, "record_id": identifier, "split": split}
    if labels is not None:
        result["labels"] = labels
    return result


class BenchmarkTests(unittest.TestCase):
    def test_rank_order_acceptable_routes_and_gm_family(self):
        labels = {"routes": [gm(8), {"kind": "composite", "description": "triad"}],
                  "gm_families": [1]}
        report = evaluate_benchmark(
            manifest(case("a", labels), case("b", {"routes": [gm(3)], "gm_families": [0]})),
            bundle(record("a", [candidate(2, gm(8)), candidate(1, gm(9))]),
                   record("b", [candidate(1, gm(4))])),
            top_k=(1, 2))
        m = report["metrics"]
        self.assertEqual((2, 2), (m["route_accuracy"]["numerator"],
                                  m["route_accuracy"]["denominator"]))
        self.assertEqual((1, 2), (m["top_2_candidate_accuracy"]["numerator"],
                                  m["top_2_candidate_accuracy"]["denominator"]))
        self.assertEqual(2, m["gm_family_accuracy"]["numerator"])

    def test_route_kind_can_be_correct_with_wrong_family_and_exact_patch(self):
        report = evaluate_benchmark(
            manifest(case("a", {"routes": [gm(73)], "gm_families": [9]})),
            bundle(record("a", [candidate(1, gm(1))])),
            top_k=(1,))
        m = report["metrics"]
        self.assertEqual(1, m["route_accuracy"]["value"])
        self.assertEqual(0, m["gm_family_accuracy"]["value"])
        self.assertEqual(0, m["top_1_candidate_accuracy"]["value"])

    def test_annotation_does_not_change_concrete_patch_and_coverage_counts_numeric_records(self):
        report=evaluate_benchmark(
            manifest(case('a', {'routes':[gm(11, True)]}),case('b', {'observed_pitch_offset_semitones':0})),
            bundle(record('a',[candidate(1,gm(11))]),record('b')),
            top_k=(1,))
        self.assertEqual(1,report['metrics']['top_1_candidate_accuracy']['value'])
        self.assertEqual({'cases':2,'predicted_cases':1,'records_present':2},report['coverage'])

    def test_missing_labels_and_missing_predictions_are_not_implicit_negatives(self):
        report = evaluate_benchmark(
            manifest(case("a", {"routes": [gm(1)], "observed_pitch_offset_semitones": 2}),
                     case("b"), case("c", {"abstain": False}, "validation")),
            bundle(record("b", [candidate(1, gm(1))])))
        self.assertEqual({"cases": 3, "predicted_cases": 1, "records_present": 1}, report["coverage"])
        m = report["metrics"]
        self.assertEqual((0, 1), (m["route_accuracy"]["numerator"],
                                  m["route_accuracy"]["denominator"]))
        self.assertEqual(1, m["pitch_mae_semitones"]["eligible_cases"])
        self.assertEqual(0, m["pitch_mae_semitones"]["denominator"])
        self.assertIsNone(m["pitch_mae_semitones"]["value"])
        self.assertEqual(0, m["abstention_accuracy"]["value"])
        self.assertEqual(1, report["by_split"]["validation"]["coverage"]["cases"])

    def test_channel_ten_percussion_is_a_distinct_route(self):
        report = evaluate_benchmark(
            manifest(case("a", {"routes": [gm(10, True)]}),
                     case("b", {"routes": [{"kind": "gm_percussion", "note": 38}]})),
            bundle(record("a", [candidate(1, gm(10))]),
                   record("b", [candidate(1, {"kind": "gm_percussion", "note": 38,
                                             "name": "Snare"})])))
        self.assertEqual(1, report["metrics"]["route_accuracy"]["value"])
        self.assertEqual(1, report["metrics"]["top_1_candidate_accuracy"]["value"])
        swapped = evaluate_benchmark(
            manifest(case('a', {'routes':[gm(38)]}),
                     case('b', {'routes':[{'kind':'gm_percussion','note':38}]})),
            bundle(record('a',[candidate(1,{'kind':'gm_percussion','note':38})]),
                   record('b',[candidate(1,gm(38))])))
        self.assertEqual(0,swapped['metrics']['route_accuracy']['value'])
        self.assertEqual(0,swapped['metrics']['top_1_candidate_accuracy']['value'])

    def test_abstention_and_ood_denominators(self):
        abstain = {"kind": "abstain", "reason": "unknown source"}
        report = evaluate_benchmark(
            manifest(case("a", {"abstain": True}), case("b", {"abstain": True}),
                     case("c", {"abstain": False}), case("d", {"abstain": True})),
            bundle(record("a", [candidate(1, abstain)]),
                   record("b", [candidate(1, gm(1))]),
                   record("c", [candidate(1, abstain)])))
        m = report["metrics"]
        self.assertEqual((1, 4), (m["abstention_accuracy"]["numerator"],
                                  m["abstention_accuracy"]["denominator"]))
        self.assertEqual(.5, m["abstention_precision"]["value"])
        self.assertEqual(1 / 3, m["abstention_recall"]["value"])
        self.assertEqual((1, 2), (m["ood_false_accept_rate"]["numerator"],
                                  m["ood_false_accept_rate"]["denominator"]))
        self.assertEqual(3, m["ood_false_accept_rate"]["eligible_cases"])

    def test_pitch_candidate_precedence_and_exact_chord_voicing(self):
        report = evaluate_benchmark(
            manifest(case("a", {"observed_pitch_offset_semitones": -4.75,
                                  "component_offsets": [-5, 0, 4]}),
                     case("b", {"component_offsets": []})),
            bundle(record("a", [candidate(1, {"kind": "composite"}, (-5, 25))],
                          observed=(-2, 0), components=[-5, 0, 16]),
                   record("b", [candidate(1, {"kind": "composite"})], components=[])))
        m = report["metrics"]
        self.assertEqual(0, m["pitch_mae_semitones"]["value"])
        self.assertAlmostEqual(5 / 6, m["chord_note_precision"]["value"])
        self.assertAlmostEqual(5 / 6, m["chord_note_recall"]["value"])

    def test_empty_chords_and_multiset_matching(self):
        report = evaluate_benchmark(
            manifest(case("a", {"component_offsets": []}),
                     case("b", {"component_offsets": [0, 0, 7]}),
                     case("c", {"component_offsets": [0]})),
            bundle(record("a", components=[0]),
                   record("b", components=[0, 7]),
                   record("c", components=[])))
        m = report["metrics"]
        self.assertAlmostEqual((0 + 1 + 0) / 3, m["chord_note_precision"]["value"])
        self.assertAlmostEqual((0 + 2 / 3 + 0) / 3, m["chord_note_recall"]["value"])

    def test_blinded_reviews_only(self):
        reviews = [
            {"case_id": "a", "blinded": True, "preferred_route": gm(1)},
            {"case_id": "a", "blinded": False, "preferred_route": gm(2)},
        ]
        report = evaluate_benchmark(
            manifest(case("a")), bundle(record("a", [candidate(1, gm(1))])),
            reviews=reviews)
        m = report["metrics"]["human_blinded_preference_agreement"]
        self.assertEqual((1, 1), (m["numerator"], m["denominator"]))

    def test_validation_rejects_duplicates_and_inconsistent_routes(self):
        good = manifest(case("a"))
        for changed in (
            manifest(case("a"), case("a")),
            manifest(case("a"), {"case_id": "b", "record_id": "a", "split": "test"}),
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "duplicate"):
                evaluate_benchmark(changed, bundle())
        for changed in (
            bundle(record("a"), record("a")),
            bundle(record("a", [candidate(1, gm(1)), candidate(1, gm(2))])),
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "duplicate"):
                evaluate_benchmark(good, changed)
        with self.assertRaisesRegex(ValueError, "numbering disagrees"):
            evaluate_benchmark(
                manifest(case("a", {"routes": [gm(1)]})),
                bundle(record("a", [candidate(1, {**gm(1), "program_1based": 5})])))
        with self.assertRaisesRegex(ValueError, "consecutive"):
            evaluate_benchmark(good, bundle(record("a", [candidate(2, gm(1))])))
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            evaluate_benchmark(good, bundle(record("a", [
                candidate(1, {**gm(1), "bogus": True})])))
        for top_k in ((), (0,), (1, 1), (True,)):
            with self.subTest(top_k=top_k), self.assertRaises(ValueError):
                evaluate_benchmark(good, bundle(), top_k=top_k)

    def test_cli_writes_json_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "manifest.json").write_text(json.dumps(manifest(case("a"))), encoding="utf-8")
            (root / "predictions.json").write_text(json.dumps(bundle()), encoding="utf-8")
            output = root / "report.json"
            result = subprocess.run(
                [sys.executable, "-m", "instrument_analysis.benchmark",
                 str(root / "manifest.json"), str(root / "predictions.json"),
                 "--output", str(output)],
                capture_output=True, text=True, check=False)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("instrument-benchmark-report/v1",
                             json.loads(output.read_text(encoding="utf-8"))["schema_version"])


if __name__ == "__main__":
    unittest.main()
