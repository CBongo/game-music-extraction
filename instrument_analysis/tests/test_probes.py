from copy import deepcopy
import unittest
from instrument_analysis import make_bundle
from instrument_analysis.probes import plan_source_probes, plan_native_register_probes


class SourceProbeTests(unittest.TestCase):
    def bundle(self):
        return make_bundle([{'record_id':'source:7','source':{'platform':'snes','game_or_module':'test','driver':'test'},'identity':{'source_instrument_id':7},'observation':{'usage':{'provenance':{'method':'test'},'sequences':[{'sequence_id':1,'note_occurrences':[
            {'raw_sequence_note':n,'track_transpose_semitones':2,'sounding_midi_note':n-3,'pitch_context_complete':True}
            for n in [40,40,52,64,76]]}]}}}], 'test')

    def test_usage_quantiles_keep_pitch_and_grid_does_not_invent_it(self):
        bundle=self.bundle()
        before=deepcopy(bundle)
        plan=plan_source_probes(bundle)
        self.assertEqual('planned',plan['status'])
        usage=[p for p in plan['probes'] if p['selection']=='usage_quantile']
        self.assertEqual([40,52,76],[p['trigger_note'] for p in usage])
        self.assertEqual([37,49,73],[p['expected_sounding_midi_note'] for p in usage])
        grid=[p for p in plan['probes'] if p['selection']=='standard_grid']
        self.assertEqual([48,60,72],[p['trigger_note'] for p in grid])
        self.assertTrue(all(not p['comparison_pitch_known'] for p in grid))
        plan['probes'][0]['identity']['source_instrument_id']=99
        self.assertEqual(before,bundle)
        single=plan_source_probes(bundle,standard_notes=(),max_usage_notes=1)
        self.assertEqual(52,single['probes'][0]['trigger_note'])

    def test_unknown_usage_and_curated_mapping_do_not_establish_sound(self):
        bundle=self.bundle()
        notes=bundle['records'][0]['observation']['usage']['sequences'][0]['note_occurrences']
        for n in notes:
            n.pop('pitch_context_complete')
        plan=plan_source_probes(bundle,standard_notes=())
        self.assertTrue(all(not p['comparison_pitch_known'] for p in plan['probes']))

    def test_invalid_conditions(self):
        for kwargs in ({'velocity':0},{'max_usage_notes':0},{'duration_seconds':float('nan')},{'standard_notes':(60,60)}):
            with self.assertRaises(ValueError):
                plan_source_probes(self.bundle(),**kwargs)


    def test_register_plan_preserves_usage_and_requires_new_measurement(self):
        plan=plan_source_probes(self.bundle())
        calibration={'schema_version':'instrument-pitch-calibration/v1','status':'calibrated',
            'basis':'acoustic_measurement','confidence':0.99,'source_record_id':'source:7',
            'sounding_midi_note':49,'native_raw_pitch':{'key':'pitch_register_raw_spu','value':4096},
            'native_manifest_sha256':'a'*64,'native_wav_sha256':'b'*64}
        result=plan_native_register_probes(plan,calibration)
        self.assertEqual([2048,4096],[p['native_raw_pitch_request']['value'] for p in result['probes']])
        self.assertEqual('planned_requires_remeasurement',result['status'])
        self.assertTrue(all(not p['acoustic_pitch_verified'] for p in result['probes']))
        self.assertIn('pitch_register_out_of_range',[p['reason'] for p in result['skipped']])
        self.assertIn('unknown_source_pitch',[p['reason'] for p in result['skipped']])
        self.assertEqual(40,result['probes'][0]['trigger_note'])
        for change in ({'status':'abstain'},{'basis':'curated'},{'confidence':0.7},{'native_manifest_sha256':'bad'}):
            with self.assertRaises(ValueError):
                plan_native_register_probes(plan,{**calibration,**change})


if __name__=='__main__':
    unittest.main()
