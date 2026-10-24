from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import struct
import unittest

from instrument_analysis import make_bundle
from instrument_analysis.akao_usage import bind_akao_sequence_usage

try:
    import jsonschema
except ImportError:
    jsonschema = None

ROOT = Path(__file__).resolve().parents[2]
SEQUENCE_PATH = 'seq.akao'
SAMPLE_PATH = 'samples.akao'
GAME = 'Synthetic AKAO'


def synthetic_sample_set() -> bytes:
    # Set 0x1234 has articulations 7 and 8, with distinct SPU sample starts.
    header = struct.pack('<4sHH6I', b'AKAO', 0x1234, 0, 0, 0, 0x1000,
                         0x2000, 7, 2).ljust(0x40, b'\0')
    art7 = struct.pack('<IIhHHH', 0x100, 0x180, 0x1000, 60, 0x9FAF, 0x5FFF)
    art8 = struct.pack('<IIhHHH', 0x200, 0x240, 0, 70, 0x8F00, 0x4000)
    return header + art7 + art8


def synthetic_sequence(*, sequence_id=3, associated_id=0x1234,
                       instrument_pointer=0x10, drum_pointer=0x4C,
                       drum_art=7) -> bytes:
    # Each table pointer is relative to its own field. Instrument table starts
    # at 0x40, then its slot-7 pointer resolves through the +0x20 pointer table
    # to a region at 0x68 which points to articulation 8 (not articulation 7).
    data = bytearray(0x1B0)
    data[:4] = b'AKAO'
    struct.pack_into('<H', data, 4, sequence_id)
    struct.pack_into('<H', data, 0x14, associated_id)
    struct.pack_into('<I', data, 0x30, instrument_pointer)
    struct.pack_into('<I', data, 0x34, drum_pointer)
    pointers = [0xFFFF] * 16
    pointers[7] = 8
    struct.pack_into('<16H', data, 0x40, *pointers)
    data[0x68:0x70] = bytes([8, 0, 127, 11, 12, 13, 14, 99])
    data[0x70:0x78] = bytes(8)  # region terminator
    drum_entry = 0x80 + 36 * 8
    data[drum_entry:drum_entry + 8] = bytes([drum_art, 60, 21, 22, 23, 24, 99, 0x80 | 23])
    data[drum_entry + 8:drum_entry + 16] = b'\xff' * 8
    return bytes(data)


def usage_bundle(*, sequence_path=SEQUENCE_PATH, game=GAME,
                 identities=None) -> dict:
    identities = identities or [
        ('sequence-3:articulation-7', 7),
        ('sequence-3:patch-slot-7', 7),
        ('sequence-3:drumkit', None),
    ]
    records = []
    for index, (raw_id, command) in enumerate(identities):
        identity = {'source_instrument_id': raw_id}
        if command is not None:
            identity['source_patch_command'] = command
        records.append({
            'record_id': f'usage:{index}',
            'source': {
                'platform': 'Sony PlayStation', 'game_or_module': game,
                'driver': 'AKAO PSX', 'source_files': [{'path': sequence_path}],
            },
            'identity': identity,
            'observation': {'usage': {
                'provenance': {'method': 'synthetic'},
                'sequences': [{
                    'sequence_id': 3, 'track_ids': [0],
                    'source_patch_commands': [command] if command is not None else [],
                    'raw_sequence_notes': [60], 'note_histogram': {'60': 1},
                    'note_occurrences': [{
                        'track_id': 0, 'raw_sequence_note': 60,
                        'time': {'value': 12, 'unit': 'midi_tick', 'timebase': 48},
                        'duration': 6, 'duration_unit': 'midi_tick',
                        'provenance': {'method': 'synthetic'},
                    }],
                }], 'sequence_count': 1,
            }},
        })
    return make_bundle(records, 'synthetic AKAO usage')


class AkaoSequenceUsageTests(unittest.TestCase):
    def bind(self, **kwargs):
        args = {
            'usage_bundle': usage_bundle(),
            'sequence_data': synthetic_sequence(),
            'sample_set_data': synthetic_sample_set(),
            'sequence_path': SEQUENCE_PATH,
            'sample_set_path': SAMPLE_PATH,
            'game_or_module': GAME,
            'format_version': '3.1',
            'sequence_base_offset': 0x500,
            'sample_base_offset': 0x2000,
        }
        args.update(kwargs)
        return bind_akao_sequence_usage(**args)

    def test_binds_articulation_patch_slot_and_drumkit_by_real_sample(self):
        original = usage_bundle()
        before = copy.deepcopy(original)
        bound, metadata, links = self.bind(usage_bundle=original)
        self.assertEqual(before, original)
        if jsonschema:
            schema = json.loads((ROOT / 'schemas/instrument-analysis-v1.schema.json').read_text())
            jsonschema.Draft202012Validator(schema).validate(bound)
            jsonschema.Draft202012Validator(schema).validate(metadata)

        by_id = {r['identity']['source_instrument_id']: r for r in bound['records']}
        self.assertEqual(set(by_id), {
            'sequence-3:articulation-7', 'sequence-3:patch-slot-7', 'sequence-3:drumkit'})
        self.assertEqual('seq.akao', by_id['sequence-3:articulation-7']['source']['source_files'][0]['path'])

        articulation = by_id['sequence-3:articulation-7']
        patch = by_id['sequence-3:patch-slot-7']
        drumkit = by_id['sequence-3:drumkit']
        art7_sample = articulation['identity']['sample_ids'][0]
        slot_sample = patch['identity']['sample_ids'][0]
        self.assertIn('spu-offset-0x00000100', art7_sample)
        self.assertIn('spu-offset-0x00000200', slot_sample)
        self.assertNotEqual(art7_sample, slot_sample)
        self.assertEqual(slot_sample, patch['observation']['zones'][0]['sample_id'])
        self.assertEqual(7, articulation['identity']['source_patch_command'])
        self.assertEqual(7, patch['identity']['source_patch_command'])
        slot_record = next(r for r in metadata['records'] if r['record_id'] == links['usage:1'])
        self.assertEqual(7, slot_record['identity']['song_local_slot'])

        for record in (articulation, patch):
            note = record['observation']['usage']['sequences'][0]['note_occurrences'][0]
            self.assertEqual(60, note['raw_sequence_note'])
            self.assertNotIn('sounding_midi_note', note)
        association = articulation['observation']['usage']['provenance']['parameters']['sample_set_association']
        self.assertEqual({'sample_set_id': 0x1234, 'sample_set_path': SAMPLE_PATH}, association['parameters'])
        self.assertEqual({'path': SEQUENCE_PATH, 'offset': 0x500 + 0x14}, association['location'])
        self.assertEqual(association, patch['observation']['usage']['provenance']['parameters']['sample_set_association'])

        bindings = drumkit['observation']['usage']['percussion_bindings']
        self.assertEqual(1, len(bindings))
        binding = bindings[0]
        self.assertEqual(36, binding['source_trigger_note'])
        self.assertEqual('sample-set-1234:articulation-7', binding['underlying_instrument_id'])
        self.assertEqual(36, binding['zone_id'])
        self.assertEqual(60, binding['source_sample_note'])
        self.assertEqual(99, binding['volume_raw'])
        self.assertEqual(23, binding['pan_raw'])
        zone = drumkit['observation']['zones'][0]
        self.assertEqual(art7_sample, zone['sample_id'])
        self.assertEqual(58 + 36 - 60, zone['pitch']['source_unity_key']['value'])
        self.assertEqual(0x500 + 0x80 + 36 * 8, zone['provenance']['location']['offset'])
        self.assertTrue(zone['provenance']['parameters']['reverb_enabled'])
        self.assertEqual('samples.akao:set-1234:articulation-7', links['usage:0'])
        self.assertIn(links['usage:0'], {r['record_id'] for r in metadata['records']})

    def test_binding_then_grounded_calibration_preserves_raw_trigger(self):
        from instrument_analysis import calibrate_usage
        bound, metadata, links = self.bind()
        art_usage = bound['records'][0]
        note = art_usage['observation']['usage']['sequences'][0]['note_occurrences'][0]
        note['pitch_context_complete'] = True
        note['track_transpose_semitones'] = -2
        target = next(r for r in metadata['records'] if r['record_id'] == links[art_usage['record_id']])
        target['observation']['pitch']['observed_pitch_offset'] = {
            'semitones': -5, 'residual_cents': 0, 'basis': 'measured', 'confidence': 1,
            'provenance': {'method': 'synthetic grounded sounding reference'},
        }
        result = calibrate_usage(bound, metadata, record_links=links)
        joined = result['records'][0]['observation']['usage']['sequences'][0]['note_occurrences'][0]
        self.assertEqual(53, joined['sounding_midi_note'])
        self.assertEqual(60, joined['raw_sequence_note'])
        self.assertNotIn('sounding_midi_note', note)
        if jsonschema:
            schema = json.loads((ROOT / 'schemas/instrument-analysis-v1.schema.json').read_text())
            jsonschema.Draft202012Validator(schema).validate(result)

    def test_rejects_incompatible_version_association_and_bad_pointers(self):
        with self.assertRaisesRegex(ValueError, 'version 3.1 or 3.2'):
            self.bind(format_version='3.0')
        with self.assertRaisesRegex(ValueError, 'associated sample-set ID'):
            self.bind(sequence_data=synthetic_sequence(associated_id=0x9999))
        with self.assertRaisesRegex(ValueError, 'pointer at 0x30'):
            self.bind(sequence_data=synthetic_sequence(instrument_pointer=0xFFFFFFF0))
        with self.assertRaisesRegex(ValueError, 'pointer at 0x34'):
            self.bind(sequence_data=synthetic_sequence(drum_pointer=0xFFFFFFF0))

    def test_rejects_wrong_source_sequence_and_missing_articulations(self):
        other_game = usage_bundle(game='Other game')
        with self.assertRaisesRegex(ValueError, 'game does not match'):
            self.bind(usage_bundle=other_game)
        wrong_sequence = usage_bundle(identities=[('sequence-4:articulation-7', 7)])
        # Sequence source matches but its observed sequence ID conflicts.
        wrong_sequence['records'][0]['observation']['usage']['sequences'][0]['sequence_id'] = 4
        with self.assertRaisesRegex(ValueError, 'sequence ID does not match'):
            self.bind(usage_bundle=wrong_sequence)
        missing_art = usage_bundle(identities=[('sequence-3:articulation-9', 9)])
        with self.assertRaisesRegex(ValueError, 'missing articulation 9'):
            self.bind(usage_bundle=missing_art)
        with self.assertRaisesRegex(ValueError, 'missing articulation 9'):
            self.bind(sequence_data=synthetic_sequence(drum_art=9))

    def test_rejects_conflicting_metadata_and_usage_sample_ids(self):
        conflict = usage_bundle(identities=[('sequence-3:articulation-7', 7)])
        conflict['records'][0]['observation']['pitch'] = {'source_unity_key': {'value': 0}}
        with self.assertRaisesRegex(ValueError, 'conflicting pitch'):
            self.bind(usage_bundle=conflict)
        conflict_samples = usage_bundle(identities=[('sequence-3:articulation-7', 7)])
        conflict_samples['records'][0]['identity']['sample_ids'] = ['invented-sample']
        with self.assertRaisesRegex(ValueError, 'conflicting sample identities'):
            self.bind(usage_bundle=conflict_samples)
        conflict_command = usage_bundle(identities=[('sequence-3:articulation-7', 6)])
        conflict_command['records'][0]['identity']['source_patch_command'] = 6
        with self.assertRaisesRegex(ValueError, 'patch operand conflicts'):
            self.bind(usage_bundle=conflict_command)


if __name__ == '__main__':
    unittest.main()
