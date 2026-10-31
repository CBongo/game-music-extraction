# Documentary sample origins for instrument mapping

Use documented sample origins as the first source of candidate identity when they
exist. The mapping pipeline should first look up a claim, bind it to an extracted
sample, and use that origin to choose candidate replacements. Audio analysis then
checks the binding and the replacement, while still covering undocumented sounds.
This separates two useful questions: what generated the original sample, and which
GM preset reproduces its musical role well in our target bank.

## References and attribution

The user identified [NEWER VGM Sound Sources](https://docs.google.com/spreadsheets/d/1JJBlHHDc65fhZmKUGLrDTLCm6rfUU83-kbuD8Y0zU0o/edit#gid=2047725819),
maintained by the VGM Resources community. It links to
[Final Fantasy Sound Sources (Detailed)](https://docs.google.com/spreadsheets/d/1dBn9BG4zlEw1StR1uhNB57bRxRRnbo3hGP8Y-5iB-W4/edit).
The detailed sheet credits Mudstep, OKayDoKay and other researchers; retain its
per-game research credits with any imported claims. We read both sources on
2026-10-31. The external sheets were not changed.

The main document supplies manufacturer, product, bank/path, preset, notes, track
examples and demonstration links. The detailed FF4 and FF6 tabs additionally
identify sample numbers, sampled notes and some transformations. The adjacent
[research seed catalog](data/instrument-source-references.json) records URLs,
selected row facts, credits and selection fingerprints. It is not a runtime
mapping table or a full spreadsheet mirror.

## A small pilot with immediate relevance

| Lead | Documentary identification | First project action |
| --- | --- | --- |
| FF4 SNES sample `$07`, detailed tab row 17 | Roland U-220, Tone, F-092: Flute 1, sampled B5 | Bind the existing FF4 flute case to local sample bytes and make a provenance-assisted flute shortlist. |
| FF6 SNES sample `$07`, detailed tab row 17 | Roland L-CD1, Flute & Piccolo JP, P1 Flute V-sw, sampled B5 | Join the FF3/FF6 instrument table to its BRR sample and retain the different origin from FF4. |
| FF6 `$16`, detailed tab row 32 | A timpani attribution with an explicit Roland/E-MU alternative | Exercise preservation of uncertain claims; do not make it a confirmed label. |
| FF6 `$1F`, detailed tab row 41 | A whistle attribution that the row itself doubts | Exercise conflicting or rejected-origin handling. |
| FF6 `$30`, detailed tab row 58 | A Latin-percussion source with a reported +6.5-semitone change, cropped beginning and FF5 reuse | Test processing lineage and shared-sample grouping. |

The main sheet's FF4 flute row has a note preferring the U-series variant over the
related L-CD1 sound and supplies a demo link. That distinction is exactly what
would disappear in a plain instrument-family lookup. The detail sheet also lists
several explicitly uncertain origins despite the main document's confirmed-source
editorial policy. Preserve both the identification and its qualifiers.

FF8 and Chrono Cross provide another promising class: the main sheet identifies
JV-1080/JV-2080 PR-D (GM) presets, including Pan Flute. These can seed a concrete
GM candidate after validating the product's bank and numbering conventions. They
are currently game/track-level leads. The inspected FF8 detail tab did not yield
a row that binds our existing set-0x0a/articulation-76 capture, so those numbers
must not be joined merely because a source program is also numbered 076.

## Evidence model and binding

Keep a separate claim sidecar rather than extending a manufacturer name into an
acoustic measurement or overwriting a converter patch map. A claim should retain:

- Document ID and URL, tab ID/title, cell range and surrounding game/edition section.
- Observation date and a content fingerprint for the selected cells, notes and links.
- Original manufacturer/product/bank/preset spelling, sampled-key notation and qualifiers.
- Researcher credits, demo/source links, reused-source relationships and reported processing.
- The asserted origin, uncertainty state and any competing identifications.
- An independent local binding: canonical record ID, game/region/driver, source
  instrument ID, song slot or articulation namespace, source file/sample hashes
  and byte offsets. Record a missing or conflicting binding explicitly.
- A separate target-GM decision and its review evidence, if one has been made.

Row numbers are locators, not enduring identity. Preserve the selected content
when a row changes or moves; do not replace earlier evidence silently. Cell notes
and links are essential: a CSV-only import would lose several important caveats.
Headers, game headings, remake/platform sections and unresolved-source sections
must be part of parsing context. The main document's colored stars encode
coverage categories, not a confidence score for each row.

Use explicit aliases for FFII (US)/FFIV and FFIII (US)/FFVI, retaining the actual
version and region. A documented sample number, a product preset number and a
local patch command inhabit different namespaces. Join through the game's own
instrument/sample tables and verify the selected bytes, rather than matching
names or numbers alone.

## How the mapping decision changes

A locally bound origin should drive a documentary candidate branch. An explicit
original GM-bank identity can seed a concrete route. A non-GM preset, such as a
named Casio electric piano, can seed the electric-piano family and original-preset
references when captures are available. The source preset's name or ordinal does
not itself choose the best preset in GeneralUser GS. Rank and audition that
shortlist in the actual register and articulation, displaying the documentary
origin beside acoustic distances and the source's uncertainty.

Do not discard the established blind audio-only baseline. Compare it with the
provenance-assisted branch as separate methods. A verified documentary identity
can be stronger evidence than the baseline's weak nearest-neighbor match, without
presenting either a source claim or a heuristic score as a calibrated probability.
Unknown or unresolved sheet entries remain unlabeled origins; they are not
negative instrument classes or automatic SFX/abstention labels.

Reported sampled keys and fractional shifts are useful pitch investigation leads,
but require note-number convention, playback rate and driver tuning checks.
Bracketed B5 is the key reportedly sampled from the source product, not an
established game unity key or observed sounding pitch. Likewise, a full preset can
contain layers or chords while the game stores one zone or a processed fragment.
Keep composite detection and reconstruction available; an identified sample
source does not prove a single-note interpretation or recover exact voicing.

The same source waveform can appear in several libraries, sound modules, games or
rereleases. Record the lineage and group related underlying samples for train/test
splits. Use sheet labels only after blind predictions when evaluating audio-only
retrieval; disclose which documentary corpus the assisted method consulted.
Reviewer-accepted GM replacement quality remains a different label from origin
identification. This extends the existing label isolation in `pilot.py` and the
review workflow rather than feeding benchmark answers into `ranking.py`.

## Implementation order

Beads records the work under `gme-0r4.11`:

1. `.11.1`: bounded read-only importer and attributed claim sidecar, preserving
   notes, qualifiers, section context, links and content fingerprints.
2. `.11.2`: bind the two flute cases to extracted samples; use the uncertain and
   transformed FF6 rows as evidence-handling controls. Build review cards without
   changing existing maps.
3. `.11.3`: a separate documentary shortlist path, concrete candidate auditions
   and comparison against blind retrieval with shared-sample split isolation.

The FF4/FF6 pilot should precede broad harvesting: it can supply real, documented
examples for `.7.1` and `.9.2.1`, and exposes binding errors while the scope is
small. Documentary origins reduce the identity search; they do not remove the
need to ground native sequence timing, tuning and effect behavior. Existing tools
are sufficient to start the importer and bindings; obtain original-preset captures
only when a particular verification needs them.
