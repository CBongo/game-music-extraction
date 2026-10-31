# Instrument review packages

The review workflow packages hash-bound evidence and isolated audio probes for
manual comparison. It is an evidence collection tool: a reviewer preference is
not ground truth, and it does not establish human accuracy or general instrument
recognition accuracy.

The 2026-10-31 working packages are in ignored local artifacts:
`instrument-renders/tracker-review-final-20261031/` and
`instrument-renders/ff8-review-final-20261031/`. They are review evidence generated
from machine-local inputs, not checked-in benchmark labels or portable fixtures.

## Prepare a package

Create a JSON spec conforming to
[`instrument-review-spec-v1.schema.json`](../schemas/instrument-review-spec-v1.schema.json).
Paths may be absolute or relative to the spec file; each referenced file needs its SHA-256.
`source`, `analysis`, and `diagnostics` must be JSON artifacts that identify the
same `source_record_id`. Native comparison candidates reference a generated
`instrument-comparison/v1` manifest. Paired composite WAV candidates need source
and candidate WAV references, a render manifest reference, a generated `instrument-composite-preview/v1` render manifest, matching PCM16 WAV
headers, and explicit provenance with `context` and either
`gain_matching: "already_matched"` or
`gain_matching: "raw_inputs_normalized_by_review"`. The source evidence must
bind the source WAV hash and the render manifest must bind the candidate WAV
hash. Composite preview manifests also bind the source analysis and diagnostics
hashes. Their rendered WAV/MIDI are isolated trigger previews; they do not
represent a full song or effects path.

For a composite analysis, `instrument_analysis.review_rendering` can produce a
controlled candidate preview before package assembly. Its Python API is
`render_composite_preview(analysis_path, diagnostics_path, *,
trigger_midi_note=60, duration_seconds=0.923, tail_seconds=0.077,
program_0based=0, soundfont_path=..., fluidsynth_path=..., output_dir=...)`.
Pass a new output directory. It writes an
`instrument-composite-preview/v1` manifest, WAV, and MIDI; use the WAV as the
candidate in a paired-WAV spec and reference the manifest as `render_manifest`.
This renderer accepts static isolated chords and rejects unsupported event
effects. It records per-voice pitch-bend and expression quantization; it does
not emulate full sequence modulation or effects. The manifest retains the SoundFont and renderer executable hashes. The package
retains the render manifest and MIDI recipe as evidence.

Build and verify a package with the standard-library-only CLI:

```powershell
python -m instrument_analysis.review build --spec review-spec.json --output review-package
python -m instrument_analysis.review verify --package review-package
```

The output directory must not already exist. The manifest copies its evidence
and audio dependencies under `artifacts/`, records their hashes, and computes
`package_id` from canonical JSON for the manifest with `package_id` omitted.
`verify` checks that identity and every listed artifact. Keep the package
directory intact when sharing or reviewing it.

Open `review-package/index.html` locally to review. Audio uses explicit manual
controls (`preload="none"`); nothing autoplays. Candidate order is shuffled by
`ordering_seed`. The generated audition copies receive safe, consistent RMS
level matching across the candidate set. Original audio copies remain in the
package, and per-candidate gains are recorded. Blind mode masks labels in the
page until reveal, but the package contains its evidence and is not a
cryptographic blind. Inspecting those files or revealing facts should be
recorded through the judgment's `revealed` field.

The package context explicitly limits these auditions to isolated probes. They
do not render a full sequence, sound effects, arrangement, or game mix. A
judgment that supplies an octave or chord-offset correction is marked
`requires_rerender`: it records a proposed correction, not audio that has been
rendered or auditioned with that correction. Render and review a new package to
assess corrected audio.

## Import and export judgments

Download the judgment JSON from the review page. It asks for the package's
`manifest.json` to obtain the package ID; import then verifies the judgment's
package and source IDs, decision, candidate, and correction ranges against the
verified package. Stored imports are immutable and named by their content hash.

```powershell
python -m instrument_analysis.review import --package review-package --judgment judgment.json --store review-store
python -m instrument_analysis.review export --store review-store --output judgments.jsonl
```

`judgments.jsonl` contains all validated import wrappers conforming to
[`instrument-review-import-v1.schema.json`](../schemas/instrument-review-import-v1.schema.json).
The command also writes `judgments.jsonl.benchmark.jsonl`, a narrower adapter for
the existing benchmark preference metric. It includes only blinded,
unrevealed, accepted or alternate-candidate decisions with no pitch/chord
correction that requires rerender. Each adapter row has `case_id`,
`blinded: true`, and `preferred_route`, with package and judgment IDs retained
for traceability. Point the benchmark's `--reviews` argument at this adapter
file; the full JSONL import log is not accepted as benchmark reviews. This
adapter expresses one preferred route per review case, so it cannot represent
multiple acceptable alternatives, inter-reviewer agreement, free-form
rationales, or correction-based accuracy.

Keep human review results separate from authored synthetic controls,
same-bank sanity checks, historical labels, and held-out game/platform cohorts.
Do not combine isolated-probe preferences into a claim of human accuracy or
real-game accuracy. Full sequence/effect rendering and corresponding review
remain future work.

Schemas in this workflow describe interchange shape. The importer additionally
checks package identity and candidate membership; the verifier checks the
manifest hash and copied file hashes.

Validation: 143 instrument-analysis tests pass. Both local packages pass JSON
Schema, artifact-hash, nonclipping and common-RMS checks. Temporary automated
judgments exercise import, export and the benchmark adapter; no human labels
were added. Generated JavaScript passes a syntax check. Browser visual testing
was unavailable because the browser tool blocks local file URLs.

[Sequence candidate previews and correction rerenders](instrument-sequence-review.md)
now preserve explicit note timing, bend/expression curves, and revision ancestry.
Corrections can produce fresh manual review packages. Real source-native sequence
validation remains tracked under `gme-0r4.9.2.1`.
