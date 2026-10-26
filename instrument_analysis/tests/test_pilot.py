"""Verify benchmark label isolation and explicit missing-audio coverage."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from instrument_analysis.pilot import run_pilot
from instrument_analysis.rendering import make_probe_midi


class PilotTests(unittest.TestCase):
    def test_encoded_fixture_names_and_labels_do_not_enter_the_ranker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); fixtures=root/'fixtures';fixtures.mkdir()
            asset=fixtures/'secret-major-chord.wav';asset.write_bytes(b'opaque waveform')
            (fixtures/'fixtures.json').write_text(json.dumps({'schema_version':'instrument-fixtures/v1','fixtures':[{
                'case_id':'encoded-target','generator':'original deterministic oscillator sum; not native hardware',
                'wav':{'path':asset.name,'sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),'duration_seconds':1.0}}]}))
            benchmark=root/'benchmark.json';benchmark.write_text(json.dumps({'schema_version':'instrument-benchmark/v1','cases':[
                {'case_id':'secret-major-label','record_id':'secret-record','split':'synthetic-contract',
                 'asset':{'kind':'generated_fixture','fixture_id':'encoded-target'},'labels':{'routes':[{'kind':'composite'}]},'render_conditions':{'note':60}},
                {'case_id':'missing-flute','record_id':'missing-record','split':'historical-dev',
                 'asset':{'kind':'local_game_source','path':'converter.pl'},'labels':{'routes':[{'kind':'gm_program','program_0based':73,'program_1based':74}]}}]}))
            gm=root/'gm.json';gm.write_text(json.dumps({'schema_version':'instrument-render/v1','probes':[]}))
            index=root/'index.json';index.write_text('{}')
            calls=[]
            def rank(wav,index_path,**kw):
                calls.append((wav,kw))
                self.assertRegex(wav.name,r'^query-[0-9]{4}\.wav$')
                self.assertNotIn('labels',kw)
                self.assertNotIn('encoded-target',str(kw))
                self.assertNotIn('secret-major',str(kw))
                rid=kw['record_id'];facts=kw['source_facts'] or {'source':{'platform':'unknown','game_or_module':'query','driver':'test'},'identity':{'source_instrument_id':rid}}
                record={'record_id':rid,'source':facts['source'],'identity':facts['identity'],'observation':{},
                    'analysis':{'routing':[{'rank':1,'route':{'kind':'abstain','reason':'unknown'},'score':0,'basis':'inferred','provenance':{'method':'test'}}]}}
                return {'schema_version':'instrument-analysis/v1','records':[record]},{'status':'abstain','reason':'unknown'}
            with patch('instrument_analysis.pilot.prepared_reference_index'),patch('instrument_analysis.pilot.calibrate_wav_pitch',return_value={'status':'abstain'}),patch('instrument_analysis.pilot.rank_wav_source',side_effect=rank):
                summary,report=run_pilot(benchmark,index,fixtures,gm,root/'out')
            self.assertEqual([True,False],[c['audio_available'] for c in summary['cases']])
            self.assertFalse(calls[1][0].exists())
            predictions=json.loads((root/'out'/'predictions.json').read_text())
            self.assertEqual(['secret-record','missing-record'],[r['record_id'] for r in predictions['records']])
            self.assertEqual(2,report['metrics']['route_accuracy']['denominator'])
            with self.assertRaises(FileExistsError):
                run_pilot(benchmark,index,fixtures,gm,root/'out')


    def test_channel10_evidence_is_reconstructed_when_midi_was_not_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixtures=root/'fixtures';fixtures.mkdir()
            (fixtures/'fixtures.json').write_text(json.dumps({'schema_version':'instrument-fixtures/v1','fixtures':[]}))
            wav=root/'drum.wav';wav.write_bytes(b'opaque drum waveform')
            probe={'probe_id':'drum-control','channel_0based':9,'program_0based':0,
                'bank_select_msb':None,'bank_select_lsb':None,'note':38,'velocity':96,
                'route':{'kind':'gm_percussion','note':38},'wav':{'file':wav.name,'sha256':hashlib.sha256(wav.read_bytes()).hexdigest()}}
            midi=make_probe_midi(probe,duration_seconds=.5,tail_seconds=.5)
            probe['midi_sha256']=hashlib.sha256(midi).hexdigest()
            gm=root/'gm.json';gm.write_text(json.dumps({'schema_version':'instrument-render/v1','conditions':{'duration_seconds':.5,'tail_seconds':.5},'probes':[probe]}))
            benchmark=root/'benchmark.json';benchmark.write_text(json.dumps({'schema_version':'instrument-benchmark/v1','cases':[{
                'case_id':'snare','record_id':'snare','split':'same-bank-sanity',
                'asset':{'kind':'gm_reference_recipe','route':{'kind':'gm_percussion','note':38}},
                'labels':{'routes':[{'kind':'gm_percussion','note':38}]}}]}))
            index=root/'index.json';index.write_text('{}')
            def rank(wav,index,**kw):
                fact=kw['source_facts']['classification']
                evidence=Path(fact['provenance']['location']['path'])
                self.assertEqual(evidence.read_bytes(),midi)
                self.assertEqual(fact['gm_channel_0based'],9)
                self.assertNotEqual(evidence.name,'drum-control.mid')
                record={'record_id':kw['record_id'],'source':kw['source_facts']['source'],'identity':kw['source_facts']['identity'],'observation':{},
                    'analysis':{'routing':[{'rank':1,'route':{'kind':'gm_percussion','note':38},'score':1,'basis':'measured','provenance':{'method':'test'}}]}}
                return {'schema_version':'instrument-analysis/v1','records':[record]},{'status':'ranked','reason':None}
            with patch('instrument_analysis.pilot.prepared_reference_index'),patch('instrument_analysis.pilot.calibrate_wav_pitch',return_value={'status':'abstain'}),patch('instrument_analysis.pilot.rank_wav_source',side_effect=rank):
                summary,report=run_pilot(benchmark,index,fixtures,gm,root/'out')
            self.assertTrue(summary['cases'][0]['audio_available'])
            self.assertEqual(1,report['metrics']['route_accuracy']['value'])


if __name__=='__main__':
    unittest.main()
