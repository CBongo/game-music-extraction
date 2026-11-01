"""Bind five documentary origin leads to version-verified private ROM sample bytes."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path
import shutil
import uuid
from .ff4_samples import extract_ff4_sample
from .ff6_samples import extract_ff6_sample
from .schema import make_bundle

VERSION='instrument-origin-pilot/v1'
HASH_SCOPE='pilot selection_sha256 hashes canonical ASCII JSON of spreadsheet_id,sheet_id,game_title,game_section,raw_cells. Locators and our assessment are excluded. These are selected-cell fingerprints, not whole-sheet or workbook revision hashes.'
CLAIMS={
 'ff4-flute-07':('Final Fantasy IV','Flute','$07',2072026963,'A17:H17','reported',7),
 'ff6-flute-07':('Final Fantasy VI','Flute','$07',1266952582,'A17:G17','reported',7),
 'ff6-timpani-16':('Final Fantasy VI','Timpani','$16',1266952582,'A32:G32','qualified',0x16),
 'ff6-whistle-1f':('Final Fantasy VI','Whistle','$1F',1266952582,'A41:G41','doubted',0x1f),
 'ff6-claves-30':('Final Fantasy VI','Claves','$30',1266952582,'A58:G58','reported_transform',0x30)}

def _canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def _sha(data):return hashlib.sha256(data).hexdigest()
def _write(path,value):path.write_bytes(_canonical(value)+b'\n')

def _validated_claims(catalog):
    if catalog.get('catalog_version') != 1 or catalog.get('hash_scope') != HASH_SCOPE:
        raise ValueError('unsupported catalog/hash scope')
    sources=catalog.get('sources',[])
    if len({s.get('key') for s in sources}) != len(sources):raise ValueError('duplicate source key')
    by_source={s['key']:s for s in sources}
    claims=catalog.get('pilot_claims',[])
    if len(claims)!=5 or {c.get('lead_key') for c in claims} != set(CLAIMS):
        raise ValueError('pilot must contain the five explicitly scoped documentary leads')
    for claim in claims:
        game,subject,raw_id,sheet,locator,state,sample_id=CLAIMS[claim['lead_key']]
        cells=claim.get('raw_cells',[])
        source=by_source.get(claim.get('source_key'),{})
        if (claim.get('source_key')!='final-fantasy-detailed' or
            source.get('spreadsheet_id')!='1dBn9BG4zlEw1StR1uhNB57bRxRRnbo3hGP8Y-5iB-W4' or
            claim.get('game_title')!=game or claim.get('platform_scope')!='SNES' or claim.get('game_section')!='Section 1: SNES' or
            claim.get('sheet_id')!=sheet or claim.get('range')!=locator or len(cells)<7 or cells[:2]!=[subject,raw_id] or
            claim.get('external_report_state')!=state):
            raise ValueError('documentary game/section/subject/sample scope mismatch')
        selected={k:claim[k] for k in ('sheet_id','game_title','game_section','raw_cells')}
        selected['spreadsheet_id']=source['spreadsheet_id']
        if _sha(_canonical(selected))!=claim.get('selection_sha256'):
            raise ValueError('documentary selection hash mismatch')
        yield claim,source,sample_id

def _card(binding):
    esc=lambda v:html.escape(str(v),quote=True)
    origin=binding['origin'];claim=origin['claim'];sample=binding['sample']
    link='https://docs.google.com/spreadsheets/d/'+origin['source']['spreadsheet_id']+'/edit#gid='+str(claim['sheet_id'])+'&range='+claim['range']
    candidate=binding.get('candidate_replacement',{'status':'No automatic GM target; control preserves documentary uncertainty.'})
    return '<!doctype html><meta charset="utf-8"><title>'+esc(claim['lead_key'])+'</title><style>body{font:16px system-ui;max-width:950px;margin:2rem auto;padding:1rem}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:1px solid #ccc;padding:1rem 0}</style><h1>'+esc(claim['game_title']+' — '+claim['raw_cells'][0])+'</h1><p>Exact local sample bound to preferred documentary identification; original-preset audio comparison remains separate. No playback is performed.</p><section><h2>Documentary origin: '+esc(claim['external_report_state'])+'</h2><p>Preferred documentary identification; separate from replacement selection and game tuning.</p><a href="'+esc(link)+'">Credited source cells</a><p>'+esc(claim['credits'])+'</p><pre>'+esc(json.dumps(claim,indent=2))+'</pre></section><section><h2>Physical source binding</h2><p>DSP SRCN, game unity key and observed sounding offset remain unknown. Documented sampled-key brackets do not set these values.</p><pre>'+esc(json.dumps(sample,indent=2))+'</pre></section><section><h2>GM replacement: pending review</h2><pre>'+esc(json.dumps(candidate,indent=2))+'</pre></section>'

def build_origin_pilot(catalog_path, *, ff4_rom_path, ff6_rom_path, output_dir):
    """Publish new immutable bindings/cards; no origin or replacement is auditioned."""
    catalog_path=Path(catalog_path).resolve();raw=catalog_path.read_bytes()
    catalog=json.loads(raw)
    _canonical(catalog)
    claims=list(_validated_claims(catalog))
    rom_inputs={Path(p).resolve():None for p in (ff4_rom_path,ff6_rom_path)}
    for path in rom_inputs: rom_inputs[path]=_sha(path.read_bytes())
    output=Path(output_dir).resolve()
    if output.exists():raise FileExistsError(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=output.parent/('.origin-pilot-'+uuid.uuid4().hex);stage.mkdir()
    try:
        bindings=[];records=[]
        for claim,source,sample_id in claims:
            is_ff4=claim['game_title']=='Final Fantasy IV'
            metadata,brr=(extract_ff4_sample(ff4_rom_path,sample_id) if is_ff4 else extract_ff6_sample(ff6_rom_path,sample_id))
            if metadata['rom']['sha256'] != rom_inputs[Path(metadata['rom']['path']).resolve()]:
                raise ValueError('source ROM hash differs from initial input')
            if metadata['brr']['sha256']!=_sha(brr):raise ValueError('extractor payload hash mismatch')
            lead=claim['lead_key'];folder=stage/lead;folder.mkdir()
            artifact={'file':lead+'/sample.brr','sha256':_sha(brr)}
            (folder/'sample.brr').write_bytes(brr)
            binding={'lead_key':lead,'record_id':metadata['record_id'],'status':'bound',
                'binding_scope':'Physical bytes in version-verified local ROM; original hardware waveform identity remains unverified.',
                'physical_dsp_srcn':None,'source_patch_command':None,'song_local_slot':None,
                'catalog':{'path':str(catalog_path),'sha256':_sha(raw),'selection_sha256':claim['selection_sha256'],'hash_scope':catalog['hash_scope']},
                'origin':{'authority':'preferred_documentary','external_report_state':claim['external_report_state'],
                    'claim':deepcopy(claim),'source':deepcopy(source)},'sample':metadata,'artifact':artifact,
                'tuning':{'game_unity_key':None,'observed_pitch_offset':None,'reason':'Documentary sampled key and source product are not game pitch calibration.'}}
            if lead in ('ff4-flute-07','ff6-flute-07'):
                binding['candidate_replacement']={'status':'proposed_unreviewed','route':{'kind':'gm_program','program_0based':73,'program_1based':74},
                    'basis':'curated','method':'Documentation-assisted flute family shortlist; no legacy GM mapping imported and no reviewer acceptance.'}
            loc={'path':metadata['rom']['path'],'offset':metadata['brr']['file_offset'],'length':metadata['brr']['length_bytes']}
            provenance={'method':'Version-pinned ROM descriptor and exact BRR payload extraction','location':loc,
                'parameters':{'rom_payload_sha256':metadata['rom']['payload_sha256'],'descriptor_sha256':metadata['instrument']['descriptor_sha256']}}
            datum=lambda value,basis='source_fact':{'value':value,'basis':basis,'provenance':deepcopy(provenance)}
            record={'record_id':metadata['record_id'],'source':{'platform':'Super Nintendo','game_or_module':claim['game_title'],
                'region_or_version':('Final Fantasy II (US) / IV' if is_ff4 else 'Final Fantasy III (US) / VI')+'; pinned US revision 0; '+metadata['rom']['payload_sha256'],
                'driver':'FF2' if is_ff4 else 'FF3','container':'ROM','source_files':[loc]},
                'identity':{'source_instrument_id':sample_id,'sample_ids':[sample_id],'source_patch_command':None,'song_local_slot':None},
                'observation':{'synthesis':{'engine':'sample','sample':{'encoding':datum('BRR','hardware_fact'),
                    'sha256':datum(_sha(brr)),'raw_sample_path':datum(str(output/artifact['file'])),
                    'frame_count':datum(metadata['brr'].get('decoded_sample_count',metadata['brr']['frame_count']*16))}}}}
            _write(folder/'binding.json',binding);_write(folder/'record.json',record)
            _write(folder/'sample-metadata.json',metadata)
            (folder/'index.html').write_text(_card(binding),encoding='utf-8')
            bindings.append(binding);records.append(record)
        report={'schema_version':VERSION,'catalog':{'path':str(catalog_path),'sha256':_sha(raw),'hash_scope':catalog['hash_scope']},
            'bindings':bindings,'records_file':'records.json','notes':['Bindings certify local sample bytes only. Documentary origin reports retain their uncertainty.',
                'Source researchers have priority over historical converter instrument labels. GM candidates remain unreviewed.',
                'No sampled-key claim is promoted to game unity or observed sounding offset.']}
        _write(stage/'bindings.json',report);_write(stage/'records.json',make_bundle(records,'instrument_analysis.origin_pilot'))
        if any(_sha(path.read_bytes())!=expected for path,expected in rom_inputs.items()):
            raise ValueError('source ROM changed during pilot assembly')
        if catalog_path.read_bytes()!=raw:raise ValueError('catalog changed during build')
        stage.rename(output)
        return report
    finally:
        if stage.exists():
            if stage.resolve().parent != output.parent or not stage.name.startswith('.origin-pilot-'):
                raise RuntimeError('refusing cleanup outside pilot staging directory')
            shutil.rmtree(stage)

def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',required=True)
    parser.add_argument('--ff4-rom',required=True)
    parser.add_argument('--ff6-rom',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    report=build_origin_pilot(args.catalog,ff4_rom_path=args.ff4_rom,ff6_rom_path=args.ff6_rom,output_dir=args.output)
    print(json.dumps({'schema_version':report['schema_version'],'bound_leads':len(report['bindings']),
        'output_dir':str(Path(args.output).resolve())},sort_keys=True))

if __name__=='__main__':main()
