"""Original, reproducible audio fixtures for benchmark contract tests.

These clips are controlled oscillator sums, not emulations of a game driver or
acoustic instrument. Generated WAV/MOD assets are CC0-1.0; game assets stay local.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path
import struct
import wave

SYNTHETIC_CASES = {
    'tracker-major-inversion': (-5, 0, 4),
    'tracker-minor': (0, 3, 7),
    'tracker-sus4': (0, 5, 7),
    'tracker-power-octave': (0, 7, 12),
    'single-rich-tone': (0,),
    'pitch-offset-minus5': (-5,),
    'pitch-offset-plus7': (7,),
    'sfx-noise-burst': (),
    'abstain-silence': (),
}


def _frames(offsets, *, note=60, sample_rate=22050, seconds=1.0):
    count = round(sample_rate * seconds)
    for index in range(count):
        t = index / sample_rate
        envelope = min(1.0, t / 0.01, (seconds - t) / 0.08)
        # Controlled harmonics make the single-tone/chord distinction nontrivial.
        value = sum(sum(math.sin(2 * math.pi * 440 * 2 ** ((note + offset - 69) / 12) * h * t) / h
                        for h in (1, 2, 3)) for offset in offsets)
        yield max(-32767, min(32767, round(value * 0.38 * 32767 * envelope / len(offsets))))


def _mod(offsets) -> bytes:
    # ProTracker PAL period 428 establishes the sample's playback rate.
    # The oscillator samples are authored at that rate. The chord label
    # describes the generated trigger-relative sample components,
    # not a claim about an external tracker's pitch calibration.
    length = 8192
    rate = 7093789.2 / (2 * 428)
    sample = bytearray()
    for i in range(length):
        t = i / rate
        value = sum(math.sin(2 * math.pi * 261.6255653 * 2 ** (o / 12) * t) for o in offsets)
        sample.append(round(100 * value / len(offsets)) & 255)
    header = bytearray(b'GME original chords'.ljust(20, b'\0'))
    header += b'original oscillator'.ljust(22, b'\0') + struct.pack('>HBBHH', length // 2, 0, 64, 0, 1)
    header += bytes(30 * 30)
    header += bytes([1, 0]) + bytes(128) + b'M.K.'
    pattern = bytearray(1024)
    # Sample 1, period 428, channel 0; other channels silent.
    pattern[:4] = bytes([1, 0xAC, 0x10, 0])
    # Stop at row 16 via position jump; renderer must bound loop count/duration.
    pattern[16 * 16:16 * 16 + 4] = bytes([0, 0, 0x0B, 0])
    return bytes(header + pattern + sample)


def generate_fixtures(output_dir: str | Path, *, overwrite=False) -> dict:
    """Write original WAVs plus playable MOD containers and a hash manifest."""
    out = Path(output_dir)
    paths = [out / f'{name}{suffix}' for name, offsets in SYNTHETIC_CASES.items() for suffix in (('.wav', '.mod') if offsets else ('.wav',))]
    paths.append(out / 'fixtures.json')
    if not overwrite and any(p.exists() for p in paths):
        raise FileExistsError('fixture outputs already exist')
    out.mkdir(parents=True, exist_ok=True)
    records = []
    for name, offsets in SYNTHETIC_CASES.items():
        wav_path = out / f'{name}.wav'
        with wave.open(str(wav_path), 'wb') as wav:
            wav.setparams((1, 2, 22050, 0, 'NONE', 'not compressed'))
            if offsets:
                frames = list(_frames(offsets))
            elif name == 'abstain-silence':
                frames = [0] * 22050
            else:
                rng = random.Random(0)
                frames = [round(rng.uniform(-12000,12000)*math.exp(-20*i/22050)) for i in range(22050)]
            wav.writeframes(struct.pack('<22050h', *frames))
        mod_path = out / f'{name}.mod'
        if offsets:
            mod_path.write_bytes(_mod(offsets))
        record = {'case_id': name, 'license': 'CC0-1.0',
            'generator': 'original deterministic oscillator sum; not native hardware',
            'component_offsets': list(offsets), 'trigger_midi_note': 60,
            'wav': {'path': wav_path.name, 'sha256': hashlib.sha256(wav_path.read_bytes()).hexdigest(),
                    'sample_rate': 22050, 'duration_seconds': 1.0},
            'module': {'path': mod_path.name, 'sha256': hashlib.sha256(mod_path.read_bytes()).hexdigest() if offsets else '',
                       'format': 'ProTracker M.K.', 'period': 428, 'finetune': 0,
                       'loop': 'disabled', 'pitch_reference': 'synthetic trigger-relative; verify with module renderer'}}
        if not offsets:
            record.pop('module')
            record.pop('component_offsets')
        records.append(record)
    manifest = {'schema_version': 'instrument-fixtures/v1', 'fixtures': records}
    (out/'fixtures.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output_dir')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    generate_fixtures(args.output_dir, overwrite=args.overwrite)


if __name__ == '__main__':
    main()
