"""Label-isolated evaluation of original composite controls and tracker probes."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from .composites import analyze_baked_chord, expand_composite_events
from .fixtures import _mod


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _neutral(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value):
        raise ValueError('evaluation source IDs must be neutral safe identifiers')
    return value


def _analyze(items,out):
    observations=[]
    for rid,probes in items:
        _neutral(rid)
        bundle,diag=analyze_baked_chord(probes,source_record_id=rid,output_dir=out/rid)
        offsets=[c['semitone_offset'] for c in diag['components']]
        if diag['status']=='supported':
            event={'midi_note':60,'start':.25,'duration':.5,'velocity':96,
                   'pitch_bend':[0,100,-50],'vibrato':{'depth_cents':12}}
            expanded=expand_composite_events([event],bundle,diag)
            if [e['midi_note'] for e in expanded] != [60+n for n in offsets]:
                raise ValueError('event expansion differs from accepted components')
            if not all(e['start']==.25 and e['duration']==.5 and e['pitch_bend']==event['pitch_bend'] and e['vibrato']==event['vibrato'] for e in expanded):
                raise ValueError('event expansion altered common timing or modulation')
            (out/rid/'expanded-events.json').write_text(json.dumps(expanded,indent=2)+'\n')
        observations.append({'source_record_id':rid,'status':diag['status'],'quality':diag['quality'],
            'component_offsets':offsets,'reason':diag['reason'],
            'confidence_heuristic':diag['confidence_heuristic'],
            'diagnostics_sha256':_sha(out/rid/'diagnostics.json'),
            'analysis_sha256':_sha(out/rid/'analysis.json')})
    return observations


def _score(observed,labels):
    by_id={item['source_record_id']:item for item in labels}
    rows=[];positive=accepted_positive=exact=0;hits_total=expected_total=predicted_total=0
    negative=negative_false_composite=ambiguous=ambiguous_composite=ambiguous_abstain=0
    for row in observed:
        label=by_id[row['source_record_id']]
        row={**row,'expected':label['expected'],'description':label['description']}
        actual_composite=row['status']=='supported' and len(row['component_offsets'])>1
        if label['expected']=='composite':
            positive+=1;expected=Counter(label['component_offsets'])
            actual=Counter(row['component_offsets']) if row['status']=='supported' else Counter()
            hits_total+=sum((actual&expected).values());expected_total+=sum(expected.values());predicted_total+=sum(actual.values())
            accepted_positive+=int(actual_composite)
            exact+=int(actual_composite and actual==expected)
            row['expected_component_offsets']=label['component_offsets']
            row['exact_component_set']=actual_composite and actual==expected
        elif label['expected']=='not_composite':
            negative+=1;negative_false_composite+=int(actual_composite)
        else:
            ambiguous+=1;ambiguous_composite+=int(actual_composite);ambiguous_abstain+=int(row['status']=='abstain')
        rows.append(row)
    return {'schema_version':'instrument-composite-evaluation/v1','cases':rows,
        'metrics':{'positive_cases':positive,'positive_accepted':accepted_positive,'positive_exact_sets':exact,
            'positive_exact_set_coverage':exact/positive if positive else None,
            'component_precision_paired':hits_total/predicted_total if predicted_total else None,
            'component_recall_all_positive':hits_total/expected_total if expected_total else None,
            'negative_cases':negative,'negative_false_composite':negative_false_composite,
            'ambiguous_cases':ambiguous,'ambiguous_reported_composite':ambiguous_composite,
            'ambiguous_abstentions':ambiguous_abstain},
        'claim':'Original synthetic controls only; not real-world chord-recognition accuracy. Labels are attached after inference.'}


def evaluate_controls(inputs_path,labels_path,output_dir):
    inputs_path=Path(inputs_path).resolve();labels_path=Path(labels_path).resolve()
    source=json.loads(inputs_path.read_text());out=Path(output_dir).resolve();out.mkdir(parents=True,exist_ok=False)
    items=[]
    for record in source['records']:
        rid=_neutral(record['source_record_id'])
        identity=hashlib.sha256((source['generator_sha256']+rid).encode()).hexdigest()
        probes=[]
        for p in record['probes']:
            probes.append({'wav_path':p['wav_path'],'wav_sha256':p['wav_sha256'],
                'trigger_midi_note':p['trigger_midi_note'],'note_on_seconds':p['note_on_seconds'],
                'note_off_seconds':p['end_seconds']-.12,'analysis_end_seconds':p['analysis_end_seconds'],
                'end_seconds':p['end_seconds'],
                'render_conditions':{'source_identity_kind':'controlled_recipe','source_identity_sha256':identity}})
        items.append((rid,probes))
    observed=_analyze(items,out)
    labels=json.loads(labels_path.read_text())['cases']  # Evaluator-only, after inference.
    report=_score(observed,labels)
    report['inputs']={'path':str(inputs_path),'sha256':_sha(inputs_path)}
    report['labels']={'path':str(labels_path),'sha256':_sha(labels_path)}
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    return report


def evaluate_tracker(probes_root,fixtures_path,output_dir):
    probes_root=Path(probes_root).resolve();fixtures_path=Path(fixtures_path).resolve()
    out=Path(output_dir).resolve();out.mkdir(parents=True,exist_ok=False)
    summaries=sorted(probes_root.glob('*/probes.json'))
    observed=_analyze([(_neutral(json.loads(p.read_text())['source_record_id']),p) for p in summaries],out)
    fixture=json.loads(fixtures_path.read_text())
    by_hash={r['module']['sha256']:r for r in fixture['fixtures'] if 'module' in r}
    by_hash[hashlib.sha256(_mod((0,2,7))).hexdigest()]={'case_id':'authored-sus2','component_offsets':[0,2,7]}
    labels=[]
    for p in summaries:
        source=json.loads(p.read_text());record=by_hash.get(source['source']['module_sha256'])
        if record is None:raise ValueError('no construction label for this original fixture module')
        offsets=record['component_offsets'];kind='composite' if len(offsets)>1 else 'not_composite'
        if record['case_id']=='tracker-power-octave':kind='ambiguous'
        labels.append({'source_record_id':source['source_record_id'],'description':record['case_id'],
            'expected':kind,'component_offsets':offsets})
    report=_score(observed,labels)
    report['inputs']={'path':str(probes_root),'probe_summary_sha256':{p.parent.name:_sha(p) for p in summaries}}
    report['labels']={'path':str(fixtures_path),'sha256':_sha(fixtures_path),
                     'sus2_recipe':'Original fixtures._mod((0,2,7)); hash matched after inference'}
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    a=sub.add_parser('controls');a.add_argument('--inputs',required=True);a.add_argument('--labels',required=True);a.add_argument('--output',required=True)
    b=sub.add_parser('tracker');b.add_argument('--probes',required=True);b.add_argument('--fixtures',required=True);b.add_argument('--output',required=True)
    args=p.parse_args()
    report=evaluate_controls(args.inputs,args.labels,args.output) if args.mode=='controls' else evaluate_tracker(args.probes,args.fixtures,args.output)
    print(json.dumps(report['metrics'],sort_keys=True))


if __name__=='__main__':main()
