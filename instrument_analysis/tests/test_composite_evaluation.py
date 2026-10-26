"""Label isolation and coverage semantics for composite evaluation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from instrument_analysis.composite_evaluation import _score,evaluate_controls


class CompositeEvaluationTests(unittest.TestCase):
    def test_coverage_does_not_hide_partial_or_abstaining_reconstruction(self):
        labels=[{'source_record_id':'neutral','description':'truth','expected':'composite','component_offsets':[-12,4,7]}]
        base={'source_record_id':'neutral','status':'supported','component_offsets':[-12,4],'quality':'other'}
        report=_score([base],labels)
        self.assertEqual(report['metrics']['component_precision_paired'],1)
        self.assertAlmostEqual(report['metrics']['component_recall_all_positive'],2/3)
        self.assertEqual(report['metrics']['positive_exact_sets'],0)
        abstain=_score([{**base,'status':'abstain'}],labels)
        self.assertIsNone(abstain['metrics']['component_precision_paired'])
        self.assertEqual(abstain['metrics']['component_recall_all_positive'],0)

    def test_labels_are_read_only_after_neutral_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);inputs=root/'inputs.json';labels=root/'labels.json'
            inputs.write_text(json.dumps({'generator_sha256':'a'*64,'records':[{'source_record_id':'control-0000','probes':[{
                'wav_path':str(root/'neutral.wav'),'wav_sha256':'b'*64,'trigger_midi_note':60,
                'note_on_seconds':0,'end_seconds':1.2,'analysis_end_seconds':.95}]}]}))
            labels.write_text(json.dumps({'cases':[{'source_record_id':'control-0000','description':'secret-major',
                'expected':'composite','component_offsets':[-5,0,4]}]}))
            completed=False;original=Path.read_text
            def read(path,*args,**kwargs):
                if path==labels:self.assertTrue(completed)
                return original(path,*args,**kwargs)
            def analyzer(probes,**kwargs):
                nonlocal completed
                self.assertNotIn('secret-major',str(probes)+str(kwargs))
                self.assertNotIn('component_offsets',str(probes))
                self.assertEqual(probes[0]['render_conditions']['source_identity_kind'],'controlled_recipe')
                self.assertNotIn('source_sample_sha256',probes[0]['render_conditions'])
                out=kwargs['output_dir'];out.mkdir()
                for name in ['analysis.json','diagnostics.json']:(out/name).write_text('{}')
                completed=True
                return {},{'components':[],'status':'abstain','quality':'unknown','reason':'ambiguous','confidence_heuristic':0}
            with patch.object(Path,'read_text',read),patch('instrument_analysis.composite_evaluation.analyze_baked_chord',side_effect=analyzer):
                report=evaluate_controls(inputs,labels,root/'out')
            self.assertTrue(completed)
            self.assertEqual(report['metrics']['positive_exact_set_coverage'],0)


if __name__=='__main__':unittest.main()
