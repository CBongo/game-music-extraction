# Calibrated source and GM comparisons

The comparison pipeline measures isolated source audio before matching it to a
concrete GM probe. A raw chip pitch register, a sample unity key, and a sounding
MIDI note are separate facts. Unknown acoustic pitch remains unknown.

## Workflow

1. Render a native probe using the [source backends](instrument-rendering.md).
2. Run `python -m instrument_analysis.pitch NATIVE_MANIFEST --output CALIBRATION`.
3. If calibration succeeds, select a GM note within the declared cents tolerance.
   If a new native pitch register is needed, render and measure again.
4. Run the comparison CLI to emit raw phase excerpts and a sequential A/B preview.

```powershell
python -m instrument_analysis.comparisons --native-manifest NATIVE_MANIFEST --gm-manifest GM_MANIFEST --gm-probe-id gm-b00000-p000-n060-v096 --pitch-calibration CALIBRATION --output OUTPUT_DIRECTORY
```

Use `--help` for the gain and gap controls. Outputs refuse overwrites. The input
native manifest, WAV, receipt, calibration, GM manifest, and GM WAV are hash-bound.
The calibration's source record and raw register must identify this exact capture.
Acoustic F0 and reported MIDI pitch must agree. Invalid hashes, abstained or weak
calibration, wet references, out-of-tolerance pitch, missing phases, incompatible
sample rates, and clipping presentation gains fail before successful publication.
Mono FM input is duplicated to stereo for previews without resampling.

The pitch estimator uses mean-removed normalized autocorrelation, sub-sample peak
refinement, and multiple sustain windows. It searches 55-1500 Hz and abstains on
weak periodicity, inconsistent windows, competing peaks, or strong beating. Its
confidence is a periodicity heuristic, not a calibrated probability. These gates
are conservative engineering defaults, not a broadly evaluated pitch detector.
Hash-bound verified-driver evidence can also be supplied; unity metadata alone
is not accepted as an acoustic pitch reference.

`plan_source_probes()` selects usage quantiles while retaining trigger notes and
pitch context. `plan_native_register_probes(source_plan, calibration)` bridges
known sounding notes to nominal S-DSP/SPU register ratios, bounded to 14 bits.
It skips unknown source pitch, unrelated records, and out-of-range requests.
Every request is marked `planned_requires_remeasurement`: register ratios do not
replace fresh acoustic calibration, or implement driver zone/envelope changes.
YM2151 keycodes are not treated as linear pitch-register ratios.

## Phase and level handling

The comparison uses declared note-on/off times, retained pre-on sample frames,
and source clock uncertainty. It takes the common hold interval and the common
release interval separately. Any removed hold frames are recorded as a phase
jump; the raw original recordings remain intact. Event timing is not a claim of
sample-exact acoustic onset alignment. No pitch shifting or time stretching is
performed. The current melodic comparison requires a nonempty release interval.

Excerpt files retain original amplitudes. Explicit scalar gains affect only the
A/B presentation and must not clip. Native fixed voice volume and GM MIDI velocity
remain different controls. Preview level matching is not velocity calibration.

## Actual validation

The FF8 AKAO 3.1 set-10 articulation-76 probe at raw SPU pitch 4096 measures
462.59378 Hz (MIDI 69.86691), with 0.31725 cents window MAD and confidence 0.98815.
Its independent repeated capture gives the same result. A nominal register ratio
then requested 2317 (0x090D) for C4. A **fresh** capture measures 262.12981 Hz,
MIDI 60.03333, confidence 0.98579: 3.333 cents above the GeneralUser C4 target.
The original raw-pitch capture was not relabeled as C4.

The validated comparison is in ignored
`instrument-renders/ff8-spu-vs-generaluser-c4-20261025/`. It pairs that source with
GeneralUser GS program 0, note 60, velocity 96. Each excerpt contains 22050 hold
and 22050 release frames at 44100 Hz. The A/B preview has 99225 frames including
a 0.25-second silent gap. Source hold trimming removes 64 frames; one-frame
source timing uncertainty is retained in the manifest. Piano is a pipeline
example, not an endorsed replacement for the source articulation.

An additional preview in `ff8-spu-vs-generaluser-c4-levelled-20261025/` applies
GM presentation gain 14.9 to approximately match the excerpts' RMS levels.
Both raw excerpts remain unchanged and neither preview clips. Its A/B SHA-256 is
`d85f0bd64496c396d3695993894e69a8970d17c9540e8595302a989af6987246`.
The unity-gain preview SHA-256 is
`537928948013d7cc8a934b5e2e49019caa0b0b200d2b972c92cc974db31cb885`.
Independent auditing validated both schemas, all input/output hashes, exact
source phase bytes, frame counts, and clipping. The audit summary is ignored
`instrument-renders/comparison-validation-summary.json`.

The current SNES Airship capture abstains because periodic peaks are ambiguous;
the Gradius III FM capture abstains because sustain estimates disagree by an
octave. Neither is forced into a pitch-matched comparison. Full driver behavior,
held-out mapping accuracy, and cross-soundfont consensus are not established by
these successful source/reference pipeline checks.
