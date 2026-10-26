# Baked-chord analysis

`instrument_analysis.composites` detects trigger-relative tonal components in
isolated samples. The priority hypotheses are major, minor, sus2 and sus4
triads, including inversions and octave voicings. It compares those components
with single rich tones and missing-fundamental alternatives rather than treating
every spectral peak as a note.

## Render grounded tracker probes

`render_tracker_sample_probes()` supports strict four-channel ProTracker `M.K.`
modules. It retains sample bytes, finetune, volume and loop headers, then replaces
the song patterns with an isolated sample trigger, explicit tempo and note cut.
It is not whole-song replay and does not preserve original song effect commands.

```python
from instrument_analysis.tracker_rendering import render_tracker_sample_probes

probes = render_tracker_sample_probes(
    'benchmarks/instruments/generated/fixtures/tracker-major-inversion.mod',
    sample_number=1,
    triggers=[{'trigger_midi_note': 60, 'period': 428},
              {'trigger_midi_note': 62, 'period': 381},
              {'trigger_midi_note': 64, 'period': 339}],
    output_dir='instrument-renders/my-tracker-probes',
    source_record_id='sample-001',
    openmpt123_path='D:/libopenmpt/openmpt123/amd64/openmpt123.exe',
)
```

Those trigger references are grounded in the original fixture's authoring recipe.
**Period 428 does not universally establish MIDI C4 for an arbitrary sample.**
Caller-supplied trigger notes and raw periods remain distinct. For the authored
single-tone control, actual measured pitches were MIDI 60.000, 62.014 and 64.036;
integer-period rounding is visible rather than silently relabeling the audio.

The renderer records module/sample/header/executable/probe hashes, raw period,
finetune, loop and offset settings. Source names are omitted from neutral probe
summaries so the analyzer cannot use descriptive fixture names. Original source
mapping remains in the evaluator's manifest. Unsupported formats, malformed
sample bounds, invalid loops and unrepresentable settings fail explicitly.

Rendering uses silent file output, 44.1 kHz stereo PCM16, gain 0 dB, no dither or
volume ramping, and explicit resampler controls. Default `amiga_resampler=False`
uses the selected interpolation taps; `True` selects OpenMPT's A500 model.
[libopenmpt's controls](https://lib.openmpt.org/doc/group__libopenmpt__c.html)
document that enabling Amiga resampling overrides interpolation taps. The explicit
control settings and selected profile are retained in each probe. A four-byte repeat
loop is active; the two-byte disabled-repeat marker is not treated as a loop.
Sample offset uses 256-byte `9xx` units and currently rejects looped samples.

Probes use 130 BPM, six ticks per row and row-8 `EC0` note cut. The nominal cut
is 0.9230769 seconds. A one-shot sample may exhaust earlier, especially at a
higher trigger: its estimated endpoint and the later cut remain separate.
Analysis windows stop before that endpoint with a guard. The estimate uses the
raw period and signed finetune; it is not a measured acoustic note-off timestamp.

## Analyze and inspect alternatives

```powershell
python -m instrument_analysis.composites --probe-summary instrument-renders/my-tracker-probes/sample-001/probes.json --output instrument-renders/my-composite-analysis
```

The API is `analyze_baked_chord(summary_path, output_dir=...)`; it also accepts
neutral direct WAV probes with explicit trigger/timing and a shared source
identity. Sample-byte hashes and controlled-recipe hashes are different identity
kinds. A recipe hash never claims proof that decoded sample bytes were preserved.
All WAV hashes, tracker probe MODs/sample bytes and renderer identity are checked.

The standard-library baseline extracts refined peaks from a 32768-point Hann FFT,
then fits nonnegative harmonic templates. A complexity penalty compares sparse
one-, two- and three-component models. Multi-component roots need observed
fundamental support; unseen subharmonics remain competing single-tone hypotheses.
The model compares actual peak-frequency residuals, not just rounded MIDI
intervals. Equal-tempered thirds differ from just harmonic ratios, so rounded
major-chord labels alone cannot decide that comparison.

Distinct trigger probes must agree on semitone offsets and tuning. Noise,
clipping, short windows, inconsistent triggers, poor residual fit, and comparable
missing-root alternatives abstain. A strong observed third note that can also be
absorbed as a dyad's harmonic is reported as a competing model; unresolved note
count is not expanded automatically.

Outputs are canonical `analysis.json` plus detailed `diagnostics.json`. They
retain exact octave offsets, residual cents, relative levels, model residuals,
confidence heuristics and alternatives. Confidence is not a calibrated
probability. Status is `supported`, `tentative` or `abstain`; only supported
components enter the canonical composite field. For G3-C4-E4 triggered from C4,
accepted offsets are **[-5, 0, +4]**, not merely the pitch classes C/E/G.

A suspended pitch set may have multiple musical roots: C-D-G can be Csus2 or
Gsus4. All valid family/root readings are exposed. The reported primary quality
prefers a trigger-centered root when available; that naming convention does not
prove harmonic function. Accepted note components can remain useful even when
the musical root has alternatives.

## Expand accepted events

```python
from instrument_analysis.composites import analyze_baked_chord, expand_composite_events

analysis, diagnostics = analyze_baked_chord('PATH_TO_PROBES_JSON')
events = [{'midi_note': 60, 'start': 1.25, 'duration': .75, 'velocity': 99,
           'pitch_bend': [0, 100, -50], 'vibrato': {'depth_cents': 18}}]
expanded = expand_composite_events(events, analysis, diagnostics)
```

For offsets [-5,0,+4], that event becomes MIDI notes [55,60,64]. Every component
retains timing, duration, velocity and common bend/vibrato data. Residual cents
and relative levels remain explicit attributes; they are not silently converted
into note numbers or velocity. This is MIDI-note event expansion, not a complete
SMF exporter or per-note pitch-bend channel allocator. Out-of-range notes, source
identity mismatches, malformed components or unsupported analyses reject the
whole expansion. Existing converters and curated patch maps are unchanged.

## Validation and limits

The [validation record](instrument-composite-validation.md) reports all four
priority families through OpenMPT plus independent constructed controls. No
pretrained multipitch model is used in this baseline. Arbitrary real tracker
samples, strongly evolving effects, non-M.K. formats, and learned-model gains
have not been validated by these tests.

Exact octave/harmonic layers may be indistinguishable from a rich single tone.
The baseline can select a parsimonious single-tone explanation; that is not proof
that separately authored octave layers were absent. Unsupported analyses retain
alternatives for review. This limitation and the controlled-data scope must remain
visible before applying reconstructions to unfamiliar music.
