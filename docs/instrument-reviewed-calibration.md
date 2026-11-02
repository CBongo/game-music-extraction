# Reviewed native corpus and abstention calibration

This workflow measures human acceptability of exact auditioned candidates. It does
not derive human judgments from documentary instrument names, ranking scores,
synthetic construction labels, or previous route preferences. Multiple candidates
may be acceptable; a fully rejected auditioned pool means none of that pool is
acceptable, not that the source is globally out of domain. An unreviewed candidate
is unknown, never an implicit negative.

The current native corpus is being prepared with development sources from FF4,
FF6, and FF8 and separate FF5 held-out voices. Genuine human candidate judgments
are still pending. A report with `status: awaiting_reviews` and `thresholds: null`
is an audit of readiness, not a fitted operating point. Existing default thresholds
remain unchanged. Task .7.1 remains incomplete until real listening judgments and
fit/evaluation results exist. Artifact paths, live counts, and verification results
belong in the session's final validation record.

## Freeze and audition

A corpus (`instrument-reviewed-corpus/v1`) binds each case to its exact diagnostic
JSON and review package by path and SHA-256, along with canonical source record,
source-sample hash, origin family, game, and predeclared split. Keep documentary
facts outside blind ranking input. Families describe source sample lineage, not
GM classes. Known shared samples and documented lineage must stay on one side of
the split; games cannot occur in both development and held-out partitions.
Unknown transformed sharing is not discovered by this audit.

Build the corpus from an `instrument-reviewed-corpus-plan/v1` plan first:

```powershell
python -m instrument_analysis.reviewed_corpus --plan <plan.json> --output <new-corpus-directory> --audio-top-k 3
```

The output corpus optionally pins its build plan with `plan: {path, sha256}`.
Case metadata paths retained from the plan resolve relative to that pinned plan,
not relative to the generated corpus directory.

The plan binds a frozen reference index, native manifests, pitch calibrations,
policy, lineage links, and exact sample byte proofs (source file hash, offset,
length, encoded sample hash, and BRR or PSX_ADPCM encoding). The reusable builder
accepts native development/held-out cases. It freezes all 128-route rankings for
all cases before creating any comparisons or review packages. Its default top
three audition candidates are packaged blind; the full ranking remains available
in the pinned diagnostics. It writes `corpus.json` in a fresh output directory.
Controls remain a separately constructed control cohort.

Build a new offline batch; output directories must not already exist:

```powershell
python -m instrument_analysis.candidate_reviews build --corpus <corpus.json> --output <new-batch-directory>
```

Open the batch's `index.html`. Playback is manual and opt-in. Enter a reviewer ID,
audition the source and each candidate being assessed, select `acceptable` or
`unacceptable`, and explicitly acknowledge auditioning that exact pair. The export
records the source and candidate audio hashes. Leave unauditioned candidates
unassessed. Partial batches can be exported; no assessment is invented for skipped
cases. The import verifies those audio hashes against the immutable package.

The evidence/rank reveal button permanently marks that case as revealed for the
export. Also select “I already inspected/know source evidence” when prior source
exposure could influence the judgment. This uses the same `revealed` flag. A blind
package is insufficient if the reviewer has already seen documentary identities.
Revealed, nonblind, documentary-assisted, revised, and correction-bearing judgments
remain auditable but are excluded from calibration. Export the JSON download and
retain the frozen batch alongside it. Do not edit hashes to bypass a mismatch.

## Import and fit

```powershell
python -m instrument_analysis.candidate_reviews import --corpus <corpus.json> --judgment <candidate-reviews.json> --store <review-store>
python -m instrument_analysis.threshold_calibration --corpus <corpus.json> --store <review-store> --output <new-calibration-directory>
```

Imports are canonical content-addressed JSON files opened exclusively; reports are
published into fresh directories. Reimporting identical content cannot overwrite
an existing import. A later assessment should be a new immutable import, with
conflicts resolved explicitly outside the fitting step. Conflicting reviewers or
packages for the same labeled case, and conflicting decisions for a candidate,
raise errors. The tool does not fabricate a majority vote. Verification repeats
package, corpus, import, and bound artifact checks; input changes during processing
fail publication. Retain all input pins with every report.

Selection uses only reviewed native development top-one judgments. It requires
at least four reviewed development cases, at least three independent known-lineage groups from the split audit, and both
acceptable and unacceptable top-one examples; policy can require larger minima.
The predeclared nonnegative distance/margin grid maximizes accepted correct
examples subject to its empirical false-accept bound. False accepts are divided
by reviewed negative development cases. Ties prefer more accepted cases, then a
smaller distance threshold and larger margin threshold. The result is an empirical
operating point, not a probability or confidence estimate.

Held-out labels are evaluation-only: permuting them cannot change fitted
thresholds. Same-bank and synthetic controls use the separate `control` split and
cannot satisfy development minima. Report development, held-out, and controls
separately. Coverage counts all finite-score cases accepted by the fitted operator, including
unreviewed cases; `accepted_unreviewed` exposes those separately. Accepted-correct/incorrect counts, reviewed denominators,
and selective accuracy are exposed; uncertainty remains null rather than implying
a supported interval. A non-fitted status leaves thresholds null. Fitting publishes
a report only and does not install new ranker defaults.

## Real SNES cartridge snapshot boundary

`instrument_analysis.snes_snapshot.capture_snes_snapshot` runs the pinned FF5
cartridge payload in MAME and invokes `snes/capture_snapshot.lua` at a requested
frame callback (600 for this corpus capture). The supported file and stripped
payload have exact SHA-256 pins. The observed cartridge header checksum does not
match the patched payload sum; header fields are recorded observations, not an
approved checksum claim. The manifest records actual MAME version/executable,
capture script, IPL source, request identity, receipt, ARAM/DSP sizes and hashes,
and emitted SPC bytes. Its actual version is `snes-cartridge-snapshot/v1`.

The earlier FF5 input SPC lacked the required DSP block and is invalid for this
workflow. The replacement is a real runtime copy of 64 KiB ARAM plus 128 DSP
register readbacks, with PC captured in the SPC header. A/X/Y/PSW/SP are unknown
and written as zero. F2 is restored after reads; F1 is set to zero to expose upper
ARAM and disable timers. ARAM I/O reads and DSP readbacks do not serialize hidden
DSP state; timer reads can clear counters. This is isolated voice replay input,
not a resumable game, full song, or validated sequence/effect reproduction.

FF5 held-out BRR hashes have no exact matches among the examined 22 FF4 and 63 FF6
samples. That bounded byte comparison does not establish independence from every
possible transformed or undocumented shared source. Keep any later discovered
lineage evidence in the split audit before fitting.

## Contracts and related validation

The seven schemas in `schemas/` cover corpus build plan, reviewed corpus, candidate review export,
immutable import, batch manifest, calibration report, and cartridge snapshot. They
use JSON Schema 2020-12, fixed version strings, strict structural objects,
SHA-256 references, and numeric bounds. Optional `metadata` on raw corpus/case and
review records is the extension point; metadata is not a label or eligibility
exception. New structural semantics require an explicit version/schema update.
Schema checks do not replace runtime hash verification, cross-record identity,
route consistency, origin split checks, or human audition.

See the [instrument benchmark](../benchmarks/instruments/README.md),
[ranking validation record](instrument-ranking-validation.md), and
[local review workflow](instrument-review.md). Historical pilot metrics and
same-bank sanity checks remain separate from this reviewed native experiment.

## November 1 native review checkpoint

The frozen plan is `benchmarks/instruments/native-review-plan-v1.json`.
The local batch is
`instrument-renders/reviewed-native-20261101/listening-batch-final/index.html`.
It contains eight cases with three candidates each, with independently randomized
case and candidate presentation orders. Package JSON and audio bindings stay
unchanged. The same frozen corpus reproduces the same presentation order; this
is masking for honest blind review, not a cryptographic or independently managed
experiment.

Six development cases cover the historical FF4 flute/trumpet/bass voices, FF6
Pan Flute/Oboe, and the existing FF8 articulation. Two held-out FF5 runtime voices
come from an actual 600-frame MAME cartridge capture. Their raw BRR payloads have
no exact matches among all 22 FF4 and 63 FF6 ROM sample payloads checked. The old
FF5 `.spc` file is only a partial driver image and was not used as a capture.
The FF8 family identifier denotes local bank/articulation byte identity, with no
asserted documentary source-preset binding. FF5 family identifiers denote runtime
SRCN sample payloads. Documentary lineage annotations apply to the documented
FF4/FF6 cases; declared families do not certify different original synthesizer
waveforms, and unknown transformed sharing remains untested.

The initial checked run has zero genuine human labels, six development cases,
two held-out cases, and no control cases pooled into fitting. Its status is
`awaiting_reviews`, with null thresholds and empty reviewed correctness metrics.
Existing ranker defaults are unchanged. After downloading the judgment JSON,
import it against `corpus/corpus.json` under the same local run directory and
write the next calibration into a new output directory. Prior source exposure
is explicit; simply identifying an instrument by ear is part of the task.

All 252 instrument-analysis tests pass. Validation checks 39 schema definitions
and 71 live artifacts and temporary revealed controls, including source sample
spans, exact native pitch bindings, frozen ranks, package inventories, level
matching without clipping, and exclusion of revealed controls from calibration.
The audit is `benchmarks/instruments/native-review-validation-v1.json`.
Genuine judgments and fitted/held-out evaluation remain the unfinished work in
`.7.1.2`; the preparation checkpoint is `.7.1.1`.
