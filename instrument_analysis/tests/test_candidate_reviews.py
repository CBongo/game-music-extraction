import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from instrument_analysis.review import _canonical, _digest, _hash
from instrument_analysis.candidate_reviews import (build_candidate_review_batch,
    import_candidate_reviews, load_verified_candidate_reviews, load_calibration_labels)


class CandidateReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root / 'package'
        self.package.mkdir()
        for name in ['source.wav', 'one.wav', 'two.wav']:
            (self.package / name).write_bytes(name.encode())
        self.manifest = {'schema_version': 'instrument-review-package/v1', 'source_record_id': 'record',
            'case_id': 'secret-game', 'blind': True, 'facts': {'secret': '<script>evil</script>'},
            'artifacts': {n: _hash(self.package / n) for n in ['source.wav', 'one.wav', 'two.wav']},
            'candidates': [{'candidate_id': n, 'source_audio': 'source.wav', 'candidate_audio': n + '.wav',
                            'route': {'kind': 'gm_program', 'program_0based': i}}
                           for i, n in enumerate(['one', 'two'])]}
        self.publish()
        self.store = self.root / 'store'

    def publish(self):
        self.manifest.pop('package_id', None)
        self.manifest['package_id'] = _digest(self.manifest)
        (self.package / 'manifest.json').write_bytes(_canonical(self.manifest))
        (self.root / 'diagnostics.json').write_text('{}')
        corpus = {'schema_version': 'instrument-reviewed-corpus/v1', 'cases': [{
            'case_id': 'neutral', 'record_id': 'record', 'sample_sha256': 'f' * 64,
            'split': 'development', 'source_kind': 'native', 'game_id': 'secret-game',
            'origin_family_id': 'family',
            'diagnostics': {'path': 'diagnostics.json', 'sha256': _hash(self.root / 'diagnostics.json')},
            'review_package': {'path': 'package/manifest.json', 'sha256': _hash(self.package / 'manifest.json')}}]}
        self.corpus = self.root / 'corpus.json'
        self.corpus.write_bytes(_canonical(corpus))

    def judgment(self, decisions=('acceptable', 'acceptable'), **change):
        review = {'case_id': 'neutral', 'source_record_id': 'record',
                  'package_id': self.manifest['package_id'], 'revealed': False,
                  'candidate_assessments': [dict(candidate_id=c['candidate_id'], decision=d,
                      audition_acknowledged=True, source_audio_sha256=self.manifest['artifacts'][c['source_audio']],
                      candidate_audio_sha256=self.manifest['artifacts'][c['candidate_audio']])
                      for c, d in zip(self.manifest['candidates'], decisions)]}
        review.update(change)
        raw = {'schema_version': 'instrument-candidate-reviews/v1', 'corpus_sha256': _hash(self.corpus),
               'reviewer_id': 'reviewer', 'reviews': [review]}
        return raw

    def import_raw(self, raw):
        path = self.root / 'reviews.json'
        path.write_bytes(_canonical(raw))
        return import_candidate_reviews(self.corpus, path, self.store)

    def test_multiacceptable_and_immutable(self):
        self.import_raw(self.judgment())
        rows = load_verified_candidate_reviews(self.corpus, self.store)
        self.assertEqual(len(rows[0]['candidate_assessments']), 2)
        self.assertEqual(load_calibration_labels(self.package, self.store)['acceptable'], ['one', 'two'])
        with self.assertRaises(FileExistsError): self.import_raw(self.judgment())

    def test_all_rejected_only_pool(self):
        self.import_raw(self.judgment(('unacceptable', 'unacceptable')))
        labels = load_calibration_labels(self.package, self.store)
        self.assertEqual(labels['unacceptable'], ['one', 'two'])
        self.assertEqual(labels['acceptable'], [])
        self.assertNotIn('out_of_domain', labels)

    def test_bindings_and_missing_ack(self):
        original = self.judgment()
        mutations = [lambda r: r.update(corpus_sha256='0' * 64),
                     lambda r: r['reviews'][0].update(package_id='bad'),
                     lambda r: r['reviews'][0].update(source_record_id='bad'),
                     lambda r: r['reviews'][0]['candidate_assessments'][0].update(source_audio_sha256='bad'),
                     lambda r: r['reviews'][0]['candidate_assessments'][0].update(candidate_audio_sha256='bad'),
                     lambda r: r['reviews'][0]['candidate_assessments'][0].update(audition_acknowledged=False)]
        for mutate in mutations:
            raw = copy.deepcopy(original)
            mutate(raw)
            with self.assertRaises(ValueError): self.import_raw(raw)

    def test_revealed_documentary_and_corrections_audit_only(self):
        self.import_raw(self.judgment(revealed=True))
        self.import_raw(self.judgment(correction_requested=True))
        self.assertEqual(load_verified_candidate_reviews(self.corpus, self.store), [])
        self.manifest['method_context'] = 'documentary_assisted'
        self.publish()
        other = self.root / 'other-store'
        self.store = other
        wrapper = self.import_raw(self.judgment())
        self.assertFalse(wrapper['normalized_reviews'][0]['calibration_eligible'])
        self.assertEqual(load_verified_candidate_reviews(self.corpus, self.store), [])

    def test_conflicting_reviewers_and_decisions(self):
        self.import_raw(self.judgment())
        raw = self.judgment()
        raw['reviewer_id'] = 'different'
        with self.assertRaises(ValueError): self.import_raw(raw)
        with self.assertRaises(ValueError): self.import_raw(self.judgment(('unacceptable', 'acceptable')))
        raw = self.judgment(('unacceptable', 'unacceptable'), revealed=True)
        raw['reviewer_id'] = 'different'
        self.import_raw(raw)
        self.assertEqual(load_calibration_labels(self.package, self.store)['acceptable'], ['one', 'two'])
        self.assertEqual(len(load_verified_candidate_reviews(self.corpus, self.store)), 1)

    def test_verified_comparison_reference_identity(self):
        candidate = self.manifest['candidates'][0]
        candidate['route'].update(gm_probe_id='probe', bank=0, preset_name='Secret label', velocity=100)
        canonical = {'kind': 'gm_program', 'program_0based': 0, 'program_1based': 1}
        expected = {'probe_id': 'probe', 'bank': 0, 'route': canonical,
                    'manifest_sha256': 'a' * 64, 'soundfont_sha256': 'b' * 64}
        comparison = {'route': candidate['route'], 'input': {
            'gm_manifest': {'sha256': 'a' * 64}, 'soundfont': {'sha256': 'b' * 64}}}
        (self.package / 'comparison.json').write_bytes(_canonical(comparison))
        candidate['comparison'] = 'comparison.json'
        candidate['evidence'] = {'reference': dict(expected, extra_index_field='retained only in evidence')}
        self.manifest['artifacts']['comparison.json'] = _hash(self.package / 'comparison.json')
        self.publish()
        self.import_raw(self.judgment())
        row = load_verified_candidate_reviews(self.corpus, self.store)[0]
        self.assertEqual(row['candidate_assessments'][0]['reference'], expected)
        candidate['evidence']['reference']['probe_id'] = 'unvalidated'
        self.publish()
        self.store = self.root / 'another-store'
        with self.assertRaises(ValueError): self.import_raw(self.judgment())

    def test_changed_judgment_not_published(self):
        from instrument_analysis import candidate_reviews as module
        original = module._normalize
        def changed(raw, digest, cases):
            result = original(raw, digest, cases)
            (self.root / 'reviews.json').write_text('{}')
            return result
        with patch.object(module, '_normalize', side_effect=changed):
            with self.assertRaises(ValueError): self.import_raw(self.judgment())
        self.assertEqual(list(self.store.glob('*.json')), [])

    def test_tampered_import_and_package(self):
        wrapper = self.import_raw(self.judgment())
        path = self.store / (wrapper['review_id'] + '.json')
        path.write_text('{}')
        with self.assertRaises(ValueError): load_verified_candidate_reviews(self.corpus, self.store)
        (self.package / 'source.wav').write_bytes(b'changed')
        with self.assertRaises(ValueError): build_candidate_review_batch(self.corpus, output_dir=self.root / 'batch')

    def test_batch_manual_blind_and_copied(self):
        result = build_candidate_review_batch(self.corpus, output_dir=self.root / 'batch')
        html = (self.root / 'batch/index.html').read_text()
        self.assertNotIn('secret-game', html.split('<script>')[0])
        self.assertNotIn('<script>evil', html)
        self.assertNotIn('autoplay', html)
        self.assertNotIn('type=\'file\'', html)
        self.assertIn("a.preload='none'", html)
        self.assertIn('not_auditioned', html)
        self.assertIn('I already inspected/know source evidence', html)
        for name, digest in result['artifacts'].items():
            self.assertEqual(_hash(self.root / 'batch' / name), digest)
        with self.assertRaises(FileExistsError): build_candidate_review_batch(self.corpus, output_dir=self.root / 'batch')

    def test_independent_deterministic_blind_presentation(self):
        corpus = json.loads(self.corpus.read_text())
        case = corpus['cases'][0]
        corpus['cases'] = [dict(case, case_id='neutral-' + str(i)) for i in range(12)]
        self.corpus.write_bytes(_canonical(corpus))
        original_package = (self.package / 'manifest.json').read_bytes()
        payloads = []
        for name in ['batch-a', 'batch-b']:
            manifest = build_candidate_review_batch(self.corpus, output_dir=self.root / name)
            html = (self.root / name / 'index.html').read_text()
            payloads.append(json.loads(html.split('const M=', 1)[1].split(';const states=', 1)[0]))
            self.assertNotIn('presentation_candidate_orders', manifest['cases'][0])
        self.assertEqual(payloads[0], payloads[1])
        data = payloads[0]
        self.assertCountEqual(data['presentation_case_order'], [c['case_id'] for c in corpus['cases']])
        self.assertNotEqual(data['presentation_case_order'], [c['case_id'] for c in corpus['cases']])
        first_candidates = set()
        for entry in data['cases']:
            order = data['presentation_candidate_orders'][entry['case_id']]
            self.assertCountEqual(order, ['one', 'two'])
            first_candidates.add(order[0])
            candidates = {c['candidate_id']: c for c in entry['package']['candidates']}
            for candidate_id in order:
                self.assertEqual(candidates[candidate_id]['candidate_audio'], candidate_id + '.wav')
        self.assertEqual(first_candidates, {'one', 'two'})
        self.assertEqual((self.package / 'manifest.json').read_bytes(), original_package)


if __name__ == '__main__':
    unittest.main()
