"""Immutable correction rerenders; invoking this API authorizes the new render only."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import shutil
import uuid
from .review import (_bound, _canonical, _digest, _hash, _read_json,
                     build_review_package, verify_package, resolve_ref)

def build_review_revision(parent_package_dir, imported_judgment_path, recipe_path, *,
                          soundfont_path, fluidsynth_path, output_dir):
    """Render a proposed correction and publish a fresh, unjudged review package.

    Sequence pairing requires explicit recipe-hash-bound source sequence evidence.
    Isolated pairing additionally proves original trigger, duration and source bytes.
    """
    from .sequence_rendering import render_sequence_preview
    parent_dir = Path(parent_package_dir).resolve()
    parent = verify_package(parent_dir)
    imported_path = Path(imported_judgment_path).resolve()
    imported = _read_json(imported_path, 'imported judgment')
    value = dict(imported)
    identity = value.pop('judgment_id', None)
    if imported.get('schema_version') != 'instrument-review-import/v1' or _digest(value) != identity:
        raise ValueError('imported judgment hash mismatch')
    if imported.get('package_id') != parent['package_id'] or imported.get('source_record_id') != parent['source_record_id']:
        raise ValueError('correction parent/source mismatch')
    judgment = imported['judgment']
    if judgment.get('package_id') != parent['package_id'] or judgment.get('source_record_id') != parent['source_record_id'] or judgment.get('decision') not in ('accepted', 'alternate_candidate'):
        raise ValueError('only accepted/alternate proposed corrections may be rerendered')
    octave = judgment.get('octave_correction', 0)
    offsets = judgment.get('chord_offsets_correction')
    if type(judgment.get('correction_requested',False)) is not bool:
        raise ValueError('correction_requested must be boolean')
    if type(octave) is not int or not -4 <= octave <= 4 or (not octave and offsets is None and judgment.get('correction_requested') is not True):
        raise ValueError('explicit valid correction required')
    if offsets is not None and (not isinstance(offsets,list) or not offsets or len(offsets)>15 or any(type(n) is not int or not -48<=n<=48 for n in offsets)):
        raise ValueError('invalid correction offsets')
    candidate = next((c for c in parent['candidates'] if c['candidate_id'] == judgment.get('candidate_id')), None)
    if not candidate or candidate['route'].get('kind') != 'gm_program':
        raise ValueError('revision currently requires a selected melodic GM candidate')
    recipe_path = Path(recipe_path).resolve()
    recipe = _read_json(recipe_path, 'base recipe')
    if recipe.get('schema_version') != 'instrument-sequence-recipe/v1' or recipe.get('source_record_id') != parent['source_record_id']:
        raise ValueError('recipe/source mismatch')
    try:
        source_manifest = _bound(recipe.get('source'), recipe_path.parent)
    except ValueError:
        source_manifest = resolve_ref(parent_dir, recipe.get('source'))
    allowed_source_hash = parent['artifacts'][parent['evidence']['source']]
    if parent.get('revision'):
        allowed_source_hash = parent['facts']['source'].get('original_source_manifest', {}).get('sha256', allowed_source_hash)
    if _hash(source_manifest) != allowed_source_hash:
        raise ValueError('recipe must bind exact parent source manifest')
    context = recipe.get('provenance', {}).get('source_context', {})
    recipe_hash = _hash(recipe_path)
    if 'source_sequence_audio' in recipe.get('provenance', {}):
        source_audio = _bound(recipe['provenance']['source_sequence_audio'], recipe_path.parent)
        captured = _read_json(source_manifest, 'sequence source manifest').get('source_context', {})
        if not context.get('capture_context_sha256') or any(context.get(k) != captured.get(k) for k in ('capture_context_sha256','source_audio_sha256')):
            raise ValueError('source sequence capture context differs from bound source manifest')
        if context.get('source_record_id') != parent['source_record_id'] or context.get('source_audio_sha256') != _hash(source_audio):
            raise ValueError('source sequence audio/context mismatch')
    else:
        source_audio = parent_dir / candidate['original_source_audio']
        provenance = candidate.get('render_provenance', {}).get('manifest')
        original_ref = parent.get('revision', {}).get('original_render_manifest')
        if original_ref:
            original_path = resolve_ref(parent_dir, original_ref)
            original_hash = original_ref['sha256']
        elif provenance:
            original_path = parent_dir / provenance
            original_hash = parent['artifacts'].get(provenance)
        else:
            original_path, original_hash = None, None
        if not original_path or context.get('original_render_manifest_sha256') != original_hash:
            raise ValueError('isolated recipe lacks original render binding')
        original = _read_json(original_path, 'original render')
        conditions = original.get('conditions', {})
        events = recipe.get('events', [])
        if original.get('schema_version') != 'instrument-composite-preview/v1' or len(events) != 1:
            raise ValueError('isolated pairing requires one original composite trigger')
        event = events[0]
        if (event.get('start_seconds') != 0 or event.get('midi_note') != conditions.get('trigger_midi_note') or
            abs(event.get('duration_seconds', -1)-conditions.get('duration_seconds', -2)) > .0005 or
            abs(recipe.get('end_seconds', -1)-conditions.get('duration_seconds', 0)-conditions.get('tail_seconds', 0)) > .0005 or
            event.get('pitch_curve') or event.get('expression_curve') or event.get('residual_cents',0) != 0 or event.get('relative_level_db',0) != 0 or event.get('velocity') != conditions.get('velocity')):
            raise ValueError('isolated recipe does not match original source context')
        if context.get('source_audio_sha256') != _hash(source_audio):
            raise ValueError('isolated source audio hash mismatch')
    records = parent['facts']['analysis'].get('records', [])
    record = next((r for r in records if r.get('record_id') == parent['source_record_id']), {})
    components = deepcopy(record.get('analysis',{}).get('composite',{}).get('components', []))
    if offsets is not None:
        components = [{'semitone_offset':n, 'residual_cents': components[i].get('residual_cents',0) if len(components)==len(offsets) else 0,
                       'relative_level_db':components[i].get('relative_level_db',0) if len(components)==len(offsets) else 0} for i,n in enumerate(offsets)]
    if not components: components=[{'semitone_offset':0,'residual_cents':0,'relative_level_db':0}]
    components = [{k:c[k] for k in ('semitone_offset','residual_cents','relative_level_db')} for c in components]
    corrected = deepcopy(recipe)
    corrected['source'] = {'path':str(source_manifest),'sha256':_hash(source_manifest)}
    corrected['correction'] = {'parent_package_id':parent['package_id'],'parent_judgment_id':identity,
        'base_recipe_sha256':recipe_hash,'correction_semantics':'absolute override relative to original source recipe; not additive to parent candidate','candidate_octave_adjustment':octave,'component_offsets':components,
        'audition_status':'rendered_pending_review','component_mapping':'preserve ordered cents/levels when component count agrees; otherwise explicit zero cents/equal levels'}
    output=Path(output_dir).resolve()
    if output.exists(): raise FileExistsError(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=output.parent/('.revision-'+uuid.uuid4().hex);stage.mkdir()
    def ref(path): return {'path':str(path.resolve()),'sha256':_hash(path)}
    try:
        corrected_path=stage/'corrected-recipe.json';corrected_path.write_bytes(_canonical(corrected))
        render_sequence_preview(corrected_path,program_0based=candidate['route']['program_0based'],
            soundfont_path=soundfont_path,fluidsynth_path=fluidsynth_path,output_dir=stage/'render',
            octave_adjustment=octave,component_offsets=components)
        render_path=stage/'render'/'manifest.json'
        render=_read_json(render_path,'revision render')
        wrapper={'source_record_id':parent['source_record_id'],'original_facts':parent['facts']['source'],
            'original_source_manifest':ref(source_manifest),
            'source_audio':ref(source_audio),'source_context':context}
        source_path=stage/'source-context.json';source_path.write_bytes(_canonical(wrapper))
        revision={'parent_package_id':parent['package_id'],'parent_judgment_id':identity,'audition_status':'rendered_pending_review',
            'parent_manifest':ref(parent_dir/'manifest.json'),'imported_judgment':ref(imported_path),
            'base_recipe':ref(recipe_path),'corrected_recipe':ref(corrected_path),'render_manifest':ref(render_path)}
        if 'source_sequence_audio' not in recipe.get('provenance', {}):
            revision['original_render_manifest'] = ref(original_path)
        spec={'schema_version':'instrument-review-spec/v1','source_record_id':parent['source_record_id'],
            'case_id':parent['case_id'],'blind':parent['blind'],'ordering_seed':parent['ordering_seed'],
            'source':ref(source_path),'analysis':ref(parent_dir/parent['evidence']['analysis']),
            'diagnostics':ref(parent_dir/parent['evidence']['diagnostics']),'revision':revision,
            'candidates':[{'candidate_id':'revision-'+_digest(corrected['correction'])[:16],
                'source_record_id':parent['source_record_id'],'render_manifest':ref(render_path),
                'paired_wavs':{'source':ref(source_audio),'candidate':ref(render_path.parent/render['wav']['file'])},
                'render_provenance':{'context':'Correction rerender against explicitly bound source context; pending fresh review',
                    'gain_matching':'raw_inputs_normalized_by_review'},'route':candidate['route']}]}
        spec_path=stage/'spec.json';spec_path.write_bytes(_canonical(spec))
        manifest=build_review_package(spec_path,stage/'review')
        (stage/'review').rename(output)
        return manifest
    finally:
        if stage.exists(): shutil.rmtree(stage)

def main(argv=None):
    import argparse
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('parent','judgment','recipe','soundfont','fluidsynth','output'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args(argv)
    manifest=build_review_revision(args.parent,args.judgment,args.recipe,
        soundfont_path=args.soundfont,fluidsynth_path=args.fluidsynth,output_dir=args.output)
    print(json.dumps({'package_id':manifest['package_id'],'source_record_id':manifest['source_record_id'],
        'audition_status':manifest['revision']['audition_status'],'output_dir':str(Path(args.output).resolve())},sort_keys=True))

if __name__=='__main__': main()
