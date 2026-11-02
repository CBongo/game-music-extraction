"""Synthetic attributed claims and mocked version-pinned ROM extraction."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from instrument_analysis import imported_bindings as bridge
from instrument_analysis.source_claims import import_source_claims


class ImportedBindingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = {'key': 'test-detail', 'spreadsheet_id': sorted(bridge.PREFERRED_SOURCES)[0],
                  'authority': 'preferred_documentary', 'credit': 'Synthetic research credit'}
        profile = {'source': source, 'observed_on': '2026-11-01', 'selections': [{
            'sheet_id': 1, 'range': 'A1:C2', 'header_rows': [1], 'data_rows': [2],
            'columns': {'subject': 'A', 'sample_id': 'B', 'origin': 'C'},
            'context': {'game_title': 'Final Fantasy VI', 'platform_scope': 'SNES',
                        'edition': 'original', 'section': 'Section 1: SNES'}}]}
        snapshot = {'spreadsheetId': source['spreadsheet_id'], 'sheets': [{
            'properties': {'sheetId': 1, 'title': 'VI'}, 'data': [{'rowData': [
                {'values': [{'formattedValue': x} for x in ('Name', 'ID', 'Origin')]},
                {'values': [{'formattedValue': x} for x in ('Pan Flute', '$03', 'Example flute')]}]}]}]}
        self.claims = import_source_claims(snapshot, profile)
        self.claim_path = self.root / 'claims.json'; self.write(self.claim_path, self.claims)
        self.rom = self.root / 'rom.bin'; self.rom.write_bytes(b'private mock ROM')
        claim = self.claims['claims'][0]
        self.entry = {'binding_key': 'ff6-panflute-03', 'claim_identity': claim['identity'],
            'claim_content_sha256': claim['content_sha256'], 'game': 'ff6', 'region': 'US',
            'revision': 0, 'namespace': 'global_sample', 'sample_id': 3,
            'rom': {'path': str(self.rom), 'sha256': bridge._sha(self.rom.read_bytes())},
            'origin_family_id': 'pan-flute'}
        self.plan = {'schema_version': bridge.PLAN_VERSION,
            'source_report': {'path': str(self.claim_path), 'sha256': bridge._sha(self.claim_path.read_bytes())},
            'bindings': [self.entry]}
        self.plan_path = self.root / 'plan.json'; self.write(self.plan_path, self.plan)
        self.output = self.root / 'bound'
        self.brr = b'\x01' + b'\0' * 8
        self.metadata = {'schema_version': 'instrument-rom-sample/v1', 'game': 'Final Fantasy VI',
            'platform': 'SNES', 'region': 'US', 'driver': 'synthetic mechanics',
            'record_id': 'snes:ff6-us:global-sample-03',
            'rom': {'path': str(self.rom), 'sha256': self.entry['rom']['sha256'],
                    'payload_sha256': self.entry['rom']['sha256'], 'version_approved': True},
            'instrument': {'global_sample_id': 3, 'descriptor_sha256': 'a' * 64},
            'brr': {'file_offset': 0, 'length_bytes': 9, 'sha256': bridge._sha(self.brr),
                    'decoded_sample_count': 16, 'frame_count': 1}}
        self.extract = patch.object(bridge, 'extract_ff6_sample', return_value=(self.metadata, self.brr))
        self.extract.start(); self.addCleanup(self.extract.stop)

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def build(self):
        self.write(self.plan_path, self.plan)
        return bridge.build_imported_bindings(self.plan_path, output_dir=self.output)

    def test_build_and_validate_preserves_claim_and_unknown_tuning(self):
        result = self.build()
        binding, claims_path = bridge.validate_imported_binding(self.output / 'bindings.json', self.metadata['record_id'])
        self.assertEqual(result['bindings'][0], binding)
        self.assertEqual(claims_path, self.claim_path)
        self.assertEqual(binding['imported_claim']['claim'], self.claims['claims'][0])
        self.assertEqual(binding['origin']['claim']['raw_cells'], ['Pan Flute', '$03', 'Example flute'])
        self.assertIsNone(binding['tuning']['game_unity_key'])
        self.assertNotIn('candidate_replacement', binding)
        with self.assertRaises(FileExistsError):
            self.build()

    def test_stale_report_rejected(self):
        self.claim_path.write_bytes(self.claim_path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'revision/hash'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_ff4_production_platform_and_approval_shape(self):
        self.entry['game'] = 'ff4'
        self.metadata.update(game='Final Fantasy IV', platform='Super Nintendo', record_id='snes:ff4-us:global-sample-03')
        self.metadata['rom'].pop('version_approved')
        self.metadata['rom']['variant_verified'] = True
        with patch.object(bridge, 'extract_ff4_sample', return_value=(self.metadata, self.brr)):
            metadata, _ = bridge._extract(self.entry, self.rom, self.metadata['record_id'])
            self.assertEqual(metadata['platform'], 'Super Nintendo')
            self.metadata['platform'] = 'SNES'
            with self.assertRaises(ValueError):
                bridge._extract(self.entry, self.rom, self.metadata['record_id'])

    def test_explicit_namespace_and_exact_numeric_identity(self):
        for field, value in [('namespace', 'gm_program'), ('sample_id', 4), ('revision', 1), ('game', 'ff4')]:
            with self.subTest(field=field):
                original = self.entry[field]; self.entry[field] = value
                with self.assertRaises(ValueError):
                    self.build()
                self.entry[field] = original

    def test_unknown_disputed_ambiguous_and_stale_content_rejected(self):
        for field, value in [('external_report_state', 'unknown'), ('external_report_state', 'disputed'),
                             ('identity_status', 'ambiguous_duplicate'), ('content_sha256', '0' * 64)]:
            with self.subTest(field=field, value=value):
                claim = self.claims['claims'][0]; original = claim[field]; claim[field] = value
                self.write(self.claim_path, self.claims)
                self.plan['source_report']['sha256'] = bridge._sha(self.claim_path.read_bytes())
                with self.assertRaises(ValueError):
                    self.build()
                claim[field] = original

    def test_qualified_can_bind_without_automatic_candidate(self):
        self.claims['claims'][0]['external_report_state'] = 'qualified'
        self.write(self.claim_path, self.claims)
        self.plan['source_report']['sha256'] = bridge._sha(self.claim_path.read_bytes())
        binding = self.build()['bindings'][0]
        self.assertEqual(binding['origin']['external_report_state'], 'qualified')
        self.assertNotIn('candidate_replacement', binding)

    def test_validator_rejects_byte_or_metadata_tampering(self):
        self.build()
        artifact = self.output / self.entry['binding_key'] / 'sample.brr'
        artifact.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'BRR artifact'):
            bridge.validate_imported_binding(self.output / 'bindings.json', self.metadata['record_id'])
        artifact.write_bytes(self.brr)
        self.metadata['instrument']['descriptor_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'metadata mismatch'):
            bridge.validate_imported_binding(self.output / 'bindings.json', self.metadata['record_id'])

    def test_failed_extraction_leaves_no_output_or_staging(self):
        self.metadata['rom']['version_approved'] = False
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob('.imported-bindings-*')), [])


if __name__ == '__main__':
    unittest.main()
