"""Choose source probe triggers from raw usage without importing curated GM maps."""
from __future__ import annotations
from copy import deepcopy
import math
from typing import Any
from .schema import validate_semantics


def plan_source_probes(bundle: dict[str,Any], *, standard_notes=(48,60,72),
                       max_usage_notes=3, velocity=96, duration_seconds=0.5,
                       tail_seconds=0.5, sample_rate=44100) -> dict[str,Any]:
    """Return a schema-linked plan, never a claim that audio has been rendered.

    Usage quantiles retain their source pitch state and expected sounding pitch
    when known. Standard-grid triggers have unknown sounding pitch until source
    calibration is supplied. Curated existing_mapping has no influence.
    """
    validate_semantics(bundle)
    if type(max_usage_notes) is not int or max_usage_notes<1:
        raise ValueError('max_usage_notes must be positive')
    if type(velocity) is not int or not 1<=velocity<=127:
        raise ValueError('velocity must be 1..127')
    if type(sample_rate) is not int or not 8000<=sample_rate<=192000:
        raise ValueError('invalid sample rate')
    for value in (duration_seconds,tail_seconds):
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value<0:
            raise ValueError('durations must be finite and nonnegative')
    if duration_seconds<=0:
        raise ValueError('note duration must be positive')
    if any(type(n) is not int or not 0<=n<=127 for n in standard_notes) or len(set(standard_notes))!=len(standard_notes):
        raise ValueError('standard notes must be unique MIDI integers')
    result={'schema_version':'instrument-source-probe-plan/v1','status':'planned',
            'conditions':{'velocity':velocity,'duration_seconds':duration_seconds,
                          'tail_seconds':tail_seconds,'sample_rate':sample_rate},'probes':[]}
    for record in bundle['records']:
        occurrences=[n for seq in record['observation'].get('usage',{}).get('sequences',[])
                     for n in seq.get('note_occurrences',[])]
        eligible=[n for n in occurrences if n.get('pitch_context_complete') is True]
        pool=eligible or occurrences
        ordered=sorted(pool,key=lambda n:n['raw_sequence_note'])
        chosen=[]
        if ordered:
            for i in range(min(max_usage_notes,len(ordered))):
                count=min(max_usage_notes,len(ordered))
                index=(len(ordered)-1)//2 if count==1 else round(i*(len(ordered)-1)/(count-1))
                candidate=ordered[index]
                signature=tuple(candidate.get(k) for k in ('raw_sequence_note','global_transpose_semitones','track_transpose_semitones','ramp_transpose_semitones','sample_tuning_semitones'))
                if not any(signature==entry[0] for entry in chosen):
                    chosen.append((signature,candidate))
        entries=[(n,'usage_quantile') for _,n in chosen]
        represented={n['raw_sequence_note'] for n,_ in entries}
        entries.extend(({'raw_sequence_note':n},'standard_grid') for n in standard_notes if n not in represented)
        for index,(note,origin) in enumerate(entries):
            probe={'probe_id':f'{record["record_id"]}:probe-{index}',
                   'source_record_id':record['record_id'],'source':deepcopy(record['source']),
                   'identity':deepcopy(record['identity']),'trigger_note':note['raw_sequence_note'],
                   'selection':origin,'source_pitch_context':deepcopy(note),
                   'comparison_pitch_known':note.get('pitch_context_complete') is True and 'sounding_midi_note' in note}
            if probe['comparison_pitch_known']:
                probe['expected_sounding_midi_note']=note['sounding_midi_note']
            result['probes'].append(probe)
    return result


def plan_native_register_probes(source_plan: dict, calibration: dict) -> dict:
    """Plan SPU/S-DSP register ratios from grounded usage; remeasure each result."""
    if source_plan.get('schema_version') != 'instrument-source-probe-plan/v1':
        raise ValueError('unsupported source probe plan')
    if (calibration.get('schema_version') != 'instrument-pitch-calibration/v1'
            or calibration.get('status') != 'calibrated'
            or calibration.get('basis') not in {'acoustic_measurement', 'verified_driver_reference'}):
        raise ValueError('a grounded calibrated pitch artifact is required')
    confidence, measured = calibration.get('confidence'), calibration.get('sounding_midi_note')
    if (type(confidence) not in (float, int) or not math.isfinite(confidence)
            or not 0.8 <= confidence <= 1 or type(measured) not in (float, int)
            or not math.isfinite(measured) or not 0 <= measured <= 127):
        raise ValueError('calibration pitch or confidence is invalid')
    raw = calibration.get('native_raw_pitch', {})
    if (raw.get('key') not in {'pitch_register_raw_s_dsp', 'pitch_register_raw_spu'}
            or type(raw.get('value')) is not int or not 1 <= raw['value'] <= 0x3fff):
        raise ValueError('only nonzero calibrated S-DSP/SPU pitch registers are supported')
    for field in ('native_manifest_sha256', 'native_wav_sha256'):
        value = calibration.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('calibration requires exact input hashes')
    result = {'schema_version': 'instrument-native-register-plan/v1',
              'status': 'planned_requires_remeasurement',
              'conditions': deepcopy(source_plan['conditions']),
              'calibration_manifest_sha256': calibration['native_manifest_sha256'],
              'calibration_wav_sha256': calibration['native_wav_sha256'],
              'probes': [], 'skipped': []}
    for probe in source_plan['probes']:
        reason = None
        if probe['source_record_id'] != calibration.get('source_record_id'):
            reason = 'different_source_record'
        elif probe.get('comparison_pitch_known') is not True:
            reason = 'unknown_source_pitch'
        target = probe.get('expected_sounding_midi_note')
        if reason is None and (type(target) not in (int, float) or not math.isfinite(target) or not 0 <= target <= 127):
            reason = 'invalid_target_pitch'
        register = None if reason else round(raw['value'] * 2 ** ((target - measured) / 12))
        if reason is None and not 1 <= register <= 0x3fff:
            reason = 'pitch_register_out_of_range'
        if reason:
            result['skipped'].append({'probe_id': probe['probe_id'], 'reason': reason})
            continue
        result['probes'].append({**deepcopy(probe),
            'native_raw_pitch_request': {'key': raw['key'], 'value': register},
            'target_sounding_midi_note': target,
            'predicted_register_rounding_cents': 1200 * math.log2(register / raw['value']) - 100 * (target - measured),
            'acoustic_pitch_verified': False})
    return result
