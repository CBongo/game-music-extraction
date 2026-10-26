# Instrument mapping baseline

`instrument_analysis.ranking` implements a conservative, deterministic retrieval
baseline over concrete rendered GM presets. It returns ranked alternatives and
explicit abstention, not automatic edits to the converters' patch maps.

## Run it

Build an index from one or more dry render manifests:

```powershell
python -m instrument_analysis.ranking index --gm-manifest instrument-renders/generaluser-full-20261024/manifest.json --output instrument-renders/gm-index.json
```

Repeat `--gm-manifest` for other banks or timing/pitch grids. Feature extraction
is cached by WAV hash, phase times, and feature implementation hash. An index is
bound to its original manifests and WAVs and rejects changed note, bank, preset,
velocity, phase conditions, sample properties, or feature implementation.

Rank a native capture with a [measured calibration](instrument-comparisons.md):

```powershell
python -m instrument_analysis.ranking rank-native --native-manifest NATIVE_MANIFEST --pitch-calibration CALIBRATION --index INDEX --output OUTPUT_DIRECTORY
```

The API also supports ordinary PCM16 WAV queries through `rank_wav_source()` and
`calibrate_wav_pitch()`. Their calibration binds the exact WAV, source record, and
event boundaries; ordinary audio is not labeled as emulator output. Native input
supports the existing FM and SNES/SPU manifests, validates receipts and dry
conditions, and retains the emulator's timing uncertainty.

Each query writes canonical `predictions.json` and a separate `diagnostics.json`.
Outputs refuse overwrites. For a batch, use `with prepared_reference_index(path)
as prepared:` and pass `prepared_index=prepared`. Entry and exit rehash all bound
inputs, each query checks the active handle and exact index hash, and a closed
handle cannot be reused. This avoids re-reading the full reference corpus for
every query without relying on modification-time-only validation.

## What the score means

The standard-library extractor uses Hann-windowed radix-2 FFTs and 32 logarithmic
frequency bands, with 64-bin envelope shapes for attack, sustain, and release.
Per-channel power is averaged, so opposite stereo polarity cannot cancel the
spectral features. Timbre vectors and envelope shapes are normalized; absolute
RMS/peak level and clipping remain separate diagnostics. Padded audio outside the
requested event does not determine signal quality. Empty or insufficient phases
are rejected. The simple box-filter downsampler is a baseline approximation.

Per-phase distance weights are spectral cosine 0.55, envelope cosine 0.20,
centroid 0.10, rolloff 0.08, flatness 0.04, and zero crossing 0.03. Attack,
sustain, and release contribute 0.30, 0.45, and 0.25, respectively, renormalized
when a phase has no usable signal. At least half the phase weight must be
comparable. Component distances are retained for inspection.

Pitched candidates must be within 25 cents of the calibrated sounding note and
have compatible hold/release windows. Native clock uncertainty is included in
the timing tolerance. Insufficient reference pitch-grid coverage causes an
abstention; 128 programs at three sampled notes does not cover every source key.
The current audition octave adjustment is zero because only matched pitches are
compared. Any measured source pitch offset is preserved separately.

Default acceptance requires best distance at most 0.35 and a runner-up margin of
at least 0.04. These are explicit engineering defaults, not fitted probabilities
or validated confidence calibration. Canonical candidate score is one minus
distance; diagnostics also expose the raw distance and a confidence heuristic.
On ambiguous matches, abstention is first and the tentative top candidates remain
available for review. A single nearest neighbor does not force acceptance.

## Routing and context

Normal-channel melodic presets, including melodic percussion, remain separate
from channel-10 drum keys. An unpitched query needs explicit, hash-bound channel-10
source evidence to enter the drum-reference branch; its trigger key is not
interpreted as an acoustic MIDI pitch. Grounded SFX or composite facts produce an
explicit handoff, with no invented chord components or GM replacement.

Optional `--canonical-record` supplies observed source metadata and usage.
Optional `--source-facts` supplies a hash-bound routing context with explicit
source/hardware basis and provenance. Existing curated mappings are removed
before inference and never determine distance or routing. Unknown acoustic
function remains unknown; this baseline is not a general SFX/chord detector.

Known occurrence-level sounding notes produce a separate functional **coverage**
diagnostic: how much of the usage is represented by each candidate's sampled
reference-note grid. Its blend weight is zero. This describes evidence
availability, not an instrument's physical range or suitability for a musical
role. Raw source ticks and effects are not silently converted into durations.

Bank/note/velocity variants are grouped beneath the same GM route before the
routing margin is computed. Best variants and per-font distances are exposed;
variants of one SoundFont do not count as cross-font consensus. Only one concrete
SoundFont has been validated so far.

## Reproduce the pilot

```powershell
python -m instrument_analysis.pilot --benchmark benchmarks/instruments/pilot-v1.json --index INDEX --fixtures benchmarks/instruments/generated/fixtures --gm-manifest instrument-renders/generaluser-full-20261024/manifest.json --output OUTPUT_DIRECTORY
```

The runner copies audio to neutral query IDs and file names, measures its pitch,
and passes only waveform, actual timing, and whitelisted source facts to the
ranker. Encoded fixture names, labels, splits, and existing mappings do not enter
inference. Benchmark IDs are restored only afterward for the evaluator. The
channel-10 control reconstructs the deterministic MIDI and verifies its recorded
hash before citing its channel as evidence; original temporary MIDI files need
not be retained.

The original synthetic WAVs declare a 0.92-second hold and 0.08-second release.
The same-bank controls use the actual render manifest's times, recorded in the
audit, rather than pretending that an older render followed a different recipe.
Converter scripts or sample-bank files are not treated as audio when game renders
are unavailable. Predictions, metric denominators, per-query diagnostics, and a
hash-bound run audit are saved together.

The pilot is a development/plumbing demonstration. It contains no held-out game
or platform split and no broad reviewed OOD set. Missing audio, conservative
abstention, provisional historical labels, and same-bank self-matches must remain
visible. These metrics do not establish game-instrument mapping accuracy.

Actual corpus coverage, native abstention, pilot denominators, and artifact hashes
are recorded in [the initial validation record](instrument-ranking-validation.md).
