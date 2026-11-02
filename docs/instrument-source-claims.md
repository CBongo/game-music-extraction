# Bounded documentary source claims

`instrument_analysis.source_claims` imports an offline, read-only Google Sheets
CellData capture. It makes no network calls. Capture native
`sheets[].data[].rowData[].values` with sheet IDs, grid offsets, and declared
`requested_ranges`; retain notes, hyperlinks and text runs, not only values.

A separate profile supplies source attribution, observation date, explicitly
bounded selections, column mappings, header/data rows and context. Split sections
when their meaning differs. Use `identity_fields` to name mapped columns that
identify a row: detailed sample tables normally use `sample_id`; a main summary
row needs its explicit game/platform key. Preserve raw aliases exactly. An alias
is documentary wording, not an automatic mapping between regions or releases.

Run an import with:

```sh
python -m instrument_analysis.source_claims --snapshot capture.json --profile profile.json --output claims.json
python -m instrument_analysis.source_claims --compare old.json new.json --output comparison.json
```

The CLI creates output exclusively and refuses to overwrite existing files. Its
`input_refs` record hashes of the actual input bytes. API reports omit these file
references. Schemas are in `schemas/instrument-source-claims-v1.schema.json` and
`schemas/instrument-source-claims-comparison-v1.schema.json`. Report and claim
structures are strict; raw CellData, source attribution, profile and context
metadata remain extensible.

These research sources are preferred documentary authority over historical voicing
assignments. Their claims remain reports by their authors. Lexical uncertainty
states describe wording only; processing/reuse flags are separate hints. Coverage
markers are never confidence scores. Sampled key names do not establish sounding
pitch or game unity key.

Snapshot and selection fingerprints identify partial captures, not workbook
revisions. Omitted CellData are not verified blank. Canonical claim content hashes
include raw cells, notes, links, header credits, aliases and source attribution;
they exclude row position. Identity uses explicit raw fields and context, not row
numbers. Missing identities and collisions remain ambiguous rather than silently
merging claims. A comparison can report both changed and moved for one identity.
Removals mean absence from the selected captures only, never deletion from an
entire workbook.

This import does not create sample byte bindings, measured acoustic facts, or GM
program derivations. Those require separately reviewed evidence and integration.
The existing five-claim pilot remains unchanged. Live capture counts and future
integration status are recorded with their reviewed artifacts, not inferred here.

The source profile must explicitly request `authority: "preferred_documentary"`.
That policy is permitted only for the two approved VGM Resources workbook IDs;
other sources default to neutral `documentary` authority. A comparison verifies
claim content and identity hashes and rejects mismatched source/schema versions.
`scope_changed` records changes to the selected rectangle separately from row
movement and content edits. One identity can appear in multiple change lists.
The CLI parses and hashes the same frozen input bytes, then checks that the files
remain unchanged before creating its output.

The committed profiles are `docs/data/instrument-source-claims-main-profile.json`
and `docs/data/instrument-source-claims-detail-profile.json`. They select 24 main
sheet rows (14 FFIV source leads, five Stuff to Find rows, and five FFIV Advance
rows) plus 85 detailed rows (22 FFIV and 63 FFVI SNES sample entries). Main and
detailed claims remain separate observations; the importer does not merge them
by name. Threaded comments, revision history, and demo media were not captured.
The rules and per-game credits are retained with the imported evidence.

The local November 1 review index is
`instrument-renders/source-claims-20261101/index.html`. The immutable final
sidecars are in its `final/` directory. They extend documentary coverage without
expanding the five version-pinned ROM bindings or making new GM assignments.

Validation: all 214 instrument-analysis tests pass. All 30 instrument schema
definitions passed schema checks; seven live reports and authored comparison
controls passed validation. Moving the 22 FFIV detailed rows preserved their
content identities and reported their new locators/scopes. A note-only edit
reported one content change. The live import contains 69 reported, 28 unknown,
11 qualified and one disputed entry; these are wording states, not confidence
or accuracy labels. The compact audit is committed as
`docs/data/instrument-source-import-validation-20261101.json`.
