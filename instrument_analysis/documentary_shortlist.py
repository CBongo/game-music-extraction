"""Independent documentary proposals with hash-verified audio comparison links."""
from __future__ import annotations
from copy import deepcopy
import json
import math
import re
from pathlib import Path
import shutil
import uuid
from .origin_pilot import _validated_claims, _canonical, _sha
from .ff4_samples import extract_ff4_sample
from .ff6_samples import extract_ff6_sample
from .ranking import _index_for_query, _wav_info
from .comparisons import _calibration, _native_key, _local_file

VERSION='instrument-documentary-shortlist/v1'
FLUTE_PROGRAMS=(73,72,74,75)
# Semantic alternatives declared independently of acoustic baseline rankings.
SEMANTIC_PROGRAMS={
    'Flute':('reported-flute-family-fixed-alternatives/v1',FLUTE_PROGRAMS),
    'Pan Flute':('reported-pan-flute-family-fixed-alternatives/v1',(75,73,72,74)),
    'Oboe':('reported-oboe-family-fixed-alternatives/v1',(68,69,70,71)),
    'Harp':('reported-harp-fixed-target/v1',(46,)),
}

def _hash(path):return _sha(Path(path).read_bytes())
def _read(path):
    obj=json.loads(Path(path).read_bytes());_canonical(obj)
    if not isinstance(obj,dict):raise ValueError('JSON object required')
    return obj

def _ref(path):return {'path':str(Path(path).resolve()),'sha256':_hash(path)}
def _bound(ref,base):
    if not isinstance(ref,dict) or not isinstance(ref.get('path'),str):raise ValueError('hash-bound path required')
    path=(base/ref['path']).resolve()
    if not path.is_file() or _hash(path)!=ref.get('sha256'):raise ValueError('stale/missing bound input')
    return path

def _origin(bindings_path,source_record_id):
    report=_read(bindings_path)
    if report.get('schema_version')=='instrument-imported-origin-bindings/v1':
        from .imported_bindings import validate_imported_binding
        return validate_imported_binding(bindings_path,source_record_id)
    matches=[b for b in report.get('bindings',[]) if b.get('record_id')==source_record_id]
    if report.get('schema_version')!='instrument-origin-pilot/v1' or len(matches)!=1:raise ValueError('unique bound source record required')
    binding=matches[0]
    if binding.get('status')!='bound' or binding.get('origin',{}).get('authority')!='preferred_documentary':raise ValueError('preferred documentary bound origin required')
    cat_path=_bound(binding['catalog'],bindings_path.parent);catalog=_read(cat_path)
    claims={c['lead_key']:c for c,s,n in _validated_claims(catalog)}
    if claims.get(binding['lead_key'])!=binding['origin']['claim']:raise ValueError('origin claim differs from verified catalog selection')
    claim=claims[binding['lead_key']]
    catalog_source=next(s for s in catalog['sources'] if s['key']==claim['source_key'])
    if binding['origin'].get('source')!=catalog_source:raise ValueError('documentary source attribution differs from catalog')
    if binding['origin'].get('external_report_state')!=claim['external_report_state']:raise ValueError('origin uncertainty changed')
    meta=binding['sample'];rom=_bound(meta['rom'],bindings_path.parent)
    sample_id=meta['instrument']['global_sample_id']
    actual,brr=(extract_ff4_sample(rom,sample_id) if meta['game']=='Final Fantasy IV' else extract_ff6_sample(rom,sample_id))
    if (actual['record_id']!=source_record_id or actual['rom']['payload_sha256']!=meta['rom']['payload_sha256'] or
        actual['instrument']['descriptor_sha256']!=meta['instrument']['descriptor_sha256'] or actual['brr']!=meta['brr']):raise ValueError('source ROM/sample descriptor binding changed')
    artifact=binding['artifact'];path=(bindings_path.parent/artifact['file']).resolve()
    if not path.is_relative_to(bindings_path.parent.resolve()) or not path.is_file() or _hash(path)!=artifact['sha256'] or path.read_bytes()!=brr:raise ValueError('bound BRR artifact changed')
    return binding,cat_path

def _rule(binding):
    origin=binding['origin'];claim=origin['claim'];state=origin['external_report_state']
    if state!='reported':return {'rule_id':'uncertain-origin-no-automatic-target/v1','status':'abstain','reason':'Origin qualifiers or processing require a separate target-selection rule; documentary identity and processing reports are preserved.','predeclared_programs':[]}
    gm=origin.get('gm_reference')
    if gm is not None:
        if (not isinstance(gm,dict) or gm.get('map_kind')!='GM' or gm.get('numbering_convention')!='one_based_gm_1_to_128' or
            type(gm.get('program_1based')) is not int or not 1<=gm['program_1based']<=128 or not isinstance(gm.get('evidence'),dict) or not gm['evidence']):raise ValueError('explicit GM identity requires numbered map evidence')
        evidence=gm['evidence'];cells=claim.get('raw_cells',[])
        if (len(cells)<5 or evidence.get('selection_sha256')!=claim.get('selection_sha256') or
            evidence.get('bank_raw')!=cells[3] or evidence.get('program_raw')!=cells[4] or
            evidence.get('numbering_basis')!='verified_one_based_gm_bank' or cells[3] not in ('GM','PR-D (GM)','Patch -> PR-D (GM)')):
            raise ValueError('GM identity evidence is not bound to an explicit GM-bank source selection')
        prefix=re.fullmatch(r'(\d{1,3})\s+\S.*',cells[4])
        if prefix is None or int(prefix.group(1))!=gm['program_1based']:
            raise ValueError('GM program prefix disagrees with verified one-based numbering')
        return {'rule_id':'explicit-one-based-gm-identity/v1','status':'proposed','reason':'Explicit documented GM identity; target bank remains a separate audition.','predeclared_programs':[gm['program_1based']-1]}
    subject=claim['raw_cells'][0]
    if subject in SEMANTIC_PROGRAMS:
        rule_id,programs=SEMANTIC_PROGRAMS[subject]
        reason=('Flute73 primary; Piccolo72, Recorder74 and PanFlute75 related auditions fixed before audio retrieval.'
            if subject=='Flute' else f'{subject} semantic target and family alternatives fixed before audio retrieval; source preset ordinals are not GM numbering.')
        return {'rule_id':rule_id,'status':'proposed','reason':reason,'predeclared_programs':list(programs)}
    return {'rule_id':'insufficient-documentary-evidence/v1','status':'abstain','reason':'No declared documentary target rule; no SFX, drum or composite class inferred.','predeclared_programs':[]}

def _baseline(path,index_path,binding):
    if path is None:return None,{'status':'metadata_only','reason':'No independent audio baseline supplied.'}
    path=Path(path).resolve();baseline=_read(path);query=baseline.get('query',{})
    if baseline.get('schema_version')!='instrument-ranking-diagnostics/v1' or query.get('source_record_id')!=binding['record_id']:raise ValueError('audio baseline source mismatch')
    if baseline.get('index',{}).get('sha256')!=_hash(index_path):raise ValueError('audio baseline reference index mismatch')
    if query.get('classification') or query.get('source_facts'):
        return baseline,{'status':'metadata_only','reason':'Grounded source-function handoff or source-fact-assisted baseline; not an independent acoustic retrieval.'}
    if query.get('kind')!='native':return baseline,{'status':'metadata_only','reason':'Only hash-bound calibrated native baselines currently attach auditions.'}
    native_path=_bound(query.get('native_manifest'),path.parent);native=_read(native_path)
    wav=_bound(query.get('native_wav'),path.parent)
    if native.get('source_record_id')!=binding['record_id'] or native.get('wav',{}).get('sha256')!=_hash(wav):raise ValueError('native baseline identity mismatch')
    info=_wav_info(wav,native['wav'],'baseline native')
    receipt_path=_bound(query.get('native_receipt'),path.parent)
    if receipt_path!=_local_file(native_path,native.get('receipt_file'),'native receipt'):
        raise ValueError('baseline native receipt path differs from manifest')
    receipt=_read(receipt_path)
    if receipt.get('status')!='ok':raise ValueError('native receipt did not succeed')
    conditions=native.get('conditions',{});alignment=conditions.get('wav_alignment',{})
    def timing(obj,key):
        value=obj.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('invalid native timing')
        return value
    trim=timing(alignment,'actual_trim_start_seconds')
    raw_times=[timing(conditions,k) for k in ('note_on_time_seconds','note_off_time_seconds','capture_end_time_seconds')]
    for key,value in zip(('note_on_time_seconds','note_off_time_seconds','capture_end_time_seconds'),raw_times):
        if abs(value-timing(receipt,key))>1e-6:raise ValueError('native receipt timing differs from manifest')
    on,off,end=raw_times[0]-trim,raw_times[1]-trim,min(info['frames']/info['sample_rate'],raw_times[2]-trim)
    if not 0<=on<off<end:raise ValueError('native has no valid hold/tail window')
    phase=query.get('event_boundaries_seconds',{})
    for key,value in zip(('note_on','note_off','end'),(on,off,end)):
        if type(phase.get(key)) not in (int,float) or not math.isfinite(phase[key]) or abs(phase[key]-value)>1e-9:
            raise ValueError('baseline phase differs from receipt-bound native timing')
    uncertainty=timing(alignment,'alignment_uncertainty_seconds')
    if type(query.get('alignment_uncertainty_seconds')) not in (int,float) or query['alignment_uncertainty_seconds']!=uncertainty:
        raise ValueError('baseline alignment uncertainty differs from native source')
    spc_ref={'path':native.get('source',{}).get('spc_path'),'sha256':native.get('source',{}).get('spc_sha256')}
    spc=_bound(spc_ref,native_path.parent).read_bytes();instrument=native.get('instrument',{})
    start=instrument.get('sample_start_address');end=instrument.get('sample_end_address_exclusive')
    if type(start) is not int or type(end) is not int or not 0<=start<end<=65536 or _sha(spc[256+start:256+end])!=binding['sample']['brr']['sha256']:raise ValueError('native audio source BRR differs from bound documentary sample')
    cal_path=_bound(query.get('pitch_calibration'),path.parent);cal=_read(cal_path)
    if cal.get('status')!='calibrated' or cal.get('basis')!='acoustic_measurement' or type(cal.get('confidence')) not in (int,float) or cal['confidence']<.8:
        return baseline,{'status':'metadata_only','reason':'Acoustic pitch calibration unavailable or weak.'}
    note,_=_calibration(cal,cal_path,native_path,_hash(native_path),wav,_hash(wav),native,_native_key(native))
    if query.get('calibrated_midi_note')!=note:raise ValueError('baseline pitch differs from bound calibration')
    phase=query.get('event_boundaries_seconds')
    if not isinstance(phase,dict) or any(type(phase.get(k)) not in (int,float) or not math.isfinite(phase[k]) for k in ('note_on','note_off','end')):return baseline,{'status':'metadata_only','reason':'No grounded phase boundaries.'}
    return baseline,{'status':'eligible','reason':'Independently calibrated acoustic baseline and exact native BRR source verified.','calibrated_midi_note':note}

def build_documentary_shortlist(bindings_path,index_path,*,source_record_id,audio_diagnostics_path=None,output_dir=None,prepared_index=None):
    bindings_path=Path(bindings_path).resolve();index_path=Path(index_path).resolve()
    initial_bindings_hash=_hash(bindings_path);initial_index_hash=_hash(index_path)
    initial_audio_hash=_hash(audio_diagnostics_path) if audio_diagnostics_path is not None else None
    binding,cat_path=_origin(bindings_path,source_record_id)
    index=_index_for_query(index_path,prepared_index)
    rule=_rule(binding);candidates=[]
    baseline,gate=_baseline(audio_diagnostics_path,index_path,binding)
    rows=baseline.get('candidates',[]) if baseline else []
    for ordinal,program in enumerate(rule['predeclared_programs']):
        route={'kind':'gm_program','program_0based':program,'program_1based':program+1}
        matching=[r for r in rows if r.get('route')==route]
        if len(matching)>1:raise ValueError('duplicate audio baseline route')
        row=matching[0] if matching else None;variants=[]
        if gate['status']=='eligible' and row:
            refs=[r for r in index['references'] if r['probe_id']==row.get('probe_id') and r['soundfont_sha256']==row.get('soundfont_sha256') and r['route']==route and r.get('bank')==row.get('bank')]
            query=baseline['query'];phase=query['event_boundaries_seconds'];tol=max(.01,query.get('alignment_uncertainty_seconds',0))
            def eligible(ref):
                cond=ref.get('conditions',{});features=ref.get('features',{})
                return (abs((gate['calibrated_midi_note']-ref['note'])*100)<=25 and
                    abs(phase['note_off']-phase['note_on']-cond.get('hold_seconds',-1))<=tol and
                    abs(phase['end']-phase['note_off']-cond.get('tail_seconds',-1))<=tol and
                    features.get('valid') and not features.get('silent') and features.get('event_clipped_sample_count',0)==0)
            refs=[ref for ref in refs if eligible(ref)]
            if len(refs)!=1:raise ValueError('baseline selected probe has absent/ambiguous eligible phase variants in verified index')
            ref=refs[0]
            variants=[deepcopy(ref)]
        acoustic=({'rank':row['rank'],'distance':row['distance'],'functional':deepcopy(baseline.get('functional'))} if row else None)
        candidates.append({'candidate_id':f'documentary-gm-{program:03d}','documentary_priority':1 if ordinal==0 else 2,'route':route,'basis':'curated','review_status':'proposed_unreviewed','rule_id':rule['rule_id'],'reference_variants':variants,'acoustic':acoustic})
    inputs={'bindings':_ref(bindings_path),'catalog':_ref(cat_path),'index':_ref(index_path)}
    if audio_diagnostics_path is not None:inputs['audio_diagnostics']=_ref(audio_diagnostics_path)
    inputs['source_rom']={'path':binding['sample']['rom']['path'],'sha256':binding['sample']['rom']['sha256']}
    inputs['source_brr']={'path':str((bindings_path.parent/binding['artifact']['file']).resolve()),'sha256':binding['artifact']['sha256']}
    if (inputs['bindings']['sha256']!=initial_bindings_hash or inputs['index']['sha256']!=initial_index_hash or
        inputs['catalog']['sha256']!=binding['catalog']['sha256'] or (initial_audio_hash is not None and inputs['audio_diagnostics']['sha256']!=initial_audio_hash)):
        raise ValueError('shortlist input changed during validation')
    if any(_hash(ref['path'])!=ref['sha256'] for ref in inputs.values()):raise ValueError('shortlist input changed during build')
    report={'schema_version':VERSION,'source_record_id':source_record_id,'inputs':inputs,
        'origin':deepcopy(binding['origin']),'rule':rule,'audition_gate':gate,'candidates':candidates,
        'independent_audio':{'status':baseline.get('status') if baseline else 'not_supplied','functional':deepcopy(baseline.get('functional')) if baseline else None},
        'no_fused_winner':True,'generator':{'module':'instrument_analysis.documentary_shortlist','module_sha256':_hash(Path(__file__))}}
    if output_dir is not None:
        output=Path(output_dir).resolve()
        if output.exists():raise FileExistsError(output)
        output.parent.mkdir(parents=True,exist_ok=True);stage=output.parent/('.documentary-'+uuid.uuid4().hex);stage.mkdir()
        try:
            (stage/'manifest.json').write_bytes(_canonical(report)+b'\n')
            if any(_hash(ref['path'])!=ref['sha256'] for ref in inputs.values()):raise ValueError('shortlist input changed during publication')
            stage.rename(output)
        finally:
            if stage.exists():
                if stage.resolve().parent!=output.parent or not stage.name.startswith('.documentary-'):raise RuntimeError('unsafe staging cleanup')
                shutil.rmtree(stage)
    return report


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bindings',required=True);parser.add_argument('--index',required=True)
    parser.add_argument('--source-record-id',required=True);parser.add_argument('--audio-diagnostics')
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    report=build_documentary_shortlist(args.bindings,args.index,source_record_id=args.source_record_id,
        audio_diagnostics_path=args.audio_diagnostics,output_dir=args.output)
    print(json.dumps({'source_record_id':report['source_record_id'],'rule':report['rule']['rule_id'],
        'audition_gate':report['audition_gate']['status'],'candidate_count':len(report['candidates'])},sort_keys=True))

if __name__=='__main__':main()
