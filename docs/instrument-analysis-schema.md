# Instrument observation and analysis contract

The versioned JSON contract in
[`schemas/instrument-analysis-v1.schema.json`](../schemas/instrument-analysis-v1.schema.json)
is the boundary between source-specific extraction and source-independent instrument
analysis. It is deliberately an interchange format rather than a promise that every
extractor can populate every field.

The companion
[`schemas/examples/instrument-analysis-v1.examples.json`](../schemas/examples/instrument-analysis-v1.examples.json)
covers an AKAO PSX sample, an SNES BRR sample with a reverse-engineered pitch
reference, a YM2151 FM patch, and a ProTracker sample containing a baked chord.

## Design rules

1. Preserve source facts. Raw opcodes, operands, addresses, register values, sample
   files, and driver identifiers stay alongside normalized values. A consumer must
   never need to reverse a lossy normalization to audit a result.
2. Mark epistemic status. A datum says whether it is a `source_fact`,
   `hardware_fact`, `measured`, `inferred`, or `curated` value and carries enough
   provenance to reproduce it. Confidence is required for uncertain values and is
   always in the closed interval 0 through 1.
3. Keep identities distinct. `source_patch_command` is the byte or token occurring
   in a sequence, `song_local_slot` is its indirection-table position, and
   `source_instrument_id` identifies the global driver instrument. They may happen
   to be equal but are not interchangeable.
4. Preserve multi-zone instruments. AKAO tones and other key/velocity splits use
   `zones`; pitch, envelope, loop, gain, and pan remain attached to the sample and
   articulation they describe rather than being flattened into program defaults.
   Song-local percussion tables use `percussion_bindings` for the same reason.
5. Keep raw and sounding notes distinct. `raw_sequence_notes` are driver inputs;
   `sounding_midi_notes` include driver transpose and sample tuning. Existing
   `@transpose` values should be imported as curated evidence until their original
   meaning has been verified. Timed driver transposition remains an effect with
   `semantic: "transpose"` and normalized scope/value; it is not folded into the
   instrument's `observed_pitch_offset`.
6. Route before ranking exact patches. The route kinds are normal-channel GM
   program (including melodic percussion), channel-10 GM percussion note, SFX,
   harmonic composite, and explicit abstention. A result may retain several ranked
   alternatives.
7. Use both GM numberings. `program_0based` is the MIDI program-change byte and
   `program_1based` is the conventional GM patch number. Producers must emit both;
   consumers should reject a pair where `program_1based != program_0based + 1`.
8. Do not encode notation transposition. Score-level treatment of clarinet,
   trumpet, and other transposing instruments belongs to the later arrangement
   phase.

## Pitch conventions

MIDI note names use `60 = C4`.

`observed_pitch_offset` is defined as:

```text
sounding pitch - pitch named by the raw sequenced note
```

A positive value therefore means that the source sounds higher/sharper than its
raw sequence note. The integral portion is stored in `semitones`; any remainder is
stored in `residual_cents` in the inclusive range -50 through +50. For example, a
sample that sounds G3 when triggered by C4 has an offset of -5 semitones.

`candidate_audition_octave_adjustment` is different: it is a whole-octave shift
applied only when rendering a particular GM candidate so that comparison happens
in a useful register. It must not be folded into the observed pitch offset.

For a composite sample, every `components[].semitone_offset` uses the raw trigger
as zero. Thus the ProTracker example triggered at C4 and sounding G3+C4+E4 is
`[-5, 0, +4]`; this preserves inversion and voicing rather than reducing the result
to the pitch classes of a generic C-major chord.

## Producer guidance

The current `*mus.pl` and `*txt.pl` scripts can initially populate identity, usage,
raw notes, source transpose commands, and timed effects. Hardware/sample extractors
can independently add synthesis, pitch, envelope, and loop observations. A merge
step should join records by source plus the three-part instrument identity, without
allowing a curated `@patchmap` entry to overwrite raw identity or measured data.

Unknown information is omitted rather than represented by a magic number. An
analyzer unable to make a useful classification should emit an `abstain` route with
a reason, which is different from omitting analysis because it has not run.

## Metadata adapters

The standard-library-only `instrument_analysis` package provides the first source
adapters for this contract:

- `adapt_akao_v31_sample_set` joins the 0x40-byte sample header and 16-byte
  articulations used by later PlayStation AKAO sample sets to the matching
  instrument pointer table and 8-byte regions. It emits one record per true
  instrument with its key-split zones, preserving raw ADSR fields, adjusted unity
  key, residual fine tune, and exact source offsets. The caller must explicitly
  confirm format version 3.1 or 3.2; earlier versions use different layouts. A
  unity key alone is never reported as an observed pitch offset. Raw key bounds
  above MIDI 127 remain in the envelope metadata while canonical zone ranges are
  normalized in the same spirit as the existing vgmtrans parser.
- `adapt_snes_brr_instrument` applies the SNES DSP pitch-register equation to one
  or more driver note references. A sounding-note reference is required before it
  derives a sample unity pitch or raw-to-sounding offset. Multiple references are
  cross-checked; disagreement is retained in provenance and reduces confidence.
  `decode_snes_pitch_table` supplies those references directly from a decoded ROM
  table; the FF2 regression fixture covers the verified 13-word table at 04/96FE.
  Renderers and measurement tools feed their results through `sounding_note` and
  should set the reference basis and confidence accordingly; rendering itself is
  deliberately outside this metadata adapter.

Both adapters return ordinary dictionaries, so extractors can call them without a
new dependency. Wrap records with `make_bundle`; `validate_semantics` also checks
cross-field rules that JSON Schema cannot express, including agreement between
zero-based and one-based GM program numbers.

Run the deterministic adapter tests with:

```powershell
python -m unittest discover -s instrument_analysis/tests -p 'test_*.py' -v
```

## Raw decoder usage sidecars

The Chrono Cross and Final Fantasy III (SNES) legacy decoders can optionally write
an `instrument-analysis/v1` JSON sidecar while executing their existing parser:

```powershell
# From psx/cc with the existing akao/*.bin inputs and titles.pl.
perl -I. ccmus.pl --instrument-usage usage.json -t

# From snes/ff3; create the normal mid/ output directory first.
perl ff3mus.pl --rom f_fan3.fig --instrument-usage usage.json 01
```

`lib/InstrumentUsage.pm` collects observations inline, before curated GM program,
drum-key, or patch-octave substitutions. It uses core Perl modules. Records retain
source patch operands, song-local slots, source instrument identity, voice,
executed event index, raw note, octave, onset, decoded step duration, ties, effect
opcodes/operands, and original file offsets. Each note also retains the location
of its patch selection in provenance parameters. Duration means decoded note-step
length including ties, rather than the shortened MIDI gate. Times use `midi_tick`
with the actual ticks-per-quarter timebase: 48 for Chrono Cross and 96 for FF3.
Histograms count note triggers including traversed loop iterations; ties extend
durations without increasing counts. Reparsed voices replace earlier captures.

These records describe the existing decoder traversal, including its loop limits;
they are not emulator traces. Chrono Cross text mode traverses repeats differently
from MIDI mode, so use MIDI mode for expanded usage. Notes and effects before the
first explicit patch selection are omitted because the initial instrument is
unestablished. Effect occurrences belong to the patch selected when the command
executes; inherited state across later patch changes is not yet materialized.
Unknown sounding pitch is omitted. Source notes and historical `@transpose` values
do not establish acoustic pitch. Raw transpose commands remain available without
asserting an unverified signed interpretation.

FF3 uses its unfiltered song slot table for source IDs; its existing MIDI mapping
still uses the legacy table behavior. Low direct patch commands retain unresolved
`direct-command-N` identities. Chrono Cross distinguishes A1 individual articulations from FE14 key-split slots,
including when their operand bytes match. Both are scoped to the sequence until
the associated sample set is joined. Resolving its instrument/percussion tables
to sample identities is follow-up work.
Calibration joins, normalized driver-transpose state, and contextual percussion
bindings remain part of the broader usage-capture feature (`gme-0r4.5`).

The schema permits raw note occurrences without `sounding_midi_note`, and retains
optional `time`, `duration`, and `duration_unit`. Producers should populate sounding
pitch only after establishing sample tuning and driver state. Existing bundles
containing sounding notes remain valid.

Query raw instruments, songs, or voices without consulting curated GM mappings:

```python
import json
from instrument_analysis import query_usage

with open('usage.json', encoding='utf-8') as source:
    bundle = json.load(source)
rows = query_usage(bundle, source_instrument_id=7, sequence_id=1, track_id=0)
```

Each row contains source, identity, sequence, selected tracks, and raw occurrences.
ID types matter: integer `7` differs from string `"7"`. Tests construct synthetic
data and compare MIDI/text output with capture disabled and enabled. No game ROM
or extracted music is checked in. Installing `jsonschema` enables optional full
schema checks in the tests; runtime collection and queries need no such dependency.
