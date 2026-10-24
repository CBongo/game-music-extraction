"""Bind later PSX AKAO sequence usage to its explicit associated sample set."""
from __future__ import annotations

from copy import deepcopy
import re
import struct
from typing import Any

from .akao import _datum, _decode_sample_set, _provenance, adapt_akao_v31_sample_set
from .schema import make_bundle, validate_semantics


def bind_akao_sequence_usage(
    usage_bundle: dict[str, Any],
    sequence_data: bytes,
    sample_set_data: bytes,
    *,
    sequence_path: str,
    sample_set_path: str,
    game_or_module: str,
    format_version: str,
    sequence_base_offset: int = 0,
    sample_base_offset: int = 0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Return enriched usage, metadata, and explicit calibration record links.

    The caller confirms v3.1/3.2; earlier AKAO layouts are incompatible. Records
    must name the exact sequence path and ID. Raw identities and notes remain
    unchanged. Unity metadata alone never establishes a sounding-pitch offset.
    """
    if format_version not in ('3.1', '3.2'):
        raise ValueError('sequence binding requires explicit AKAO version 3.1 or 3.2')
    if len(sequence_data) < 0x40 or sequence_data[:4] != b'AKAO':
        raise ValueError('AKAO sequence header is shorter than 0x40 bytes or has invalid magic')
    if sequence_base_offset < 0 or sample_base_offset < 0:
        raise ValueError('source base offsets must be non-negative')
    validate_semantics(usage_bundle)
    sequence_id = struct.unpack_from('<H', sequence_data, 4)[0]
    associated_id = struct.unpack_from('<H', sequence_data, 0x14)[0]
    header, articulations = _decode_sample_set(sample_set_data, sample_set_path, sample_base_offset)
    if associated_id != header['sample_set_id']:
        raise ValueError('sequence associated sample-set ID does not match supplied sample set')
    namespace = f'{sample_set_path}:set-{associated_id:04x}'
    source = {
        'platform': 'Sony PlayStation', 'game_or_module': game_or_module,
        'driver': 'AKAO PSX', 'container': f'sample-set 0x{associated_id:04x}',
        'source_files': [
            {'path': sequence_path, 'offset': sequence_base_offset, 'length': len(sequence_data)},
            {'path': sample_set_path, 'offset': sample_base_offset, 'length': len(sample_set_data)},
        ],
    }
    association = _provenance('AKAO sequence associated sample-set header', sequence_path,
                              sequence_base_offset + 0x14)
    association['parameters'] = {'sample_set_id': associated_id, 'sample_set_path': sample_set_path}

    def pointer(field: int, minimum: int) -> int | None:
        relative = struct.unpack_from('<I', sequence_data, field)[0]
        if relative == 0:
            return None
        target = field + relative
        if target < 0x40 or target + minimum > len(sequence_data):
            raise ValueError(f'AKAO table pointer at 0x{field:x} is outside sequence data')
        return target

    instrument_offset = pointer(0x30, 0x20)
    drum_offset = pointer(0x34, 8)
    melodic = {}
    if instrument_offset is not None:
        end = drum_offset if drum_offset is not None and drum_offset > instrument_offset else len(sequence_data)
        for record in adapt_akao_v31_sample_set(
            sample_set_data, instrument_data=sequence_data[instrument_offset:end],
            game_or_module=game_or_module, source_path=sample_set_path,
            instrument_source_path=sequence_path, format_version=format_version,
            sample_base_offset=sample_base_offset,
            instrument_base_offset=sequence_base_offset + instrument_offset,
        ):
            slot = record['identity']['song_local_slot']
            record['record_id'] = f'{sequence_path}:sequence-{sequence_id}:sample-set-{associated_id:04x}:instrument-{slot}'
            melodic[slot] = record

    def articulation_record(art_id: int) -> dict[str, Any]:
        if art_id not in articulations:
            raise ValueError(f'sequence references missing articulation {art_id}')
        art = articulations[art_id]
        sample_id = f"{namespace}#spu-offset-0x{art['sample_start']:08x}"
        return {
            'record_id': f'{namespace}:articulation-{art_id}', 'source': deepcopy(source),
            'identity': {'source_instrument_id': f'sample-set-{associated_id:04x}:articulation-{art_id}',
                         'sample_ids': [sample_id]},
            'observation': {
                'synthesis': {'engine': 'sample'},
                'pitch': {
                    'source_unity_key': _datum(art['unity_key'], 'midi_note', art['provenance']),
                    'source_fine_tune_cents': _datum(art['fine_cents'], 'cent', art['provenance']),
                },
                'envelopes': [{'kind': 'amplitude', 'raw': {'adsr1': art['adsr1'], 'adsr2': art['adsr2']},
                               'basis': 'source_fact', 'provenance': art['provenance']}],
            },
        }

    def drum_record() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if drum_offset is None:
            raise ValueError('drumkit usage has no sequence drum table')
        zones, bindings, sample_ids = [], [], []
        for key in range(128):
            offset = drum_offset + key * 8
            if offset + 8 > len(sequence_data):
                break
            raw = sequence_data[offset:offset + 8]
            if raw == b'\xff' * 8:
                break
            if raw == b'\0' * 8:
                continue
            art_id, sample_note, attack, sustain, mode, release, volume, pan_reverb = raw
            art_record = articulation_record(art_id)
            art = articulations[art_id]
            sample_id = art_record['identity']['sample_ids'][0]
            if sample_id not in sample_ids:
                sample_ids.append(sample_id)
            provenance = _provenance('AKAO v3.1 drum region', sequence_path,
                                     sequence_base_offset + offset)
            provenance['parameters'] = {'raw_region_bytes': list(raw), 'reverb_enabled': bool(pan_reverb & 128)}
            zones.append({
                'zone_id': key, 'sample_id': sample_id,
                'key_range': {'minimum': key, 'maximum': key},
                'pitch': {
                    'source_unity_key': _datum(art['unity_key'] + key - sample_note, 'midi_note', provenance),
                    'source_fine_tune_cents': _datum(art['fine_cents'], 'cent', art['provenance']),
                },
                'envelopes': [{'kind': 'amplitude',
                    'raw': {'articulation_adsr1': art['adsr1'], 'articulation_adsr2': art['adsr2'],
                            'attack': attack, 'sustain_rate': sustain, 'sustain_mode': mode, 'release': release},
                    'basis': 'source_fact', 'provenance': provenance}],
                'provenance': provenance,
            })
            binding = {'sequence_id': sequence_id, 'source_trigger_note': key,
                       'underlying_instrument_id': art_record['identity']['source_instrument_id'],
                       'zone_id': key, 'volume_raw': volume, 'pan_raw': pan_reverb & 127,
                       'provenance': provenance}
            if sample_note <= 127:
                binding['source_sample_note'] = sample_note
            bindings.append(binding)
        return ({'record_id': f'{namespace}:sequence-{sequence_id}:drumkit',
                 'source': deepcopy(source),
                 'identity': {'source_instrument_id': f'sample-set-{associated_id:04x}:sequence-{sequence_id}:drumkit',
                              'sample_ids': sample_ids},
                 'observation': {'synthesis': {'engine': 'sample'}, 'zones': zones}}, bindings)

    result = deepcopy(usage_bundle)
    metadata, links = {}, {}
    for record in result['records']:
        paths = [item['path'] for item in record['source'].get('source_files', [])]
        if sequence_path not in paths:
            continue
        if record['source']['game_or_module'] != game_or_module:
            raise ValueError('usage game does not match requested AKAO source')
        if record['source']['platform'] not in ('Sony PlayStation', 'psx') or record['source']['driver'] not in ('AKAO', 'AKAO PSX'):
            raise ValueError('usage is not PSX AKAO')
        sequences = record['observation'].get('usage', {}).get('sequences', [])
        if any(type(s['sequence_id']) is not int or s['sequence_id'] != sequence_id for s in sequences):
            raise ValueError('usage sequence ID does not match supplied sequence')
        raw_id = record['identity']['source_instrument_id']
        match = re.fullmatch(rf'sequence-{sequence_id}:(articulation|patch-slot)-(\d+)', str(raw_id))
        bindings = None
        if raw_id == f'sequence-{sequence_id}:drumkit':
            target, bindings = drum_record()
        elif match:
            kind, number = match[1], int(match[2])
            if record['identity'].get('source_patch_command') != number:
                raise ValueError('usage patch operand conflicts with raw instrument identity')
            if kind == 'articulation':
                target = articulation_record(number)
            else:
                if number not in melodic:
                    raise ValueError(f'key-split slot {number} has no sequence instrument regions')
                target = melodic[number]
        else:
            raise ValueError(f'unrecognized sequence-scoped AKAO identity: {raw_id!r}')
        observation = record['observation']
        for field in ('zones', 'pitch', 'envelopes', 'synthesis'):
            if field in target['observation']:
                value = deepcopy(target['observation'][field])
                if field in observation and observation[field] != value:
                    raise ValueError(f'usage already contains conflicting {field}')
                observation[field] = value
        samples = target['identity']['sample_ids']
        if 'sample_ids' in record['identity'] and record['identity']['sample_ids'] != samples:
            raise ValueError('usage contains conflicting sample identities')
        record['identity']['sample_ids'] = list(samples)
        usage = observation.setdefault('usage', {'provenance': association})
        usage['provenance'].setdefault('parameters', {})['sample_set_association'] = deepcopy(association)
        if bindings is not None:
            if 'percussion_bindings' in usage and usage['percussion_bindings'] != bindings:
                raise ValueError('usage contains conflicting percussion bindings')
            usage['percussion_bindings'] = bindings
        metadata[target['record_id']] = deepcopy(target)
        links[record['record_id']] = target['record_id']
    return result, make_bundle(metadata.values(), 'AKAO sequence/sample binding'), links
