"""Correction rerender provenance and fresh-audition regression tests."""
import json
from pathlib import Path
import shutil
from unittest.mock import patch
import unittest
from instrument_analysis.tests import test_review as fixtures
from instrument_analysis.review import _hash, _digest, import_judgment, verify_package, export_judgments
from instrument_analysis.review_revisions import build_review_revision

class RevisionTests(unittest.TestCase):
    setUp = fixtures.ReviewTests.setUp
    build = fixtures.ReviewTests.build
    judgment = fixtures.ReviewTests.judgment
    def revision_fixture(self):
        render_path=self.root/'render.json';render=json.loads(render_path.read_text())
        render['conditions']={'trigger_midi_note':60,'duration_seconds':.01,'tail_seconds':.0025,'velocity':96}
        render_path.write_text(json.dumps(render))
        self.spec['candidates'][0]['render_manifest']['sha256']=_hash(render_path)
        parent=self.build()
        imported=import_judgment(self.root/'package',self.judgment(parent,octave_correction=1),self.root/'store')
        imported_path=self.root/'store'/(imported['judgment_id']+'.json')
        candidate=parent['candidates'][0]
        source=self.root/'package'/parent['evidence']['source']
        recipe={'schema_version':'instrument-sequence-recipe/v1','source_record_id':'source-1',
            'source':{'path':str(source),'sha256':_hash(source)},
            'provenance':{'context':'isolated','basis':'bound authored probe','timing_basis':'explicit','pitch_basis':'explicit','effects_complete':True,
                'source_context':{'original_render_manifest_sha256':_hash(render_path),'source_audio_sha256':parent['artifacts'][candidate['original_source_audio']]}},
            'events':[{'event_id':'one','midi_note':60,'start_seconds':0,'duration_seconds':.01,'velocity':96,'residual_cents':0,'relative_level_db':0}],
            'end_seconds':.0125}
        p=self.root/'recipe.json';p.write_text(json.dumps(recipe));return parent,imported_path,p
    def fake_render(self,recipe_path,**kwargs):
        out=Path(kwargs['output_dir']);out.mkdir()
        shutil.copyfile(self.root/'b.wav',out/'sequence.wav');(out/'sequence.mid').write_bytes(b'MIDI recipe')
        recipe=json.loads(Path(recipe_path).read_text())
        manifest={'schema_version':'instrument-sequence-preview/v1','source_record_id':'source-1','recipe':{'path':str(recipe_path),'sha256':_hash(Path(recipe_path))},'source':recipe['source'],
            'wav':{'file':'sequence.wav','sha256':_hash(out/'sequence.wav')},'midi':{'file':'sequence.mid','sha256':_hash(out/'sequence.mid')},'conditions':{},'correction':recipe['correction']}
        (out/'manifest.json').write_text(json.dumps(manifest));return manifest
    def test_revision_requires_fresh_audio_acknowledgment(self):
        parent,j,p=self.revision_fixture()
        with patch('instrument_analysis.sequence_rendering.render_sequence_preview',side_effect=self.fake_render) as render:
            revision=build_review_revision(self.root/'package',j,p,soundfont_path='unused',fluidsynth_path='unused',output_dir=self.root/'revision')
            self.assertEqual(render.call_args.kwargs['octave_adjustment'],1)
        self.assertNotEqual(parent['package_id'],revision['package_id'])
        self.assertEqual(revision['revision']['audition_status'],'rendered_pending_review')
        self.assertEqual(parent['facts']['analysis'],revision['facts']['analysis'])
        self.assertEqual(verify_package(self.root/'package'),parent)
        c=revision['candidates'][0]
        fresh=self.judgment(revision,candidate_id=c['candidate_id'])
        with self.assertRaises(ValueError):import_judgment(self.root/'revision',fresh,self.root/'fresh')
        fresh=self.judgment(revision,candidate_id=c['candidate_id'],audition_acknowledged=True,
            source_audio_sha256=revision['artifacts'][c['source_audio']],candidate_audio_sha256=revision['artifacts'][c['candidate_audio']])
        row=import_judgment(self.root/'revision',fresh,self.root/'fresh')
        self.assertEqual(row['audition_status'],'reviewer_reported_audition')
        self.assertEqual(export_judgments(self.root/'fresh',self.root/'fresh.jsonl')['benchmark_exported'],0)
    def test_revision_rejects_unrelated_context_and_judgment_tamper(self):
        parent,j,p=self.revision_fixture();recipe=json.loads(p.read_text());recipe['events'][0]['midi_note']=61;p.write_text(json.dumps(recipe))
        with patch('instrument_analysis.sequence_rendering.render_sequence_preview') as render:
            with self.assertRaises(ValueError):build_review_revision(self.root/'package',j,p,soundfont_path='unused',fluidsynth_path='unused',output_dir=self.root/'revision')
            render.assert_not_called()
        data=json.loads(j.read_text());data['judgment']['octave_correction']=2;j.write_text(json.dumps(data))
        with self.assertRaises(ValueError):build_review_revision(self.root/'package',j,p,soundfont_path='unused',fluidsynth_path='unused',output_dir=self.root/'revision')

    def test_revision_abstention_and_iterative_absolute_correction(self):
        from instrument_analysis.review import resolve_ref
        parent,j,p=self.revision_fixture()
        with patch('instrument_analysis.sequence_rendering.render_sequence_preview',side_effect=self.fake_render):
            revision=build_review_revision(self.root/'package',j,p,soundfont_path='unused',fluidsynth_path='unused',output_dir=self.root/'revision')
        c=revision['candidates'][0]
        audio={'audition_acknowledged':True,'source_audio_sha256':revision['artifacts'][c['source_audio']],
            'candidate_audio_sha256':revision['artifacts'][c['candidate_audio']]}
        for decision in ('no_match','ambiguous'):
            fresh=self.judgment(revision,decision=decision,candidate_id=None,**audio)
            row=import_judgment(self.root/'revision',fresh,self.root/'fresh')
            self.assertEqual(row['audition_status'],'reviewer_reported_audition')
        fresh=self.judgment(revision,candidate_id=c['candidate_id'],octave_correction=2,**audio)
        row=import_judgment(self.root/'revision',fresh,self.root/'fresh')
        archived_recipe=resolve_ref(self.root/'revision',revision['revision']['base_recipe'])
        with patch('instrument_analysis.sequence_rendering.render_sequence_preview',side_effect=self.fake_render) as render:
            second=build_review_revision(self.root/'revision',self.root/'fresh'/(row['judgment_id']+'.json'),archived_recipe,
                soundfont_path='unused',fluidsynth_path='unused',output_dir=self.root/'revision-2')
            self.assertEqual(render.call_args.kwargs['octave_adjustment'],2)
        self.assertEqual(second['revision']['parent_package_id'],revision['package_id'])
        self.assertTrue(resolve_ref(self.root/'revision-2',second['revision']['original_render_manifest']).is_file())

        c=second['candidates'][0]
        audio={'audition_acknowledged':True,'source_audio_sha256':second['artifacts'][c['source_audio']],
            'candidate_audio_sha256':second['artifacts'][c['candidate_audio']]}
        fresh=self.judgment(second,candidate_id=c['candidate_id'],octave_correction=3,**audio)
        third_judgment=import_judgment(self.root/'revision-2',fresh,self.root/'third-store')
        original_recipe=resolve_ref(self.root/'revision-2',second['revision']['base_recipe'])
        with patch('instrument_analysis.sequence_rendering.render_sequence_preview',side_effect=self.fake_render) as render:
            third=build_review_revision(self.root/'revision-2',self.root/'third-store'/(third_judgment['judgment_id']+'.json'),original_recipe,
                soundfont_path='unused',fluidsynth_path='unused',output_dir=self.root/'revision-3')
            self.assertEqual(render.call_args.kwargs['octave_adjustment'],3)
        self.assertEqual(third['facts']['source']['original_source_manifest']['sha256'],parent['artifacts'][parent['evidence']['source']])

        c=third['candidates'][0]
        fresh=self.judgment(third,candidate_id=c['candidate_id'],octave_correction=0,correction_requested=True,
            audition_acknowledged=True,source_audio_sha256=third['artifacts'][c['source_audio']],candidate_audio_sha256=third['artifacts'][c['candidate_audio']])
        reset=import_judgment(self.root/'revision-3',fresh,self.root/'reset-store')
        self.assertEqual(reset['audition_status'],'requires_rerender')
        base_recipe=resolve_ref(self.root/'revision-3',third['revision']['base_recipe'])
        with patch('instrument_analysis.sequence_rendering.render_sequence_preview',side_effect=self.fake_render) as render:
            build_review_revision(self.root/'revision-3',self.root/'reset-store'/(reset['judgment_id']+'.json'),base_recipe,
                soundfont_path='unused',fluidsynth_path='unused',output_dir=self.root/'reset-revision')
            self.assertEqual(render.call_args.kwargs['octave_adjustment'],0)
