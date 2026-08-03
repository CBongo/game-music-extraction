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
