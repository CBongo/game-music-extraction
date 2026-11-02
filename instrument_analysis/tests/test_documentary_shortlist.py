"""Documentary rules remain independent from acoustic ranks and uncertainty."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from instrument_analysis.tests import test_origin_pilot as fixtures
from instrument_analysis.documentary_shortlist import build_documentary_shortlist, _origin, _rule

class DocumentaryShortlistTests(unittest.TestCase):
    def test_declared_imported_subjects_ignore_source_preset_ordinals(self):
        for subject, programs in [('Pan Flute',[75,73,72,74]),('Oboe',[68,69,70,71]),('Harp',[46])]:
            binding={'origin':{'external_report_state':'reported','claim':{
                'raw_cells':[subject,'$03','Source synth','Preset bank','999 unrelated preset']}}}
            self.assertEqual(_rule(binding)['predeclared_programs'],programs)
            # No acoustic ranking, preset number, or named model enters the rule.
            binding['origin']['claim']['raw_cells'][4]='001 Another preset'
            self.assertEqual(_rule(binding)['predeclared_programs'],programs)
            for state in ('qualified','doubted','reported_transform'):
                binding['origin']['external_report_state']=state
                self.assertEqual(_rule(binding)['status'],'abstain')
                self.assertEqual(_rule(binding)['predeclared_programs'],[])

    def test_imported_origin_dispatch_preserves_validation_errors(self):
        path=self.root/'generic-bindings.json'
        path.write_text(json.dumps({'schema_version':'instrument-imported-origin-bindings/v1'}))
        expected=({'record_id':'generic-source'},self.root/'imported-claims.json')
        with patch('instrument_analysis.imported_bindings.validate_imported_binding',return_value=expected) as validate:
            self.assertEqual(_origin(path,'generic-source'),expected)
            validate.assert_called_once_with(path,'generic-source')
        for message in ('imported claim changed','stale helper input'):
            with patch('instrument_analysis.imported_bindings.validate_imported_binding',side_effect=ValueError(message)):
                with self.assertRaisesRegex(ValueError,message):_origin(path,'generic-source')

    def setUp(self):
        self.fixture=fixtures.OriginPilotTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root;self.fixture.build()
        self.bindings=self.root/'output/bindings.json';self.index=self.root/'index.json';self.index.write_text('{}')
    def build(self,record='snes:ff4-us:global-sample-07',**kwargs):
        with patch('instrument_analysis.documentary_shortlist.extract_ff4_sample',side_effect=lambda p,n:self.fixture.extract('Final Fantasy IV',p,n)),patch('instrument_analysis.documentary_shortlist.extract_ff6_sample',side_effect=lambda p,n:self.fixture.extract('Final Fantasy VI',p,n)),patch('instrument_analysis.documentary_shortlist._index_for_query',return_value={'references':[]}):
            return build_documentary_shortlist(self.bindings,self.index,source_record_id=record,**kwargs)
    def test_fixed_flute_family_and_controls_are_not_audio_fit(self):
        for game in ('ff4','ff6'):
            report=self.build(f'snes:{game}-us:global-sample-07')
            self.assertEqual([c['route']['program_0based'] for c in report['candidates']],[73,72,74,75])
            self.assertEqual([c['documentary_priority'] for c in report['candidates']],[1,2,2,2])
            self.assertEqual(report['audition_gate']['status'],'metadata_only')
            self.assertTrue(report['no_fused_winner'])
            self.assertTrue(all(not c['reference_variants'] and c['acoustic'] is None for c in report['candidates']))
        for sample in ('16','1f','30'):
            report=self.build('snes:ff6-us:global-sample-'+sample)
            self.assertEqual(report['candidates'],[])
            self.assertEqual(report['rule']['status'],'abstain')
            self.assertIn(report['origin']['external_report_state'],('qualified','doubted','reported_transform'))
    def test_binding_catalog_brr_and_rom_tamper(self):
        before=self.bindings.read_bytes();obj=json.loads(before)
        obj['bindings'][0]['origin']['claim']['raw_cells'][2]='changed'
        self.bindings.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):self.build()
        self.bindings.write_bytes(before)
        artifact=self.root/'output/ff4-flute-07/sample.brr';artifact.write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.build()
    def test_explicit_gm_numbering_and_unknown_hypothesis(self):
        binding={'origin':{'external_report_state':'reported','claim':{'selection_sha256':'a'*64,'raw_cells':['Pan Flute','$07','Roland JV1080','PR-D (GM)','076 Pan Flute']},'gm_reference':{'map_kind':'GM','program_1based':76,'numbering_convention':'one_based_gm_1_to_128','evidence':{'selection_sha256':'a'*64,'bank_raw':'PR-D (GM)','program_raw':'076 Pan Flute','numbering_basis':'verified_one_based_gm_bank'}}}}
        self.assertEqual(_rule(binding)['predeclared_programs'],[75])
        binding['origin']['gm_reference']['numbering_convention']='maybe_zero_based'
        with self.assertRaises(ValueError):_rule(binding)
        del binding['origin']['gm_reference']
        binding['origin']['claim']['raw_cells'][0]='Unknown'
        self.assertEqual(_rule(binding)['predeclared_programs'],[])
    def test_independent_audio_rank_and_documentary_priority_do_not_merge(self):
        route={'kind':'gm_program','program_0based':73,'program_1based':74}
        ref={'probe_id':'probe73','soundfont_sha256':'f'*64,'route':route,'bank':0,'note':60,'conditions':{'hold_seconds':.5,'tail_seconds':.5},'features':{'valid':True}}
        baseline={'status':'ranked','query':{'event_boundaries_seconds':{'note_on':0,'note_off':.5,'end':1}},'functional':{'status':'not_scored'},'candidates':[{'route':route,'probe_id':'probe73','soundfont_sha256':'f'*64,'bank':0,'rank':95,'distance':.7}]}
        with patch('instrument_analysis.documentary_shortlist._baseline',return_value=(baseline,{'status':'eligible','calibrated_midi_note':60})),patch('instrument_analysis.documentary_shortlist._index_for_query',return_value={'references':[ref]}):
            with patch('instrument_analysis.documentary_shortlist.extract_ff4_sample',side_effect=lambda p,n:self.fixture.extract('Final Fantasy IV',p,n)):
                report=build_documentary_shortlist(self.bindings,self.index,source_record_id='snes:ff4-us:global-sample-07')
        candidate=report['candidates'][0]
        self.assertEqual(candidate['documentary_priority'],1);self.assertEqual(candidate['acoustic']['rank'],95)
        self.assertEqual(candidate['reference_variants'],[ref]);self.assertEqual(report['candidates'][1]['reference_variants'],[])
    def test_immutable_publication_and_input_unchanged(self):
        before=self.bindings.read_bytes();report=self.build(output_dir=self.root/'shortlist')
        self.assertEqual(json.loads((self.root/'shortlist/manifest.json').read_text()),report)
        self.assertEqual(before,self.bindings.read_bytes())
        with self.assertRaises(FileExistsError):self.build(output_dir=self.root/'shortlist')
    def test_wrong_source_or_index_baseline_rejects(self):
        p=self.root/'baseline.json';p.write_text(json.dumps({'schema_version':'instrument-ranking-diagnostics/v1','query':{'source_record_id':'wrong'},'index':{'sha256':'x'*64}}))
        with self.assertRaises(ValueError):self.build(audio_diagnostics_path=p)

    def test_acoustic_gate_checks_native_sample_and_weak_pitch(self):
        import hashlib
        import wave
        from instrument_analysis.documentary_shortlist import _baseline
        binding=json.loads(self.bindings.read_text())['bindings'][0]
        brr=(self.root/'output/ff4-flute-07/sample.brr').read_bytes()
        ram=bytearray(65536);ram[0x3000:0x3000+len(brr)]=brr
        spc=self.root/'sample.spc';spc.write_bytes(bytes(256)+ram)
        wav=self.root/'native.wav'
        with wave.open(str(wav),'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(b'\x00\x10'*8000)
        def ref(path):return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        native={'source_record_id':binding['record_id'],'engine':'MAME SNES S-DSP','key':{'pitch_register_raw_s_dsp':4096},
            'source':{'spc_path':str(spc),'spc_sha256':ref(spc)['sha256']},'instrument':{'sample_start_address':0x3000,'sample_end_address_exclusive':0x3000+len(brr)},
            'wav':{'sha256':ref(wav)['sha256'],'sample_rate':8000,'channels':1,'sample_width_bytes':2,'frames':8000}}
        receipt=self.root/'receipt.json';receipt.write_text(json.dumps({'status':'ok','note_on_time_seconds':0,'note_off_time_seconds':.5,'capture_end_time_seconds':1}))
        native['receipt_file']='receipt.json';native['conditions']={'note_on_time_seconds':0,'note_off_time_seconds':.5,'capture_end_time_seconds':1,'wav_alignment':{'actual_trim_start_seconds':0,'alignment_uncertainty_seconds':0}}
        n=self.root/'native.json';n.write_text(json.dumps(native))
        cal=self.root/'calibration.json';cal.write_text(json.dumps({'status':'calibrated','basis':'acoustic_measurement','confidence':.7}))
        baseline={'schema_version':'instrument-ranking-diagnostics/v1','index':ref(self.index),'query':{'kind':'native','source_record_id':binding['record_id'],
            'native_manifest':ref(n),'native_wav':ref(wav),'native_receipt':ref(receipt),'alignment_uncertainty_seconds':0,'pitch_calibration':ref(cal),'calibrated_midi_note':60,
            'event_boundaries_seconds':{'note_on':0,'note_off':.5,'end':1}}}
        p=self.root/'baseline.json';p.write_text(json.dumps(baseline))
        self.assertEqual(_baseline(p,self.index,binding)[1]['status'],'metadata_only')
        cal.write_text(json.dumps({'status':'calibrated','basis':'acoustic_measurement','confidence':.99}))
        baseline['query']['pitch_calibration']=ref(cal);p.write_text(json.dumps(baseline))
        with patch('instrument_analysis.documentary_shortlist._calibration',return_value=(60,ref(cal))):
            self.assertEqual(_baseline(p,self.index,binding)[1]['status'],'eligible')
        baseline['query']['event_boundaries_seconds']['note_off']=.4;p.write_text(json.dumps(baseline))
        with self.assertRaisesRegex(ValueError,'phase differs'):_baseline(p,self.index,binding)
        baseline['query']['event_boundaries_seconds']['note_off']=.5;baseline['query']['native_receipt']['sha256']='0'*64;p.write_text(json.dumps(baseline))
        with self.assertRaisesRegex(ValueError,'stale'):_baseline(p,self.index,binding)
        baseline['query']['native_receipt']=ref(receipt)
        ram[0x3000]=99;spc.write_bytes(bytes(256)+ram);native['source']['spc_sha256']=ref(spc)['sha256'];n.write_text(json.dumps(native))
        baseline['query']['native_manifest']=ref(n);p.write_text(json.dumps(baseline))
        with self.assertRaisesRegex(ValueError,'BRR differs'):_baseline(p,self.index,binding)

    def test_non_gm_source_preset_ordinal_cannot_become_gm_map(self):
        report=json.loads(self.bindings.read_text())
        for binding in report['bindings']:
            claim=binding['origin']['claim'];cells=claim['raw_cells']
            binding['origin']['gm_reference']={'map_kind':'GM','numbering_convention':'one_based_gm_1_to_128','program_1based':92,
                'evidence':{'selection_sha256':claim['selection_sha256'],'bank_raw':cells[3],'program_raw':cells[4],'numbering_basis':'verified_one_based_gm_bank'}}
            if binding['origin']['external_report_state']=='reported':
                with self.assertRaisesRegex(ValueError,'explicit GM-bank'):_rule(binding)
            else:self.assertEqual(_rule(binding)['status'],'abstain')

    def test_duplicate_probe_names_resolve_by_phase_before_uniqueness(self):
        route={'kind':'gm_program','program_0based':73,'program_1based':74}
        ref={'probe_id':'shared','soundfont_sha256':'f'*64,'route':route,'bank':0,'note':60,'manifest_sha256':'correct-manifest',
            'conditions':{'hold_seconds':.5,'tail_seconds':.5},'features':{'valid':True}}
        other=deepcopy(ref);other['manifest_sha256']='other-phase';other['conditions']={'hold_seconds':.92,'tail_seconds':.08}
        baseline={'status':'abstain','query':{'event_boundaries_seconds':{'note_on':0,'note_off':.5,'end':1}},
            'candidates':[{'route':route,'probe_id':'shared','soundfont_sha256':'f'*64,'bank':0,'rank':33,'distance':.2}]}
        def run(refs):
            with patch('instrument_analysis.documentary_shortlist._baseline',return_value=(baseline,{'status':'eligible','calibrated_midi_note':60})),patch('instrument_analysis.documentary_shortlist._index_for_query',return_value={'references':refs}),patch('instrument_analysis.documentary_shortlist.extract_ff4_sample',side_effect=lambda p,n:self.fixture.extract('Final Fantasy IV',p,n)):
                return build_documentary_shortlist(self.bindings,self.index,source_record_id='snes:ff4-us:global-sample-07')
        report=run([other,ref])
        self.assertEqual(report['candidates'][0]['reference_variants'][0]['manifest_sha256'],'correct-manifest')
        self.assertEqual(report['candidates'][0]['acoustic']['rank'],33)
        ambiguous=deepcopy(ref);ambiguous['manifest_sha256']='same-phase-other-manifest'
        with self.assertRaisesRegex(ValueError,'ambiguous eligible phase'):run([ref,ambiguous])
