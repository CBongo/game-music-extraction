# Instrument sequence review

Sequence review renders a GM candidate from an explicit, hash-bound event
recipe. It does not render the source game's sequence. Treat the resulting WAV
as an audition aid for the stated recipe, never as native sequence accuracy or
evidence that an instrument route generalizes to a game.

## Ground each recipe in source evidence

Use [`instrument-sequence-recipe-v1.schema.json`](../schemas/instrument-sequence-recipe-v1.schema.json).
The recipe binds a `source_record_id` to a JSON source artifact by path and
SHA-256. The source artifact must identify that same record. Record the basis
for event timing, pitch, and effects in `provenance`; `effects_complete` must be
true. Every event explicitly supplies an ID, MIDI note, start and duration in
seconds, velocity, residual cents, and relative level. Optional pitch and
expression curves use offsets in seconds from the start of the event.

Do not fill unknown sequence values with invented precision or quietly omit
source effects. The FF3 decoder now retains converter tempo events, note gate
lengths, and velocities in usage provenance. These describe the legacy MIDI
conversion and explicitly carry `effects_complete: false`; they are not native
driver timing or envelope measurements. The original raw step duration stays
separate from the converter's shortened gate duration. Unsupported event effects
are rejected by the recipe renderer rather than silently dropped.

`instrument_analysis.usage_timing.audit_usage_timing()` integrates the conductor
tempo map across note gates and exposes blocking reasons. A real Prelude decode
produced five instrument records and 1,459 occurrences. Every record remains
blocked for source-complete rendering: effects are incompletely normalized,
sample sounding pitch is ungrounded, and some pitch contexts are incomplete.
The ignored evidence is in `instrument-renders/ff3-usage-timing-20261031/`.

## Render a candidate preview

The standard-library recipe-to-MIDI builder is
`instrument_analysis.sequence_rendering.make_sequence_midi(recipe,
program_0based, octave_adjustment=0, component_offsets=None)`. It returns MIDI
bytes and an event list with channel allocation and tick-quantized pitch and
expression curves. Events use 1000 MIDI ticks per second. Simultaneous expanded
voices get independent melodic channels; more than 15 reserved voices,
out-of-range pitches/bends, invalid curves, and events that collapse at 1 ms
resolution are errors.

For a rendered WAV, use:

```python
from instrument_analysis.sequence_rendering import render_sequence_preview

manifest = render_sequence_preview(
    "recipe.json",
    program_0based=0,
    soundfont_path="GeneralUser-GS.sf2",
    fluidsynth_path="fluidsynth",
    output_dir="instrument-renders/sequence-preview-001",
)
```

The output directory must be new. The renderer verifies the recipe, source,
SoundFont, and FluidSynth executable hashes before and after rendering. It
publishes a MIDI file, a dry WAV trimmed to the recipe's quantized end time, and
an `instrument-sequence-preview/v1` manifest. The manifest records render
conditions, exact quantized event data, and the hashes of recipe, source,
SoundFont, executable, MIDI, and WAV. Review the manifest against
[`instrument-sequence-preview-v1.schema.json`](../schemas/instrument-sequence-preview-v1.schema.json).

This is a candidate-only FluidSynth render with chorus and reverb disabled.
CC11 expression approximates the supplied relative dB values; a SoundFont does
not guarantee acoustic level ratios. Effects are only represented if explicitly
captured as pitch or expression curves in the recipe. Other source effects are
not modeled by this renderer. Mark their approximation in `provenance.basis`
and do not claim complete effect coverage if a material behavior is missing.
A sequence preview is an approximation and makes no claim about the source
driver's envelopes, modulation, effect buses, timing, or mix.

## Review and correct the audio

Use the preview WAV and manifest as a candidate in the hash-bound manual review
workflow described in [`instrument-review.md`](instrument-review.md). Keep the
audition blind where possible, and use the review page's manual audio controls;
it does not autoplay. Record any changed event timing, notes, pitch curves,
expression, octave, or chord voicing as an explicit correction. A correction
must produce a newly rendered candidate WAV and manifest, which should be
reviewed again. Do not report a judgment on corrected audio until the reviewer
has actually auditioned that new render.

There is no demonstrated native sequence accuracy or held-out game sequence
accuracy result for this workflow. Keep recipe construction assumptions,
candidate renders, and human preferences separate from accuracy claims.

## Rerender and review a correction

Corrections create a new package instead of changing the old one. The API is
`instrument_analysis.review_revisions.build_review_revision(parent_package_dir,
imported_judgment_path, recipe_path, *, soundfont_path, fluidsynth_path,
output_dir)`. It accepts an imported, accepted or alternate-candidate judgment
with an explicit octave or chord-offset correction, and the base recipe used
for that candidate. The output directory must be new. This call renders and
packages the corrected candidate; it does not claim that a reviewer has heard
it.

The equivalent CLI is:

```powershell
python -m instrument_analysis.review_revisions --parent review-package --judgment imported-judgment.json --recipe base-recipe.json --soundfont GeneralUser-GS.sf2 --fluidsynth fluidsynth --output corrected-review
```

The base recipe must bind the exact source manifest from the parent package.
For a correction to an isolated composite probe, provenance `source_context`
must also bind the parent render-manifest hash and original source-audio hash;
the recipe must preserve the original trigger note, velocity, duration, end
time, and single-event shape. For a full sequence correction, the recipe must
provide a hash-bound `source_sequence_audio` reference and `source_context`
fields for the matching record ID, `source_audio_sha256`, and
`capture_context_sha256`. The latter two hashes must match the `source_context`
in the bound source manifest, and the audio hash must match the referenced audio
file. These checks keep the rerender tied to the reviewed capture context.

The fresh review page requires an explicit `audition_acknowledged: true` in its
judgment and the SHA-256 hashes of the exact source and candidate audio presented
by that revision package. The importer checks these hashes and records
`reviewer_reported_audition`; this is an auditable reviewer attestation, not
independent proof of playback. A corrected render remains
`rendered_pending_review` until that acknowledgment is imported. Revision
judgments stay out of the existing preference benchmark adapter.

Corrections specify absolute octave and chord-offset overrides relative to the
original source recipe. They are not added to the last candidate's correction.
Repeated isolated revisions retain the original render proof. `resolve_ref()`
locates archived evidence by hash; paths recorded during assembly can be historical.

Live controls in `instrument-renders/sequence-control-20261031/` verify an authored
three-note timeline with overlapping bend/expression curves. The correction
pipeline control is `instrument-renders/correction-control-20261031/review-release-final/`;
it is an automated example, not a human choice, and remains pending fresh review.
Neither control establishes native game sequence accuracy.

Use `correction_requested: true` (the explicit rerender checkbox in the page) to
request a reset to zero octave adjustment. An unchecked fresh acceptance does
not implicitly request another render. After a correction, single-candidate
no-match and ambiguous judgments remain available with fresh audition acknowledgment.

Validation: 155 instrument-analysis tests pass, including unchanged FF3 MIDI
output with sidecar collection, tempo integration across a note gate, overlapping
voice curves, controller resets, repeated absolute corrections and reset-to-zero.
Live control manifests and real FF3 usage pass their JSON Schemas. Audio is
nonzero and unclipped. No human judgment or native sequence accuracy result was
created by these checks. Browser visual verification remains unavailable because
the browser tool blocks local file URLs; generated JavaScript is syntax checked.

SoundFont release tails can outlive MIDI note-off. By default, a channel remains
reserved until the recipe ends, so later bends cannot retune an older tail.
This limits a render to 15 expanded voices. An explicit
`provenance.release_guard_seconds` permits reuse after the stated tail budget;
CC120 then cuts any lingering release before the next note's controllers change.
This is recorded tail truncation, not proof that the envelope had finished.
Longer excerpts must choose and disclose that policy or be split into windows.
