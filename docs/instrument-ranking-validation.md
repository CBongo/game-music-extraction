# Initial instrument-ranking validation

Validated on 2026-10-26. This is a deterministic baseline and pilot audit, not
held-out game accuracy or calibrated confidence. See [usage and score semantics](instrument-ranking.md).

## Reference corpus

The combined local index contains **815 probes** from one concrete SoundFont:
431 earlier GeneralUser GS probes (all 128 programs at notes 48/60/72 plus 47
standard drum keys, 0.5-second hold and 0.5-second tail), and 384 additional
melodic probes (all 128 programs at notes 55/60/67, 0.92-second hold and
0.08-second release). The latter timings match the original controlled fixture
envelope. Dry settings, actual WAV hashes, source manifests, feature version,
phase conditions, and exact bank/preset identity are bound in the index.
The sampled grids cover only those notes and conditions.

## Native query

The calibrated FF8 AKAO 3.1 articulation-76 C4 capture returns **abstain** under
the default distance 0.35 and margin 0.04 gates. The top two program alternatives
are 95 (one-based 96) at distance 0.0985548612 and 89 (one-based 90) at
0.0985949430. Their margin is about 0.000040, far below the required separation.
Tentative top candidates and distance components remain available for audition;
no converter patch map was changed. Exactly 128 pitch-matched melodic probes
participated in this query against the original 431-probe index.

## Label-isolated pilot

All 16 pilot cases emit predictions and diagnostics. **11 have actual audio**:
9 original controlled WAVs and 2 same-bank GM controls. The 3 historical converter
examples and 2 unlabeled game-source cases have no bound audio input in this
runner and explicitly abstain. Converter/sample-bank bytes are never interpreted
as WAV data. Input files and record IDs are neutralized before inference, and
benchmark labels are used only afterward by the evaluator.

| Split | Cases with route labels | Correct top route | Interpretation |
| --- | ---: | ---: | --- |
| historical-dev | 3 | 0 | Missing native audio; provisional labels retained |
| synthetic-contract | 6 | 1 | Silence abstains; composite/SFX recovery is not established |
| same-bank-sanity | 2 | 2 | Melodic vibraphone and channel-10 snare self-match controls |
| unlabeled-review | 0 | n/a | Unknown labels excluded from supervised denominators |

Overall top-route correctness is **3/11 (27.3%)**, with the missing historical
audio visible in that denominator. Only the two self-match controls receive an
accepted GM route; 14 cases abstain, with tentative alternatives retained where
pitch and reference coverage permit them. These counts show a functioning
conservative pipeline and substantial remaining data/model limitations.

Measured pitch-offset MAE is **0.0007333 semitones on 3 synthetic monophonic
cases**. That narrow authored-signal result does not validate arbitrary game
sample pitch. Composite-note prediction coverage is **0/7**; the chord detector
remains separate work. The reported OOD false-acceptance cohort has only **one
silence case**, so its 0/1 result is not evidence of general OOD rejection.
No held-out game/platform cohort or blinded preference judgments are available.

Results are in ignored `instrument-renders/pilot-baseline-20261026-final/`:
`run.json` records source availability and actual event windows, `predictions.json`
is canonical output, `report.json` exposes metric denominators, and each neutral
query has diagnostics. Static same-bank controls, synthetic construction labels,
and missing real game examples are not pooled into a generalization claim.

## Integrity checks

The full instrument-analysis suite passes 114 tests, with focused coverage for
pitch calibration, feature math, routing, label isolation, tamper detection, and
silent capture arguments. Python compilation also passes.

Independent validation checks the combined index, canonical predictions, metric
report, all 16 query diagnostics/bundles, and 11 query-pitch calibrations against
the published schemas. Index loading also verifies original manifests and WAVs.
The batch context verifies all bound inputs before and after evaluation; tests
cover mutation during a batch and reuse of a closed handle. Reconstructed MIDI
for the channel-10 control must match the original render's recorded MIDI hash.
No speaker playback is needed by indexing, calibration, ranking, or evaluation.

Artifact SHA-256 values:

- Combined reference index: `b07b80c84ab5e74049ec9fc17217c05d5144b33966f948826918efee5468f00b`
- Pilot predictions: `194a44a3d7601e3c2385945971c027cc8b5580cc374ecbfc5e1ebe0f92ffd002`
- Pilot metric report: `21d3e2363b18e0f80c87932152b1d365645eeae356a8b0fedc5f738404fe7312`
- Pilot run audit: `a581b186bca7dfd08f82ea60a9d6b5fbaba4f43fec79c3bb1b06b83a8b7f20a7`
