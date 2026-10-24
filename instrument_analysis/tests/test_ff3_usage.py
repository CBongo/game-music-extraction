import json
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path


try:
    import jsonschema
except ImportError:
    jsonschema = None


ROOT = Path(__file__).resolve().parents[2]
DECODER = ROOT / "snes" / "ff3" / "ff3mus.pl"


def _snes_file_offset(bank_address: str) -> int:
    bank, address = bank_address.split("/")
    return ((int(bank, 16) & 0x3F) << 16) + int(address, 16) + 0x200


def _put_3byte_pointer(data: bytearray, offset: int, target: int) -> None:
    encoded = target - 0x200
    data[offset:offset + 3] = bytes(
        (encoded & 0xFF, (encoded >> 8) & 0xFF, (encoded >> 16) & 0x3F)
    )


def _make_ff3_rom(path: Path) -> tuple[int, int]:
    # Build a headered ROM containing only the tables and two tiny voice streams
    # consumed by ff3mus.pl. No game ROM data is copied into this fixture.
    base = _snes_file_offset("C5/0000")
    spcbase = base + 0x70E + 2 - 0x200
    song_offset = 0x56000
    data = bytearray(0x57000)

    # Song index 1 points to a length word followed by the song structure.
    _put_3byte_pointer(data, base + 0x3E96 + 3, song_offset)

    # Keep the song-local slot 0 empty and put instrument 7 in slot 1. This
    # catches accidental use of the decoder's zero-filtered legacy @inst list.
    struct.pack_into("<H", data, base + 0x3F95 + 0x20 + 0, 0)
    struct.pack_into("<H", data, base + 0x3F95 + 0x20 + 2, 7)

    # The decoder uses the opcode table's byte counts to advance command streams.
    op_lengths = bytearray(60)
    op_lengths[0xC9 - 0xC4] = 3  # vibrato operands
    op_lengths[0xD9 - 0xC4] = 1  # transpose operand
    op_lengths[0xDC - 0xC4] = 1  # patch command operand
    data[spcbase + 0x18F9:spcbase + 0x18F9 + 60] = op_lengths
    data[spcbase + 0x17D1:spcbase + 0x17D1 + 14] = bytes(range(4, 18))

    voice0 = bytes([
        0xDC, 0x21,             # select song-local slot 1
        0xD9, 0xFE,             # preserve raw transpose byte (not normalized)
        0x00,                   # C at octave 4, duration index 0
        0xA9,                   # tie, duration index 1
        0xB8,                   # rest, duration index 2
        0xC9, 0x03, 0x04, 0x05, # vibrato and raw operands
        0x3B,                   # E at octave 4, duration index 3
        0xEB,                   # halt
    ])
    voice1 = bytes([0xDC, 0x21]) + bytes(range(0, 6 * 14, 14)) + bytes([0xEB])

    # FF3 song header: relocation offset zero, empty voice sentinel, then two
    # voice pointers and six empty pointers (with an unused alternate table).
    song = bytearray(0x24)
    struct.pack_into("<HH", song, 0, 0x1C24, 0xFFFF)
    struct.pack_into("<8H", song, 4, 0x1C24, 0x1C40, *([0xFFFF] * 6))
    struct.pack_into("<8H", song, 0x14, *([0xFFFF] * 8))
    song.extend(voice0)
    song.extend(bytes(0x40 - len(song)))
    song.extend(voice1)
    struct.pack_into("<H", data, song_offset, len(song))
    data[song_offset + 2:song_offset + 2 + len(song)] = song

    path.write_bytes(data)
    return song_offset, 0x24


class FF3UsageIntegrationTests(unittest.TestCase):
    def test_sidecar_captures_source_usage_without_changing_midi(self):
        perl = shutil.which("perl")
        if perl is None:
            self.skipTest("Perl is required to run the FF3 decoder")

        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            rom = temp / "synthetic-ff3.smc"
            song_offset, first_voice_offset = _make_ff3_rom(rom)

            plain_cwd = temp / "plain"
            usage_cwd = temp / "usage"
            (plain_cwd / "mid").mkdir(parents=True)
            (usage_cwd / "mid").mkdir(parents=True)
            sidecar = usage_cwd / "instrument-usage.json"

            common = [perl, str(DECODER), "--rom", str(rom), "01"]
            subprocess.run(common, cwd=plain_cwd, check=True, capture_output=True, text=True)
            subprocess.run(
                [perl, str(DECODER), "--rom", str(rom),
                 "--instrument-usage", str(sidecar), "01"],
                cwd=usage_cwd, check=True, capture_output=True, text=True,
            )

            midi_name = "01 - Prelude.mid"
            self.assertEqual(
                (plain_cwd / "mid" / midi_name).read_bytes(),
                (usage_cwd / "mid" / midi_name).read_bytes(),
            )

            bundle = json.loads(sidecar.read_text(encoding="utf-8"))
            if jsonschema:
                schema = json.loads((ROOT / 'schemas/instrument-analysis-v1.schema.json').read_text())
                jsonschema.Draft202012Validator(schema).validate(bundle)
            self.assertEqual("instrument-analysis/v1", bundle["schema_version"])
            records = bundle["records"]
            self.assertEqual(1, len(records))
            record = records[0]
            identity = record["identity"]
            self.assertEqual(7, identity["source_instrument_id"])
            self.assertEqual(0x21, identity["source_patch_command"])
            self.assertEqual(1, identity["song_local_slot"])

            usage = record["observation"]["usage"]
            self.assertEqual(1, usage["sequence_count"])
            sequence = usage["sequences"][0]
            occurrences = sequence["note_occurrences"]
            track0 = [n for n in occurrences if n["track_id"] == 0]
            track1 = [n for n in occurrences if n["track_id"] == 1]

            # Voice 1 is longer, so the legacy MIDI parser restarts voice 0.
            # begin_track must replace the earlier capture rather than duplicate it.
            self.assertEqual(2, len(track0))
            self.assertEqual(6, len(track1))
            self.assertEqual([48, 52], [n["raw_sequence_note"] for n in track0])
            self.assertEqual(18, track0[0]["duration"])
            self.assertEqual(30, track0[1]["time"]["value"])
            self.assertEqual(44, track0[1]["time"]["value"] + track0[1]["duration"])

            effects = record["observation"]["effects"]
            transpose = next(effect for effect in effects if effect["raw_opcode"] == 0xD9)
            self.assertEqual([0xFE], transpose["raw_operands"])
            self.assertEqual({}, transpose["normalized"])
            vibrato = next(effect for effect in effects if effect["raw_opcode"] == 0xC9)
            self.assertEqual([3, 4, 5], vibrato["raw_operands"])
            self.assertEqual("vibrato", vibrato["semantic"])
            self.assertEqual(
                song_offset + 2 + first_voice_offset + 2,
                transpose["provenance"]["location"]["offset"],
            )
            self.assertEqual(
                song_offset + 2 + first_voice_offset + 7,
                vibrato["provenance"]["location"]["offset"],
            )


if __name__ == "__main__":
    unittest.main()
