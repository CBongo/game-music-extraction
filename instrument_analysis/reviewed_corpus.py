"""Freeze independent native acoustic rankings before making blind auditions."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import uuid
from .origin_splits import validate_origin_splits
from .ranking import prepared_reference_index, rank_native_source
from .comparisons import build_matched_comparison
from .review import build_review_package


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def _ref(path, base=None):
    return {'path':os.path.relpath(path,base) if base else str(path), 'sha256':_hash(path)}


def _sample_proof(case, native, native_path, bound):
    """Bind the declared bytes to the exact source span used by the renderer."""
    sample = case['sample']
    source = bound(sample['source'])
    offset, length = sample.get('offset'), sample.get('length')
    if type(offset) is not int or type(length) is not int or offset < 0 or length <= 0:
        raise ValueError('invalid sample span')
    data = source.read_bytes()
    if offset+length > len(data) or hashlib.sha256(data[offset:offset+length]).hexdigest() != sample.get('sha256'):
        raise ValueError('sample span hash mismatch')
    provenance, instrument = native.get('source', {}), native.get('instrument', {})
    if sample.get('encoding') == 'BRR':
        key = 'spc'
        start, end = instrument.get('sample_start_address'), instrument.get('sample_end_address_exclusive')
        expected_offset = 256+start if type(start) is int else None
        expected_length = end-start if type(start) is int and type(end) is int else None
    elif sample.get('encoding') == 'PSX_ADPCM':
        key = 'akao_bank'
        payload,start,end = (instrument.get(k) for k in ('payload_offset','sample_start_offset','sample_end_block_offset'))
        expected_offset = payload+start if type(payload) is int and type(start) is int else None
        expected_length = end+16-start if type(start) is int and type(end) is int else None
    else:
        raise ValueError('unsupported sample encoding')
    source_name = provenance.get(key+'_path')
    if not isinstance(source_name,str): raise ValueError('native source sample provenance missing')
    native_source = (native_path.parent/source_name).resolve()
    if native_source != source or provenance.get(key+'_sha256') != sample['source']['sha256'] or offset != expected_offset or length != expected_length:
        raise ValueError('sample span differs from native renderer source')


def _eligible_reference(row, diagnostic, index):
    query = diagnostic['query']; phase=query.get('event_boundaries_seconds', {})
    note=query.get('calibrated_midi_note'); uncertainty=query.get('alignment_uncertainty_seconds',0)
    def finite(v):return type(v) in (int,float) and math.isfinite(v)
    if not finite(note) or not finite(uncertainty) or uncertainty<0 or not all(finite(phase.get(k)) for k in ('note_on','note_off','end')):
        raise ValueError('candidate requires grounded acoustic pitch and phase')
    tolerance=max(.01,uncertainty)
    matches=[]
    for ref in index['references']:
        if not all(ref.get(k)==row.get(k) for k in ('probe_id','soundfont_sha256','bank','route')):continue
        conditions,features=ref.get('conditions',{}),ref.get('features',{})
        if (finite(ref.get('note')) and abs((note-ref['note'])*100)<=25 and
            abs(phase['note_off']-phase['note_on']-conditions.get('hold_seconds',-1))<=tolerance and
            abs(phase['end']-phase['note_off']-conditions.get('tail_seconds',-1))<=tolerance and
            features.get('valid') is True and not features.get('silent') and features.get('event_clipped_sample_count',0)==0):matches.append(ref)
    if len(matches)!=1:raise ValueError('selected acoustic probe has absent or ambiguous phase variants')
    return matches[0]


def build_reviewed_corpus(plan_path, *, output_dir, audio_top_k=3):
    """Publish once; inference receives only native audio, pitch, and GM references."""
    if type(audio_top_k) is not int or not 1<=audio_top_k<=128:raise ValueError('audio_top_k outside 1..128')
    plan_path=Path(plan_path).resolve(strict=True);output=Path(output_dir).resolve()
    if output.exists():raise FileExistsError(output)
    pins={str(plan_path):_hash(plan_path)}
    def bound(ref,base=None):
        if not isinstance(ref,dict) or not isinstance(ref.get('path'),str):raise ValueError('hash-bound input required')
        path=((base or plan_path.parent)/ref['path']).resolve(strict=True)
        if _hash(path)!=ref.get('sha256'):raise ValueError('input hash mismatch: '+str(path))
        pins[str(path)]=ref['sha256'];return path
    plan=_read(plan_path)
    if plan.get('schema_version')!='instrument-reviewed-corpus-plan/v1':raise ValueError('unsupported corpus plan')
    index_path=bound(plan['index']);cases=plan['cases']
    if not isinstance(cases,list) or not cases:raise ValueError('nonempty cases required')
    if any(not isinstance(c.get('case_id'),str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',c['case_id']) for c in cases):raise ValueError('neutral normalized case IDs required')
    if len({c['case_id'] for c in cases})!=len(cases):raise ValueError('duplicate case ID')
    if any(c.get('source_kind')!='native' or c.get('split') not in ('development','held_out') for c in cases):raise ValueError('native development/held_out cases required')
    fields=('case_id','record_id','split','origin_family_id')
    split_cases=[{**{k:c[k] for k in fields},'sample_sha256':c['sample']['sha256']} for c in cases]
    links=json.loads(json.dumps(plan.get('lineage_links',[])))
    for link in links:
        if 'path' in link.get('evidence',{}):link['evidence']['path']=str(bound(link['evidence']))
    validate_origin_splits(split_cases,lineage_links=links)
    dev_games={c['game_id'] for c in cases if c['split']=='development'}
    if dev_games & {c['game_id'] for c in cases if c['split']=='held_out'}:raise ValueError('game partition leakage')
    inputs={}
    for case in cases:
        native_path=bound(case['native_manifest']);cal_path=bound(case['pitch_calibration']);native=_read(native_path)
        if native.get('source_record_id')!=case['record_id']:raise ValueError('native source record mismatch')
        _sample_proof(case,native,native_path,bound)
        for field in ('receipt_file',):
            if field in native:
                p=(native_path.parent/native[field]).resolve(strict=True);pins[str(p)]=_hash(p)
        if isinstance(native.get('wav'),dict):
            wav=native['wav'];bound({'path':wav['file'],'sha256':wav['sha256']},native_path.parent)
        inputs[case['case_id']]=(native_path,cal_path)
    output.parent.mkdir(parents=True,exist_ok=True)
    prefix='.'+output.name+'-';stage=output.parent/(prefix+uuid.uuid4().hex);stage.mkdir()
    try:
        # Freeze every ranking before the first listening package or comparison.
        frozen={}
        with prepared_reference_index(index_path) as prepared:
            index=_read(index_path)
            for case in cases:
                native_path,cal_path=inputs[case['case_id']]
                folder=stage/'cases'/case['case_id']/'audio-only'
                _,diagnostic=rank_native_source(native_path,cal_path,index_path,output_dir=folder,top_k=128,prepared_index=prepared)
                query=diagnostic.get('query',{})
                if query.get('classification') or query.get('source_facts'):raise ValueError('ranking must remain acoustic only')
                frozen[case['case_id']]=(_ref(folder/'diagnostics.json',stage),_ref(folder/'predictions.json',stage),diagnostic)
        corpus_cases=[]
        for case in cases:
            cid=case['case_id'];native_path,cal_path=inputs[cid]
            diag_ref,pred_ref,diagnostic=frozen[cid]
            entries=[]
            manifests={m['sha256']:bound(m,index_path.parent) for m in index['manifests']}
            for i,row in enumerate(diagnostic.get('candidates',[])[:audio_top_k]):
                ref=_eligible_reference(row,diagnostic,index)
                comparison_dir=stage/'cases'/cid/'comparisons'/str(i+1)
                build_matched_comparison(native_path,manifests[ref['manifest_sha256']],gm_probe_id=ref['probe_id'],pitch_calibration_path=cal_path,output_dir=comparison_dir)
                matches=list(comparison_dir.glob('*.comparison.json'))
                if len(matches)!=1:raise ValueError('comparison did not publish exactly one manifest')
                identity={k:ref[k] for k in ('probe_id','manifest_sha256','soundfont_sha256','bank','route')}
                entries.append({'candidate_id':'candidate-'+str(i+1),'comparison':_ref(matches[0],stage), 'evidence':{'reference':identity,'acoustic':{'rank':row['rank'],'distance':row['distance']}}})
            if not entries:raise ValueError('no acoustic candidates available for blind audition')
            spec={'schema_version':'instrument-review-spec/v1','source_record_id':case['record_id'],'case_id':cid,'blind':True,'ordering_seed':0,'source':_ref(native_path),'analysis':pred_ref,'diagnostics':diag_ref,'candidates':entries}
            spec_path=stage/('spec-'+cid+'.json');_write(spec_path,spec)
            package_dir=stage/'cases'/cid/'review'
            build_review_package(spec_path,package_dir)
            core={k:case[k] for k in ('case_id','record_id','split','source_kind','game_id','origin_family_id')}
            corpus_cases.append({**core,'sample_sha256':case['sample']['sha256'],'diagnostics':diag_ref,'review_package':_ref(package_dir/'manifest.json',stage), **({'metadata':case['metadata']} if 'metadata' in case else {})})
        corpus={'schema_version':'instrument-reviewed-corpus/v1','policy':plan['policy'],'cases':corpus_cases,'lineage_links':links,'plan':_ref(plan_path)}
        _write(stage/'corpus.json',corpus)
        for diag_ref,pred_ref,_ in frozen.values():
            for ref in (diag_ref,pred_ref):
                if _hash(stage/ref['path'])!=ref['sha256']:raise ValueError('frozen acoustic baseline changed')
        for path,digest in pins.items():
            if _hash(path)!=digest:raise ValueError('input changed during corpus assembly')
        stage.rename(output)
    finally:
        if stage.exists():
            resolved=stage.resolve()
            if resolved.parent!=output.parent.resolve() or not resolved.name.startswith(prefix):raise ValueError('unsafe corpus stage cleanup')
            shutil.rmtree(resolved)
    return corpus


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--plan',required=True);parser.add_argument('--output',required=True);parser.add_argument('--audio-top-k',type=int,default=3)
    args=parser.parse_args(argv);corpus=build_reviewed_corpus(args.plan,output_dir=args.output,audio_top_k=args.audio_top_k)
    print(json.dumps({'cases':len(corpus['cases']),'output':str(Path(args.output).resolve())}));return 0


if __name__=='__main__':raise SystemExit(main())
