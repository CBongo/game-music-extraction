"""Calibration selection must not learn from controls or held-out judgments."""
import unittest
from instrument_analysis.threshold_calibration import _select, _metrics


class ThresholdSelectionTests(unittest.TestCase):
    def setUp(self):
        self.policy={'distance_grid':[.1,.2,.4], 'margin_grid':[0,.1,.2], 'max_false_accept_rate':0}
        self.development=[{'rank':1,'distance':.1,'margin':.2,'top1_correct':True}, {'distance':.2,'margin':.1,'top1_correct':True}, {'distance':.3,'margin':.2,'top1_correct':False}]

    def test_max_correct_with_no_empirical_false_accept(self):
        self.assertEqual(_select(self.development,self.policy),{'distance_threshold':.2,'margin_threshold':.1})

    def test_conservative_tie(self):
        self.assertEqual(_select(self.development[:1]+self.development[2:],self.policy),{'distance_threshold':.1,'margin_threshold':.2})

    def test_empty_evaluation_has_no_accuracy_or_uncertainty(self):
        result=_metrics([],None)
        self.assertIsNone(result['selective_accuracy'])
        self.assertIsNone(result['coverage'])
        self.assertIsNone(result['uncertainty'])

    def test_unreviewed_not_negative(self):
        result=_metrics([{'rank':1,'distance':.1,'margin':.2,'top1_correct':None}],{'distance_threshold':.4,'margin_threshold':0})
        self.assertEqual(result['accepted_incorrect'],0)
        self.assertEqual(result['accepted_unreviewed'],1)
        self.assertEqual(result['coverage'],1)
        self.assertEqual(result['reviewed_top1_cases'],0)
        self.assertIsNone(result['selective_accuracy'])

    def test_false_accept_denominator_is_reviewed_negatives(self):
        policy=dict(self.policy,max_false_accept_rate=.5)
        rows=self.development+[{'distance':.9,'margin':.1,'top1_correct':False}]
        self.assertEqual(_select(rows,policy)['distance_threshold'],.4)

    def test_partial_labels_do_not_suppress_operator_coverage(self):
        rows=[{'distance':.1,'margin':.2,'top1_correct':True},
              {'distance':.1,'margin':.2,'top1_correct':None},
              {'distance':None,'margin':None,'top1_correct':None}]
        result=_metrics(rows,{'distance_threshold':.2,'margin_threshold':.1})
        self.assertEqual(result['coverage'],2/3)
        self.assertEqual(result['accepted_correct'],1)
        self.assertEqual(result['accepted_unreviewed'],1)
        self.assertEqual(result['selective_accuracy'],1)
        self.assertIsNone(_metrics(rows,None)['coverage'])





class CalibrationWrapperTests(unittest.TestCase):
    def test_no_labels_cannot_fit_and_documentary_input_rejected(self):
        import hashlib,json,tempfile,types,sys
        from pathlib import Path
        from unittest.mock import patch
        from instrument_analysis.threshold_calibration import fit_thresholds
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def write(name,value):
                p=root/name;p.write_text(json.dumps(value));return {'path':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            index=write('index.json',{'references':[{'probe_id':'a','soundfont_sha256':'f'*64,'bank':0,'route':{'kind':'gm_program','program_0based':0},'manifest_sha256':'e'*64,'note':60,'conditions':{'hold_seconds':1,'tail_seconds':1},'features':{'valid':True}}]})
            package={'case_id':'c','source_record_id':'r','artifacts':{},'candidates':[]}
            package_ref=write('manifest.json',package)
            diag={'schema_version':'instrument-ranking-diagnostics/v1','query':{'source_record_id':'r','kind':'native','calibrated_midi_note':60,'event_boundaries_seconds':{'note_on':0,'note_off':1,'end':2}},'index':index,'candidates':[{'rank':1,'distance':.1,'probe_id':'a','soundfont_sha256':'f'*64,'bank':0,'route':{'kind':'gm_program','program_0based':0}}, {'rank':2,'distance':.3,'route':{'kind':'gm_program','program_0based':1}}]}
            diag_ref=write('diagnostic.json',diag)
            case={'case_id':'c','record_id':'r','split':'development','source_kind':'native','game_id':'game','sample_sha256':'a'*64,'origin_family_id':'family','diagnostics':diag_ref,'review_package':package_ref}
            corpus=write('corpus.json',{'schema_version':'instrument-reviewed-corpus/v1','cases':[case],'lineage_links':[],'policy':{'distance_grid':[.2],'margin_grid':[.1],'max_false_accept_rate':0,'min_reviewed_development_cases':4,'min_reviewed_development_families':3}})
            fake=types.ModuleType('instrument_analysis.candidate_reviews');fake.load_verified_candidate_reviews=lambda *_:[]
            with patch.dict(sys.modules,{'instrument_analysis.candidate_reviews':fake}), patch('instrument_analysis.threshold_calibration.verify_package',return_value=package):
                report=fit_thresholds(root/'corpus.json',store_dir=root/'store',output_dir=root/'out')
                self.assertEqual(report['status'],'awaiting_reviews')
                self.assertIsNone(report['thresholds'])
                self.assertEqual(report['pending_cases'],1)
                def changing_store(*args):
                    (root/'store').mkdir(exist_ok=True)
                    (root/'store'/'new-review.json').write_text('{}')
                    return []
                fake.load_verified_candidate_reviews=changing_store
                with self.assertRaisesRegex(ValueError,'review store changed while loading labels'):
                    fit_thresholds(root/'corpus.json',store_dir=root/'store',output_dir=root/'raced')
                self.assertFalse((root/'raced').exists())
                fake.load_verified_candidate_reviews=lambda *_:[]
                with self.assertRaises(FileExistsError):fit_thresholds(root/'corpus.json',store_dir=root/'store',output_dir=root/'out')
                diag['query']['source_facts']={'documentary':'label'}
                case['diagnostics']=write('diagnostic.json',diag)
                write('corpus.json',{'schema_version':'instrument-reviewed-corpus/v1','cases':[case],'policy':report['policy']})
                with self.assertRaisesRegex(ValueError,'documentary'):
                    fit_thresholds(root/'corpus.json',store_dir=root/'store',output_dir=root/'contaminated')



class CalibrationIntegrationTests(unittest.TestCase):
    def test_heldout_labels_and_controls_do_not_change_fit(self):
        import hashlib,json,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from instrument_analysis.review import _digest
        from instrument_analysis.threshold_calibration import fit_thresholds
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def write(name,value):
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            route={'kind':'gm_program','program_0based':0,'program_1based':1}
            ref={'probe_id':'a','soundfont_sha256':'f'*64,'bank':0,'route':route,'manifest_sha256':'e'*64,'note':60,'conditions':{'hold_seconds':1,'tail_seconds':1},'features':{'valid':True}}
            other=dict(ref,conditions={'hold_seconds':2,'tail_seconds':1})
            index=write('index.json',{'references':[other,ref]})
            cases=[];labels=[]
            policy={'distance_grid':[.2,.4],'margin_grid':[.1],'max_false_accept_rate':0,'min_reviewed_development_cases':4,'min_reviewed_development_families':3}
            for i in range(6):
                cid='case-'+str(i);rid='record-'+str(i)
                package={'schema_version':'instrument-review-package/v1','case_id':cid,'source_record_id':rid,'artifacts':{},'candidates':[{'candidate_id':'a','reference':ref}]}
                # Use the repository's package schema and content digest.
                from instrument_analysis.review import VERSION
                package['schema_version']=VERSION;package['package_id']=_digest(package)
                pref=write(cid+'/manifest.json',package)
                d=.1 if i%2==0 else .3
                diag={'schema_version':'instrument-ranking-diagnostics/v1','query':{'kind':'native','source_record_id':rid,'calibrated_midi_note':60,'event_boundaries_seconds':{'note_on':0,'note_off':1,'end':2}},'index':index,'candidates':[dict(ref,rank=1,distance=d),{'rank':2,'distance':d+.2,'route':{'kind':'gm_program','program_0based':1,'program_1based':2}}]}
                dref=write(cid+'/diag.json',diag)
                cases.append({'case_id':cid,'record_id':rid,'split':'development' if i<4 else 'held_out' if i==4 else 'control','source_kind':'native' if i<5 else 'synthetic_control','game_id':'development-game' if i<4 else 'heldout-game','origin_family_id':'family-'+str(i),'sample_sha256':hashlib.sha256(cid.encode()).hexdigest(),'diagnostics':dref,'review_package':pref})
                labels.append({'case_id':cid,'candidate_assessments':[{'candidate_id':'a','decision':'acceptable' if i%2==0 else 'unacceptable','reference':ref}]})
            def run(name):
                write('corpus.json',{'schema_version':'instrument-reviewed-corpus/v1','cases':cases,'policy':policy})
                with patch('instrument_analysis.candidate_reviews.load_verified_candidate_reviews',return_value=labels):
                    return fit_thresholds(root/'corpus.json',store_dir=root/'store',output_dir=root/name)
            report=run('one')
            self.assertEqual(report['status'],'fitted')
            self.assertEqual(report['thresholds']['distance_threshold'],.2)
            labels.append(labels[0].copy())
            deduplicated=run('deduplicated')
            self.assertEqual(deduplicated['cases'][0]['reviewed_candidates'],1)
            labels.pop()
            labels[4]['candidate_assessments'][0]['decision']='unacceptable'
            labels[5]['candidate_assessments'][0]['decision']='acceptable'
            changed=run('two')
            self.assertEqual(report['thresholds'],changed['thresholds'])
            self.assertNotEqual(report['metrics']['held_out']['accepted_correct'],changed['metrics']['held_out']['accepted_correct'])
            # Only one reviewed class in development cannot calibrate.
            for label in labels[:4]:label['candidate_assessments'][0]['decision']='acceptable'
            insufficient=run('three')
            self.assertEqual(insufficient['status'],'insufficient_reviewed_classes')
            self.assertIsNone(insufficient['thresholds'])
            for i,label in enumerate(labels[:4]):label['candidate_assessments'][0]['decision']='acceptable' if i%2==0 else 'unacceptable'
            # Three family aliases share known bytes: only two independent groups.
            for i in (1,2):cases[i]['sample_sha256']=cases[0]['sample_sha256']
            aliased=run('aliased')
            self.assertEqual(aliased['status'],'insufficient_reviewed_classes')
            self.assertIsNone(aliased['thresholds'])
            # A memoized global file must still reject a contradictory declaration.
            diagnostic_path=Path(cases[1]['diagnostics']['path'])
            original_diagnostic=diagnostic_path.read_bytes()
            contradictory=json.loads(original_diagnostic)
            contradictory['index']['sha256']='0'*64
            cases[1]['diagnostics']=write('case-1/diag.json',contradictory)
            with self.assertRaisesRegex(ValueError,'conflicting input hash'):
                run('contradictory')
            diagnostic_path.write_bytes(original_diagnostic)
            cases[1]['diagnostics']['sha256']=hashlib.sha256(original_diagnostic).hexdigest()
            # Cached inputs are checked again before immutable publication.
            index_path=Path(index['path']);original_index=index_path.read_bytes()
            real_metrics=_metrics
            def mutate_after_reads(*args):
                index_path.write_bytes(original_index+b' ')
                return real_metrics(*args)
            with patch('instrument_analysis.threshold_calibration._metrics',side_effect=mutate_after_reads):
                with self.assertRaisesRegex(ValueError,'input changed during calibration'):
                    run('mutated')
            self.assertFalse((root/'mutated').exists())
            index_path.write_bytes(original_index)
            cases[4]['game_id']='development-game'
            with self.assertRaisesRegex(ValueError,'game appears'):
                run('leaked')
