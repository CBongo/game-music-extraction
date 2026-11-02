"""Read-only documentary claims from explicitly bounded Sheets CellData captures.

No network access, acoustic facts, tuning inference, or GM decisions occur here.
Column names and context are caller supplied; raw values are never normalized.
"""
from __future__ import annotations

from copy import deepcopy
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

VERSION = 'instrument-source-claims/v1'
PREFERRED_SOURCES = frozenset({
    '1JJBlHHDc65fhZmKUGLrDTLCm6rfUU83-kbuD8Y0zU0o',
    '1dBn9BG4zlEw1StR1uhNB57bRxRRnbo3hGP8Y-5iB-W4',
})


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _column(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Z]+', value):
        raise ValueError('columns must be explicit uppercase A1 column letters')
    result = 0
    for char in value:
        result = result * 26 + ord(char) - 64
    return result - 1


def _bounds(value):
    match = re.fullmatch(r'([A-Z]+)([1-9][0-9]*):([A-Z]+)([1-9][0-9]*)', value or '')
    if not match:
        raise ValueError('selection requires a bounded A1 rectangle, e.g. A1:H25')
    a, first, b, last = match.groups()
    bounds = (_column(a), int(first)-1, _column(b)+1, int(last))
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise ValueError('selection bounds are reversed')
    return bounds


def _text(cell):
    if 'formattedValue' in cell:
        return str(cell['formattedValue'])
    value = cell.get('effectiveValue', cell.get('userEnteredValue', {}))
    return str(next(iter(value.values()), ''))


def _state(text):
    # Flags describe wording, not the truth or confidence of a source identification.
    if re.search(r'\b(disputed|doubt(?:ed|ful)?|debated|incorrect|not confirmed|probably not|not it)\b', text, re.I):
        return 'disputed'
    if re.search(r'\b(unknown|unidentified|stuff\s*to\s*find|not found|no idea)\b|\?{2,}', text, re.I):
        return 'unknown'
    if '?' in text or re.search(r'\b(maybe|may be|might|possibly|probably|uncertain|likely|suspect|need second opinions?)\b', text, re.I):
        return 'qualified'
    return 'reported'


def import_source_claims(snapshot, profile):
    """Import native Sheets ``sheets[].data[].rowData[].values`` CellData.

    ``profile`` supplies ``source`` (key, spreadsheet_id, attribution metadata)
    and ``selections``: sheet_id, bounded range, scope context, columns mapping field
    names to A1 letters, optional 1-based data_rows/header_rows and raw_aliases.
    identity_fields defaults to ['sample_id']; explicit mapped compound fields
    support layouts without sample IDs. Context must contain scope, not credits.
    Every selected row retains all supplied CellData keys. Missing cells remain
    empty dictionaries. A capture is only evidence for its selected rectangles.
    """
    snapshot, profile = deepcopy(snapshot), deepcopy(profile)
    source = profile['source']
    if not source.get('key') or not source.get('spreadsheet_id'):
        raise ValueError('source key and spreadsheet_id are required')
    authority = source.get('authority', 'documentary')
    if authority not in ('documentary', 'preferred_documentary'):
        raise ValueError('unsupported source authority policy')
    if authority == 'preferred_documentary' and source['spreadsheet_id'] not in PREFERRED_SOURCES:
        raise ValueError('preferred authority requires an approved documentary source')
    if snapshot.get('spreadsheetId') != source['spreadsheet_id']:
        raise ValueError('snapshot spreadsheet does not match explicit source')
    if not (profile.get('observed_on') or snapshot.get('observed_on')):
        raise ValueError('observed_on is required in profile or snapshot')
    sheets = snapshot.get('sheets', [])
    claims, selections = [], []
    requested = snapshot.get('requested_ranges', [])
    for selection in profile['selections']:
        left, top, right, bottom = _bounds(selection['range'])
        candidates = [s for s in sheets if s.get('properties', {}).get('sheetId') == selection['sheet_id']]
        if len(candidates) != 1:
            raise ValueError('selection must identify exactly one captured sheet')
        sheet = candidates[0]
        coverage = 'incomplete_request_metadata'
        sheet_requests = []
        if requested:
            covered = False
            for request in requested:
                if not isinstance(request, str) or '!' not in request:
                    raise ValueError('requested_ranges must contain sheet-qualified bounded A1 ranges')
                title, rectangle = request.rsplit('!', 1)
                title = title.strip("'").replace("''", "'")
                rl, rt, rr, rb = _bounds(rectangle)
                if title == sheet.get('properties', {}).get('title'):
                    sheet_requests.append((rl,rt,rr,rb))
                if title == sheet.get('properties', {}).get('title') and rl <= left and rt <= top and rr >= right and rb >= bottom:
                    covered = True
            if not covered:
                raise ValueError('selection lies outside declared requested ranges')
            coverage = 'declared_bounded_request; omitted CellData are not verified blank'
        context = selection.get('context', {})
        if not context:
            raise ValueError('explicit context is required')
        columns = {name: _column(col) for name, col in selection['columns'].items()}
        if any(col < left or col >= right for col in columns.values()):
            raise ValueError('profile column outside selection bounds')
        cells = {}
        for grid in sheet.get('data', []):
            sr, sc = grid.get('startRow', 0), grid.get('startColumn', 0)
            if not isinstance(sr, int) or not isinstance(sc, int) or sr < 0 or sc < 0:
                raise ValueError('invalid grid offsets')
            for ri, row in enumerate(grid.get('rowData', []), sr):
                for ci, cell in enumerate(row.get('values', []), sc):
                    if requested and not any(rl <= ci < rr and rt <= ri < rb for rl,rt,rr,rb in sheet_requests):
                        raise ValueError('captured CellData outside declared requested ranges')
                    if left <= ci < right and top <= ri < bottom:
                        if (ri, ci) in cells:
                            raise ValueError('overlapping captured grid cells')
                        cells[ri, ci] = cell
        if not cells:
            raise ValueError('no captured cells inside selection')
        header_rows = selection.get('header_rows', [])
        data_rows = selection.get('data_rows', list(range(top+1, bottom+1)))
        if any(not isinstance(r, int) or not top < r <= bottom for r in header_rows + data_rows):
            raise ValueError('profile row outside selection bounds')
        headers = [{'row': r, 'cells': [cells.get((r-1,c), {}) for c in range(left,right)]} for r in header_rows]
        selected = {'selection': selection, 'cells': [{'row': r+1, 'column': c+1, 'cell': cell}
                    for (r,c),cell in sorted(cells.items())]}
        selection_hash = _hash(selected)
        selections.append({'profile': selection, 'selection_sha256': selection_hash,
                           'captured_cell_count': len(cells), 'headers': headers,
                           'coverage': coverage})
        for row in data_rows:
            if row in header_rows:
                continue
            raw = [cells.get((row-1,c), {}) for c in range(left,right)]
            if not any(_text(cell) or cell.get('note') or cell.get('hyperlink') or cell.get('textFormatRuns') for cell in raw):
                continue
            fields = {name: _text(cells.get((row-1,c), {})) for name,c in columns.items()}
            identity_fields = selection.get('identity_fields', ['sample_id'])
            if not identity_fields or any(name not in fields for name in identity_fields):
                raise ValueError('identity_fields must name explicitly mapped columns')
            syntax = {name: fields[name] for name in identity_fields}
            identity_parts = {'source_key': source['key'], 'spreadsheet_id': source['spreadsheet_id'],
                              'sheet_id': selection['sheet_id'], 'context': context, 'identity_fields_raw': syntax}
            # Blank sample syntax has no safe identity; never invent one from a row number.
            identity = _hash(identity_parts) if any(value.strip() for value in syntax.values()) else None
            content = {'context': context, 'fields': fields, 'raw_cells': raw,
                       'headers': [h['cells'] for h in headers], 'raw_aliases': selection.get('raw_aliases', {}),
                       'source_metadata': source}
            wording = '\n'.join(value for name, value in fields.items() if name in ('origin', 'source', 'notes') or name.startswith('origin_')) + '\n' + '\n'.join(str(c.get('note', '')) for c in raw)
            if re.search(r'stuff\s*to\s*find', json.dumps(context), re.I):
                wording += '\nStuffToFind'
            claims.append({'identity': identity, 'identity_parts': identity_parts,
                           'identity_status': 'unique' if identity else 'missing_identity_fields',
                           'content_sha256': _hash(content), 'selection_sha256': selection_hash,
                           'locator': {'sheet_id': selection['sheet_id'], 'row': row,
                                       'range': re.sub(r'[1-9][0-9]*', str(row), selection['range']),
                                       'selection_range': selection['range']},
                           **content, 'external_report_state': _state(wording),
                           'authority': authority, 'evidence_kind': 'documentary_claim',
                           'coverage_markers_are_confidence': False,
                           'processing_reuse_flags': sorted(set(re.findall(
                               r'\b(?:reused?|processed|processing|resampled|filtered|reversed|layered|pitch[- ]shifted|pitched (?:up|down)|sample (?:cut|cropped)|also (?:in|used))\b', wording, re.I)))})
    counts = Counter(c['identity'] for c in claims if c['identity'])
    for claim in claims:
        if counts[claim['identity']] > 1:
            claim['identity_status'] = 'ambiguous_duplicate'
    return {'schema_version': VERSION, 'source': source, 'profile': profile,
            'scope': 'partial_bounded_cell_capture', 'snapshot_sha256': _hash(snapshot), 'source_sha256': _hash(source),
            'observed_on': profile.get('observed_on', snapshot.get('observed_on')),
            'requested_ranges': snapshot.get('requested_ranges', []),
            'selections': selections, 'claims': claims,
            'limitations': ['Documentary wording is preserved; uncertainty flags are conservative lexical hints.',
                            'Named sampled keys do not establish sounding pitch or game unity key.',
                            'No measured acoustic facts or GM replacement decisions are inferred.']}


def compare_claim_imports(old, new):
    """Compare identities only when unique in both captures; absence is scope limited."""
    if any(report.get('schema_version') != VERSION for report in (old, new)):
        raise ValueError('unsupported claim import schema version')
    if any(old.get('source', {}).get(key) != new.get('source', {}).get(key)
           for key in ('key', 'spreadsheet_id')):
        raise ValueError('cannot compare different documentary sources')
    content_fields = ('context', 'fields', 'raw_cells', 'headers', 'raw_aliases', 'source_metadata')
    def index(report):
        if report.get('source_sha256') != _hash(report['source']):
            raise ValueError('source metadata hash mismatch')
        result = {}
        for claim in report['claims']:
            if claim['source_metadata'] != report['source']:
                raise ValueError('claim attribution differs from report source')
            if claim['identity'] is not None and claim['identity'] != _hash(claim['identity_parts']):
                raise ValueError('claim identity hash mismatch')
            actual_hash = _hash({key: claim[key] for key in content_fields})
            if actual_hash != claim.get('content_sha256'):
                raise ValueError('claim content hash does not match emitted content')
            result.setdefault(claim['identity'], []).append(claim)
        return result
    before, after = index(old), index(new)
    result = {'schema_version': 'instrument-source-claims-comparison/v1',
              'scope': 'selected_captures_only', 'added': [], 'removed': [],
              'changed': [], 'moved': [], 'scope_changed': [], 'unchanged': [], 'ambiguous': []}
    for key in sorted(set(before) | set(after), key=lambda k: k or ''):
        a, b = before.get(key, []), after.get(key, [])
        record = {'identity': key, 'old': a, 'new': b}
        if key is None or len(a) > 1 or len(b) > 1:
            result['ambiguous'].append(record)
        elif not a:
            result['added'].append(record)
        elif not b:
            result['removed'].append(record)
        else:
            changed = a[0]['content_sha256'] != b[0]['content_sha256']
            moved = a[0]['locator']['row'] != b[0]['locator']['row']
            scope_changed = a[0]['locator']['selection_range'] != b[0]['locator']['selection_range']
            if changed: result['changed'].append(record)
            if moved: result['moved'].append(record)
            if scope_changed: result['scope_changed'].append(record)
            if not changed and not moved and not scope_changed: result['unchanged'].append(record)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot')
    parser.add_argument('--profile')
    parser.add_argument('--compare', nargs=2, metavar=('OLD', 'NEW'))
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    if args.compare:
        if args.snapshot or args.profile: parser.error('--compare excludes --snapshot/--profile')
        paths = args.compare
    else:
        if not args.snapshot or not args.profile: parser.error('--snapshot and --profile are required')
        paths = [args.snapshot, args.profile]
    frozen = [(Path(p), Path(p).read_bytes()) for p in paths]
    parsed = [json.loads(raw.decode('utf-8-sig')) for _, raw in frozen]
    result = compare_claim_imports(*parsed) if args.compare else import_source_claims(*parsed)
    result['input_refs'] = [{'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
                            for path, raw in frozen]
    payload = json.dumps(result, indent=2, ensure_ascii=False)+'\n'
    if any(path.read_bytes() != raw for path, raw in frozen):
        raise ValueError('input file changed during import; output was not published')
    with Path(args.output).open('x', encoding='utf-8') as output:
        output.write(payload)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
