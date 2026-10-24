"""End-to-end coverage for opt-in Chrono Cross AKAO usage capture."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest


try:
    import jsonschema
except ImportError:
    jsonschema = None


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "psx" / "cc" / "ccmus.pl"
TITLES = ROOT / "psx" / "cc" / "titles.pl"


def synthetic_akao() -> bytes:
    # One voice at body offset 0x30; pointer values are relative to each pointer.
    stream = bytes([
        0xA1, 0x07,             # articulation 7
        0xA5, 0x04,             # octave 4
        0xAD, 0x12,             # envelope parameter
        0xC0, 0x05,             # uninterpreted transpose byte
        0xB4, 1, 2, 3,          # vibrato on with raw operands
        0xD8, 0x23,             # pitch bend raw operand
        0x04,                   # C, duration 12 ticks
        0xFC, 0x06,             # tie extending last note
        0xFE, 0x14, 0x07,       # key-split slot 7; distinct from articulation 7
        0x0F,                   # C# at duration-table index 4
        0xA0,                   # halt
    ])
    body = bytearray(0x30 + len(stream))
    struct.pack_into("<I", body, 0, 1)       # voice mask, voice 0
    struct.pack_into("<H", body, 4, 0x30-4) # pointer relative to pointer field
    body[0x30:] = stream
    header = struct.pack("<4sHH24B", b"AKAO", 3, len(body), *([0] * 24))
    return header + bytes(body)


@unittest.skipUnless(shutil.which("perl"), "Perl is required for the CC decoder")
class ChronoCrossUsageTests(unittest.TestCase):
    def setUp(self):
        probe = subprocess.run(
            [shutil.which("perl"), "-MMIDI", "-MCompress::Zlib", "-e", "1"],
            capture_output=True,
            text=True,
        )
        if probe.returncode:
            self.skipTest("Perl MIDI and Compress::Zlib modules are required")

    def _run(self, *, text_mode: bool, capture: bool):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        work = Path(temp.name)
        (work / "akao").mkdir()
        shutil.copyfile(TITLES, work / "titles.pl")
        source = work / "akao" / "03-0001.bin"
        source.write_bytes(synthetic_akao())
        usage_path = work / "usage.json"
        args = [shutil.which("perl"), "-I.", str(SCRIPT)]
        if text_mode:
            args.append("-t")
        if capture:
            args.extend(["--instrument-usage", str(usage_path)])
        result = subprocess.run(args, cwd=work, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        suffix = ".txt" if text_mode else ".mid"
        outputs = list(work.glob(f"akao/*{suffix}"))
        self.assertEqual(1, len(outputs), f"expected one {suffix} output")
        result = outputs[0].read_bytes()
        bundle = json.loads(usage_path.read_text()) if capture else None
        return temp, result, bundle

    def test_opt_in_capture_preserves_text_and_midi_and_records_usage(self):
        baseline_mid_dir, baseline_mid, _ = self._run(text_mode=False, capture=False)
        capture_mid_dir, captured_mid, _ = self._run(text_mode=False, capture=True)
        baseline_txt_dir, baseline_txt, _ = self._run(text_mode=True, capture=False)
        capture_txt_dir, captured_txt, bundle = self._run(text_mode=True, capture=True)
        try:
            self.assertEqual(baseline_mid, captured_mid)
            self.assertEqual(baseline_txt, captured_txt)
            if jsonschema:
                schema = json.loads((ROOT / 'schemas/instrument-analysis-v1.schema.json').read_text())
                jsonschema.Draft202012Validator(schema).validate(bundle)
            self.assertEqual("instrument-analysis/v1", bundle["schema_version"])
            self.assertEqual(2, len(bundle["records"]))
            by_id = {r['identity']['source_instrument_id']: r for r in bundle['records']}
            self.assertEqual({'sequence-3:articulation-7', 'sequence-3:patch-slot-7'}, set(by_id))

            first = by_id['sequence-3:articulation-7']
            self.assertEqual(7, first['identity']['source_patch_command'])
            self.assertNotIn('song_local_slot', first['identity'])
            sequence = first["observation"]["usage"]["sequences"][0]
            self.assertEqual([48], sequence["raw_sequence_notes"])
            self.assertEqual(4, sequence["note_occurrences"][0]["octave_state"])
            self.assertEqual(18, sequence["note_occurrences"][0]["duration"])
            self.assertEqual(48, sequence["note_occurrences"][0]["time"]["timebase"])
            self.assertEqual(0x20 + 0x30 + 0x0E, sequence["note_occurrences"][0]["provenance"]["location"]["offset"])

            effects = first["observation"]["effects"]
            self.assertTrue(
                {"envelope_change", "transpose", "vibrato", "pitch_bend"}
                <= {effect["semantic"] for effect in effects},
            )
            transpose = next(e for e in effects if e["semantic"] == "transpose")
            self.assertEqual([5], transpose["raw_operands"])
            self.assertEqual({"raw_value": 5}, transpose["normalized"])
            bend = next(e for e in effects if e["semantic"] == "pitch_bend")
            self.assertEqual("0xD8", bend["raw_opcode"])
            self.assertEqual([0x23], bend["raw_operands"])

            second = by_id['sequence-3:patch-slot-7']
            self.assertEqual(7, second['identity']['song_local_slot'])
            patch_effect = next(
                e for e in second["observation"]["effects"]
                if e["raw_opcode"] == "0xFE14"
            )
            self.assertEqual([7], patch_effect["raw_operands"])
            self.assertEqual({"source_patch_command": 7}, patch_effect["normalized"])
            note = second["observation"]["usage"]["sequences"][0]["note_occurrences"][0]
            self.assertEqual(49, note["raw_sequence_note"])
            self.assertEqual(48, patch_effect["time"]["timebase"])
        finally:
            baseline_mid_dir.cleanup()
            capture_mid_dir.cleanup()
            baseline_txt_dir.cleanup()
            capture_txt_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
