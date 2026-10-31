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
    op_lengths[0xD9 - 0xC4] = 1  # transpose-set operand
    op_lengths[0xDA - 0xC4] = 1  # transpose-change operand
    op_lengths[0xDC - 0xC4] = 1  # patch command operand
    op_lengths[0xF0 - 0xC4] = 1  # tempo operand
    data[spcbase + 0x18F9:spcbase + 0x18F9 + 60] = op_lengths
    data[spcbase + 0x17D1:spcbase + 0x17D1 + 14] = bytes(range(4, 18))

    voice0 = bytes([
        0xC9, 0x03, 0x04, 0x05, # vibrato before patch selection
        0xCA,                   # vibrato off before first note
        0xDC, 0x21,             # select song-local slot 1
        0xD9, 0xFE,             # signed set: -2 semitones
        0x00,                   # C at octave 4, duration index 0
        0xA9,                   # tie, duration index 1
        0xB8,                   # rest, duration index 2
        0xDA, 0xFF,             # signed relative change: -1, state becomes -3
        0x3B,                   # E at octave 4, duration index 3
        0xD9, 0x7F,             # set state to +127
        0xDA, 0x01,             # wrap signed state to -128
        0x1C,                   # D at octave 4, duration index 0
        0xF0, 120,              # conductor tempo retained in sidecar provenance
        0xEB,                   # halt
    ])
    # Seven notes make this voice longer than voice 0, forcing the decoder's redo.
    voice1 = bytes([0xDC, 0x21]) + bytes(range(0, 7 * 14, 14)) + bytes([0xEB])

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
                schema = json.loads((ROOT / "schemas/instrument-analysis-v1.schema.json").read_text())
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
            self.assertEqual(3, len(track0))
            self.assertEqual(7, len(track1))
            self.assertEqual([48, 52, 50], [n["raw_sequence_note"] for n in track0])
            self.assertEqual([-2, -3, -128], [n["track_transpose_semitones"] for n in track0])
            self.assertTrue(all(n["pitch_context_complete"] for n in track0))
            self.assertEqual(18, track0[0]["duration"])
            render = track0[0]["provenance"]["parameters"]["render_context"]
            self.assertEqual(14, render["gate_duration_ticks"])
            self.assertEqual(100, render["velocity_0_127"])
            self.assertFalse(render["effects_complete"])
            context = usage["provenance"]["parameters"]["sequence_render_contexts"][0]
            self.assertEqual(1, context["sequence_id"])
            self.assertEqual(96, context["ticks_per_quarter"])
            self.assertEqual(500000, context["default_microseconds_per_quarter"])
            self.assertFalse(context["effects_complete"])
            self.assertEqual([{"tick": 52, "microseconds_per_quarter": 499200}], context["tempo_events"])
            self.assertEqual(30, track0[1]["time"]["value"])
            self.assertEqual(44, track0[2]["time"]["value"])

            # A synthetic sounding reference establishes sample offset separately
            # from the actual source transpose retained by the decoder sidecar.
            from instrument_analysis import SnesPitchReference, adapt_snes_brr_instrument, calibrate_usage, make_bundle
            anchor = adapt_snes_brr_instrument(
                game_or_module='Final Fantasy III', instrument_id=7, sample_id=7,
                source_path=str(rom), driver='FF3', source_patch_command=0x21,
                song_local_slot=1,
                references=[SnesPitchReference(0x1000, 48, 43, basis='measured',
                                              method='synthetic grounded sounding reference')],
            )
            calibrated = calibrate_usage(bundle, make_bundle([anchor], 'synthetic calibration'),
                record_links={record['record_id']: anchor['record_id']})
            joined_notes = calibrated['records'][0]['observation']['usage']['sequences'][0]['note_occurrences']
            first = next(n for n in joined_notes if n['track_id'] == 0)
            self.assertEqual(41, first['sounding_midi_note'])  # raw 48, driver -2, sample -5
            self.assertEqual(-5, first['sample_tuning_semitones'])
            self.assertNotIn('sounding_midi_note', track0[0])  # raw bundle stays unchanged
            if jsonschema:
                jsonschema.Draft202012Validator(schema).validate(calibrated)

            effects = record["observation"]["effects"]
            transpose = next(
                effect for effect in effects
                if effect["raw_opcode"] == 0xD9 and effect["raw_operands"] == [0xFE]
            )
            self.assertEqual({
                "scope": "track", "operation": "set", "value": -2, "unit": "semitone",
            }, transpose["normalized"])
            relative_effects = [effect for effect in effects if effect["raw_opcode"] == 0xDA]
            self.assertEqual([-1, 1], [e["normalized"]["delta"] for e in relative_effects])
            self.assertEqual([-3, -128], [e["normalized"]["value"] for e in relative_effects])

            vibrato_on = next(effect for effect in effects if effect["raw_opcode"] == 0xC9)
            vibrato_off = next(effect for effect in effects if effect["raw_opcode"] == 0xCA)
            self.assertEqual([3, 4, 5], vibrato_on["raw_operands"])
            self.assertEqual("vibrato", vibrato_on["semantic"])
            self.assertTrue(
                vibrato_on["provenance"]["parameters"]["observed_before_patch_selection"]
            )
            self.assertTrue(
                vibrato_off["provenance"]["parameters"]["observed_before_patch_selection"]
            )
            self.assertEqual(
                song_offset + 2 + first_voice_offset + 7,
                transpose["provenance"]["location"]["offset"],
            )
            self.assertEqual(
                song_offset + 2 + first_voice_offset,
                vibrato_on["provenance"]["location"]["offset"],
            )


if __name__ == "__main__":
    unittest.main()
