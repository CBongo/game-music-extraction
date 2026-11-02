"""Tests for bounded documentary CellData import and identity comparison."""
from copy import deepcopy
import unittest
import json
from pathlib import Path
import tempfile
from instrument_analysis.source_claims import import_source_claims, compare_claim_imports, main


def cell(value, **extra):
    return {'userEnteredValue': {'stringValue': value}, 'formattedValue': value, **extra}


class SourceClaimsTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'spreadsheetId': 'book', 'requested_ranges': ['Tab!A1:D8'], 'sheets': [
            {'properties': {'sheetId': 7, 'title': 'Tab'}, 'data': [{'rowData': [
                {'values': [cell('Title'), cell('ID'), cell('Origin'), cell('Notes')]},
                {'values': [cell('Flute'), cell('$07'), cell('Preset [C4]', hyperlink='https://example.org',
                    textFormatRuns=[{'startIndex': 0, 'format': {'link': {'uri': 'https://example.org'}}}]),
                    cell('Maybe', note='Attribution note', unknownVendorField={'keep': True})]}]}]}]}
        self.profile = {'observed_on': '2026-11-01', 'source': {'key': 'source', 'spreadsheet_id': 'book',
                        'credits': ['Contributor'], 'unknown_source_field': {'retain': True}},
                        'selections': [{'sheet_id': 7, 'range': 'A1:D8', 'header_rows': [1],
                        'context': {'game_title': 'Game', 'platform_scope': 'SNES', 'section': 'SNES'},
                        'columns': {'subject': 'A', 'sample_id': 'B', 'origin': 'C', 'notes': 'D'}}]}

    def run_import(self):
        return import_source_claims(self.snapshot, self.profile)

    def test_raw_fields_and_attribution_retained(self):
        before = deepcopy(self.snapshot)
        report = self.run_import()
        claim = report['claims'][0]
        self.assertEqual(claim['raw_cells'], self.snapshot['sheets'][0]['data'][0]['rowData'][1]['values'])
        self.assertEqual(report['source'], self.profile['source'])
        self.assertEqual(claim['external_report_state'], 'qualified')
        self.assertEqual(self.snapshot, before)
        self.assertNotIn('unity_key', claim)
        self.assertNotIn('gm_program', claim)
        self.assertEqual(report['scope'], 'partial_bounded_cell_capture')

    def test_move_and_note_change(self):
        old = self.run_import()
        rows = self.snapshot['sheets'][0]['data'][0]['rowData']
        rows.insert(1, {})
        new = self.run_import()
        diff = compare_claim_imports(old, new)
        self.assertEqual(len(diff['moved']), 1)
        self.assertEqual(diff['changed'], [])
        rows[2]['values'][3]['note'] = 'Changed attribution'
        diff = compare_claim_imports(new, self.run_import())
        self.assertEqual(len(diff['changed']), 1)

    def test_duplicates_never_overwrite(self):
        rows = self.snapshot['sheets'][0]['data'][0]['rowData']
        rows.append(deepcopy(rows[1]))
        report = self.run_import()
        self.assertEqual(len(report['claims']), 2)
        self.assertTrue(all(c['identity_status'] == 'ambiguous_duplicate' for c in report['claims']))
        self.assertEqual(len(compare_claim_imports(report, report)['ambiguous']), 1)

    def test_unknown_disputed_and_stuff_to_find(self):
        row = self.snapshot['sheets'][0]['data'][0]['rowData'][1]
        for wording, state in [('Unknown', 'unknown'), ('Disputed attribution', 'disputed'),
                               ('StuffToFind', 'unknown'), ('Preset ***', 'reported')]:
            row['values'][3] = cell(wording)
            self.assertEqual(self.run_import()['claims'][0]['external_report_state'], state)

    def test_partial_capture_and_explicit_rows(self):
        self.profile['selections'][0]['data_rows'] = [2]
        self.snapshot['requested_ranges'].append('Tab!A101:D101')
        self.snapshot['sheets'][0]['data'].append({'startRow': 100, 'rowData': [{'values': [cell('Other')]}]})
        self.assertEqual(len(self.run_import()['claims']), 1)
        self.assertEqual(self.run_import()['selections'][0]['captured_cell_count'], 8)

    def test_bad_bounds_and_source(self):
        for bad in ['A:D', 'D8:A1', 'A0:D8']:
            self.profile['selections'][0]['range'] = bad
            with self.assertRaises(ValueError): self.run_import()
        self.profile['selections'][0]['range'] = 'A1:D8'
        self.profile['selections'][0]['data_rows'] = [9]
        with self.assertRaises(ValueError): self.run_import()

    def test_exact_sample_syntax_and_context_identity(self):
        old = self.run_import()
        self.snapshot['sheets'][0]['data'][0]['rowData'][1]['values'][1] = cell('$7')
        new = self.run_import()
        self.assertNotEqual(old['claims'][0]['identity'], new['claims'][0]['identity'])
        diff = compare_claim_imports(old, new)
        self.assertEqual(len(diff['added']), 1)
        self.assertEqual(len(diff['removed']), 1)

    def test_compound_identity_source_credit_and_scope(self):
        selection = self.profile['selections'][0]
        selection['identity_fields'] = ['subject', 'origin']
        old = self.run_import()
        self.profile['source']['credits'].append('Another contributor')
        new = self.run_import()
        self.assertEqual(old['claims'][0]['identity'], new['claims'][0]['identity'])
        self.assertEqual(len(compare_claim_imports(old, new)['changed']), 1)
        self.assertEqual(new['claims'][0]['locator']['range'], 'A2:D2')
        selection['context']['section'] = 'StuffToFind'
        self.assertEqual(self.run_import()['claims'][0]['external_report_state'], 'unknown')

    def test_uncertainty_scope_and_processing(self):
        row = self.snapshot['sheets'][0]['data'][0]['rowData'][1]
        row['values'][0] = cell('UNKNOWN?')
        for wording, state in [('Probably not it', 'disputed'), ('May be filtered', 'qualified'),
                               ('Might be reused', 'qualified'), ('need second opinions', 'qualified'),
                               ('Filtered', 'reported')]:
            row['values'][3] = cell(wording)
            self.assertEqual(self.run_import()['claims'][0]['external_report_state'], state)
        self.assertEqual(self.run_import()['claims'][0]['processing_reuse_flags'], ['Filtered'])

    def test_request_bounds_enforced(self):
        self.snapshot['requested_ranges'] = ['Tab!A1:D2']
        self.profile['selections'][0]['range'] = 'A1:D3'
        with self.assertRaises(ValueError): self.run_import()
        self.profile['selections'][0]['range'] = 'A1:D2'
        self.snapshot['sheets'][0]['data'].append({'startRow': 2, 'rowData': [{'values': [cell('Outside')]}]})
        with self.assertRaises(ValueError): self.run_import()

    def test_cli_exclusive_output_and_input_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot, profile, output = (root / name for name in ('snapshot.json', 'profile.json', 'out.json'))
            snapshot.write_text(json.dumps(self.snapshot), encoding='utf-8')
            profile.write_text(json.dumps(self.profile), encoding='utf-8')
            args = ['--snapshot', str(snapshot), '--profile', str(profile), '--output', str(output)]
            self.assertEqual(main(args), 0)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(len(report['input_refs']), 2)
            self.assertEqual(len(report['input_refs'][0]['sha256']), 64)
            before = output.read_bytes()
            with self.assertRaises(FileExistsError): main(args)
            self.assertEqual(output.read_bytes(), before)
            compared = root / 'compared.json'
            self.assertEqual(main(['--compare', str(output), str(output), '--output', str(compared)]), 0)
            self.assertEqual(len(json.loads(compared.read_text())['unchanged']), 1)

    def test_observation_required(self):
        del self.profile['observed_on']
        with self.assertRaises(ValueError): self.run_import()

    def test_blank_formatted_cells_are_not_claims(self):
        self.snapshot['sheets'][0]['data'][0]['rowData'].append({'values': [{'userEnteredFormat': {'bold': True}}]})
        self.assertEqual(len(self.run_import()['claims']), 1)


    def test_authority_requires_explicit_approved_source(self):
        self.assertEqual(self.run_import()['claims'][0]['authority'], 'documentary')
        self.profile['source']['authority'] = 'preferred_documentary'
        with self.assertRaisesRegex(ValueError, 'approved'): self.run_import()
        book = '1JJBlHHDc65fhZmKUGLrDTLCm6rfUU83-kbuD8Y0zU0o'
        self.profile['source']['spreadsheet_id'] = self.snapshot['spreadsheetId'] = book
        self.assertEqual(self.run_import()['claims'][0]['authority'], 'preferred_documentary')

    def test_content_tampering_is_rejected(self):
        old = self.run_import()
        new = deepcopy(old)
        new['claims'][0]['raw_cells'][3]['note'] = 'Edited without a new import'
        with self.assertRaisesRegex(ValueError, 'content hash'): compare_claim_imports(old, new)

    def test_comparison_requires_same_source_and_schema(self):
        old = self.run_import()
        new = deepcopy(old)
        new['schema_version'] = 'future/v9'
        with self.assertRaisesRegex(ValueError, 'schema'): compare_claim_imports(old, new)
        new = deepcopy(old)
        new['source']['spreadsheet_id'] = 'different'
        with self.assertRaisesRegex(ValueError, 'different documentary'): compare_claim_imports(old, new)

    def test_selection_scope_changes_are_visible(self):
        old = self.run_import()
        self.profile['selections'][0]['range'] = 'A1:D7'
        diff = compare_claim_imports(old, self.run_import())
        self.assertEqual(len(diff['scope_changed']), 1)
        self.assertEqual(diff['changed'], [])
        self.assertEqual(diff['unchanged'], [])

    def test_hyperlink_only_change_is_content_change(self):
        old = self.run_import()
        self.snapshot['sheets'][0]['data'][0]['rowData'][1]['values'][2]['hyperlink'] = 'https://example.org/new'
        diff = compare_claim_imports(old, self.run_import())
        self.assertEqual(len(diff['changed']), 1)
        self.assertEqual(diff['moved'], [])

    def test_actual_processing_and_reuse_wording_preserved(self):
        wording = 'Pitched up 6.5 semitones, first part of sample cut, also in FFV'
        self.snapshot['sheets'][0]['data'][0]['rowData'][1]['values'][3] = cell(wording)
        claim = self.run_import()['claims'][0]
        self.assertEqual(claim['fields']['notes'], wording)
        self.assertEqual(set(claim['processing_reuse_flags']), {'Pitched up', 'sample cut', 'also in'})
        self.assertEqual(claim['external_report_state'], 'reported')

    def test_profile_is_not_mutated(self):
        before = deepcopy(self.profile)
        self.run_import()
        self.assertEqual(before, self.profile)

    def test_cli_rejects_input_changes_before_publication(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sp, pr, out = (root / name for name in ('snapshot.json', 'profile.json', 'out.json'))
            sp.write_text(json.dumps(self.snapshot), encoding='utf8')
            pr.write_text(json.dumps(self.profile), encoding='utf8')
            def change_input(snapshot, profile):
                report = import_source_claims(snapshot, profile)
                sp.write_text('{}', encoding='utf8')
                return report
            with patch('instrument_analysis.source_claims.import_source_claims', side_effect=change_input):
                with self.assertRaisesRegex(ValueError, 'changed during import'):
                    main(['--snapshot', str(sp), '--profile', str(pr), '--output', str(out)])
            self.assertFalse(out.exists())


if __name__ == '__main__': unittest.main()
