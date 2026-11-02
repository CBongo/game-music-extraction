"""Development-only acoustic abstention calibration from explicit human auditions.

No documentary label or synthetic control can stand in for a reviewed native case.
The selected operating point is empirical and is never a probability estimate.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import shutil
import uuid
from .origin_splits import validate_origin_splits
from .review import verify_package


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _metrics(rows, thresholds):
    reviewed = [r for r in rows if r['top1_correct'] is not None]
    accepted = [] if thresholds is None else [r for r in rows if _finite(r['distance']) and _finite(r['margin']) and r['distance'] <= thresholds['distance_threshold'] and r['margin'] >= thresholds['margin_threshold']]
    accepted_reviewed = [r for r in accepted if r['top1_correct'] is not None]
    correct = sum(r['top1_correct'] for r in accepted_reviewed)
    return {'cases': len(rows), 'reviewed_top1_cases': len(reviewed),
            'accepted_correct': correct, 'accepted_incorrect': len(accepted_reviewed)-correct,
            'accepted_unreviewed': len(accepted)-len(accepted_reviewed),
            'coverage': len(accepted)/len(rows) if rows and thresholds else None,
            'selective_accuracy': correct/len(accepted_reviewed) if accepted_reviewed else None,
            'top1_reviewed_coverage': len(reviewed)/len(rows) if rows else None,
            'uncertainty': None}


def _select(rows, policy):
    """Use development labels only; false accepts are divided by reviewed negatives."""
    choices = []
    negatives = sum(not r['top1_correct'] for r in rows)
    for distance in policy['distance_grid']:
        for margin in policy['margin_grid']:
            accepted = [r for r in rows if r['distance'] <= distance and r['margin'] >= margin]
            good = sum(r['top1_correct'] for r in accepted)
            bad = len(accepted)-good
            if bad/negatives <= policy['max_false_accept_rate']:
                choices.append(((good, len(accepted), -distance, margin), distance, margin))
    if not choices:
        return None
    _, distance, margin = max(choices)
    return {'distance_threshold': distance, 'margin_threshold': margin}


def fit_thresholds(corpus_path, *, store_dir, output_dir):
    """Publish one immutable calibration report, even while human labels are absent."""
    from .candidate_reviews import load_verified_candidate_reviews
    corpus_path = Path(corpus_path).resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(output)
    pins = {str(corpus_path): _hash(corpus_path)}
    resolved_refs = {}
    def bound(ref, base):
        if not isinstance(ref, dict) or not isinstance(ref.get('path'), str):
            raise ValueError('hash-bound reference required')
        key = ('' if Path(ref['path']).is_absolute() else str(base), ref['path'])
        if key in resolved_refs:
            path = resolved_refs[key]
            if pins[str(path)] != ref.get('sha256'):
                raise ValueError('conflicting input hash: ' + str(path))
            return path
        path = (base / ref['path']).resolve(strict=True)
        if str(path) in pins:
            if pins[str(path)] != ref.get('sha256'):
                raise ValueError('conflicting input hash: ' + str(path))
            resolved_refs[key] = path
            return path
        if _hash(path) != ref.get('sha256'):
            raise ValueError('input hash mismatch: ' + str(path))
        pins[str(path)] = ref['sha256']
        resolved_refs[key] = path
        return path
    corpus = _read(corpus_path)
    if corpus.get('schema_version') != 'instrument-reviewed-corpus/v1':
        raise ValueError('unsupported reviewed corpus')
    policy = corpus['policy']
    for key in ('distance_grid', 'margin_grid'):
        if not isinstance(policy.get(key), list) or not policy[key] or any(not _finite(v) or v < 0 for v in policy[key]):
            raise ValueError('finite nonnegative threshold grid required')
    if not _finite(policy.get('max_false_accept_rate')) or not 0 <= policy['max_false_accept_rate'] <= 1:
        raise ValueError('invalid false accept bound')
    for key in ('min_reviewed_development_cases', 'min_reviewed_development_families'):
        if type(policy.get(key)) is not int or policy[key] < 1:
            raise ValueError('positive reviewed minimum required')
    cases = corpus['cases']
    if len({c['case_id'] for c in cases}) != len(cases):
        raise ValueError('duplicate case ID')
    native = [c for c in cases if c['source_kind'] == 'native' and c['split'] != 'control']
    if any(c['split'] not in ('development','held_out','control') or c['source_kind'] not in ('native','same_bank_control','synthetic_control') or (c['source_kind'] != 'native' and c['split'] != 'control') for c in cases):
        raise ValueError('controls must have separate control split')
    audit_fields = ('case_id','record_id','split','sample_sha256','origin_family_id')
    audit = validate_origin_splits([{k:c[k] for k in audit_fields} for c in native], lineage_links=corpus.get('lineage_links', [])) if native else {'status':'no_native_cases'}
    dev_games = {c['game_id'] for c in native if c['split']=='development'}
    if dev_games & {c['game_id'] for c in native if c['split']=='held_out'}:
        raise ValueError('game appears in both development and held_out')
    store = Path(store_dir).resolve()
    def snapshot_store():
        return {str(p):_hash(p) for p in sorted(store.glob('*.json'))} if store.exists() else {}
    store_snapshot = snapshot_store()
    for path, digest in store_snapshot.items():
        if path in pins and pins[path] != digest:
            raise ValueError('conflicting review store input hash')
        pins[path] = digest
    labels = load_verified_candidate_reviews(corpus_path, store_dir)
    if snapshot_store() != store_snapshot:
        raise ValueError('review store changed while loading labels')
    by_case = {}
    for label in labels:
        by_case.setdefault(label['case_id'], []).extend(label['candidate_assessments'])
        for pin in label.get('pins', []): bound(pin, corpus_path.parent)
    for case_id, assessments in by_case.items():
        unique = {}
        for assessment in assessments:
            candidate_id = assessment['candidate_id']
            if candidate_id in unique and unique[candidate_id] != assessment:
                raise ValueError('conflicting duplicate candidate assessment')
            unique[candidate_id] = assessment
        by_case[case_id] = list(unique.values())
    rows = []
    indexes = {}
    for case in cases:
        diagnostic_path = bound(case['diagnostics'], corpus_path.parent)
        diagnostic = _read(diagnostic_path)
        query = diagnostic.get('query', {})
        if diagnostic.get('schema_version') != 'instrument-ranking-diagnostics/v1' or query.get('source_record_id') != case['record_id']:
            raise ValueError('diagnostic source identity mismatch')
        if query.get('classification') or query.get('source_facts'):
            raise ValueError('documentary or classification-assisted ranking is ineligible')
        if case['source_kind']=='native' and query.get('kind') != 'native':
            raise ValueError('native corpus requires native query')
        for key in ('native_manifest','native_wav','native_receipt','pitch_calibration'):
            if key in query: bound(query[key], diagnostic_path.parent)
        if 'native_manifest' in query:
            native_manifest = _read(bound(query['native_manifest'], diagnostic_path.parent))
            if native_manifest.get('source_record_id') != case['record_id'] or native_manifest.get('wav', {}).get('sha256') != query.get('native_wav', {}).get('sha256'):
                raise ValueError('native manifest source/WAV identity mismatch')
        index_path = bound(diagnostic['index'], diagnostic_path.parent)
        if index_path not in indexes:
            index = _read(index_path)
            for manifest_ref in index.get('manifests', []):
                bound(manifest_ref, index_path.parent)
                if manifest_ref.get('soundfont'): bound(manifest_ref['soundfont'], index_path.parent)
            for reference in index.get('references', []):
                if reference.get('wav'): bound(reference['wav'], index_path.parent)
            indexes[index_path] = index
        index = indexes[index_path]
        package_path = bound(case['review_package'], corpus_path.parent)
        package = verify_package(package_path.parent)
        if package.get('source_record_id') != case['record_id'] or package.get('case_id') != case['case_id']:
            raise ValueError('review package source mismatch')
        for name, digest in package['artifacts'].items():
            bound({'path':name,'sha256':digest}, package_path.parent)
        candidates = diagnostic.get('candidates', [])
        if any(c.get('rank') != i for i,c in enumerate(candidates, 1)):
            raise ValueError('ranking ranks must be consecutive')
        eligible = len(candidates)>=2 and all(_finite(c.get('distance')) for c in candidates)
        routes = [json.dumps(c.get('route'),sort_keys=True) for c in candidates]
        if len(set(routes)) != len(routes):
            raise ValueError('diagnostics must contain distinct route groups')
        if eligible and any(a['distance']>b['distance'] for a,b in zip(candidates,candidates[1:])):
            raise ValueError('ranking rows must be ordered')
        top = candidates[0] if eligible else None
        correctness = None
        if top:
            references = [r for r in index.get('references',[]) if all(r.get(k)==top.get(k) for k in ('probe_id','soundfont_sha256','bank','route'))]
            phase = query.get('event_boundaries_seconds', {})
            note = query.get('calibrated_midi_note')
            uncertainty = query.get('alignment_uncertainty_seconds', 0)
            if not _finite(uncertainty) or uncertainty < 0:
                raise ValueError('invalid alignment uncertainty')
            tol = max(.01, uncertainty)
            if not _finite(note) or not all(_finite(phase.get(k)) for k in ('note_on','note_off','end')):
                raise ValueError('grounded pitch and phase boundaries required')
            def eligible_reference(ref):
                conditions, features = ref.get('conditions', {}), ref.get('features', {})
                return (_finite(ref.get('note')) and abs((note-ref['note'])*100)<=25
                    and abs(phase['note_off']-phase['note_on']-conditions.get('hold_seconds',-1))<=tol
                    and abs(phase['end']-phase['note_off']-conditions.get('tail_seconds',-1))<=tol
                    and features.get('valid') is True and not features.get('silent')
                    and features.get('event_clipped_sample_count',0)==0)
            references = [r for r in references if eligible_reference(r)]
            if len(references)!=1:
                raise ValueError('top candidate lacks unique full reference identity')
            ref = references[0]
            for assessment in by_case.get(case['case_id'], []):
                candidate = next((c for c in package['candidates'] if c['candidate_id']==assessment['candidate_id']), None)
                identity = assessment.get('reference') or (candidate or {}).get('reference')
                if identity and all(identity.get(k)==ref.get(k) for k in ('probe_id','manifest_sha256','soundfont_sha256','bank','route')):
                    value = assessment['decision'] == 'acceptable'
                    if correctness is not None and correctness != value: raise ValueError('conflicting top1 reviews')
                    correctness = value
        rows.append({'case_id':case['case_id'], 'split':case['split'], 'source_kind':case['source_kind'], 'origin_family_id':case['origin_family_id'], 'distance':top['distance'] if top else None, 'margin':candidates[1]['distance']-top['distance'] if top else None, 'top1_correct':correctness, 'eligible_routes':len(candidates), 'reviewed_candidates':len(by_case.get(case['case_id'], []))})
    development = [r for r in rows if r['source_kind']=='native' and r['split']=='development' and r['top1_correct'] is not None]
    thresholds = None
    status = 'awaiting_reviews'
    if development:
        status = 'insufficient_reviewed_classes'
        reviewed_ids = {r['case_id'] for r in development}
        independent_groups = sum(bool(reviewed_ids.intersection(group['case_ids'])) for group in audit.get('groups', []))
        enough = len(development)>=max(4,policy['min_reviewed_development_cases']) and independent_groups>=max(3,policy['min_reviewed_development_families'])
        if enough and {r['top1_correct'] for r in development}=={True,False}:
            thresholds = _select(development, policy)
            status = 'fitted' if thresholds else 'no_feasible_operating_point'
    partitions = {split:_metrics([r for r in rows if r['split']==split], thresholds) for split in ('development','held_out','control')}
    report = {'schema_version':'instrument-threshold-calibration/v1','status':status,'thresholds':thresholds,'policy':policy,'split_audit':audit,'cases':rows,'metrics':partitions,'pending_cases':sum(r['top1_correct'] is None for r in rows if r['source_kind']=='native'), 'scope':'Empirical reviewed top1 acceptability among auditioned candidates only; no global OOD or cross-game accuracy claim.', 'input_pins':[{'path':p,'sha256':h} for p,h in sorted(pins.items())]}
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = '.'+output.name+'-'
    stage = output.parent / (prefix+uuid.uuid4().hex)
    stage.mkdir()
    try:
        (stage/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
        for path,digest in pins.items():
            if _hash(path)!=digest: raise ValueError('input changed during calibration')
        if snapshot_store() != store_snapshot:
            raise ValueError('review store changed during calibration')
        stage.rename(output)
    finally:
        if stage.exists():
            resolved = stage.resolve()
            if resolved.parent != output.parent.resolve() or not resolved.name.startswith(prefix):
                raise ValueError('unsafe calibration stage cleanup')
            shutil.rmtree(resolved)
    return report


run_threshold_calibration = fit_thresholds


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', required=True)
    parser.add_argument('--store', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    report = fit_thresholds(args.corpus, store_dir=args.store, output_dir=args.output)
    print(json.dumps({'status':report['status'], 'thresholds':report['thresholds'], 'output':str(Path(args.output).resolve())}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
