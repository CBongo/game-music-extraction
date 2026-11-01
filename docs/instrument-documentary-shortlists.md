# Documentary instrument shortlists

This workflow keeps researcher-documented source identity and acoustic GM
ranking as separate evidence branches. Its output proposes candidate routes for
manual review; it does not select a winning mapping or establish accuracy.
Credited source research takes precedence for sound-source identity and
voicing over older converter maps such as `snes/ff2/ff2mus.pl`. The `.pl` maps
remain useful for note/event decoding and table mechanics; disagreements with
the credited source research are treated as likely corrections to those older
maps, not as grounds to downgrade the source research.

This remains a five-claim source-documentation pilot: the two flute leads plus
the FFVI timpani, whistle, and claves claims. The explicit structured GM-bank
route is a tested contract only; none of these five claims currently has a live
GM-bank binding.

See the [origin pilot record](instrument-origin-pilot.md) and its
[selected source rows](data/instrument-source-references.json) for the exact
claims, credits, uncertainty, and local ROM-byte bindings. Keep the source
researcher's wording and qualifiers with every shortlist.

## Documentary branch

`instrument_analysis.documentary_shortlist.build_documentary_shortlist` reads
an `instrument-origin-pilot/v1` binding, verifies the selected catalog claim,
re-extracts the bound ROM sample, and checks the copied BRR bytes. It builds the
documentary proposal without using the audio-only ranking to create or order
that proposal.

For the two reported flute rows, the fixed rule is
`reported-flute-family-fixed-alternatives/v1`: GM program 73 (one-based 74,
Flute) is the primary documented-family candidate; related auditions are
programs 72, 74, and 75 (Piccolo, Recorder, and Pan Flute). Their priorities
remain visible: the first program has documentary priority 1, and the related
programs have priority 2. These are audition candidates, not equivalence
claims. In the emitted combined review, the union contains six FFIV candidates
and seven FFVI candidates. The primary program 73 ranks 33rd and 40th in the
independent acoustic branch, respectively. The related program 72 ranks 19th
and 11th; program 74 ranks 3rd and 4th; program 75 ranks 50th and 31st. These
audio ranks are evidence alongside documentary priority, not a fused ordering.
FFIV's U-220 Flute 1 and FFVI's L-CD1 Flute V-sw are grouped as a
documented flute source family because the sheet notes that the U-Series
variant matches better than L-CD1. That lineage note does not prove the
presets' audio is identical.

The structured `origin.gm_reference` contract is tested with synthetic
fixtures, but is not bound to any of the five live pilot claims. If supplied,
it is usable only when its map kind is `GM`, its numbering convention is
`one_based_gm_1_to_128`, its `program_1based` is in range, and its evidence is
bound to the exact source row. The evidence retains its selected-cell
fingerprint, raw bank/program wording, and
`numbering_basis: "verified_one_based_gm_bank"`; accepted raw bank labels are
`GM`, `PR-D (GM)`, or `Patch -> PR-D (GM)`. For example, a documented
`PR-D (GM)` program `076 Pan Flute` can be represented explicitly as
`program_1based: 76`, which would yield GM route program 75 zero-based. The
shortlist converts only that explicitly typed one-based value. It never infers
that a product patch ordinal or text like “F-092” is a GM program or bank number.
Qualified, doubted, transformed, or unreported source identifications abstain
from automatic target suggestions; insufficient documentary evidence does not
imply SFX, percussion, composite, or a negative GM class.

## Independent audio branch and pitch gate

To attach audio evidence, pass a hash-bound `instrument-ranking-diagnostics/v1`
file from a native capture for the same source record. The shortlist accepts it
only when it references the same 815-entry reference index, has no source
classification or source-fact assistance, and its MAME SPC contains the exact
BRR bytes from the documentary binding. The native capture must have an
acoustic pitch calibration with confidence at least 0.8 and grounded event
phase boundaries. The selected reference must then match the calibrated pitch
and phase windows and contain valid, non-silent, unclipped features. Otherwise
the audition gate is `metadata_only`; documentary candidates remain clearly
proposed without verified audio reference variants.

The audio ranker is run independently with the SoundFont index and native
capture only. It does not receive documentary claims, curated route labels,
source patch text, or the shortlist rule. Where a documentary route also
appears in the audio top list, its shortlist row carries the audio rank and
distance separately and may include the exact verified index reference row.
There is no combined score or fused winner. A distance is a ranking heuristic,
not a probability. The current 815-reference index contains one distinct
SoundFont SHA-256, so it supplies no cross-SoundFont consensus. The FFIV and
FFVI audio-only baseline runs request top 128 routes from that index; the
combined review union includes the top three audio-only routes alongside all
eligible documentary proposals.

In the current run, both rankers abstain because the top two route groups are
too close. The FFIV capture calibrates to MIDI 59.9907; its top three
zero-based programs are 4 (distance 0.106442), 89 (0.121563), and 74
(0.121806). The FFVI capture calibrates to MIDI 59.98467; its top three are 79
(0.101710), 4 (0.109464), and 89 (0.114678). These are the sorted alternatives
available for audition after the ranker's abstention. The documentary shortlist
does not override the abstention or convert any of these alternatives into a
confirmed source or target mapping.
Functional coverage is `not_scored` for both captures because there are no
grounded occurrence-level sounding MIDI notes; raw triggers and source-tick
effects are not converted into usage notes.

For the FFIV/FFVI flute leads, the local C4 captures and GeneralUser program-73
comparisons are isolated ROM-sample probes. They measure a controlled render of
the bound BRR bytes; they do not verify the original U-220 or L-CD1 preset
waveform or a song performance. The shortlist does not convert those captures
into reviewer acceptance or a source-pitch correction.

## Build a shortlist

Use a new output directory. Inputs include the binding report, combined GM
reference index, and source record ID; optional audio diagnostics can be
supplied as a separate branch:

```powershell
python -m instrument_analysis.documentary_shortlist --bindings instrument-renders/origin-binding-pilot-20261101/bindings.json --index instrument-renders/gm-baseline-combined-index-20261026.json --source-record-id snes:ff4-us:global-sample-07 --output instrument-renders/documentary-mapping-20261101/ff4-flute
```

Add `--audio-diagnostics <hash-bound-ranking-diagnostics.json>` to run the
independent acoustic gate and attach verified comparisons. Use diagnostics
from the matching native sample capture; the CLI does not synthesize or rank
audio by itself. The current audio-only FFIV and FFVI rank runs are in
`instrument-renders/documentary-mapping-20261101/audio-only-ff4/diagnostics.json`
and `audio-only-ff6/diagnostics.json`.

The CLI writes a hash-bound `manifest.json` and prints the source ID, rule,
audition-gate status, and candidate count. The report follows
[`instrument-documentary-shortlist-v1.schema.json`](../schemas/instrument-documentary-shortlist-v1.schema.json).
It records hashes for the binding, source catalog, source ROM, copied BRR,
reference index, optional audio diagnostics, and the generating module. If
using a different local audio diagnostic path or index, update those arguments
to the exact files in that run.

## Keep related source samples in one split

The separate `instrument_analysis.origin_splits.validate_origin_splits` helper
audits known sample hashes, explicitly curated source-family IDs, and
documented lineage links. Give each case exactly `case_id`, `record_id`,
`split` (`development` or `held_out`), `sample_sha256`, and
`origin_family_id`; provide evidence for any cross-family relationship. It
rejects connected sample/family groups that span development and held-out
splits. Family IDs describe source sample lineage, not GM instrument class.

The audit only checks relationships supplied to it. It does not discover
unknown shared samples, fit thresholds, or keep documentary evidence out of a
ranker on its own; ranking isolation is enforced by the separate input path.
For this pilot, the FFIV U-220 and FFVI L-CD1 flute leads are both development
cases and are linked by the documented shared flute variant. The audit unions
cases with the same canonical record ID as well as matching sample hashes and
curated family links. A file-backed lineage link must resolve to an existing
file with the declared SHA-256; URL evidence must include an explicit locator.
The two leads form one development lineage group, leaving zero eligible
held-out cases. The final live audit is
`instrument-renders/documentary-mapping-20261101/split-audit-final.json`; its
schema is
[`instrument-origin-splits-v1.schema.json`](../schemas/instrument-origin-splits-v1.schema.json).

The curated family IDs are `roland-u220-flute1` and
`roland-lcd1-flute-vsw`. Their link is backed by the main-sheet row 142
observation recorded in the source catalog. Run the Python API directly; the
audit helper has no CLI:

```python
from instrument_analysis.origin_splits import validate_origin_splits

audit = validate_origin_splits(cases, lineage_links=lineage_links)
```

Each case must provide its actual sample SHA-256; names alone are not sufficient
to establish sample sharing.

Do not describe this pilot as held-out cross-game accuracy or confirmed GM
mapping. The reported names, local ROM-byte bindings, isolated pitch captures,
documentary candidates, and future blinded reviewer decisions each answer a
different question and must remain separately traceable.

## Prepare the combined review union

`instrument_analysis.documentary_review.build_documentary_review` forms a
review union from the eligible documentary candidate references and the
audio-only top `k` list (default 3). Shared references are deduplicated, with
both branch-membership fields retained. Documentary priority and acoustic rank
stay separate in each row; no fused score or winner is computed. It builds
matched comparisons and a review package when the pitch gate is eligible;
otherwise it publishes a metadata-only report.

```powershell
python -m instrument_analysis.documentary_review --shortlist instrument-renders/documentary-mapping-20261101/shortlist-ff4-flute-07/manifest.json --output instrument-renders/documentary-mapping-20261101/review-ff4
python -m instrument_analysis.documentary_review --shortlist instrument-renders/documentary-mapping-20261101/shortlist-ff6-flute-07/manifest.json --output instrument-renders/documentary-mapping-20261101/review-ff6
```

The review packages are documentary-assisted and unblinded so the reviewer can
assess evidence for both claims. The origin assessment control is separate from
the replacement decision, and choosing GM Flute does not accept the documented
U-220 or L-CD1 source identification. `human_label_count` starts at zero; a
proposed route is not a review judgment. Exported documentary-assisted
judgments are retained in the full review log but excluded from the isolated,
blind-audio benchmark adapter. The corresponding outputs are in
`instrument-renders/documentary-mapping-20261101/review-ff4/` and
`review-ff6/`; each contains `manifest.json`, `report.json`, and the
hash-bound review package. The separate `split-audit-final.json` records one
development lineage group and zero held-out cases. The review interface shows
source identity, documentary priority, and acoustic rank alongside audition
controls when evidence is revealed. Origin assessment remains separate from
the GM replacement judgment. Corrected audio is rendered into a revision
package and must be auditioned again; it is not counted as a new accuracy label.
The combined report labels this a development pilot and makes no accuracy
claim.

The manifest follows
[`instrument-documentary-review-v1.schema.json`](../schemas/instrument-documentary-review-v1.schema.json),
and the branch and candidate detail follows
[`instrument-documentary-review-report-v1.schema.json`](../schemas/instrument-documentary-review-report-v1.schema.json).

## Local validation

The November 1 pilot passed all 193 instrument-analysis tests, Python syntax
checks, generated review JavaScript syntax checks, and 28 schema definitions.
The live schema audit checked 22 artifacts and temporary automated judgment
controls; those controls were discarded and do not count as human labels.
The local entry point is `instrument-renders/documentary-mapping-20261101/index.html`;
`schema-validation.json` records the schema checks. Existing maps were not changed.
