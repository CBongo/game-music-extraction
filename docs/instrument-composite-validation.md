# Baked-chord validation

Validated on 2026-10-26 with installed openmpt123/libopenmpt 0.8.9 and the
standard-library harmonic-template analyzer. These are original authored controls,
not reviewed commercial-game samples or held-out real-module accuracy.

## Tracker-backed probes

Eight original `M.K.` samples were rendered at three explicit source triggers
(MIDI 60/62/64, raw periods 428/381/339). All 24 captures use the explicit
non-Amiga eight-tap profile, 44.1 kHz stereo PCM16 and file output. Independent
checks verified module/WAV hashes, original selected sample bytes and full sample
header, renderer executable hash, nonzero unclipped PCM, timing bounds and schema.

| Authored source | Result | Trigger-relative offsets |
| --- | --- | --- |
| Major, second inversion | supported major | [-5, 0, +4] |
| Minor | supported minor | [0, +3, +7] |
| Suspended fourth | supported sus4 | [0, +5, +7] |
| Suspended second | supported sus2 | [0, +2, +7] |
| Rich single tone | supported single | [0] |
| Single tone shifted down | supported single | [-5] |
| Single tone shifted up | supported single | [+7] |
| Power/octave sample | abstain | competing missing-fundamental explanation |

All **4/4 priority triads** had exact component sets, with no composite call on
any of the three single-tone controls. The power/octave sample remains unresolved
rather than being converted to an invented low single note. Accepted note-event
expansion was checked with common timing, velocity, pitch bends and vibrato.

The strict tracker path preserves raw signed finetune and repeat headers.
Additional live tests confirm four-byte repeat loops are active, while the
two-byte disabled marker is not treated as a loop. Signed finetune changes
one-shot endpoint estimates in the expected direction. Explicit default resampler
controls reproduced all 24 earlier captures byte-for-byte; the optional A500
profile also rendered successfully. Interpolation and Amiga filter emulation are
recorded separately.

## Independent constructed controls

Eighteen neutral sources each supply three trigger WAVs (54 captures). The controls
vary harmonic profiles, phase, voicing and source register, and include rich
single tones, missing fundamentals, an exact harmonic 4:5:6 signal, octave/power
sets, chorus, vibrato, pitch-scaled frozen noise, inharmonic partials and silence.
Labels are loaded only after inference; file names and source IDs are neutral.
These adversarial controls helped harden the method, so they are a development
regression corpus rather than a held-out statistical test set.

- **6/7** positive component sets recovered exactly; all six accepted sets were
  correct. Paired component precision is 1.0, while recall across all positive
  cases is **17/20 = 0.85**. Coverage is not hidden behind paired precision.
- The difficult wide major [-12,+4,+7] abstains: the fifth can also be absorbed
  as the lower root's third harmonic, and a two-note versus three-note fit is
  not resolved. Both hypotheses remain in diagnostics.
- **0/7** negative controls were called composite.
- **0/4** explicitly ambiguous harmonic-layer controls were called composite;
  three abstained, while the pure octave stack selected the parsimonious single
  periodic-tone explanation. That result does not prove authored octave layers
  were absent or reconstruct their multiplicity.

Primary major/minor/sus2/sus4 and the independent first-inversion major control
were recovered; a nontriadic tritone dyad was retained as `other`, rather than
being forced into a common triad. Suspended-root alternatives remain explicit:
C-D-G can support both Csus2 and Gsus4 readings without changing recovered notes.

## Reproduce and audit

```powershell
python -m instrument_analysis.composite_fixtures NEW_CONTROL_DIRECTORY
python -m instrument_analysis.composite_evaluation controls --inputs NEW_CONTROL_DIRECTORY/inputs.json --labels NEW_CONTROL_DIRECTORY/labels.json --output NEW_EVALUATION_DIRECTORY
python -m instrument_analysis.composite_evaluation tracker --probes instrument-renders/tracker-original-probes-explicit-20261026 --fixtures benchmarks/instruments/generated/fixtures/fixtures.json --output NEW_TRACKER_EVALUATION_DIRECTORY
```

The saved final evaluations are ignored local artifacts in
`instrument-renders/composite-control-evaluation-v2-20261026/` and
`instrument-renders/composite-tracker-evaluation-v2-20261026/`. Each source has
canonical analysis, diagnostics and, when supported, expanded note events.
Input sample-byte identities and synthetic recipe identities are distinct;
recipe-only controls do not claim decoded sample-byte proof.

All **26 diagnostics and 26 canonical bundles** validate against their schemas.
The complete instrument-analysis suite passes **129 tests**, and Python
compilation passes. Regression tests cover the four target families, inversions,
independent wide voicing, ambiguous wide component count, harmonic alternatives,
noise/short/invalid captures, cross-trigger consistency, identity tampering,
label isolation and all-or-nothing MIDI-note event expansion.

No pretrained multipitch model or reviewed real-world chord corpus was evaluated.
Acceptance/ambiguity thresholds and confidence values are engineering heuristics.
The next review workflow must preserve exact accepted alternatives and uncertainty
before unfamiliar samples are used in conversion.

Artifact SHA-256 values:

- Constructed-control report: `deece17d52e0b14a6ba64d56c4884fc845df3ac27ea69bb101384b82d3d4bee9`
- Tracker-control report: `a1d8b57412c89d39f71f6764ba648520734f164bbc55665369e20bb53efd72a5`
- Major-inversion diagnostics: `ff2fdc63990757e8c386d4dd7a685fdfcfb8cd2510a412605b5751522cb7d563`
- Major-inversion canonical analysis: `306c6f7b6b5c56fceae15892606a48972ab25f0ddd3b2410eedd7901c1305aa8`
