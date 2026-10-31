"""Immutable, offline matched-probe review and scoped human judgments (stdlib only)."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil
import uuid
import wave
import math
from array import array
import sys
from .comparisons import _hash, _read_json as _comparison_read_json, _verified_wav, _local_file

VERSION = 'instrument-review-package/v1'
JUDGMENT_VERSION = 'instrument-review-judgment/v1'

def _read_json(path, label):
    value = _comparison_read_json(path, label)
    try: json.dumps(value, allow_nan=False)
    except ValueError as exc: raise ValueError(label + ' contains nonfinite JSON numbers') from exc
    return value

def _route(route):
    if not isinstance(route, dict) or route.get('kind') not in ('gm_program', 'gm_percussion'):
        raise ValueError('candidate route must identify a GM program or drum note')
    key, maximum = ('program_0based', 127) if route['kind'] == 'gm_program' else ('note', 127)
    if type(route.get(key)) is not int or not 0 <= route[key] <= maximum:
        raise ValueError('invalid GM route number')
    if 'program_1based' in route and route['program_1based'] != route.get('program_0based', -2) + 1:
        raise ValueError('GM numbering mismatch')
    if 'bank' in route and (type(route['bank']) is not int or not 0 <= route['bank'] <= 16383):
        raise ValueError('invalid GM bank')

def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()

def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()

def _bound(ref, base):
    if not isinstance(ref, dict) or not isinstance(ref.get('path'), str):
        raise ValueError('artifact requires path and sha256')
    path = (base / ref['path']).resolve()
    if not path.is_file() or _hash(path) != ref.get('sha256'):
        raise ValueError('artifact missing or hash mismatch: ' + str(path))
    return path

def _belongs(value, source_id):
    if not isinstance(value, dict):
        return False
    if 'records' in value:
        records = value['records']
        return (isinstance(records, list) and sum(isinstance(r,dict) and r.get('record_id') == source_id for r in records) == 1 and len({r.get('record_id') for r in records if isinstance(r,dict)}) == len(records))
    if 'source_record_id' in value or 'record_id' in value:
        return value.get('source_record_id', value.get('record_id')) == source_id
    query = value.get('query')
    return isinstance(query,dict) and query.get('source_record_id') == source_id

def build_review_package(spec_path, output_dir):
    """Publish once; bind and copy every referenced evidence file and audition WAV."""
    spec_path = Path(spec_path).resolve()
    spec = _read_json(spec_path, 'review spec')
    if spec.get('schema_version') != 'instrument-review-spec/v1':
        raise ValueError('unsupported review spec')
    source_id = spec.get('source_record_id')
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError('source_record_id required')
    if type(spec.get('blind', False)) is not bool or type(spec.get('ordering_seed', 0)) is not int:
        raise ValueError('blind must be boolean and ordering_seed integer')
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / ('.review-' + uuid.uuid4().hex)
    stage.mkdir()
    inventory = {}
    inputs = {}
    presentation_target = 2048.0
    def matched_audio(paths, cid):
        planes = []
        headers = []
        for path in paths:
            with wave.open(str(path), 'rb') as w:
                headers.append(w.getparams())
                pcm = array('h', w.readframes(w.getnframes()))
                if sys.byteorder != 'little': pcm.byteswap()
                planes.append(pcm)
        rms = [math.sqrt(sum(float(v)*v for v in pcm)/len(pcm)) for pcm in planes]
        if min(rms) <= 0:
            raise ValueError('silent audio cannot be gain matched')
        target = min(presentation_target, *(32700*r/max(1,max(abs(v) for v in pcm)) for r,pcm in zip(rms,planes)))
        gains = [target/r for r in rms]
        names = []
        for i,(pcm,gain,header) in enumerate(zip(planes,gains,headers)):
            name = 'artifacts/audition-' + hashlib.sha256(cid.encode()).hexdigest()[:16] + '-' + str(i) + '.wav'
            scaled = array('h', (round(v*gain) for v in pcm))
            if sys.byteorder != 'little': scaled.byteswap()
            with wave.open(str(stage/name), 'wb') as w:
                w.setparams(header); w.writeframes(scaled.tobytes())
            inventory[name] = _hash(stage/name)
            names.append(name)
        return names, {'method':'PCM RMS scaling with peak headroom', 'gains': gains, 'source_rms': rms}
    def copy(path):
        digest = _hash(path)
        name = 'artifacts/' + digest + path.suffix.lower()
        dest = stage / name
        dest.parent.mkdir(exist_ok=True)
        if not dest.exists():
            shutil.copyfile(path, dest)
        if _hash(dest) != digest:
            raise ValueError('artifact changed during copy')
        inventory[name] = digest
        inputs[path] = digest
        return name
    def bind(ref, base):
        path = _bound(ref, base)
        return path, copy(path)
    def references(value, base):
        if isinstance(value, dict):
            if 'path' in value and 'sha256' in value:
                path, _ = bind(value, base)
                # Bind the declared file, without traversing unrelated reference corpora.
            else:
                for v in value.values(): references(v, base)
        elif isinstance(value, list):
            for v in value: references(v, base)
    try:
        evidence = {}
        facts = {}
        for key in ('source', 'analysis', 'diagnostics'):
            path, evidence[key] = bind(spec.get(key), spec_path.parent)
            facts[key] = _read_json(path, key)
            if not _belongs(facts[key], source_id):
                raise ValueError(key + ' does not identify source_record_id')
            # Original evidence JSON is preserved verbatim; comparison inputs bind audition provenance.
        candidates = []
        seen = set()
        for entry in spec.get('candidates', []):
            cid = entry.get('candidate_id')
            if not isinstance(cid, str) or not cid or cid in seen:
                raise ValueError('candidate_id must be unique nonempty string')
            seen.add(cid)
            if 'comparison' in entry:
                path, comparison_file = bind(entry['comparison'], spec_path.parent)
                comparison = _read_json(path, 'comparison')
                if comparison.get('schema_version') != 'instrument-comparison/v1' or comparison.get('source_record_id') != source_id:
                    raise ValueError('comparison source mismatch')
                if comparison.get('input', {}).get('native_manifest', {}).get('sha256') != spec['source']['sha256']:
                    raise ValueError('comparison native source hash mismatch')
                references(comparison.get('input'), path.parent)
                source_path, _, _ = _verified_wav(path, comparison['outputs']['native_excerpt'], 'source')
                candidate_path, _, _ = _verified_wav(path, comparison['outputs']['gm_excerpt'], 'candidate')
                preview_path, _, _ = _verified_wav(path, comparison['outputs']['ab_preview'], 'A/B preview')
                row = {'candidate_id': cid, 'source_audio': copy(source_path), 'candidate_audio': copy(candidate_path), 'ab_audio': copy(preview_path), 'comparison': comparison_file, 'route': comparison['route'], 'render_provenance': comparison['conditions']}
            else:
                pair = entry.get('paired_wavs', {})
                paths = [bind(pair.get(key), spec_path.parent)[0] for key in ('source', 'candidate')]
                headers = []
                for p in paths:
                    with wave.open(str(p), 'rb') as w:
                        header = (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes())
                        if w.getcomptype() != 'NONE' or header[2] != 2 or header[3] <= 0:
                            raise ValueError('paired audio requires nonempty PCM16 WAV')
                        headers.append(header)
                provenance = entry.get('render_provenance', {})
                provenance_path, provenance_file = bind(entry.get('render_manifest'), spec_path.parent)
                render_manifest = _read_json(provenance_path, 'render provenance')
                if render_manifest.get('schema_version') not in ('instrument-composite-preview/v1', 'instrument-sequence-preview/v1'):
                    raise ValueError('unsupported paired render manifest')
                if render_manifest.get('schema_version') == 'instrument-composite-preview/v1':
                    for key in ('analysis','diagnostics'):
                        if render_manifest.get('input',{}).get(key,{}).get('sha256') != spec[key]['sha256']:
                            raise ValueError('composite render ' + key + ' hash mismatch')
                if render_manifest.get('schema_version') == 'instrument-sequence-preview/v1' and not spec.get('revision'):
                    raise ValueError('sequence preview requires explicit review revision provenance')
                for key in ('wav', 'midi'):
                    meta = render_manifest.get(key, {})
                    artifact = _local_file(provenance_path, meta.get('file'), 'composite ' + key)
                    if _hash(artifact) != meta.get('sha256'):
                        raise ValueError('composite ' + key + ' hash mismatch')
                    if key == 'wav' and artifact.resolve() != paths[1]:
                        raise ValueError('candidate WAV differs from declared composite WAV')
                    copy(artifact)
                references(render_manifest.get('input', {}), provenance_path.parent)
                for key in ('source','recipe'):
                    if key in render_manifest: references(render_manifest[key], provenance_path.parent)
                if 'soundfont' in render_manifest:
                    references(render_manifest['soundfont'], provenance_path.parent)
                if not _belongs(render_manifest, source_id):
                    raise ValueError('render provenance source mismatch')
                def contains_hash(value, digest):
                    if isinstance(value, dict):
                        return value.get('sha256') == digest or any(contains_hash(v,digest) for v in value.values())
                    return isinstance(value,list) and any(contains_hash(v,digest) for v in value)
                if not contains_hash(render_manifest, _hash(paths[1])) or not contains_hash(facts['source'], _hash(paths[0])):
                    raise ValueError('paired WAV hashes lack source/render provenance binding')
                provenance = dict(provenance, manifest=provenance_file)
                if headers[0] != headers[1] or not provenance.get('context') or provenance.get('gain_matching') not in ('already_matched', 'raw_inputs_normalized_by_review'):
                    raise ValueError('paired WAVs require matching headers and explicit already_matched gain provenance')
                if entry.get('source_record_id') != source_id:
                    raise ValueError('paired source mismatch')
                row = {'candidate_id': cid, 'source_audio': copy(paths[0]), 'candidate_audio': copy(paths[1]), 'route': entry.get('route', {}), 'render_provenance': provenance}
            _route(row['route'])
            presentation, levels = matched_audio([stage / row['source_audio'], stage / row['candidate_audio']], cid)
            row['original_source_audio'] = row['source_audio']
            row['original_candidate_audio'] = row['candidate_audio']
            row['source_audio'], row['candidate_audio'] = presentation
            row['gain_matching'] = levels
            row.pop('ab_audio', None)
            row['evidence'] = entry.get('evidence', {})
            candidates.append(row)
        if not candidates:
            raise ValueError('at least one candidate required')
        presentation_target = min(c['gain_matching']['source_rms'][0] * c['gain_matching']['gains'][0] for c in candidates)
        for row in candidates:
            presentation, levels = matched_audio([stage / row['original_source_audio'], stage / row['original_candidate_audio']], row['candidate_id'])
            row['source_audio'], row['candidate_audio'] = presentation
            row['gain_matching'] = levels
        random.Random(spec.get('ordering_seed', 0)).shuffle(candidates)
        manifest = {'schema_version': VERSION, 'source_record_id': source_id, 'case_id': spec.get('case_id', source_id), 'blind': spec.get('blind', False), 'ordering_seed': spec.get('ordering_seed', 0), 'evidence': evidence, 'facts': facts, 'candidates': candidates, 'context': 'Isolated probe contexts only; no full sequence/effect rendering. Corrections require rerender and were not auditioned.', 'artifacts': inventory}
        if spec.get('revision'):
            manifest['revision'] = spec['revision']
            references(spec['revision'], spec_path.parent)
            manifest['context'] = 'Corrected candidate rerender; rendered_pending_review. Scope and source context are explicitly bound in revision recipe; fresh audition acknowledgment required.'
        # Bind the UI too. Blindness is presentation masking, not cryptographic concealment.
        (stage / 'index.html').write_text(_html(manifest), encoding='utf-8')
        inventory['index.html'] = _hash(stage / 'index.html')
        manifest['package_id'] = _digest(manifest)
        (stage / 'manifest.json').write_bytes(_canonical(manifest))
        if any(_hash(p) != digest for p, digest in inputs.items()):
            raise ValueError('input changed during assembly')
        stage.rename(output)
        return manifest
    finally:
        if stage.exists(): shutil.rmtree(stage)

def verify_package(package_dir):
    root = Path(package_dir)
    manifest = _read_json(root / 'manifest.json', 'package')
    value = dict(manifest)
    package_id = value.pop('package_id', None)
    if value.get('schema_version') != VERSION or _digest(value) != package_id:
        raise ValueError('package manifest hash mismatch')
    for name, digest in manifest['artifacts'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or _hash(path) != digest:
            raise ValueError('package artifact hash mismatch')
    return manifest

def resolve_ref(package_dir, ref):
    """Resolve archived provenance by hash first, then a still-valid original path."""
    manifest = verify_package(package_dir)
    if not isinstance(ref, dict) or not isinstance(ref.get('sha256'), str):
        raise ValueError('artifact reference requires sha256')
    for name, digest in manifest['artifacts'].items():
        if digest == ref['sha256']:
            return (Path(package_dir) / name).resolve()
    return _bound(ref, Path(package_dir))

def import_judgment(package_dir, judgment_path, store_dir):
    manifest = verify_package(package_dir)
    judgment = _read_json(Path(judgment_path), 'judgment')
    if judgment.get('schema_version') != JUDGMENT_VERSION or judgment.get('package_id') != manifest['package_id'] or judgment.get('source_record_id') != manifest['source_record_id']:
        raise ValueError('judgment package/source mismatch')
    if not isinstance(judgment.get('reviewer_id'), str) or not judgment['reviewer_id'].strip() or type(judgment.get('revealed')) is not bool:
        raise ValueError('reviewer_id and revealed boolean required')
    decision = judgment.get('decision')
    if decision not in ('accepted', 'rejected', 'alternate_candidate', 'no_match', 'ambiguous'):
        raise ValueError('invalid decision')
    candidates = {c['candidate_id']: c for c in manifest['candidates']}
    cid = judgment.get('candidate_id')
    if cid is not None and cid not in candidates or decision in ('accepted', 'alternate_candidate', 'rejected') and cid not in candidates:
        raise ValueError('invalid candidate selection')
    if decision in ('no_match', 'ambiguous') and cid is not None:
        raise ValueError('abstention cannot select a candidate')
    requested = judgment.get('correction_requested', False)
    if type(requested) is not bool:
        raise ValueError('correction_requested must be boolean')
    octave = judgment.get('octave_correction', 0)
    offsets = judgment.get('chord_offsets_correction')
    if type(octave) is not int or not -4 <= octave <= 4:
        raise ValueError('invalid octave correction')
    if offsets is not None and (not isinstance(offsets, list) or not offsets or len(offsets) > 16 or any(type(n) is not int or not -48 <= n <= 48 for n in offsets)):
        raise ValueError('invalid chord offsets')
    if (requested or octave or offsets is not None) and cid is None:
        raise ValueError('correction requires candidate')
    acknowledged = False
    if manifest.get('revision'):
        selected = candidates.get(cid) or (manifest['candidates'][0] if cid is None and len(candidates) == 1 else None)
        if selected is None or judgment.get('audition_acknowledged') is not True:
            raise ValueError('revision requires selected candidate and explicit fresh audition acknowledgment')
        for field, audio in (('source_audio_sha256','source_audio'),('candidate_audio_sha256','candidate_audio')):
            if judgment.get(field) != manifest['artifacts'][selected[audio]]:
                raise ValueError('revision audition audio hash mismatch')
        acknowledged = True
    result = {'schema_version': 'instrument-review-import/v1', 'judgment': judgment, 'package_id': manifest['package_id'], 'source_record_id': manifest['source_record_id'], 'case_id': manifest['case_id'], 'blind': manifest['blind'], 'preferred_route': candidates[cid]['route'] if cid else None, 'audition_status': 'requires_rerender' if requested or octave or offsets is not None else ('reviewer_reported_audition' if acknowledged else 'unchanged_probe'), 'scope': 'Reviewer preference for bound isolated probe evidence; not ground truth.'}
    result['judgment_id'] = _digest(result)
    store = Path(store_dir)
    store.mkdir(parents=True, exist_ok=True)
    target = store / (result['judgment_id'] + '.json')
    with target.open('xb') as stream: stream.write(_canonical(result))
    return result

def export_judgments(store_dir, output_path):
    rows = []
    all_rows = []
    counts = {}
    for path in sorted(Path(store_dir).glob('*.json')):
        row = _read_json(path, 'stored judgment')
        value = dict(row)
        identity = value.pop('judgment_id', None)
        if _digest(value) != identity or path.stem != identity:
            raise ValueError('stored judgment hash mismatch')
        all_rows.append(row)
        judgment = row['judgment']
        if row['blind'] and not judgment['revealed'] and judgment['decision'] in ('accepted', 'alternate_candidate') and row['audition_status'] == 'unchanged_probe':
            rows.append({'case_id': row['case_id'], 'blinded': True, 'preferred_route': ({'kind': 'gm_program', 'program_0based': row['preferred_route']['program_0based'], 'program_1based': row['preferred_route']['program_0based'] + 1} if row['preferred_route']['kind'] == 'gm_program' else {'kind': 'gm_percussion', 'note': row['preferred_route']['note']}), 'judgment_id': identity, 'package_id': row['package_id']})
            key = json.dumps(row['preferred_route'], sort_keys=True)
            counts[key] = counts.get(key, 0) + 1
    with Path(output_path).open('x', encoding='utf-8') as stream:
        for row in all_rows: stream.write(json.dumps(row, sort_keys=True) + '\n')
    benchmark_path = Path(str(output_path) + '.benchmark.jsonl')
    with benchmark_path.open('x', encoding='utf-8') as stream:
        for row in rows: stream.write(json.dumps(row, sort_keys=True) + '\n')
    return {'exported': len(all_rows), 'benchmark_exported': len(rows), 'benchmark_path': str(benchmark_path), 'preference_counts': counts}

def _html(manifest):
    # Details are inserted only after reveal: labels and ranks are absent from the initial DOM.
    data = json.dumps(manifest).replace('<', '\\u003c')
    page = '''<!doctype html><meta charset="utf-8"><title>Isolated probe review</title>
<style>body{font:16px system-ui;max-width:980px;margin:2rem auto;padding:0 1rem;background:#f5f7fa;color:#182434}label{display:block;margin:1rem 0}button,select,input{font:inherit;padding:.4rem}audio{vertical-align:middle;max-width:90%}#audio>div{background:white;border:1px solid #bcc6d3;border-radius:8px;padding:1rem;margin:1rem 0}pre{white-space:pre-wrap;max-height:24rem;overflow:auto}details{margin:1rem 0}</style><h1>Isolated probe review</h1><p>No full sequence/effect rendering. Audio uses manual controls only. Source and candidate controls use RMS matched, peak limited presentation WAVs. Original PCM remains in bound artifacts. Corrections require rerender and have not been auditioned.</p>
<p>Blind mode masks labels in the interface; evidence files remain inspectable. Record any external inspection by revealing before submitting.</p>
<button id="reveal">Reveal evidence and ranks</button><details><summary>Revealed evidence, ranks and uncertainty</summary><pre id="facts"></pre></details><div id="audio"></div>
<label>Reviewer <input id="reviewer"></label><label>Decision <select id="decision"><option>accepted</option><option>rejected</option><option>alternate_candidate</option><option>no_match</option><option>ambiguous</option></select></label>
<label>Candidate <select id="candidate"><option value="">None</option></select></label>
<label><input id="request-correction" type="checkbox">Request rerender with these absolute corrections</label><label>Octave correction (absolute from original source recipe) <input id="octave" type="number" min="-4" max="4" value="0"></label>
<label>Chord offsets correction (absolute from original source recipe; comma separated semitones; blank means unchanged)<input id="offsets"></label>
<label><input id="acknowledged" type="checkbox">I auditioned the selected source and candidate audio for this package</label><button id="download">Download judgment JSON</button><p id="status"></p>
<script>const M=DATA; let revealed=!M.blind;
const el=id=>document.getElementById(id);
function details(){el('facts').textContent=JSON.stringify({context:M.context,facts:M.facts,candidates:M.candidates},null,2);}
M.candidates.forEach((c,i)=>{let o=document.createElement('option');o.value=c.candidate_id;o.textContent='Candidate '+(i+1);el('candidate').append(o);let d=document.createElement('div');d.append('Candidate '+(i+1)+' ');for(const [k,label] of [['source_audio','Source'],['candidate_audio','Candidate'],['ab_audio','Gain matched A/B']]){if(!c[k])continue;let p=document.createElement('p');p.append(label+' ');let a=document.createElement('audio');a.controls=true;a.preload='none';a.src=c[k];p.append(a);d.append(p);}el('audio').append(d);});
if(revealed)details();el('reveal').onclick=()=>{revealed=true;details();};
el('download').onclick=()=>{if(!el('reviewer').value.trim())throw Error('Reviewer is required');const decision=el('decision').value;const selected=el('candidate').value;if(['accepted','rejected','alternate_candidate'].includes(decision)!==Boolean(selected))throw Error('Choose a candidate for accepted/rejected/alternate; choose None for no_match/ambiguous');const correction=el('offsets').value.trim();const j={schema_version:'instrument-review-judgment/v1',package_id:PACKAGE_ID,source_record_id:M.source_record_id,reviewer_id:el('reviewer').value,decision:el('decision').value,candidate_id:el('candidate').value||null,revealed,octave_correction:Number(el('octave').value)};if(el('request-correction').checked){if(!j.candidate_id)throw Error('Correction request requires a selected candidate');j.correction_requested=true;}if(M.revision){const c=M.candidates.find(c=>c.candidate_id===j.candidate_id)||(M.candidates.length===1?M.candidates[0]:null);if(!c||!el('acknowledged').checked)throw Error('Fresh revision audition acknowledgment required');j.audition_acknowledged=true;j.source_audio_sha256=M.artifacts[c.source_audio];j.candidate_audio_sha256=M.artifacts[c.candidate_audio];}if(correction)j.chord_offsets_correction=correction.split(',').map(Number);const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(j,null,2)],{type:'application/json'}));a.download='judgment.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);el('status').textContent='Import with CLI to validate. Corrections require rerender.';};
// Manifest is local and fetched only on explicit download because its ID binds this HTML.
el('status').textContent='Downloading asks you to select this package manifest.json to bind the judgment.';const download=el('download').onclick;el('download').onclick=async()=>{try{const handle=await new Promise(resolve=>{let input=document.createElement('input');input.type='file';input.accept='.json';input.onchange=()=>resolve(input.files[0]);input.click();});const m=JSON.parse(await handle.text());const comparable=v=>{let c=JSON.parse(JSON.stringify(v));delete c.package_id;delete c.artifacts['index.html'];return c;};const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);if(stable(comparable(m))!==stable(comparable(M)))throw Error('Wrong manifest for this review page');PACKAGE_ID=m.package_id;download();}catch(e){el('status').textContent=String(e);}};let PACKAGE_ID=null;
</script>'''.replace('DATA', data)
    if manifest.get('revision'):
        page = page.replace('No full sequence/effect rendering.', 'Correction rerender against explicitly bound source context; see revealed recipe for supported timing and effects.')
    return page

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('build'); p.add_argument('--spec', required=True); p.add_argument('--output', required=True)
    p = sub.add_parser('verify'); p.add_argument('--package', required=True)
    p = sub.add_parser('import'); p.add_argument('--package', required=True); p.add_argument('--judgment', required=True); p.add_argument('--store', required=True)
    p = sub.add_parser('export'); p.add_argument('--store', required=True); p.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    if args.command == 'build': result = build_review_package(args.spec, args.output)
    elif args.command == 'verify': result = verify_package(args.package)
    elif args.command == 'import': result = import_judgment(args.package, args.judgment, args.store)
    else: result = export_judgments(args.store, args.output)
    print(json.dumps(result, sort_keys=True))

if __name__ == '__main__': main()
