"""Synthetic layout tests; no game ROM bytes are distributed."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from instrument_analysis.ff6_samples import (parse_ff6_sample, extract_ff6_sample,
    POINTER_TABLE, LOOP_TABLE, TUNING_TABLE, ADSR_TABLE, PAYLOAD_SIZE)


def synthetic(header=0):
    data = bytearray(0x60100 + header)
    for sid in (1, 7):
        index = sid - 1
        offset = 0x60000 + 32 * index
        data[header + POINTER_TABLE + index * 3:header + POINTER_TABLE + index * 3 + 3] = (0xC00000 + offset).to_bytes(3, "little")
        data[header + LOOP_TABLE + index * 2:header + LOOP_TABLE + index * 2 + 2] = (9).to_bytes(2, "little")
        data[header + TUNING_TABLE + index * 2:header + TUNING_TABLE + index * 2 + 2] = b"\x22\x33"
        data[header + ADSR_TABLE + index * 2:header + ADSR_TABLE + index * 2 + 2] = b"\x44\x55"
        data[header + offset:header + offset + 2] = (18).to_bytes(2, "little")
        data[header + offset + 2:header + offset + 20] = b"\x00" * 9 + b"\x03" + b"\x00" * 8
    return data


class FF6SampleTests(unittest.TestCase):
    def test_one_based_entry_and_header_offsets_and_hashes(self):
        for header in (0, 512):
            data = bytes(synthetic(header))
            metadata, brr = parse_ff6_sample(data, 7, header_bytes=header)
            self.assertEqual(metadata["record_id"], "snes:ff6-us:global-sample-07")
            self.assertEqual(metadata["instrument"]["table_index"], 6)
            self.assertEqual(metadata["instrument"]["pointer_entry"]["offset"], header + POINTER_TABLE + 18)
            self.assertEqual(metadata["brr"]["file_offset"], header + 0x600C2)
            self.assertEqual(metadata["brr"]["sha256"], hashlib.sha256(brr).hexdigest())
            self.assertEqual(metadata["brr"]["loop_relative_bytes"], 9)
            self.assertTrue(metadata["brr"]["looping"])
            self.assertEqual(metadata["brr"]["frame_count"], 2)
            self.assertEqual(metadata["brr"]["decoded_sample_count"], 32)
            descriptor = bytes.fromhex("c000c61200090022334455")
            self.assertEqual(metadata["instrument"]["descriptor_sha256"], hashlib.sha256(descriptor).hexdigest())
            self.assertFalse(metadata["rom"]["version_approved"])

    def test_rejects_bad_bounds_ids_length_end_and_loop(self):
        for sid in (0, 64, True):
            with self.assertRaises(ValueError):
                parse_ff6_sample(bytes(synthetic()), sid)
        for offset, value in ((0x600C0, b"\x00\x00"), (0x600C0, b"\x11\x00"),
                              (0x600C0, b"\xff\xff"), (0x600CB, b"\x00"),
                              (0x600C2, b"\x01"), (LOOP_TABLE + 12, b"\x01\x00"),
                              (LOOP_TABLE + 12, b"\x12\x00")):
            data = synthetic()
            data[offset:offset + len(value)] = value
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                parse_ff6_sample(bytes(data), 7)
        with self.assertRaises(ValueError):
            parse_ff6_sample(b"short", 7)

    def test_production_wrapper_rejects_unknown_rom(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "unknown.sfc"
            path.write_bytes(bytes(PAYLOAD_SIZE))
            with self.assertRaisesRegex(ValueError, "unsupported FF6 ROM"):
                extract_ff6_sample(path, 7)


if __name__ == "__main__":
    unittest.main()
