# Imported documentary byte bindings

The imported binding workflow joins an explicitly selected [bounded source claim](instrument-source-claims.md) to version-pinned local ROM bytes. A physical binding does not confirm the claimed source synthesizer, preset, or original hardware waveform. Attribution, wording qualifiers, raw cells, notes, headers, aliases, source metadata, and processing/reuse flags remain documentary evidence. See [documentary shortlists](instrument-documentary-shortlists.md) for the independent target-selection and review branch.

## Explicit plan and API

The checked-in [November 1 plan](data/instrument-imported-origin-plan-20261101.json) follows [instrument-imported-origin-plan/v1](../schemas/instrument-imported-origin-plan-v1.schema.json). Every entry names the exact claim identity and content SHA-256, game, US revision 0, global-sample namespace, numeric sample ID, ROM file SHA-256, and origin-family grouping. Source-report and ROM paths resolve relative to the plan file, independently of the working directory.

```python
from instrument_analysis.imported_bindings import build_imported_bindings
report = build_imported_bindings(
    "docs/data/instrument-imported-origin-plan-20261101.json",
    output_dir="instrument-renders/imported-bindings-20261101",
)
```

```console
python -m instrument_analysis.imported_bindings --plan docs/data/instrument-imported-origin-plan-20261101.json --output instrument-renders/imported-bindings-20261101
```

The output directory must be new. Publication stages the artifacts and checks that frozen inputs did not change before publishing. `bindings.json` follows [instrument-imported-origin-bindings/v1](../schemas/instrument-imported-origin-bindings-v1.schema.json); `records.json` contains neutral physical records. Each binding has extracted BRR bytes, sample metadata, a normalized claim for shortlist compatibility, and an `imported_claim` containing the complete original claim. `validate_imported_binding(report_path, source_record_id)` rechecks the plan, report revision, source attribution, exact current ROM extraction, and generated artifacts before shortlist use.

Claim identity covers source, sheet, explicit scope context, and raw sample-ID identity fields. Claim content hashes cover the documentary content; report hashes pin its whole captured revision, including locators and selections. Moving a row can preserve claim/content identity while changing its report revision. A changed report therefore needs a deliberate new plan and output directory. JSON Schema validates shape; runtime validation checks identity/hash equality and physical extraction.

## Scope and namespace

`$07` means a documentary global sample ID only after matching the original SNES game section and exact raw identity syntax. FF4 maps to `snes:ff4-us:global-sample-07`; FF6 maps to `snes:ff6-us:global-sample-07`. These are different records. Global sample IDs do not establish physical DSP SRCN values, driver patch commands, or song-local slots; those fields remain null. File, payload, descriptor and BRR hashes preserve distinct byte identities. Original source hardware remains unverified.

The plan contains FF4 Flute $07 and FF6 Flute $07 regression bindings, reported FF4 Harp $04, reported FF6 Pan Flute $03 and Oboe $0A, and qualified FF6 Harp $1B. The last can bind physically while remaining a qualified documentary claim. It stays metadata-only with no automatic GM target.

## Independent target selection

Reported semantic rules predeclare zero-based GM alternatives independently of audio retrieval: Flute 73/72/74/75, Pan Flute 75/73/72/74, Oboe 68/69/70/71, and Harp 46. Source preset ordinals are not GM programs. No route is accepted automatically.

Constructed probes use separately measured pitch calibration and an audio-only frozen baseline before documentary and acoustic results are combined for review. Documentary sampled-key text never supplies a game unity key, measured pitch offset, or calibration. Probe construction does not establish song playback behavior. Combined reviews preserve documentary membership/priority and acoustic rank separately, with no fused winner or human accuracy label.

This workflow creates new outputs; it does not update the old five-binding origin pilot, its catalog, or its review packages. Live result counts and validation evidence belong to the separately recorded execution audit.

## Validated November 1 expansion

The generated entry point is
`instrument-renders/imported-origin-bindings-20261101/index.html`. It links six
binding cards and two new combined reviews, each with seven candidates. The
FF4 and FF6 flute bindings reproduce the earlier BRR bytes exactly. The two
harps remain metadata-only in this run; FF6's qualified claim has no automatic
target. No historical converter mappings were changed.

| New native probe | Raw S-DSP pitch | Measured MIDI note | Documentary primary (zero-based) | Audio-only rank |
| --- | --- | --- | --- | --- |
| FF6 Pan Flute $03 | 840 | 59.94661 | 75 | 23 |
| FF6 Oboe $0A | 1608 | 60.00590 | 68 | 59 |

Both independent acoustic baselines abstain because their top routes are too
close. These ranks do not override the credited source identities, and the
GM replacements remain unreviewed. All references come from one GeneralUser
SoundFont; this is not independent bank consensus. Functional suitability is
not scored without grounded occurrence-level usage.

All six cases belong to development data: five known source-lineage groups and
zero held-out cases. The flute relationship reuses the earlier documented
U-Series/L-CD1 note. Different instrument families or products are not assumed
to share identical waveforms. Unknown sharing is not discovered by this audit.

Validation passed 225 instrument-analysis tests, all 32 instrument schema
definitions, and 38 live artifacts, plus generated JavaScript syntax checks.
Review inventories, frozen baseline hashes, exact BRR sources, common source
presentation audio, and unclipped RMS-matched copies were checked. Playback
remains manual only. The compact audit is
`docs/data/instrument-imported-origin-validation-20261101.json`.
