import math
import struct
import unittest

from instrument_analysis import (
    SnesPitchReference,
    adapt_akao_v31_sample_set,
    adapt_snes_brr_instrument,
    decode_akao_v31_fine_tune,
    decode_snes_pitch_table,
    make_bundle,
    validate_semantics,
)


class AkaoAdapterTests(unittest.TestCase):
    def test_decodes_v31_header_articulations_and_raw_envelopes(self):
        header = struct.pack(
            "<4sHH6I", b"AKAO", 0x1234, 6, 8, 12, 0x10000, 0x2000, 42, 2
        ).ljust(0x40, b"\0")
        positive = struct.pack("<IIhHHH", 0x100, 0x180, 0x1000, 60, 0x9FAF, 0x5FFF)
        negative = struct.pack("<IIhHHH", 0x200, 0x240, -0x1000, 72, 0x8F00, 0x4000)
        pointers = [0xFFFF] * 16
        pointers[3] = 8
        instrument_data = struct.pack("<16H", *pointers) + bytes(8)
        instrument_data += bytes([42, 0, 71, 46, 127, 3, 12, 127])
        instrument_data += bytes([43, 72, 0xB3, 45, 96, 7, 10, 100])
        instrument_data += bytes(8)

        records = adapt_akao_v31_sample_set(
            header + positive + negative,
            instrument_data=instrument_data,
            game_or_module="Test AKAO game",
            source_path="test.akao",
            instrument_source_path="test.instrument",
            format_version="3.1",
            sample_base_offset=0x2000,
            instrument_base_offset=0xE10,
        )

        self.assertEqual(
            ["sample-set-1234:slot-3"],
            [r["identity"]["source_instrument_id"] for r in records],
        )
        self.assertEqual(3, records[0]["identity"]["song_local_slot"])
        self.assertEqual(2, len(records[0]["observation"]["zones"]))
        self.assertEqual(
            0x9FAF,
            records[0]["observation"]["zones"][0]["envelopes"][0]["raw"]["articulation_adsr1"],
        )
        self.assertEqual({"minimum": 72, "maximum": 127}, records[0]["observation"]["zones"][1]["key_range"])
        second_raw = records[0]["observation"]["zones"][1]["envelopes"][0]["raw"]
        self.assertEqual(0xB3, second_raw["raw_key_high"])
        self.assertEqual(0xE10 + 0x28, records[0]["observation"]["zones"][0]["provenance"]["location"]["offset"])
        self.assertIn("test.akao:set-1234", records[0]["identity"]["sample_ids"][0])

    def test_fine_tune_matches_fixed_point_equation(self):
        unity, residual, total = decode_akao_v31_fine_tune(0x1000, 60)
        self.assertEqual(58, unity)
        self.assertEqual(3, residual)
        self.assertAlmostEqual(203.91, total, places=2)

    def test_rejects_truncated_and_invalid_articulations(self):
        header = struct.pack("<4sHH6I", b"AKAO", 1, 0, 0, 0, 0, 0, 0, 1).ljust(0x40, b"\0")
        with self.assertRaisesRegex(ValueError, "declares 1 articulations"):
            adapt_akao_v31_sample_set(
                header, instrument_data=bytes(32), game_or_module="x",
                source_path="x", instrument_source_path="i", format_version="3.1",
            )
        backwards_loop = struct.pack("<IIhHHH", 0x200, 0x100, 0, 60, 0, 0)
        with self.assertRaisesRegex(ValueError, "precedes"):
            adapt_akao_v31_sample_set(
                header + backwards_loop, instrument_data=bytes(32), game_or_module="x",
                source_path="x", instrument_source_path="i", format_version="3.1",
            )

    def test_requires_explicit_supported_format_version(self):
        with self.assertRaisesRegex(ValueError, "explicitly confirm"):
            adapt_akao_v31_sample_set(
                bytes(64), instrument_data=bytes(32), game_or_module="x",
                source_path="x", instrument_source_path="i", format_version="3.0",
            )


class SnesAdapterTests(unittest.TestCase):
    def test_decodes_verified_ff2_pitch_table(self):
        values = [
            0x0879, 0x08FA, 0x0983, 0x0A14, 0x0AAD, 0x0B50, 0x0BFC,
            0x0CB2, 0x0D74, 0x0E41, 0x0F1A, 0x1000, 0x10F3,
        ]
        refs = decode_snes_pitch_table(
            struct.pack("<13H", *values), offset=0, count=13,
            reference_index=11, reference_note=60,
            method="FF2 pitch table at SNES 04/96FE",
        )
        self.assertEqual(0x1000, refs[11].register_value)
        self.assertEqual(60, refs[11].raw_note)
        self.assertAlmostEqual(12, 12 * math.log2(values[-1] / values[0]), delta=0.02)
        self.assertIn("04/96FE", refs[0].method)

    def test_multiple_octave_references_cross_check_pitch(self):
        record = adapt_snes_brr_instrument(
            game_or_module="Test SNES game",
            instrument_id=12,
            sample_id=12,
            source_path="driver-pitch-table.bin",
            references=[
                SnesPitchReference(0x10C3, 60, 60),
                SnesPitchReference(0x2186, 72, 72),
            ],
        )
        pitch = record["observation"]["pitch"]
        expected_native = 60 - 12 * math.log2(0x10C3 / 0x1000)
        expected_key = math.floor(expected_native + 0.5)
        self.assertEqual(expected_key, pitch["source_unity_key"]["value"])
        self.assertAlmostEqual(
            (expected_native - expected_key) * 100,
            pitch["source_fine_tune_cents"]["value"],
        )
        self.assertEqual(0, pitch["observed_pitch_offset"]["semitones"])
        self.assertAlmostEqual(0, pitch["observed_pitch_offset"]["residual_cents"])
        self.assertEqual(1, pitch["source_unity_key"]["confidence"])

    def test_conflicting_references_are_visible_and_reduce_confidence(self):
        record = adapt_snes_brr_instrument(
            game_or_module="Test SNES game",
            instrument_id=1,
            sample_id=1,
            source_path="table.bin",
            references=[
                SnesPitchReference(0x1000, 60, 60),
                SnesPitchReference(0x2000, 72, 72.5, basis="measured"),
            ],
        )
        unity = record["observation"]["pitch"]["source_unity_key"]
        self.assertLess(unity["confidence"], 1)
        self.assertIn("conflict", unity["provenance"]["note"])
        offset = record["observation"]["pitch"]["observed_pitch_offset"]
        self.assertEqual("measured", offset["basis"])

    def test_metadata_only_reference_does_not_invent_observed_pitch(self):
        record = adapt_snes_brr_instrument(
            game_or_module="Test SNES game",
            instrument_id=1,
            sample_id=1,
            source_path="table.bin",
            references=[SnesPitchReference(0x10C3, 60)],
        )
        pitch = record["observation"]["pitch"]
        self.assertNotIn("source_unity_key", pitch)
        self.assertNotIn("observed_pitch_offset", pitch)

    def test_reference_confidence_is_preserved(self):
        record = adapt_snes_brr_instrument(
            game_or_module="Test SNES game", instrument_id=1, sample_id=1,
            source_path="table.bin",
            references=[SnesPitchReference(0x1000, 60, confidence=0.4)],
        )
        reference = record["observation"]["pitch"]["pitch_register_reference"]
        self.assertEqual(0.4, reference["confidence"])

    def test_rejects_invalid_epistemic_basis(self):
        with self.assertRaisesRegex(ValueError, "basis"):
            adapt_snes_brr_instrument(
                game_or_module="Test SNES game", instrument_id=1, sample_id=1,
                source_path="table.bin",
                references=[SnesPitchReference(0x1000, 60, basis="guess")],
            )


class SchemaHelperTests(unittest.TestCase):
    def test_bundle_and_semantic_program_number_validation(self):
        bundle = make_bundle([], "unit test")
        self.assertEqual("instrument-analysis/v1", bundle["schema_version"])
        bad = {
            "schema_version": "instrument-analysis/v1",
            "records": [{
                "record_id": "x",
                "analysis": {"routing": [{"route": {
                    "kind": "gm_program", "program_0based": 73, "program_1based": 73,
                }}]},
            }],
        }
        with self.assertRaisesRegex(ValueError, "numbering disagrees"):
            validate_semantics(bad)


if __name__ == "__main__":
    unittest.main()
