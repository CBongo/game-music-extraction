"""Original CC0 composite-analysis controls; labels never enter the analyzer."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import struct
import wave

# Independent test controls, not an instrument vocabulary or ranking input.
CASES = [
    ('major-inversion', (-5, 0, 4), 'composite'),
    ('minor-root', (0, 3, 7), 'composite'),
    ('sus4-root', (0, 5, 7), 'composite'),
    ('sus2-root', (0, 2, 7), 'composite'),
    ('major-wide', (-12, 4, 7), 'composite'),
    ('major-first-inversion', (-8, -5, 0), 'composite'),
    ('tritone-dyad', (0, 6), 'composite'),
    ('octave-stack', (-12, 0, 12), 'ambiguous'),
    ('power-dyad', (0, 7), 'ambiguous'),
    ('rich-single', (0,), 'not_composite'),
    ('missing-fundamental', (0,), 'ambiguous'),
    ('just-major-harmonics', (0,), 'ambiguous'),
    ('shifted-single', (-5,), 'not_composite'),
    ('chorused-single', (0,), 'not_composite'),
    ('vibrato-single', (0,), 'not_composite'),
    ('noise', (), 'not_composite'),
    ('metallic', (0,), 'not_composite'),
    ('silence', (), 'not_composite'),
]


def generate_composite_controls(output_dir, *, sample_rate=22050, trigger_notes=(60,64,67)):
    out=Path(output_dir).resolve();out.mkdir(parents=True,exist_ok=False)
    inputs=[];labels=[];seconds=1.2
    for number,(name,offsets,expected) in enumerate(CASES):
        rid=f'control-{number:04d}';probes=[]
        noise_rng=random.Random(700+number)
        noise_base=[noise_rng.uniform(-1,1) for _ in range(math.ceil(sample_rate*seconds*2**((max(trigger_notes)-min(trigger_notes))/12))+2)] if name=='noise' else []
        for trigger in trigger_notes:
            rng=random.Random(500+number)
            phase=[rng.random()*2*math.pi for _ in offsets]
            values=[]
            for frame in range(round(sample_rate*seconds)):
                t=frame/sample_rate
                envelope=min(1.,t/.025,(seconds-t)/.12)
                value=0.
                for voice,offset in enumerate(offsets):
                    f0=440*2**((trigger+offset-69)/12)
                    base=2*math.pi*f0*t+phase[voice]
                    if name=='just-major-harmonics':
                        base=2*math.pi*(f0/4)*t+phase[voice]
                        profile=((4,1.),(5,.8),(6,.6))
                    elif name=='missing-fundamental':
                        profile=((2,1.),(3,.7),(4,.45))
                    elif name=='rich-single':
                        profile=((1,.3),(2,1.),(3,.7),(4,.2),(5,.12),(7,.07))
                    elif name=='metallic':
                        value+=math.sin(base)+.8*math.sin(2*math.pi*f0*1.37*t)+.4*math.sin(2*math.pi*f0*2.73*t)
                        continue
                    else:
                        profile=((1,1.),(2,.17),(3,.07),(4,.025))
                    if name=='vibrato-single':
                        base+=(f0*(2**(.12/12)-1)/5)*math.sin(2*math.pi*5*t)
                    for harmonic,level in profile:
                        value+=level*math.sin(harmonic*base)
                    if name=='chorused-single':
                        value+=.8*math.sin(2*math.pi*f0*2**(.07/12)*t+.3)
                if name=='noise':
                    position=frame*2**((trigger-min(trigger_notes))/12)
                    a=int(position);fraction=position-a
                    value=noise_base[a]*(1-fraction)+noise_base[a+1]*fraction
                scale=5500/max(1,len(offsets))
                values.append(round(max(-32767,min(32767,value*scale*envelope))))
            filename=f'{rid}-t{trigger:03d}.wav';path=out/filename
            with wave.open(str(path),'wb') as stream:
                stream.setparams((1,2,sample_rate,0,'NONE','not compressed'))
                stream.writeframes(struct.pack('<'+'h'*len(values),*values))
            probes.append({'wav_path':str(path),'wav_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'trigger_midi_note':trigger,'note_on_seconds':0.,'analysis_start_seconds':.15,
                'analysis_end_seconds':.95,'end_seconds':seconds,
                'trigger_basis':'authored equal-tempered oscillator; source notes known by construction'})
        inputs.append({'source_record_id':rid,'probes':probes})
        labels.append({'source_record_id':rid,'description':name,'expected':expected,
            'component_offsets':list(offsets) if expected=='composite' else None})
    source_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for filename,value in [('inputs.json',{'schema_version':'instrument-composite-control-inputs/v1','license':'CC0-1.0 for generated audio','generator_sha256':source_hash,'records':inputs}),
                           ('labels.json',{'schema_version':'instrument-composite-control-labels/v1','generator_sha256':source_hash,'cases':labels})]:
        (out/filename).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
    return inputs,labels


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output_dir');a=p.parse_args()
    inputs,_=generate_composite_controls(a.output_dir)
    print(json.dumps({'sources':len(inputs),'renders':sum(len(x['probes']) for x in inputs)}))


if __name__=='__main__':
    main()
