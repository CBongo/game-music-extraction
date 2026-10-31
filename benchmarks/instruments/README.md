# Instrument benchmark pilot

`pilot-v1.json` is a 16-case manifest, not a published accuracy result. Its labels
are deliberately separated by origin:

- `historical-dev`: FF4/FF2-US flute, trumpet, and bass replacement choices from
  `snes/ff2/ff2mus.pl`. These are provisional historical judgments, not acoustic
  ground truth. The raw legacy transpose is recorded as a candidate audition
  octave adjustment; it is not an observed pitch offset.
- `synthetic-contract`: original oscillator sums with exact major-inversion,
  minor, sus4, power/octave, single-tone, and pitch-offset construction; authored
  noise-burst SFX and silence controls. Labels describe construction and intended
  function, not universal waveform-to-GM equivalence.
- `same-bank-sanity`: a normal-channel vibraphone recipe and a channel-10 snare
  recipe. These test routing plumbing. They must never count as cross-game or
  cross-bank generalization evidence.
- `unlabeled-review`: ambiguous Gradius III FM and AKAO multi-zone sources.
  Empty labels mean unknown. They are excluded from supervised denominators,
  rather than silently being labeled abstention or negative examples.

The manifest/code use the repository license; the original generated signals and
module samples are dedicated under CC0-1.0. Game ROMs, extracted music, and banks
are private inputs and are not included. Recipes point to local source artifacts.
The historical script hash makes its label provenance auditable. No license for
any game content is implied by the repository's code license.

Generate the original one-second mono PCM16 WAVs, simple original ProTracker
`M.K.` modules for pitched cases, and their SHA-256 manifest:

```powershell
python -m instrument_analysis.fixtures benchmarks/instruments/generated/fixtures
```

This generator is standard-library-only and refuses to overwrite outputs unless
`--overwrite` is explicit. WAVs are controlled oscillator/control signals, not
native SNES/SPU/FM renders. Modules use a declared PAL sample-rate/period reference,
zero finetune, non-looping original samples, and a bounded pattern loop to be
handled by the module player. Seven generated modules have now been rendered through installed openmpt123
0.8.9, with explicit 44.1 kHz mono PCM16, gain 0 dB, 8-tap interpolation, ramping
0, dithering 0, repeat 0, and a one-second limit. All seven are nonzero and
unclipped; exact commands and hashes are in ignored
`instrument-renders/openmpt-fixtures-20261025/manifest.json`. Playback integrity
does not by itself validate multipitch detection or tracker pitch calibration. The checked-in tests validate
container structure and authored component offsets, not tracker-emulator fidelity.

Evaluate canonical predictions by matching `record_id` to the manifest:

```powershell
python -m instrument_analysis.benchmark benchmarks/instruments/pilot-v1.json predictions.json --output report.json
```

The evaluator validates the fields it consumes. Use the companion JSON Schemas
when full interchange validation is needed. It reports coarse route-kind accuracy,
GM-family accuracy, exact top-1/3/5 candidate accuracy, abstention precision/recall,
OOD false acceptance, semitone pitch MAE, octave-aware chord-note precision/recall,
and blinded preference agreement. Every metric exposes its denominator and
eligible/predicted coverage, and the report repeats metrics by split. Global
`records_present` counts matching output records, while `predicted_cases` counts
records with a top routing result, so numeric-only results remain visible. The
optional melodic-percussion annotation does not change a concrete GM preset's
identity; channel-10 percussion keys remain a distinct route. Missing
classification predictions count as incorrect; missing numeric predictions have
no paired error and remain visible in coverage. Unknown labels never become
negatives. Empty numeric cohorts produce `null`, not a perfect score. Pitch
estimates prefer the selected candidate's offset and otherwise use observation
pitch; distinguish metadata-known cases from acoustic inference when interpreting
that metric. Chord metrics use exact semitone offsets, including octave voicing
and repeated unison layers, and are macro-averaged by case. Empty/empty component
sets score one; a missing component prediction is unpaired.

Blinded judgments can be supplied with `--reviews reviews.json`:

```json
[{"case_id":"ff4-flute","blinded":true,"preferred_route":{"kind":"gm_program","program_0based":73,"program_1based":74}}]
```

That is an example record shape, not a saved human judgment. Unblinded entries are
ignored, unknown case IDs are errors, and no human agreement is reported until
judgments exist. Reviewers should audition matched source/candidates in randomized
order without patch names, retain source/render manifest hashes with judgments,
and allow ambiguous/no-good-match outcomes. The [local review workflow](../../docs/instrument-review.md) now captures
corrections and hash-bound provenance for isolated probes. Sequence and effect
previews remain follow-up work.

For an acoustic benchmark expansion, split by game or platform before tuning any
ranker. Keep shared sample hashes out of opposite folds. Render and hash exact
bank/engine conditions, then obtain blinded acceptable-candidate sets. Report
historical, synthetic, same-bank, and held-out results separately. Do not promote
this pilot to a perceptual leaderboard merely because contract tests pass.

Rendering instructions and remaining native-engine work are in
[`docs/instrument-rendering.md`](../../docs/instrument-rendering.md).

The [deterministic ranking baseline](../../docs/instrument-ranking.md) now includes
a neutral-query pilot runner. Its measured development results must remain
separate from same-bank controls and future held-out-game validation.

[Baked-chord analysis](../../docs/instrument-composites.md) now emits canonical
component offsets and explicit harmonic alternatives. Its original tracker and
independent-control results are reported separately in the
[composite validation record](../../docs/instrument-composite-validation.md).

[Local review packages](../../docs/instrument-review.md) provide manual audition,
blinded preference export, and proposed octave/chord corrections without changing
source facts. Playback is opt-in.

[Sequence recipe rendering and correction revisions](../../docs/instrument-sequence-review.md)
are available for explicitly grounded event recipes. Revised preferences require
fresh audio-hash acknowledgment and remain outside the isolated-probe benchmark.
