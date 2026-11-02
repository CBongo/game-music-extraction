"""Corpus sample proofs and pre-inference isolation gates."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import nullcontext
from instrument_analysis.reviewed_corpus import build_reviewed_corpus, _sample_proof


class ReviewedCorpusTests(unittest.TestCase):
    def fixture(self, root, count=2):
        def write(name,data):
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(data if isinstance(data,bytes) else json.dumps(data).encode())
            return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        source=write('native.spc',bytes(range(256))+b'123456789abcdefghij')
        gm=write('gm.json',{})
        route={'kind':'gm_program','program_0based':0,'program_1based':1}
        ref={'probe_id':'probe','manifest_sha256':gm['sha256'],'soundfont_sha256':'f'*64,'bank':0,'route':route,'note':60,'conditions':{'hold_seconds':1,'tail_seconds':1},'features':{'valid':True}}
        index=write('index.json',{'manifests':[gm],'references':[ref]})
        cases=[]
        for i in range(count):
            offset=256+9*i;data=Path(source['path']).read_bytes()[offset:offset+9]
            native={'source_record_id':'record-'+str(i),'source':{'spc_path':source['path'],'spc_sha256':source['sha256']},'instrument':{'sample_start_address':9*i,'sample_end_address_exclusive':9*i+9}}
            cases.append({'case_id':'case-'+str(i),'record_id':native['source_record_id'],'split':'development','source_kind':'native','game_id':'game','origin_family_id':'family-'+str(i),'native_manifest':write(str(i)+'/native.json',native),'pitch_calibration':write(str(i)+'/pitch.json',{}),'sample':{'source':source,'offset':offset,'length':9,'sha256':hashlib.sha256(data).hexdigest(),'encoding':'BRR'}})
        plan={'schema_version':'instrument-reviewed-corpus-plan/v1','index':index,'policy':{},'lineage_links':[],'cases':cases}
        return plan,write,ref

    def test_sample_span_must_match_renderer_even_with_valid_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan,_,_=self.fixture(root)
            case=plan['cases'][0];native=json.loads(Path(case['native_manifest']['path']).read_text())
            _sample_proof(case,native,Path(case['native_manifest']['path']),lambda r:Path(r['path']))
            # Hash proof alone is insufficient: the renderer selected another span.
            native['instrument']['sample_start_address']=1
            with self.assertRaisesRegex(ValueError,'renderer source'):
                _sample_proof(case,native,Path(case['native_manifest']['path']),lambda r:Path(r['path']))
            case['sample']['sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'span hash'):
                _sample_proof(case,native,Path(case['native_manifest']['path']),lambda r:Path(r['path']))

    def test_split_leak_rejected_before_ranking(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan,write,_=self.fixture(root)
            plan['cases'][1]['split']='held_out';plan['cases'][1]['origin_family_id']=plan['cases'][0]['origin_family_id']
            pref=write('plan.json',plan)
            with patch('instrument_analysis.reviewed_corpus.rank_native_source') as rank:
                with self.assertRaisesRegex(ValueError,'split leakage'):
                    build_reviewed_corpus(pref['path'],output_dir=root/'out')
                rank.assert_not_called()
                self.assertFalse((root/'out').exists())

    def test_all_diagnostics_frozen_before_first_comparison_and_relative_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan,write,ref=self.fixture(root);pref=write('plan.json',plan)
            events=[]
            def rank(native,cal,index,*,output_dir,top_k,prepared_index):
                self.assertEqual(top_k,128);events.append('rank')
                output_dir.mkdir(parents=True)
                diagnostic={'schema_version':'instrument-ranking-diagnostics/v1','query':{'source_record_id':json.loads(native.read_text())['source_record_id'],'calibrated_midi_note':60,'event_boundaries_seconds':{'note_on':0,'note_off':1,'end':2}},'candidates':[dict(ref,rank=1,distance=.1)]}
                (output_dir/'diagnostics.json').write_text(json.dumps(diagnostic));(output_dir/'predictions.json').write_text('{}')
                return {},diagnostic
            def compare(*args,output_dir,**kwargs):
                self.assertEqual(events.count('rank'),2);events.append('compare')
                output_dir.mkdir(parents=True);(output_dir/'p.comparison.json').write_text('{}')
            def package(spec_path,output_dir):
                self.assertEqual(events.count('rank'),2);events.append('package')
                spec=json.loads(spec_path.read_text());self.assertTrue(spec['blind'])
                self.assertFalse(Path(spec['diagnostics']['path']).is_absolute())
                self.assertIn('reference',spec['candidates'][0]['evidence'])
                output_dir.mkdir();(output_dir/'manifest.json').write_text('{}')
            with patch('instrument_analysis.reviewed_corpus.prepared_reference_index',return_value=nullcontext(object())), patch('instrument_analysis.reviewed_corpus.rank_native_source',side_effect=rank), patch('instrument_analysis.reviewed_corpus.build_matched_comparison',side_effect=compare), patch('instrument_analysis.reviewed_corpus.build_review_package',side_effect=package):
                corpus=build_reviewed_corpus(pref['path'],output_dir=root/'out',audio_top_k=1)
            self.assertEqual(events[:2],['rank','rank'])
            for case in corpus['cases']:
                for field in ('diagnostics','review_package'):
                    self.assertTrue((root/'out'/case[field]['path']).is_file())
                    self.assertNotIn('.out-',case[field]['path'])
            with self.assertRaises(FileExistsError):build_reviewed_corpus(pref['path'],output_dir=root/'out')
