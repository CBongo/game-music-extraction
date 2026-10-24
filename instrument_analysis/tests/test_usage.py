"""Exercise the real Perl collector without game data or a MIDI dependency."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from instrument_analysis import make_bundle, query_usage

ROOT = Path(__file__).resolve().parents[2]
PERL = shutil.which('perl')
try:
    import jsonschema
except ImportError:
    jsonschema = None


@unittest.skipUnless(PERL, 'Perl is required for decoder sidecars')
class UsageCollectorTests(unittest.TestCase):
    def collect(self, body, timebase=96):
        source = {'platform': 'snes', 'game_or_module': 'Synthetic', 'driver': 'test',
                  'source_files': [{'path': 'fixture.rom'}]}
        code = '''use InstrumentUsage; use JSON::PP;
my $source = JSON::PP->new->decode($ARGV[0]);
my $u = InstrumentUsage->new(source => $source, timebase => $ARGV[1]);
''' + body + '\nprint JSON::PP->new->canonical->encode($u->records);'
        env = dict(os.environ, LC_ALL='C', LANG='C')
        result = subprocess.run([PERL, '-I', str(ROOT / 'lib'), '-e', code,
                                 json.dumps(source), str(timebase)],
                                capture_output=True, text=True, env=env, check=True)
        bundle = make_bundle(json.loads(result.stdout), 'collector regression')
        if jsonschema:
            schema = json.loads((ROOT / 'schemas/instrument-analysis-v1.schema.json').read_text())
            jsonschema.Draft202012Validator(schema).validate(bundle)
        return bundle

    def test_raw_identity_timing_ties_and_unknown_pitch(self):
        bundle = self.collect('''
$u->begin_track(sequence_id => 1, track_id => 0);
$u->select_patch(sequence_id => 1, track_id => 0,
    source_instrument_id => 7, source_patch_command => 33, song_local_slot => 1);
$u->note(sequence_id => 1, track_id => 0, raw_sequence_note => 60,
    octave_state => 5, time => 12, duration => 24, event_index => 2, offset => 400);
$u->tie(sequence_id => 1, track_id => 0, duration => 6);
$u->effect(sequence_id => 1, track_id => 0, raw_opcode => 201,
    raw_operands => [1,2,3], semantic => 'vibrato', time => 36, event_index => 4, offset => 402);
''', timebase=48)
        record = bundle['records'][0]
        self.assertEqual({'source_instrument_id': 7, 'source_patch_command': 33,
                          'song_local_slot': 1}, record['identity'])
        seq = record['observation']['usage']['sequences'][0]
        self.assertEqual({'60': 1}, seq['note_histogram'])
        note = seq['note_occurrences'][0]
        self.assertEqual(30, note['duration'])
        self.assertEqual({'value': 12, 'unit': 'midi_tick', 'timebase': 48}, note['time'])
        self.assertEqual(400, note['provenance']['location']['offset'])
        self.assertNotIn('sounding_midi_note', note)
        self.assertNotIn('sounding_midi_notes', seq)
        self.assertEqual([1, 2, 3], record['observation']['effects'][0]['raw_operands'])
        self.assertEqual(48, record['observation']['effects'][0]['time']['timebase'])
        rows = query_usage(bundle, source_instrument_id=7, sequence_id=1, track_id=0)
        self.assertEqual([0], rows[0]['track_ids'])
        self.assertEqual([note], rows[0]['note_occurrences'])
        self.assertEqual([], query_usage(bundle, source_instrument_id='7'))
        self.assertEqual([], query_usage(bundle, track_id=1))

    def test_voice_reparse_replaces_observations(self):
        bundle = self.collect('''
for my $pass (1, 2) {
    $u->begin_track(sequence_id => 1, track_id => 0);
    $u->select_patch(sequence_id => 1, track_id => 0,
        source_instrument_id => $pass, source_patch_command => 32);
    $u->note(sequence_id => 1, track_id => 0, raw_sequence_note => 48 + $pass,
        time => 0, duration => 6);
}
''')
        self.assertEqual(1, len(bundle['records']))
        self.assertEqual(2, bundle['records'][0]['identity']['source_instrument_id'])

    def test_sequence_and_track_queries_preserve_context(self):
        bundle = self.collect('''
for my $seq (1, 2) {
    for my $track (0, 1) {
        $u->begin_track(sequence_id => $seq, track_id => $track);
        $u->select_patch(sequence_id => $seq, track_id => $track,
            source_instrument_id => 7, source_patch_command => 32, song_local_slot => 0);
        $u->note(sequence_id => $seq, track_id => $track,
            raw_sequence_note => 48 + $track, time => 0, duration => 6);
    }
}
''')
        self.assertEqual(1, len(bundle['records']))
        self.assertEqual(2, bundle['records'][0]['observation']['usage']['sequence_count'])
        rows = query_usage(bundle, sequence_id=2, track_id=1)
        self.assertEqual(1, len(rows))
        self.assertEqual([1], rows[0]['track_ids'])
        self.assertEqual([49], [n['raw_sequence_note'] for n in rows[0]['note_occurrences']])


    def test_prepatch_effect_is_attributed_once_and_inherited_by_notes(self):
        bundle = self.collect('''
$u->begin_track(sequence_id => 1, track_id => 0);
$u->effect(sequence_id => 1, track_id => 0, raw_opcode => 201,
    raw_operands => [1, 2], semantic => 'vibrato', time => 4, event_index => 1, offset => 100);
$u->effect(sequence_id => 1, track_id => 0, raw_opcode => 202,
    raw_operands => [7], semantic => 'vibrato', context_key => 'depth', time => 5, event_index => 2);
$u->select_patch(sequence_id => 1, track_id => 0,
    source_instrument_id => 7, source_patch_command => 32, sample_ids => [4, 5]);
$u->note(sequence_id => 1, track_id => 0, raw_sequence_note => 60,
    time => 6, duration => 8, pitch_context_complete => 0,
    track_transpose_semitones => 12);
$u->select_patch(sequence_id => 1, track_id => 0,
    source_instrument_id => 8, source_patch_command => 33);
$u->effect(sequence_id => 1, track_id => 0, raw_opcode => 201,
    raw_operands => [3, 4], semantic => 'vibrato', time => 10, event_index => 4);
$u->note(sequence_id => 1, track_id => 0, raw_sequence_note => 61,
    time => 11, duration => 8, pitch_context_complete => 1);
''')
        by_id = {r['identity']['source_instrument_id']: r for r in bundle['records']}
        first = by_id[7]
        self.assertEqual([4, 5], first['identity']['sample_ids'])
        prepatch = first['observation']['effects'][0]
        self.assertEqual(4, prepatch['time']['value'])
        self.assertTrue(prepatch['provenance']['parameters']['observed_before_patch_selection'])
        self.assertEqual(100, prepatch['provenance']['location']['offset'])
        n1 = first['observation']['usage']['sequences'][0]['note_occurrences'][0]
        self.assertIs(n1['pitch_context_complete'], False)
        self.assertEqual(12, n1['track_transpose_semitones'])
        context1 = n1['provenance']['parameters']['effect_context']
        self.assertEqual([[1, 2], [7]], [x['raw_operands'] for x in context1])

        second = by_id[8]
        n2 = second['observation']['usage']['sequences'][0]['note_occurrences'][0]
        context2 = n2['provenance']['parameters']['effect_context']
        # Opcode 201 was updated while control depth remains independently keyed.
        self.assertEqual([[3, 4], [7]], [x['raw_operands'] for x in context2])
        self.assertIs(n2['pitch_context_complete'], True)
        # Snapshots are copies: the prior note keeps the pre-update effect state.
        self.assertEqual([1, 2], context1[0]['raw_operands'])
        # Only the later real occurrence belongs to the second patch record.
        self.assertEqual(1, len(second['observation']['effects']))
        self.assertNotIn('parameters', second['observation']['effects'][0]['provenance'])

    def test_effect_context_updates_same_control_without_on_off_inference(self):
        bundle = self.collect('''
$u->begin_track(sequence_id => 1, track_id => 0);
$u->select_patch(sequence_id => 1, track_id => 0, source_instrument_id => 7);
$u->effect(sequence_id => 1, track_id => 0, raw_opcode => 9, raw_operands => [1], semantic => 'unknown', time => 1);
$u->note(sequence_id => 1, track_id => 0, raw_sequence_note => 50, duration => 4);
$u->effect(sequence_id => 1, track_id => 0, raw_opcode => 9, raw_operands => [0], semantic => 'unknown', time => 2);
$u->note(sequence_id => 1, track_id => 0, raw_sequence_note => 51, duration => 4);
''')
        note1, note2 = bundle['records'][0]['observation']['usage']['sequences'][0]['note_occurrences']
        self.assertEqual([1], note1['provenance']['parameters']['effect_context'][0]['raw_operands'])
        self.assertEqual([0], note2['provenance']['parameters']['effect_context'][0]['raw_operands'])
        self.assertEqual(2, len(bundle['records'][0]['observation']['effects']))

    def test_tie_across_pitch_control_is_not_static_pitch(self):
        bundle = self.collect('''$u->begin_track(sequence_id => 1, track_id => 0);
$u->select_patch(sequence_id => 1, track_id => 0, source_instrument_id => 7);
$u->note(sequence_id => 1, track_id => 0, raw_sequence_note => 60,
    duration => 8, time => 0, pitch_context_complete => 1);
$u->effect(sequence_id => 1, track_id => 0, raw_opcode => 201,
    raw_operands => [1], semantic => 'vibrato', time => 8);
$u->tie(sequence_id => 1, track_id => 0, duration => 8);
''')
        note = bundle['records'][0]['observation']['usage']['sequences'][0]['note_occurrences'][0]
        self.assertEqual(16, note['duration'])
        self.assertIs(note['pitch_context_complete'], False)

    def test_unestablished_patch_is_not_invented(self):
        bundle = self.collect('''
$u->begin_track(sequence_id => 1, track_id => 0);
$u->note(sequence_id => 1, track_id => 0, raw_sequence_note => 60, duration => 6);
''')
        self.assertEqual([], bundle['records'])


if __name__ == '__main__':
    unittest.main()
