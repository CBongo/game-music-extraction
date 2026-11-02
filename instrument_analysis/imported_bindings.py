"""Explicit, immutable byte bindings for attributed imported documentary claims.

Plans use instrument-imported-origin-plan/v1. No documentary family or sampled
key is an acoustic measurement, pitch calibration, or GM routing decision.
"""
from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import uuid

from .ff4_samples import extract_ff4_sample
from .ff6_samples import extract_ff6_sample
from .schema import make_bundle
from .source_claims import compare_claim_imports, PREFERRED_SOURCES, _text

VERSION = 'instrument-imported-origin-bindings/v1'
PLAN_VERSION = 'instrument-imported-origin-plan/v1'
GAMES = {'ff4': 'Final Fantasy IV', 'ff6': 'Final Fantasy VI'}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, value):
    path.write_bytes(_canonical(value) + b'\n')


def _path(base, value):
    return (base / value).resolve()


def _inputs(plan_path):
    plan_path = Path(plan_path).resolve()
    frozen = {plan_path: plan_path.read_bytes()}
    plan = json.loads(frozen[plan_path])
    if plan.get('schema_version') != PLAN_VERSION:
        raise ValueError('unsupported imported binding plan version')
    ref = plan['source_report']
    claims_path = _path(plan_path.parent, ref['path'])
    frozen[claims_path] = claims_path.read_bytes()
    if _sha(frozen[claims_path]) != ref['sha256']:
        raise ValueError('source report revision/hash mismatch')
    report = json.loads(frozen[claims_path])
    compare_claim_imports(report, report)
    source = report['source']
    if (source.get('authority') != 'preferred_documentary' or
            source.get('spreadsheet_id') not in PREFERRED_SOURCES or not source.get('credit')):
        raise ValueError('approved preferred attributed documentary source required')
    entries = plan.get('bindings', [])
    if not entries:
        raise ValueError('plan requires explicit bindings')
    seen_keys, seen_ids, seen_claims = set(), set(), set()
    selected = []
    for entry in entries:
        key = entry['binding_key']
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', key) or key in seen_keys:
            raise ValueError('unsafe or duplicate binding key')
        seen_keys.add(key)
        game = entry['game']; sample_id = entry['sample_id']
        if (game not in GAMES or entry.get('region') != 'US' or type(entry.get('revision')) is not int or
                entry['revision'] != 0 or entry.get('namespace') != 'global_sample' or
                type(sample_id) is not int or not 1 <= sample_id <= (0x3f if game == 'ff6' else 0x16)):
            raise ValueError('unsupported explicit game/region/revision/global sample scope')
        if not isinstance(entry.get('origin_family_id'), str) or not entry['origin_family_id'].strip():
            raise ValueError('explicit origin family required')
        identity = entry['claim_identity']
        matches = [c for c in report['claims'] if c.get('identity') == identity]
        if len(matches) != 1 or identity in seen_claims:
            raise ValueError('claim identity missing, ambiguous or reused')
        seen_claims.add(identity)
        claim = matches[0]; context = claim['context']; parts = claim['identity_parts']
        if (claim.get('identity_status') != 'unique' or claim.get('authority') != 'preferred_documentary' or
                claim['content_sha256'] != entry['claim_content_sha256'] or
                claim.get('external_report_state') not in ('reported', 'qualified')):
            raise ValueError('stale, ambiguous, unknown or disputed documentary claim')
        raw_id = claim['fields'].get('sample_id', '')
        if (context.get('game_title') != GAMES[game] or context.get('platform_scope') != 'SNES' or
                context.get('edition') != 'original' or context.get('section') != 'Section 1: SNES' or
                not re.fullmatch(r'\$[0-9a-fA-F]{2}', raw_id) or int(raw_id[1:], 16) != sample_id or
                parts.get('context') != context or parts.get('identity_fields_raw') != {'sample_id': raw_id} or
                parts.get('source_key') != source['key'] or parts.get('spreadsheet_id') != source['spreadsheet_id'] or
                parts.get('sheet_id') != claim['locator']['sheet_id']):
            raise ValueError('exact original SNES documentary sample scope mismatch')
        selections = [s for s in report['selections'] if s['selection_sha256'] == claim['selection_sha256'] and
                      s['profile']['sheet_id'] == claim['locator']['sheet_id'] and
                      s['profile']['range'] == claim['locator']['selection_range']]
        if len(selections) != 1:
            raise ValueError('claim selection attribution mismatch')
        rom_path = _path(plan_path.parent, entry['rom']['path'])
        frozen.setdefault(rom_path, rom_path.read_bytes())
        if _sha(frozen[rom_path]) != entry['rom']['sha256']:
            raise ValueError('ROM file hash mismatch')
        record_id = f'snes:{game}-us:global-sample-{sample_id:02x}'
        if record_id in seen_ids:
            raise ValueError('duplicate physical record identity')
        seen_ids.add(record_id)
        selected.append((entry, claim, rom_path, record_id))
    return plan_path, plan, claims_path, report, frozen, selected


def _extract(entry, rom_path, record_id):
    extractor = extract_ff4_sample if entry['game'] == 'ff4' else extract_ff6_sample
    metadata, brr = extractor(rom_path, entry['sample_id'])
    verified = metadata['rom'].get('variant_verified') if entry['game'] == 'ff4' else metadata['rom'].get('version_approved')
    if (verified is not True or metadata['record_id'] != record_id or metadata['game'] != GAMES[entry['game']] or
            metadata['platform'] != {'ff4': 'Super Nintendo', 'ff6': 'SNES'}[entry['game']] or metadata['region'] != 'US' or
            metadata['instrument']['global_sample_id'] != entry['sample_id'] or
            metadata['rom']['sha256'] != entry['rom']['sha256'] or
            Path(metadata['rom']['path']).resolve() != rom_path or
            metadata['brr']['sha256'] != _sha(brr) or metadata['brr']['length_bytes'] != len(brr)):
        raise ValueError('extractor metadata differs from explicit byte binding')
    return metadata, brr


def _binding(entry, claim, source, claims_path, report_sha, metadata, brr):
    normalized = {'lead_key': entry['binding_key'], **deepcopy(claim['context']),
                  'game_section': claim['context']['section'], **deepcopy(claim['locator']),
                  'raw_cells': [_text(c) for c in claim['raw_cells']],
                  'selection_sha256': claim['selection_sha256'], 'external_report_state': claim['external_report_state'],
                  'credits': source['credit'], 'source_key': source['key']}
    return {'lead_key': entry['binding_key'], 'record_id': metadata['record_id'], 'status': 'bound',
            'origin_family_id': entry['origin_family_id'],
            'binding_scope': 'Version-pinned local ROM bytes; original hardware waveform identity remains unverified.',
            'catalog': {'path': str(claims_path), 'sha256': report_sha},
            'origin': {'authority': 'preferred_documentary', 'external_report_state': claim['external_report_state'],
                       'processing_reuse_flags': deepcopy(claim.get('processing_reuse_flags', [])),
                       'claim': normalized, 'source': deepcopy(source)},
            'imported_claim': {'identity': claim['identity'], 'content_sha256': claim['content_sha256'],
                               'report_sha256': report_sha, 'claim': deepcopy(claim)},
            'sample': metadata, 'artifact': {'file': entry['binding_key'] + '/sample.brr', 'sha256': _sha(brr)},
            'physical_dsp_srcn': None, 'source_patch_command': None, 'song_local_slot': None,
            'tuning': {'game_unity_key': None, 'observed_pitch_offset': None,
                       'reason': 'Documentary sampled keys do not calibrate game pitch.'}}


def _record(binding, output):
    meta = binding['sample']; loc = {'path': meta['rom']['path'], 'offset': meta['brr']['file_offset'], 'length': meta['brr']['length_bytes']}
    provenance = {'method': 'Version-pinned ROM descriptor and exact BRR payload extraction', 'location': loc,
                  'parameters': {'rom_payload_sha256': meta['rom']['payload_sha256'], 'descriptor_sha256': meta['instrument']['descriptor_sha256']}}
    def datum(value, basis='source_fact'):
        return {'value': value, 'basis': basis, 'provenance': deepcopy(provenance)}
    sid = meta['instrument']['global_sample_id']
    return {'record_id': binding['record_id'], 'source': {'platform': 'Super Nintendo', 'game_or_module': meta['game'],
            'region_or_version': 'US revision 0; ' + meta['rom']['payload_sha256'], 'driver': meta['driver'],
            'container': 'ROM', 'source_files': [loc]},
            'identity': {'source_instrument_id': sid, 'sample_ids': [sid], 'source_patch_command': None, 'song_local_slot': None},
            'observation': {'synthesis': {'engine': 'sample', 'sample': {'encoding': datum('BRR', 'hardware_fact'),
                'sha256': datum(meta['brr']['sha256']), 'raw_sample_path': datum(str(output / binding['artifact']['file'])),
                'frame_count': datum(meta['brr']['decoded_sample_count'])}}}}


def _card(binding):
    return ('<!doctype html><meta charset="utf-8"><title>' + html.escape(binding['lead_key']) +
            '</title><h1>' + html.escape(binding['lead_key']) + '</h1><p>Preferred documentary identification and physical byte binding are separate evidence. '
            'No acoustic comparison, original hardware verification, GM acceptance, or pitch calibration is implied.</p><pre>' +
            html.escape(json.dumps(binding, indent=2)) + '</pre>')


def build_imported_bindings(plan_path, *, output_dir):
    """Publish an immutable directory from explicit claim and local byte identities."""
    plan_path, plan, claims_path, report, frozen, selected = _inputs(plan_path)
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / ('.imported-bindings-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        bindings, records = [], []
        for entry, claim, rom_path, record_id in selected:
            metadata, brr = _extract(entry, rom_path, record_id)
            binding = _binding(entry, claim, report['source'], claims_path, _sha(frozen[claims_path]), metadata, brr)
            folder = stage / binding['lead_key']; folder.mkdir()
            record = _record(binding, output)
            (folder / 'sample.brr').write_bytes(brr)
            for name, value in [('binding.json', binding), ('record.json', record), ('sample-metadata.json', metadata)]:
                _write(folder / name, value)
            (folder / 'index.html').write_text(_card(binding), encoding='utf-8')
            bindings.append(binding); records.append(record)
        result = {'schema_version': VERSION, 'plan': {'path': str(plan_path), 'sha256': _sha(frozen[plan_path])},
                  'catalog': {'path': str(claims_path), 'sha256': _sha(frozen[claims_path])},
                  'bindings': bindings, 'records_file': 'records.json'}
        _write(stage / 'bindings.json', result)
        _write(stage / 'records.json', make_bundle(records, 'instrument_analysis.imported_bindings'))
        (stage / 'index.html').write_text('<!doctype html><meta charset="utf-8"><h1>Imported documentary byte bindings</h1>' + ''.join(
            '<p><a href="' + b['lead_key'] + '/index.html">' + html.escape(b['lead_key']) + '</a></p>' for b in bindings), encoding='utf-8')
        if any(path.read_bytes() != raw for path, raw in frozen.items()):
            raise ValueError('input changed during binding assembly')
        stage.rename(output)
        return result
    finally:
        if stage.exists():
            if stage.resolve().parent != output.parent or not stage.name.startswith('.imported-bindings-'):
                raise RuntimeError('refusing cleanup outside binding staging directory')
            shutil.rmtree(stage)


def validate_imported_binding(report_path, source_record_id):
    """Revalidate source revision, explicit scope, current ROM extraction and artifacts."""
    report_path = Path(report_path).resolve(); raw = report_path.read_bytes(); report = json.loads(raw)
    if report.get('schema_version') != VERSION:
        raise ValueError('unsupported imported bindings report version')
    plan_ref = report['plan']; plan_path = Path(plan_ref['path'])
    if _sha(plan_path.read_bytes()) != plan_ref['sha256']:
        raise ValueError('binding plan revision/hash mismatch')
    _, _, claims_path, claims, frozen, selected = _inputs(plan_path)
    expected = []
    for entry, claim, rom_path, record_id in selected:
        metadata, brr = _extract(entry, rom_path, record_id)
        binding = _binding(entry, claim, claims['source'], claims_path, _sha(frozen[claims_path]), metadata, brr)
        expected.append(binding)
        folder = report_path.parent / binding['lead_key']
        for name, value in [('binding.json', binding), ('sample-metadata.json', metadata), ('record.json', _record(binding, report_path.parent))]:
            if json.loads((folder / name).read_bytes()) != value:
                raise ValueError('binding artifact metadata mismatch')
        if (folder / 'sample.brr').read_bytes() != brr:
            raise ValueError('binding BRR artifact mismatch')
    if (report['bindings'] != expected or report['catalog'] != {'path': str(claims_path), 'sha256': _sha(frozen[claims_path])} or
            report.get('records_file') != 'records.json' or
            json.loads((report_path.parent / 'records.json').read_bytes()) != make_bundle([_record(b, report_path.parent) for b in expected], 'instrument_analysis.imported_bindings')):
        raise ValueError('binding report or records mismatch')
    matches = [b for b in expected if b['record_id'] == source_record_id]
    if len(matches) != 1:
        raise ValueError('source record identity must select exactly one binding')
    if report_path.read_bytes() != raw or any(path.read_bytes() != data for path, data in frozen.items()):
        raise ValueError('binding inputs changed during validation')
    return matches[0], claims_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True); parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    result = build_imported_bindings(args.plan, output_dir=args.output)
    print(json.dumps({'schema_version': VERSION, 'bindings': len(result['bindings'])}))


if __name__ == '__main__':
    main()
