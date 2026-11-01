"""Known sample/source lineage is isolated across evaluation splits."""
from copy import deepcopy
import unittest
import tempfile
import hashlib
from pathlib import Path

from instrument_analysis.origin_splits import validate_origin_splits


def case(name, family, split="development", digest="a"):
    return {"case_id": name, "record_id": "record-" + name, "split": split,
            "sample_sha256": digest * 64, "origin_family_id": family}


def link(left, right):
    return {"left_family_id": left, "right_family_id": right,
            "evidence": {"url": "https://example.com/source", "locator": "row 142", "reason": "Documented shared source variant"}}


class OriginSplitTests(unittest.TestCase):
    def test_same_canonical_record_stays_together_across_case_aliases(self):
        first = case('a', 'source-one')
        second = case('b', 'different-label', 'held_out', 'b')
        second['record_id'] = first['record_id']
        with self.assertRaisesRegex(ValueError, 'connected group'):
            validate_origin_splits([first, second])

    def test_lineage_file_evidence_is_verified(self):
        with tempfile.TemporaryDirectory() as name:
            proof = Path(name) / 'lineage.json'
            proof.write_bytes(b'documented shared source')
            evidence = {'path': str(proof), 'sha256': hashlib.sha256(proof.read_bytes()).hexdigest()}
            relation = {'left_family_id': 'source-one', 'right_family_id': 'source-two', 'evidence': evidence}
            cases = [case('a', 'source-one'), case('b', 'source-two', digest='b')]
            self.assertEqual(validate_origin_splits(cases, lineage_links=[relation])['counts']['groups'], 1)
            proof.write_bytes(b'changed proof')
            with self.assertRaisesRegex(ValueError, 'missing or changed'):
                validate_origin_splits(cases, lineage_links=[relation])

    def test_rejects_equal_hash_or_equal_family_cross_split(self):
        for cases in ([case("a", "flute-one"), case("b", "flute-two", "held_out")],
                      [case("a", "flute-one"), case("b", "flute-one", "held_out", "b")]):
            with self.assertRaisesRegex(ValueError, "connected group"):
                validate_origin_splits(cases)

    def test_transitive_documented_lineage_across_games_and_rereleases(self):
        cases = [case("ff4", "roland-u220-flute1", digest="a"),
                 case("ff6", "roland-lcd1-flute-vsw", digest="b"),
                 case("rerelease", "later-variant", "held_out", "c")]
        links = [link("roland-u220-flute1", "roland-lcd1-flute-vsw"), link("roland-lcd1-flute-vsw", "later-variant")]
        with self.assertRaisesRegex(ValueError, "ff4.*ff6.*rerelease"):
            validate_origin_splits(cases, lineage_links=links)
        cases[-1]["split"] = "development"
        audit = validate_origin_splits(cases, lineage_links=links)
        self.assertEqual(audit["counts"]["groups"], 1)
        self.assertEqual(audit["counts"]["eligible_held_out_cases"], 0)

    def test_two_isolated_groups_and_file_evidence(self):
        cases = [case("a", "first-source"), case("b", "other-source", "held_out", "b")]
        before = deepcopy(cases)
        audit = validate_origin_splits(cases)
        self.assertEqual(audit["counts"]["eligible_held_out_cases"], 1)
        self.assertEqual(audit["counts"]["held_out_groups"], 1)
        self.assertEqual(cases, before)
        cases[-1]["split"] = "development"
        with tempfile.TemporaryDirectory() as name:
            proof = Path(name) / "catalog.json"
            proof.write_bytes(b"source relationship")
            evidence = {"left_family_id": "first-source", "right_family_id": "other-source",
                        "evidence": {"path": str(proof), "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}}
            self.assertEqual(validate_origin_splits(cases, lineage_links=[evidence])["counts"]["groups"], 1)

    def test_rejects_duplicate_cases_invalid_hash_unknown_family_and_link(self):
        with self.assertRaises(ValueError):
            validate_origin_splits([case("a", "source"), case("a", "other", digest="b")])
        for change in ({"sample_sha256": "not-hash"}, {"origin_family_id": None},
                       {"origin_family_id": "unknown"}, {"origin_family_id": "GM Pipe"}):
            item = case("a", "source")
            item.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_origin_splits([item])
        with self.assertRaisesRegex(ValueError, "unknown family"):
            validate_origin_splits([case("a", "source")], lineage_links=[link("source", "absent")])


if __name__ == "__main__":
    unittest.main()
