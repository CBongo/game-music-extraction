"""Contract tests for explicit raw-usage pitch calibration."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from instrument_analysis.calibration import calibrate_usage
from instrument_analysis.schema import make_bundle

try:
    import jsonschema
except ImportError:
    jsonschema = None


def offset(semitones=0, cents=0, basis="measured"):
    return {"semitones": semitones, "residual_cents": cents, "basis": basis,
            "provenance": {"method": "measured source sound",
                           "location": {"path": "sample.wav"}}}


def note(raw=60, **extra):
    return {"raw_sequence_note": raw, "pitch_context_complete": True,
            "provenance": {"method": "raw decoder", "parameters": {"patch_selection": {"method": "raw"}}},
            **extra}


def bundles(notes=None, pitch=None, zones=None, *, platform="Sony PlayStation",
            metadata_platform=None, driver="AKAO", metadata_driver="AKAO PSX"):
    source = {"platform": platform, "game_or_module": "Same Game", "driver": driver}
    meta_source = {**source, "platform": metadata_platform or platform, "driver": metadata_driver}
    usage = make_bundle([{"record_id": "use:1", "source": source,
                          "identity": {"source_instrument_id": 3, "source_patch_command": 3,
                                       "song_local_slot": 3},
                          "observation": {"usage": {"provenance": {"method": "decoder"},
                            "sequences": [{"sequence_id": 1, "raw_sequence_notes": [60],
                                           "note_occurrences": notes if notes is not None else [note()]}]}}}],
                        "test usage")
    observation = {}
    if pitch is not None:
        observation["pitch"] = pitch
    if zones is not None:
        observation["zones"] = zones
    metadata = make_bundle([{"record_id": "meta:1", "source": meta_source,
                             "identity": {"source_instrument_id": "set:3",
                                          "source_patch_command": 3, "song_local_slot": 3},
                             "observation": observation}], "test metadata")
    return usage, metadata


def zone(zid, low, high, *, pitch=None):
    return {"zone_id": zid, "sample_id": zid,
            "key_range": {"minimum": low, "maximum": high},
            "pitch": pitch or {}, "provenance": {"method": "source zone"}}


class CalibrationTests(unittest.TestCase):
    def joined(self, usage, metadata):
        return calibrate_usage(usage, metadata, record_links={"use:1": "meta:1"})

    def occurrence(self, bundle, index=0):
        return bundle["records"][0]["observation"]["usage"]["sequences"][0]["note_occurrences"][index]

    def summary(self, bundle):
        return bundle["records"][0]["observation"]["usage"]

    def test_signed_offsets_transposes_provenance_and_immutable_input(self):
        notes = [note(60, global_transpose_semitones=2, track_transpose_semitones=-1,
                      ramp_transpose_semitones=3),
                 note(64, global_transpose_semitones=-2)]
        usage, metadata = bundles(notes, {"observed_pitch_offset": offset(-5)})
        before_usage, before_meta = deepcopy(usage), deepcopy(metadata)
        result = self.joined(usage, metadata)
        self.assertEqual(59, self.occurrence(result)["sounding_midi_note"])
        self.assertEqual(-5, self.occurrence(result)["sample_tuning_semitones"])
        self.assertEqual(57, self.occurrence(result, 1)["sounding_midi_note"])
        self.assertEqual([57, 59], self.summary(result)["sequences"][0]["sounding_midi_notes"])
        self.assertEqual({"minimum": 57, "maximum": 59}, self.summary(result)["overall_sounding_note_range"])
        provenance = self.occurrence(result)["provenance"]
        self.assertEqual("raw decoder", provenance["method"])
        self.assertIn("patch_selection", provenance["parameters"])
        self.assertEqual(offset(-5)["provenance"],
                         provenance["parameters"]["pitch_calibration"]["offset_provenance"])
        self.assertEqual(before_usage, usage)
        self.assertEqual(before_meta, metadata)
        self.assertIsNot(result, usage)
        self.assert_valid(result)

    def test_positive_offset_and_psx_platform_alias(self):
        usage, metadata = bundles([note()], {"observed_pitch_offset": offset(7)},
                                  platform="psx", metadata_platform="Sony PlayStation")
        self.assertEqual(67, self.occurrence(self.joined(usage, metadata))["sounding_midi_note"])

    def test_snes_platform_alias_requires_matching_driver(self):
        usage, metadata = bundles([note()], {"observed_pitch_offset": offset(1)},
                                  platform="snes", metadata_platform="Super Nintendo",
                                  driver="FF3", metadata_driver="FF3")
        self.assertEqual(61, self.occurrence(self.joined(usage, metadata))["sounding_midi_note"])
        metadata["records"][0]["source"]["driver"] = "SPC700 custom driver"
        with self.assertRaisesRegex(ValueError, "driver"):
            self.joined(usage, metadata)
    def test_unknown_context_and_unity_key_do_not_calibrate(self):
        unity = {"value": 60, "basis": "source_fact", "provenance": {"method": "tone field"}}
        usage, metadata = bundles([note(pitch_context_complete=False), note()],
                                  {"source_unity_key": unity})
        result = self.joined(usage, metadata)
        self.assertNotIn("sounding_midi_note", self.occurrence(result))
        self.assertNotIn("sounding_midi_note", self.occurrence(result, 1))
        self.assertEqual("pitch_context_incomplete",
                         self.occurrence(result)["provenance"]["parameters"]["pitch_calibration"]["reason"])
        self.assertEqual("missing_observed_pitch_offset",
                         self.occurrence(result, 1)["provenance"]["parameters"]["pitch_calibration"]["reason"])
        self.assertNotIn("overall_sounding_note_range", self.summary(result))
        self.assert_valid(result)

    def test_zones_select_at_transposed_key_and_do_not_fallback(self):
        zones = [zone(0, 0, 61, pitch={"observed_pitch_offset": offset(-2)}),
                 zone(1, 62, 127, pitch={"observed_pitch_offset": offset(3)})]
        usage, metadata = bundles([note(60, global_transpose_semitones=2)], zones=zones)
        result = self.joined(usage, metadata)
        self.assertEqual(65, self.occurrence(result)["sounding_midi_note"])
        self.assertEqual(1, self.occurrence(result)["provenance"]["parameters"]["pitch_calibration"]["zone_id"])
        metadata["records"][0]["observation"]["pitch"] = {"observed_pitch_offset": offset(10)}
        metadata["records"][0]["observation"]["zones"][1]["pitch"] = {}
        result = self.joined(usage, metadata)
        self.assertNotIn("sounding_midi_note", self.occurrence(result))
        self.assertEqual("missing_observed_pitch_offset",
                         self.occurrence(result)["provenance"]["parameters"]["pitch_calibration"]["reason"])

    def test_overlap_and_gap_are_diagnostic(self):
        zones = [zone(0, 0, 64, pitch={"observed_pitch_offset": offset(1)}),
                 zone(1, 60, 70, pitch={"observed_pitch_offset": offset(9)})]
        usage, metadata = bundles([note(62)], zones=zones)
        result = self.joined(usage, metadata)
        self.assertNotIn("sounding_midi_note", self.occurrence(result))
        self.assertEqual("ambiguous_zone",
                         self.occurrence(result)["provenance"]["parameters"]["pitch_calibration"]["reason"])
        metadata["records"][0]["observation"]["zones"] = [zone(0, 0, 50, pitch={"observed_pitch_offset": offset(1)})]
        result = self.joined(usage, metadata)
        self.assertEqual("no_matching_zone",
                         self.occurrence(result)["provenance"]["parameters"]["pitch_calibration"]["reason"])

    def test_fractional_cents_remain_occurrences_only(self):
        usage, metadata = bundles([note()], {"observed_pitch_offset": offset(-1, 25)})
        result = self.joined(usage, metadata)
        self.assertAlmostEqual(59.25, self.occurrence(result)["sounding_midi_note"])
        self.assertAlmostEqual(-0.75, self.occurrence(result)["sample_tuning_semitones"])
        self.assertEqual("fractional_sounding_note",
                         self.occurrence(result)["provenance"]["parameters"]["pitch_calibration"]["aggregate_reason"])
        self.assertNotIn("sounding_midi_notes", self.summary(result)["sequences"][0])
        self.assertNotIn("overall_sounding_note_range", self.summary(result))
        self.assert_valid(result)

    def test_out_of_range_is_not_clamped(self):
        usage, metadata = bundles([note(127)], {"observed_pitch_offset": offset(2)})
        result = self.joined(usage, metadata)
        self.assertNotIn("sounding_midi_note", self.occurrence(result))
        self.assertEqual(2, self.occurrence(result)["sample_tuning_semitones"])
        self.assertEqual("out_of_range",
                         self.occurrence(result)["provenance"]["parameters"]["pitch_calibration"]["status"])
        self.assert_valid(result)

    def test_curated_offset_and_mapping_are_excluded(self):
        usage, metadata = bundles([note()], {"observed_pitch_offset": offset(12, basis="curated")})
        metadata["records"][0]["observation"]["existing_mapping"] = {
            "route": {"kind": "gm_program", "program_0based": 1, "program_1based": 2},
            "basis": "curated", "provenance": {"method": "GM lookup"},
            "observed_pitch_offset": offset(12, basis="curated"),
            "legacy_transpose": {"value": 3, "unit": "octave",
            "intent": "audition_octave", "provenance": {"method": "GM lookup"}}}
        result = self.joined(usage, metadata)
        self.assertNotIn("sounding_midi_note", self.occurrence(result))
        self.assertEqual("curated_pitch_offset",
                         self.occurrence(result)["provenance"]["parameters"]["pitch_calibration"]["reason"])
        del metadata["records"][0]["observation"]["pitch"]
        result = self.joined(usage, metadata)
        self.assertNotIn("sounding_midi_note", self.occurrence(result))
        self.assertEqual("missing_observed_pitch_offset",
                         self.occurrence(result)["provenance"]["parameters"]["pitch_calibration"]["reason"])

    def test_bad_links_and_duplicate_ids(self):
        usage, metadata = bundles(pitch={"observed_pitch_offset": offset()})
        for links in ({"missing": "meta:1"}, {"use:1": "absent"}):
            with self.subTest(links=links), self.assertRaises(ValueError):
                calibrate_usage(usage, metadata, record_links=links)
        duplicate = deepcopy(metadata)
        duplicate["records"].append(deepcopy(duplicate["records"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.joined(usage, duplicate)
        duplicate = deepcopy(usage)
        duplicate["records"].append(deepcopy(duplicate["records"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.joined(duplicate, metadata)
        two = deepcopy(usage)
        two["records"].append(deepcopy(two["records"][0]))
        two["records"][1]["record_id"] = "use:2"
        joined = calibrate_usage(two, metadata, record_links={"use:1": "meta:1", "use:2": "meta:1"})
        self.assertEqual(60, self.occurrence(joined)["sounding_midi_note"])
        self.assertEqual(60, joined["records"][1]["observation"]["usage"]
                         ["sequences"][0]["note_occurrences"][0]["sounding_midi_note"])

    def test_subset_links_leave_other_records_untouched(self):
        usage, metadata = bundles(pitch={"observed_pitch_offset": offset(2)})
        second = deepcopy(usage["records"][0])
        second["record_id"] = "use:2"
        second["source"]["game_or_module"] = "Other Game"
        usage["records"].append(second)
        original_second = deepcopy(second)
        original_usage = deepcopy(usage)
        joined = calibrate_usage(usage, metadata, record_links={"use:1": "meta:1"})
        self.assertEqual(62, self.occurrence(joined)["sounding_midi_note"])
        self.assertEqual(original_second, joined["records"][1])
        self.assertIsNot(joined["records"][1], usage["records"][1])
        self.assertEqual(original_usage, usage)
        unlinked = calibrate_usage(usage, metadata, record_links={})
        self.assertEqual(usage, unlinked)
        self.assertIsNot(usage, unlinked)

    def test_zero_confidence_and_conflict_abstain(self):
        for change, reason in (({"confidence": 0}, "zero_confidence_offset"),
                               ({"provenance": {"method": "SNES reference",
                                                "note": "conflict: spread exceeds tolerance"}},
                                "conflicting_pitch_references")):
            measured = offset(2)
            measured.update(change)
            usage, metadata = bundles(pitch={"observed_pitch_offset": measured})
            result = self.joined(usage, metadata)
            self.assertNotIn("sounding_midi_note", self.occurrence(result))
            self.assertEqual(reason, self.occurrence(result)["provenance"]
                             ["parameters"]["pitch_calibration"]["reason"])

    def test_invalid_offset_fields_rejected(self):
        for change in ({"basis": "unknown"}, {"residual_cents": 51},
                       {"confidence": float("nan")}, {"confidence": 1.1}):
            measured = offset()
            measured.update(change)
            usage, metadata = bundles(pitch={"observed_pitch_offset": measured})
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "invalid observed_pitch_offset"):
                self.joined(usage, metadata)

    def test_velocity_split_without_note_velocity_abstains(self):
        high_velocity = zone(1, 0, 127, pitch={"observed_pitch_offset": offset(4)})
        high_velocity["velocity_range"] = {"minimum": 64, "maximum": 127}
        usage, metadata = bundles(zones=[high_velocity])
        result = self.joined(usage, metadata)
        self.assertNotIn("sounding_midi_note", self.occurrence(result))
        self.assertEqual("velocity_context_unknown", self.occurrence(result)["provenance"]
                         ["parameters"]["pitch_calibration"]["reason"])

    def test_source_and_slot_mismatches_rejected(self):
        usage, metadata = bundles(pitch={"observed_pitch_offset": offset()})
        for field, value in (("platform", "Other"), ("game_or_module", "Other"),
                             ("driver", "Other")):
            changed = deepcopy(metadata)
            changed["records"][0]["source"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.joined(usage, changed)
        metadata["records"][0]["identity"]["song_local_slot"] = 4
        with self.assertRaisesRegex(ValueError, "song_local_slot"):
            self.joined(usage, metadata)

    def test_preexisting_conflicts_rejected(self):
        for field, value in (("sounding_midi_note", 70), ("sample_tuning_semitones", 5)):
            usage, metadata = bundles([note(**{field: value})],
                                      {"observed_pitch_offset": offset(2)})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                self.joined(usage, metadata)

    def assert_valid(self, bundle):
        if jsonschema is not None:
            path = Path(__file__).resolve().parents[2] / "schemas/instrument-analysis-v1.schema.json"
            jsonschema.Draft202012Validator(json.loads(path.read_text())).validate(bundle)


if __name__ == "__main__":
    unittest.main()
