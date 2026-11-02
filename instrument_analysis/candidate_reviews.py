"""Hash-bound offline candidate acceptability batches and immutable imports."""
from pathlib import Path
import argparse
import json
import random
import shutil
import uuid

from .review import _bound, _canonical, _digest, _hash, _read_json, verify_package

VERSION = 'instrument-candidate-reviews/v1'
IMPORT_VERSION = 'instrument-candidate-review-import/v1'


def _route_identity(route):
    if route['kind'] == 'gm_program':
        return {'kind': 'gm_program', 'program_0based': route['program_0based'],
                'program_1based': route['program_0based'] + 1}
    return {'kind': 'gm_percussion', 'note': route['note']}


def _corpus(path):
    path = Path(path).resolve()
    digest = _hash(path)
    corpus = _read_json(path, 'review corpus')
    if corpus.get('schema_version') != 'instrument-reviewed-corpus/v1':
        raise ValueError('unsupported review corpus')
    cases = {}
    if not isinstance(corpus.get('cases'), list):
        raise ValueError('corpus cases must be a list')
    for case in corpus['cases']:
        if not isinstance(case, dict):
            raise ValueError('invalid corpus case')
        cid = case.get('case_id')
        if not isinstance(cid, str) or not cid or cid in cases:
            raise ValueError('duplicate or invalid case_id')
        manifest_path = _bound(case['review_package'], path.parent)
        package = verify_package(manifest_path.parent)
        _bound(case['diagnostics'], path.parent)
        if package['source_record_id'] != case['record_id']:
            raise ValueError('corpus package source mismatch')
        cases[cid] = (case, package, manifest_path)
    if not cases:
        raise ValueError('empty review corpus')
    _recheck(path, digest, cases)
    return path, digest, cases


def _recheck(path, digest, cases):
    if _hash(path) != digest:
        raise ValueError('review corpus changed during operation')
    for case, package, manifest_path in cases.values():
        _bound(case['review_package'], path.parent)
        _bound(case['diagnostics'], path.parent)
        if verify_package(manifest_path.parent) != package:
            raise ValueError('review package changed during operation')


def _normalize(raw, corpus_sha, cases):
    if raw.get('schema_version') != VERSION or raw.get('corpus_sha256') != corpus_sha:
        raise ValueError('candidate review corpus mismatch')
    reviewer = raw.get('reviewer_id')
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError('reviewer_id required')
    result, seen = [], set()
    if not isinstance(raw.get('reviews'), list):
        raise ValueError('reviews must be a list')
    for review in raw['reviews']:
        if not isinstance(review, dict):
            raise ValueError('invalid candidate review')
        cid = review.get('case_id')
        if cid not in cases or cid in seen:
            raise ValueError('unknown or repeated review case')
        seen.add(cid)
        case, package, manifest_path = cases[cid]
        if review.get('source_record_id') != case['record_id'] or review.get('package_id') != package['package_id']:
            raise ValueError('candidate review source/package mismatch')
        if type(review.get('revealed')) is not bool:
            raise ValueError('revealed boolean required')
        candidates = {c['candidate_id']: c for c in package['candidates']}
        assessments, assessed = [], set()
        if not isinstance(review.get('candidate_assessments'), list):
            raise ValueError('candidate_assessments must be a list')
        for assessment in review['candidate_assessments']:
            if not isinstance(assessment, dict):
                raise ValueError('invalid candidate assessment')
            candidate_id = assessment.get('candidate_id')
            if candidate_id not in candidates or candidate_id in assessed:
                raise ValueError('unknown or repeated candidate assessment')
            assessed.add(candidate_id)
            candidate = candidates[candidate_id]
            if assessment.get('decision') not in ('acceptable', 'unacceptable') or assessment.get('audition_acknowledged') is not True:
                raise ValueError('explicit decision and audition acknowledgment required')
            for field, key in [('source_audio_sha256', 'source_audio'), ('candidate_audio_sha256', 'candidate_audio')]:
                if assessment.get(field) != package['artifacts'][candidate[key]]:
                    raise ValueError('candidate audition audio hash mismatch')
            reference = None
            if 'comparison' in candidate:
                comparison = _read_json(manifest_path.parent / candidate['comparison'], 'candidate comparison')
                route = comparison['route']
                if route != candidate['route']:
                    raise ValueError('candidate route differs from bound comparison')
                reference = {'probe_id': route.get('gm_probe_id'),
                             'manifest_sha256': comparison.get('input', {}).get('gm_manifest', {}).get('sha256'),
                             'soundfont_sha256': comparison.get('input', {}).get('soundfont', {}).get('sha256'),
                             'bank': route.get('bank'), 'route': _route_identity(route)}
                supplied = candidate.get('reference', candidate.get('evidence', {}).get('reference'))
                if supplied is not None and any(supplied.get(k) != v for k, v in reference.items()):
                    raise ValueError('supplied candidate reference differs from bound comparison')
            assessments.append(dict(assessment, route=candidate['route'], reference=reference))
        if not assessments:
            raise ValueError('review requires auditioned candidate assessments')
        reasons = []
        if package.get('blind') is not True: reasons.append('nonblind_package')
        if review['revealed']: reasons.append('revealed')
        if package.get('method_context', 'isolated_probe') != 'isolated_probe': reasons.append('documentary_assisted')
        if package.get('revision'): reasons.append('revision')
        if any(review.get(k) for k in ('correction_requested', 'octave_correction', 'chord_offsets_correction')):
            reasons.append('correction')
        result.append({'case_id': cid, 'source_record_id': case['record_id'], 'package_id': package['package_id'],
                       'reviewer_id': reviewer, 'calibration_eligible': not reasons, 'ineligibility_reasons': reasons,
                       'candidate_assessments': assessments, 'package_ref': {'path': str(manifest_path), 'sha256': _hash(manifest_path)}})
    return result


def _stored(store_dir):
    for path in sorted(Path(store_dir).glob('*.json')):
        row = _read_json(path, 'candidate review import')
        content = dict(row)
        identity = content.pop('review_id', None)
        if row.get('schema_version') != IMPORT_VERSION or _digest(content) != identity or path.stem != identity:
            raise ValueError('candidate review import hash mismatch')
        yield path, row


def _conflicts(rows):
    seen = {}
    for row in rows:
        key = row['case_id']
        state = seen.setdefault(key, {'reviewer': row['reviewer_id'], 'package': row['package_id'], 'decisions': {}})
        if state['reviewer'] != row['reviewer_id'] or state['package'] != row['package_id']:
            raise ValueError('conflicting reviewers or packages for labeled case')
        for a in row['candidate_assessments']:
            old = state['decisions'].setdefault(a['candidate_id'], a['decision'])
            if old != a['decision']:
                raise ValueError('conflicting candidate assessments')


def import_candidate_reviews(corpus_path, judgment_path, store_dir):
    path, digest, cases = _corpus(corpus_path)
    judgment_path = Path(judgment_path).resolve()
    judgment_digest = _hash(judgment_path)
    raw = _read_json(judgment_path, 'candidate reviews')
    rows = _normalize(raw, digest, cases)
    existing = load_verified_candidate_reviews(path, store_dir)
    _conflicts(existing + [r for r in rows if r['calibration_eligible']])
    wrapper = {'schema_version': IMPORT_VERSION, 'reviews': raw, 'normalized_reviews': rows,
               'corpus_ref': {'path': str(path), 'sha256': digest}}
    wrapper['review_id'] = _digest(wrapper)
    store = Path(store_dir)
    store.mkdir(parents=True, exist_ok=True)
    target = store / (wrapper['review_id'] + '.json')
    _recheck(path, digest, cases)
    if _hash(judgment_path) != judgment_digest:
        raise ValueError('candidate reviews changed during operation')
    with target.open('xb') as stream:
        stream.write(_canonical(wrapper))
    return wrapper


def load_verified_candidate_reviews(corpus_path, store_dir):
    _, digest, cases = _corpus(corpus_path)
    rows = []
    for path, stored in _stored(store_dir):
        if stored['corpus_ref']['sha256'] != digest:
            raise ValueError('stored candidate review belongs to another corpus')
        normalized = _normalize(stored['reviews'], digest, cases)
        if normalized != stored['normalized_reviews']:
            raise ValueError('stored candidate review binding mismatch')
        rows.extend(dict(r, review_id=stored['review_id'], import_ref={'path': str(path.resolve()), 'sha256': _hash(path)},
                         pins=[{'path': str(path.resolve()), 'sha256': _hash(path)}, stored['corpus_ref'], r['package_ref']])
                    for r in normalized if r['calibration_eligible'])
    _conflicts(rows)
    return rows


def load_calibration_labels(package_dir, store_dir):
    package = verify_package(package_dir)
    rows, pins = [], []
    for path, stored in _stored(store_dir):
        corpus = _bound(stored['corpus_ref'], Path('.'))
        _, digest, cases = _corpus(corpus)
        normalized = _normalize(stored['reviews'], digest, cases)
        if normalized != stored['normalized_reviews']:
            raise ValueError('stored candidate review binding mismatch')
        for row in normalized:
            if not row['calibration_eligible']: continue
            if row['package_id'] == package['package_id']:
                rows.append(row)
                pins.extend([{'path': str(path.resolve()), 'sha256': _hash(path)}, stored['corpus_ref'], row['package_ref']])
    _conflicts(rows)
    decisions = {a['candidate_id']: a['decision'] for r in rows for a in r['candidate_assessments']}
    return {'eligible': bool(decisions), 'acceptable': sorted(c for c, d in decisions.items() if d == 'acceptable'),
            'unacceptable': sorted(c for c, d in decisions.items() if d == 'unacceptable'),
            'reason': 'explicit_blind_candidate_auditions' if decisions else 'awaiting_reviews',
            'pins': list({(p['path'], p['sha256']): p for p in pins}.values())}


def build_candidate_review_batch(corpus_path, *, output_dir):
    path, digest, cases = _corpus(corpus_path)
    output = Path(output_dir).resolve()
    if output.exists(): raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / ('.candidate-batch-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        entries = []
        for i, (cid, (case, package, manifest_path)) in enumerate(cases.items()):
            if package.get('blind') is not True:
                raise ValueError('candidate batch requires blind packages')
            folder = 'packages/' + str(i)
            dest = stage / folder
            dest.mkdir(parents=True)
            for name in package['artifacts']:
                target = dest / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(manifest_path.parent / name, target)
            shutil.copyfile(manifest_path, dest / 'manifest.json')
            if verify_package(dest) != package:
                raise ValueError('copied package differs from frozen package')
            entries.append({'case_id': cid, 'source_record_id': case['record_id'], 'package_id': package['package_id'],
                            'folder': folder, 'package': package})
        case_order = [e['case_id'] for e in entries]
        random.Random(_digest({'corpus_sha256': digest, 'purpose': 'case_presentation'})).shuffle(case_order)
        candidate_orders = {}
        for entry in entries:
            order = [c['candidate_id'] for c in entry['package']['candidates']]
            random.Random(_digest({'corpus_sha256': digest, 'case_id': entry['case_id'],
                                  'purpose': 'candidate_presentation'})).shuffle(order)
            candidate_orders[entry['case_id']] = order
        payload = {'schema_version': VERSION, 'corpus_sha256': digest, 'cases': entries,
                   'presentation_case_order': case_order, 'presentation_candidate_orders': candidate_orders}
        batch_id = _digest(payload)
        (stage / 'index.html').write_text(_html(payload), encoding='utf-8')
        inventory = {str(p.relative_to(stage)).replace('\\', '/'): _hash(p) for p in stage.rglob('*') if p.is_file()}
        manifest = {'schema_version': 'instrument-candidate-review-batch/v1', 'batch_id': batch_id,
                    'corpus_ref': {'path': str(path), 'sha256': digest}, 'artifacts': inventory,
                    'cases': [{k: v for k, v in e.items() if k != 'package'} for e in entries]}
        manifest['manifest_id'] = _digest(manifest)
        (stage / 'manifest.json').write_bytes(_canonical(manifest))
        _recheck(path, digest, cases)
        stage.rename(output)
        return manifest
    except BaseException:
        resolved = stage.resolve()
        if resolved.parent != output.parent.resolve() or not resolved.name.startswith('.candidate-batch-'):
            raise ValueError('refusing cleanup outside candidate batch staging directory')
        shutil.rmtree(resolved)
        raise


def _html(payload):
    data = json.dumps(payload, ensure_ascii=True).replace('<', '\\u003c')
    return '''<!doctype html><meta charset="utf-8"><title>Candidate listening review</title>
<style>body{font:16px system-ui;max-width:960px;margin:2rem auto}section{border:1px solid #999;padding:1rem;margin:1rem}label{display:block;margin:.6rem}audio{display:block}pre{white-space:pre-wrap}</style>
<h1>Candidate listening review</h1><p>Manually audition the source and each candidate, then rate each auditioned candidate acceptable or unacceptable. Multiple candidates may be acceptable. Audio plays only with manual controls. Rejecting the auditioned pool does not establish global out-of-domain status. Revealing evidence or marking prior source knowledge excludes that case from blind calibration.</p>
<label>Reviewer <input id="reviewer"></label><main id="cases"></main><button id="download">Download JSON</button><p id="status"></p>
<script>const M=DATA;const states=[];const root=document.getElementById('cases');
function audio(parent,src,label){parent.append(label);const a=document.createElement('audio');a.controls=true;a.preload='none';a.src=src;parent.append(a);}
M.presentation_case_order.forEach((caseId,i)=>{const entry=M.cases.find(e=>e.case_id===caseId);const p=entry.package;const s={entry,revealed:false,assessments:[]};states.push(s);const section=document.createElement('section');root.append(section);const title=document.createElement('h2');title.textContent='Case '+(i+1);section.append(title);const reveal=document.createElement('button');reveal.textContent='Reveal evidence and ranks';section.append(reveal);const known=document.createElement('input');known.type='checkbox';const kl=document.createElement('label');kl.append(known,'I already inspected/know source evidence');section.append(kl);known.onchange=()=>{if(known.checked)s.revealed=true;};const details=document.createElement('pre');section.append(details);reveal.onclick=()=>{s.revealed=true;details.textContent=JSON.stringify(p,null,2);};
const sources=[...new Set(p.candidates.map(c=>c.source_audio))];sources.forEach(src=>audio(section,entry.folder+'/'+src,'Source'));
M.presentation_candidate_orders[caseId].forEach((candidateId,j)=>{const c=p.candidates.find(c=>c.candidate_id===candidateId);const d=document.createElement('div');section.append(d);const label=document.createElement('p');label.textContent='Candidate '+(j+1);d.append(label);audio(d,entry.folder+'/'+c.candidate_audio,'Candidate');const select=document.createElement('select');['not_auditioned','acceptable','unacceptable'].forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;select.append(o);});d.append(select);const ack=document.createElement('input');ack.type='checkbox';const al=document.createElement('label');al.append(ack,'I auditioned the exact source and candidate audio');d.append(al);s.assessments.push({c,select,ack});});});
document.getElementById('download').onclick=()=>{try{const reviewer=document.getElementById('reviewer').value.trim();if(!reviewer)throw Error('Reviewer is required');const reviews=[];for(const s of states){const candidate_assessments=[];for(const {c,select,ack} of s.assessments){if(select.value==='not_auditioned')continue;if(!ack.checked)throw Error('Acknowledge each assessed source/candidate audition');candidate_assessments.push({candidate_id:c.candidate_id,decision:select.value,audition_acknowledged:true,source_audio_sha256:s.entry.package.artifacts[c.source_audio],candidate_audio_sha256:s.entry.package.artifacts[c.candidate_audio]});}if(candidate_assessments.length)reviews.push({case_id:s.entry.case_id,source_record_id:s.entry.source_record_id,package_id:s.entry.package_id,revealed:s.revealed,candidate_assessments});}const result={schema_version:M.schema_version,corpus_sha256:M.corpus_sha256,reviewer_id:reviewer,reviews};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));a.download='candidate-reviews.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}catch(e){document.getElementById('status').textContent=String(e);}};
</script>'''.replace('DATA', data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build')
    build.add_argument('--corpus', required=True)
    build.add_argument('--output', required=True)
    ingest = sub.add_parser('import')
    ingest.add_argument('--corpus', required=True)
    ingest.add_argument('--judgment', required=True)
    ingest.add_argument('--store', required=True)
    args = parser.parse_args(argv)
    if args.command == 'build':
        result = build_candidate_review_batch(args.corpus, output_dir=args.output)
    else:
        result = import_candidate_reviews(args.corpus, args.judgment, args.store)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
