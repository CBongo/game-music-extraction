"""Label-isolated pilot demonstration; descriptive metrics, not held-out accuracy."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from .benchmark import evaluate_benchmark
from .pitch import calibrate_wav_pitch
from .ranking import rank_wav_source, prepared_reference_index
from .schema import make_bundle
from .rendering import make_probe_midi


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_pilot(benchmark_path, index_path, fixture_dir, gm_manifest_path, output_dir):
    benchmark_path, index_path = Path(benchmark_path).resolve(), Path(index_path).resolve()
    fixture_dir, gm_manifest_path = Path(fixture_dir).resolve(), Path(gm_manifest_path).resolve()
    benchmark = json.loads(benchmark_path.read_text())
    fixture_manifest = json.loads((fixture_dir / 'fixtures.json').read_text())
    gm = json.loads(gm_manifest_path.read_text())
    if benchmark.get('schema_version') != 'instrument-benchmark/v1':
        raise ValueError('unsupported benchmark')
    if fixture_manifest.get('schema_version') != 'instrument-fixtures/v1' or gm.get('schema_version') != 'instrument-render/v1':
        raise ValueError('unsupported asset manifest')
    fixtures = {item['case_id']: item for item in fixture_manifest['fixtures']}
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs = out / 'inputs'; inputs.mkdir()
    records, audit = [], []
    with prepared_reference_index(index_path) as prepared:
        for number, case in enumerate(benchmark['cases']):
            neutral = f'query-{number:04d}'
            wav = inputs / f'{neutral}.wav'
            asset, source, timing = case.get('asset', {}), None, None
            classification = None
            kind = asset.get('kind')
            if kind == 'generated_fixture':
                fixture = fixtures.get(asset.get('fixture_id'))
                if fixture is not None:
                    name = fixture['wav']['path']
                    if Path(name).name != name:
                        raise ValueError('fixture path must be a local basename')
                    source = fixture_dir / name
                    if _hash(source) != fixture['wav']['sha256']:
                        raise ValueError('fixture WAV hash differs from manifest')
                    # These original one-second fixture WAVs have a declared 80ms release.
                    if fixture['generator'] != 'original deterministic oscillator sum; not native hardware' or fixture['wav']['duration_seconds'] != 1.0:
                        raise ValueError('unsupported fixture envelope recipe')
                    timing = (0.0, .92, 1.0)
            elif kind == 'gm_reference_recipe':
                recipe = asset.get('route', {})
                candidates = [p for p in gm['probes'] if p['route'].get('kind') == recipe.get('kind')]
                if recipe.get('kind') == 'gm_program':
                    candidates = [p for p in candidates if p['route']['program_0based'] == recipe['program_0based'] and p['note'] == case['render_conditions']['note']]
                elif recipe.get('kind') == 'gm_percussion':
                    candidates = [p for p in candidates if p['route']['note'] == recipe['note']]
                if candidates:
                    probe = candidates[0]
                    source = gm_manifest_path.parent / probe['wav']['file']
                    if _hash(source) != probe['wav']['sha256']:
                        raise ValueError('GM WAV hash differs from manifest')
                    hold, tail = gm['conditions']['duration_seconds'], gm['conditions']['tail_seconds']
                    timing = (0.0, hold, hold + tail)
                    # Route context comes from actual rendered MIDI channel, never case labels.
                    if probe.get('channel_0based') == 9:
                        midi_bytes = make_probe_midi(probe, duration_seconds=hold, tail_seconds=tail)
                        if (hashlib.sha256(midi_bytes).hexdigest() != probe['midi_sha256'] or
                                bytes([0, 0x99, probe['note'], probe['velocity']]) not in midi_bytes):
                            raise ValueError('channel-10 MIDI evidence differs from render hash')
                        midi = inputs / f'{neutral}.source.mid'
                        midi.write_bytes(midi_bytes)
                        classification = {'kind': 'gm_percussion_trigger', 'basis': 'source_fact',
                            'gm_channel_0based': 9, 'evidence': 'Verified rendered MIDI channel-10 note-on',
                            'provenance': {'method': 'MIDI channel in hash-bound render manifest',
                                'location': {'path': str(midi)},
                                'parameters': {'midi_sha256': _hash(midi), 'gm_manifest_sha256': _hash(gm_manifest_path)}}}
            if source is not None:
                shutil.copyfile(source, wav)
            on, off, end = timing or (0.0, .5, 1.0)
            facts = {'source': {'platform': 'controlled audio', 'game_or_module': 'neutral query', 'driver': 'explicit waveform events'},
                     'identity': {'source_instrument_id': neutral}, 'observation': {}}
            cal = None
            if source is not None:
                cal = calibrate_wav_pitch(wav, source_record_id=neutral, note_on_seconds=on,
                    note_off_seconds=off, end_seconds=end, output_path=inputs / f'{neutral}.pitch.json')
                facts.update(source_record_id=neutral, wav_sha256=_hash(wav))
                if classification:
                    facts['classification']=classification
                # Trigger pitch is an input event; offset comes from measured PCM, never labels.
                trigger = case.get('render_conditions', {}).get('note')
                if kind == 'generated_fixture' and cal['status'] == 'calibrated' and type(trigger) is int:
                    offset = cal['sounding_midi_note'] - trigger
                    semitones = round(offset)
                    facts['observation']['pitch'] = {'observed_pitch_offset': {
                        'semitones': semitones, 'residual_cents': 100 * (offset - semitones),
                        'basis': 'measured', 'confidence': cal['confidence'],
                        'provenance': {'method': 'Measured acoustic pitch minus authored trigger event',
                            'location': {'path': str(wav)},
                            'parameters': {'wav_sha256': _hash(wav), 'trigger_midi_note': trigger}}}}
            bundle, diagnostic = rank_wav_source(wav, index_path, record_id=neutral,
                note_on_seconds=on, note_off_seconds=off, end_seconds=end,
                calibration=cal, source_facts=facts if source is not None else None, output_dir=out / neutral, prepared_index=prepared)
            # Benchmark identity is restored only after inference has completed.
            record = deepcopy(bundle['records'][0]); record['record_id'] = case['record_id']
            records.append(record)
            audit.append({'case_id': case['case_id'], 'neutral_query_id': neutral,
                'audio_available': source is not None, 'actual_event_boundaries_seconds': {'note_on':on,'note_off':off,'end':end},
                'calibration_status': cal['status'] if cal else None,
                'rank_status': diagnostic['status'], 'reason': diagnostic.get('reason'),
                'prediction_route': record['analysis']['routing'][0]['route']})
    predictions = make_bundle(records, 'instrument_analysis.pilot')
    report = evaluate_benchmark(benchmark, predictions)
    summary = {'schema_version':'instrument-pilot-run/v1',
        'benchmark':{'path':str(benchmark_path),'sha256':_hash(benchmark_path)},
        'index':{'path':str(index_path),'sha256':_hash(index_path)},
        'feature_label_isolation':'Only neutral IDs/paths, WAVs, event timing, measured pitch and whitelisted source facts enter the ranker; benchmark labels are read only by the evaluator.',
        'claim':'Descriptive pilot/plumbing demonstration; no held-out game accuracy or tuned confidence claim.',
        'cases':audit}
    for name,value in [('predictions.json',predictions),('report.json',report),('run.json',summary)]:
        (out/name).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
    return summary, report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ['benchmark','index','fixtures','gm-manifest','output']:
        parser.add_argument('--'+flag,required=True)
    args=parser.parse_args(argv)
    summary,report=run_pilot(args.benchmark,args.index,args.fixtures,args.gm_manifest,args.output)
    print(json.dumps({'cases':len(summary['cases']),'audio_available':sum(c['audio_available'] for c in summary['cases']),
        'route_accuracy':report['metrics']['route_accuracy']}))


if __name__=='__main__':
    main()
