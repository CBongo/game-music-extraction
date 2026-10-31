import json
from pathlib import Path
import tempfile
import unittest
import wave
from instrument_analysis.review import build_review_package, verify_package, import_judgment, export_judgments
from instrument_analysis.comparisons import _hash

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        for key in ('source','analysis','diagnostics'):
            (self.root/(key+'.json')).write_text(json.dumps({'source_record_id':'source-1', 'observed_offset': 12, 'rank':1}))
        for name in ('a.wav','b.wav'):
            with wave.open(str(self.root/name),'wb') as w:
                w.setnchannels(1);w.setframerate(8000);w.setsampwidth(2);w.writeframes(b'\x00\x10'*100)
        def ref(name): return {'path':name,'sha256':_hash(self.root/name)}
        (self.root/'source.json').write_text(json.dumps({'source_record_id':'source-1','wav':ref('a.wav')}))
        (self.root/'preview.mid').write_bytes(b'MThd exact MIDI recipe')
        (self.root/'render.json').write_text(json.dumps({'schema_version':'instrument-composite-preview/v1','source_record_id':'source-1','wav':dict(ref('b.wav'),file='b.wav'),'midi':dict(ref('preview.mid'),file='preview.mid'),'input':{'analysis':ref('analysis.json'),'diagnostics':ref('diagnostics.json')}}))
        self.spec = {'schema_version':'instrument-review-spec/v1','source_record_id':'source-1','case_id':'benchmark-case','blind':True,'ordering_seed':3, **{k:ref(k+'.json') for k in ('source','analysis','diagnostics')},'candidates':[{'candidate_id':'secret-program','source_record_id':'source-1','render_manifest':ref('render.json'),'paired_wavs':{'source':ref('a.wav'),'candidate':ref('b.wav')},'render_provenance':{'context':'isolated trigger','gain_matching':'already_matched'},'route':{'kind':'gm_program','program_0based':95}}]}
    def build(self):
        (self.root/'spec.json').write_text(json.dumps(self.spec))
        return build_review_package(self.root/'spec.json', self.root/'package')
    def judgment(self, manifest, **updates):
        j={'schema_version':'instrument-review-judgment/v1','package_id':manifest['package_id'],'source_record_id':'source-1','reviewer_id':'reviewer','decision':'accepted','candidate_id':'secret-program','revealed':False}
        j.update(updates)
        p=self.root/'judgment.json';p.write_text(json.dumps(j));return p
    def test_roundtrip_and_immutable(self):
        m=self.build();self.assertEqual(verify_package(self.root/'package'),m)
        j=self.judgment(m)
        row=import_judgment(self.root/'package',j,self.root/'store')
        self.assertEqual(row['audition_status'],'unchanged_probe')
        with self.assertRaises(FileExistsError): import_judgment(self.root/'package',j,self.root/'store')
        with self.assertRaises(FileExistsError): self.build()
        result=export_judgments(self.root/'store',self.root/'export.jsonl')
        self.assertEqual(result['exported'],1)
        self.assertEqual(json.loads((self.root/'export.jsonl.benchmark.jsonl').read_text())['case_id'],'benchmark-case')
    def test_tamper(self):
        m=self.build();p=self.root/'package'/m['candidates'][0]['source_audio'];p.write_bytes(b'bad')
        with self.assertRaises(ValueError):verify_package(self.root/'package')
    def test_input_hash_and_source_mismatch(self):
        self.spec['analysis']['sha256']='0'*64
        with self.assertRaises(ValueError):self.build()
        self.spec['analysis']['sha256']=_hash(self.root/'analysis.json')
        self.spec['candidates'][0]['source_record_id']='other'
        with self.assertRaises(ValueError):self.build()
    def test_invalid_selection_and_corrections(self):
        m=self.build()
        for change in ({'candidate_id':'bad'},{'decision':'no_match'},{'octave_correction':True},{'chord_offsets_correction':[1.5]},{'correction_requested':1},{'decision':'no_match','candidate_id':None,'correction_requested':True}):
            with self.assertRaises(ValueError):import_judgment(self.root/'package',self.judgment(m,**change),self.root/'store')
        row=import_judgment(self.root/'package',self.judgment(m,octave_correction=1),self.root/'store')
        self.assertEqual(row['audition_status'],'requires_rerender')
        self.assertEqual(export_judgments(self.root/'store',self.root/'export.jsonl')['benchmark_exported'],0)
    def test_blind_ui_and_reveal_exclusion(self):
        m=self.build();html=(self.root/'package/index.html').read_text()
        before_script=html.split('<script>')[0]
        self.assertNotIn('secret-program',before_script)
        self.assertNotIn('autoplay',html)
        self.assertIn('if(revealed)details()',html)
        import_judgment(self.root/'package',self.judgment(m,revealed=True),self.root/'store')
        self.assertEqual(export_judgments(self.root/'store',self.root/'export.jsonl')['benchmark_exported'],0)

    def test_strict_records_routes_numbers_and_duplicates(self):
        for mutate in (
            lambda: self.spec['candidates'][0].update(route={}),
            lambda: self.spec['candidates'].append(dict(self.spec['candidates'][0])),
            lambda: self.spec.update(ordering_seed=float('nan')),
            lambda: self.spec['candidates'][0]['render_manifest'].update(sha256='0'*64),
        ):
            original=json.loads(json.dumps(self.spec));mutate()
            with self.assertRaises(ValueError): self.build()
            self.spec=original
        p=self.root/'analysis.json'
        p.write_text(json.dumps({'records':[{'record_id':'source-1'},{'record_id':'source-1'}]}))
        self.spec['analysis']['sha256']=_hash(p)
        with self.assertRaises(ValueError):self.build()

    def test_comparison_binding(self):
        def ref(name):return {'path':str(self.root/name),'sha256':_hash(self.root/name)}
        def wav(name):return {'file':name,'sha256':_hash(self.root/name),'sample_rate':8000,'channels':1,'sample_width_bytes':2,'frames':100}
        comparison={'schema_version':'instrument-comparison/v1','source_record_id':'source-1','input':{'native_manifest':ref('source.json')},'outputs':{'native_excerpt':wav('a.wav'),'gm_excerpt':wav('b.wav'),'ab_preview':wav('a.wav')},'route':{'kind':'gm_program','program_0based':5,'program_1based':6,'bank':0,'preset_name':'Example'},'conditions':{'dry':True}}
        p=self.root/'comparison.json';p.write_text(json.dumps(comparison))
        self.spec['candidates']=[{'candidate_id':'native-comparison','comparison':ref('comparison.json')}]
        manifest=self.build()
        self.assertEqual(manifest['candidates'][0]['route']['program_0based'],5)
        j=self.judgment(manifest,candidate_id='native-comparison')
        import_judgment(self.root/'package',j,self.root/'store')
        export_judgments(self.root/'store',self.root/'export.jsonl')
        self.check_benchmark(self.root/'export.jsonl.benchmark.jsonl', {'kind':'gm_program','program_0based':5,'program_1based':6})
        import shutil
        shutil.rmtree(self.root/'package')
        comparison['input']['native_manifest']['sha256']='0'*64
        p.write_text(json.dumps(comparison));self.spec['candidates'][0]['comparison']=ref('comparison.json')
        with self.assertRaises(ValueError):self.build()

    def test_global_levels_and_decision_exports(self):
        candidate=json.loads(json.dumps(self.spec['candidates'][0]));candidate['candidate_id']='second'
        self.spec['candidates'].append(candidate)
        m=self.build();rows=m['candidates']
        self.assertEqual(_hash(self.root/'package'/rows[0]['source_audio']),_hash(self.root/'package'/rows[1]['source_audio']))
        targets=[r['gain_matching']['source_rms'][0]*r['gain_matching']['gains'][0] for r in rows]
        self.assertAlmostEqual(targets[0],targets[1])
        for decision,cid in [('no_match',None),('rejected','second'),('alternate_candidate','second')]:
            import_judgment(self.root/'package',self.judgment(m,decision=decision,candidate_id=cid),self.root/'store')
        result=export_judgments(self.root/'store',self.root/'export.jsonl')
        self.assertEqual(result['exported'],3);self.assertEqual(result['benchmark_exported'],1)

    def test_manifest_tamper_and_path_traversal(self):
        from instrument_analysis.review import _digest
        m=self.build();m['artifacts']['../source.json']=_hash(self.root/'source.json')
        m.pop('package_id');m['package_id']=_digest(m)
        (self.root/'package/manifest.json').write_text(json.dumps(m))
        with self.assertRaises(ValueError):verify_package(self.root/'package')

    def check_benchmark(self, path, route):
        from instrument_analysis.benchmark import evaluate_benchmark, load_review_preferences
        report=evaluate_benchmark({'schema_version':'instrument-benchmark/v1','cases':[{'case_id':'benchmark-case','record_id':'source-1','split':'test'}]}, {'schema_version':'instrument-analysis/v1','records':[{'record_id':'source-1','analysis':{'routing':[{'rank':1,'route':route}]}}]}, reviews=load_review_preferences(path))
        self.assertEqual(report['metrics']['human_blinded_preference_agreement']['value'],1)

    def test_percussion_benchmark_and_midi_binding(self):
        self.spec['candidates'][0]['route']={'kind':'gm_percussion','note':38,'name':'Snare','bank':128}
        m=self.build()
        self.assertTrue(any(name.endswith('.mid') for name in m['artifacts']))
        import_judgment(self.root/'package',self.judgment(m),self.root/'store')
        export_judgments(self.root/'store',self.root/'export.jsonl')
        self.check_benchmark(self.root/'export.jsonl.benchmark.jsonl',{'kind':'gm_percussion','note':38})

if __name__=='__main__': unittest.main()
