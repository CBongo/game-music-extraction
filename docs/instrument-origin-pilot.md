# Instrument source-origin pilot

This pilot attaches the researchers' preferred instrument identifications to
exact local samples from known US revision-zero ROM payloads. The sheets supply
the identity; extraction pins the corresponding local audio bytes. Original-preset
reference captures would add a separate comparison. The two GM flute auditions
are ready and await reviewer judgment.

## Source authority and scope

Use the credited [Final Fantasy Sound Sources (Detailed)](https://docs.google.com/spreadsheets/d/1dBn9BG4zlEw1StR1uhNB57bRxRRnbo3hGP8Y-5iB-W4/edit)
as the preferred source-origin authority over older converter voicing maps such
as `snes/ff2/ff2mus.pl`. The script is useful evidence for extraction and table
decoding mechanics; its historical instrument labels do not override the
credited source research. Preserve the sheet's original words, sampled-key
brackets, qualifiers, and per-game credits. Its stars describe coverage
categories, not confidence in individual rows.

[`docs/data/instrument-source-references.json`](data/instrument-source-references.json)
stores selected row cells and their locators. Each `selection_sha256` fingerprints
canonical JSON of the spreadsheet ID, sheet ID, game title, game section, and
raw cells. It is a selected-cell fingerprint, not a whole workbook revision
hash; comments were not inspected. Sheet rows are not local sample hashes or
sample IDs until separately linked.

The pilot preserves five FF4/FF6 leads:

| Local sample | Documentary identification | Retained qualification |
| --- | --- | --- |
| FFIV `$07` | Roland U-220, Tone F-092 Flute 1, sampled key B5 | Main sheet says the U-Series variant matches better than L-CD1; the linked demo was not auditioned. |
| FFVI `$07` | Roland L-CD1, Flute & Piccolo JP, P1 Flute V-sw, sampled key B5 | Reported identification. |
| FFVI `$16` | Roland L-CD1, Orchestral Percussions #2, P9 Timpani, sampled key A5 | “May be something else (Roland or E-MU).” |
| FFVI `$1F` | Fairlight CMI III, Keyboards, Martenot11 | “Probably not it.” |
| FFVI `$30` | Roland L-CD1, Latin Percussion #2, P1 Latin Perc 2, sampled key D#4 | “Pitched up 6.5 semitones, first part of sample cut, also in FFV.” |

The sampled key and any transposition noted by the sheet are not game unity
key, sounding MIDI pitch, or a measured pitch offset. FFIV's and FFVI's `$07`
are game-scoped IDs in different table conventions: FFIV uses global sample ID
7 at table index 7; FFVI uses global ID 7 at zero-based table index 6. Keep those
namespaces explicit. The current bindings leave physical DSP SRCN, song patch
command, song-local slot, game unity key, and observed pitch offset unset.

The FF8/Chrono Cross main-sheet rows are still game-level leads, not bindings
to the local FF8 set-0x0a/articulation-76 capture. “PR-D (GM) 076 Pan Flute” is
the documented source patch identifier; it is not evidence that a local sample
ID or the render index's bank identifies that patch. Chrono Cross's
`BassSequenceIminorD#` remains a separate non-GM layered-source lead.

## Build hash-bound ROM sample records

The extractors only accept the pinned US revision-zero ROM payloads. FFIV
validates the payload hash, cartridge header, and checksum; FFVI validates the
payload hash, title/map/region/revision, and mirrored checksum. Both extract the
requested BRR and retain descriptor bytes and addresses in
[`instrument-rom-sample-v1.schema.json`](../schemas/instrument-rom-sample-v1.schema.json).
The low-level `parse_ff4_sample` and `parse_ff6_sample` functions decode a
specified byte layout for controlled use; call `extract_ff4_sample` and
`extract_ff6_sample` for version validation.

Run the catalog binding pilot with locally held ROM files:

```powershell
python -m instrument_analysis.origin_pilot --catalog docs/data/instrument-source-references.json --ff4-rom <private-ff4-us-rev0-rom> --ff6-rom <private-ff6-us-rev0-rom> --output instrument-renders/origin-binding-pilot-20261101
```

The destination must be new. The pilot copies each extracted BRR, binding JSON,
sample metadata JSON, per-record canonical JSON, and a local HTML evidence card;
it also writes `bindings.json` and `records.json`. The report is described by
[`instrument-origin-pilot-v1.schema.json`](../schemas/instrument-origin-pilot-v1.schema.json).
The catalog and ROM inputs are hash-checked during the build. ROM images remain
private inputs; they are not copied into the output.

The completed local run is in
`instrument-renders/origin-binding-pilot-20261101/`: five per-lead folders,
`bindings.json`, and `records.json`. It binds the five documentary leads to
sample bytes in the pinned US ROMs; it does not resolve their song-local slot
or physical DSP SRCN.

`records.json` is an `instrument-analysis/v1` bundle recording the local BRR
bytes and their structural provenance. Its sample ID identifies the extracted
ROM table entry. It does not claim that the game actually triggered that sample
in a particular song, that its sound matches the external hardware patch, or
that a sampled-key label establishes source tuning. An entry's immutable ROM,
descriptor, and BRR hashes make later bindings auditable without conflating
these claims.

## Probe and replacement evidence

The FFIV sample has controlled native S-DSP captures under
`instrument-renders/origin-flute-pilot-20261031/native-ff4-unity/` and
`native-ff4-c4/`, and FFVI captures are under
`instrument-renders/origin-flute-pilot-20261101/native-ff6-unity/` and
`native-ff6-c4/`. The raw-register reference captures use S-DSP pitch register
4096: FFIV measures 1000.3036 Hz (MIDI 83.21835), and FFVI measures 1000.2545
Hz (MIDI 83.2175). “Unity” in those directory names means the selected raw
register reference, not the game's unity key. The C4 captures use raw register
1071: FFIV measures 261.48512 Hz (MIDI 59.9907), and FFVI measures 261.39407
Hz (MIDI 59.98467). These are controlled MAME S-DSP renderings of the bound ROM
sample bytes under recorded isolated conditions. FFVI uses a constructed SPC
input with only the selected BRR and its ADSR; FFIV uses a controlled sample
probe. They do not verify either original hardware product's patch waveform or
a game sequence performance.

`instrument-renders/origin-flute-pilot-20261031/comparison-ff4-c4/` and
`instrument-renders/origin-flute-pilot-20261101/comparison-ff6-c4/` contain
phase-matched comparisons against GeneralUser bank 0, program 73 (one-based 74,
Flute). They are unreviewed candidate comparisons, not accepted mappings or GM
accuracy results. Native fixed voice volume and GM velocity are different
controls, and comparison scalar gain only matches presentation level. No human
reviewer choice or original U-220/L-CD1 hardware-reference capture is recorded.

For FFVI, `instrument_analysis.origin_probes.build_rom_sample_probe` constructs
an isolated SPC input from bound ROM BRR and ADSR bytes. The current
`instrument-renders/origin-flute-pilot-20261101/ff6-07/constructed-spc/`
artifact is a constructed probe input, not an original FFVI performance
capture. Its pitch register is explicitly selected; no sampled note or sounding
offset is inferred from the sheet. The MAME renders remain isolated-byte probes,
not proof of the song's sequence, SRCN use, or original patch identity.
The constructed SPC manifest is described by
[`instrument-rom-sample-probe-v1.schema.json`](../schemas/instrument-rom-sample-probe-v1.schema.json).

The two flute leads have a separate proposed GM Flute route (GM 74, zero-based
73), retained with `basis: curated` and an unreviewed status. It is a shortlist
for manual audition; the older `.pl` replacement maps are not imported as
authority. The qualified Timpani, doubted Whistle, and transformed Claves rows
remain documentary controls with their qualifiers intact. “Unknown” or
“doubted” is not an SFX label or a negative GM route. Do not promote any of
these leads to a route prediction or source-tuning calibration without separate
evidence.

## Separation from ranking and calibration

Documentary origin, local ROM byte identity, a target GM route, and pitch
calibration are separate claims. The pilot stores source claims in its binding
artifact and keeps the generated record's sample provenance separate from the
proposed replacement. Ranking does not consume benchmark labels or historical
`existing_mapping` values. Calibration requires grounded note context and a
measured or verified pitch reference; it rejects curated pitch offsets.
Consequently these rows can guide which isolated probes to compare, but cannot
set a sounding note, source transpose, or held-out ranking result.

Keep any future benchmark split by game/platform and keep shared sample hashes
in one fold. Sheet-derived route judgments may be represented as historical
curated labels only after the local subject is bound and the label's scope is
explicit. Do not train/tune route choices on those labels and then report them
as held-out cross-game accuracy.

## Completed local pilot and auditions

The generated index is
`instrument-renders/origin-flute-pilot-20261101/index.html`. It links the two
manual source/GM audition packages and all five documentary binding cards.
The source proof for each audition checks the ROM sample hash against the exact
BRR bytes selected by that capture's SPC sample-directory entry. The physical
SRCN is `$40` in these two probe contexts; it remains separate from global `$07`.

| Capture | Raw S-DSP pitch | Measured sounding MIDI note | Difference from GM C4 |
| --- | --- | --- | --- |
| FF4 Rosa's Theme snapshot, isolated flute voice | 1071 | 59.99070 | -0.930 cents |
| FF6 constructed isolated ROM-sample probe | 1071 | 59.98467 | -1.533 cents |

The raw-4096 captures measured approximately 1000.304 Hz and 1000.255 Hz.
Those acoustic measurements were used to choose a new register value; fresh
captures then established the C4 comparison pitch. Documentary sampled B5 was
not used to relabel the captures or fill in a game-unity key.

The review packages are `review-ff4-c4/` and `review-ff6-c4/` beneath that index.
Both compare with GeneralUser GS program 73 (GM 74, Flute), C4, velocity 96.
They retain original PCM and provide level-matched presentation copies. All audio
is local and manual-play only. Each source/candidate pair is nonzero and unclipped,
with RMS differences below one PCM unit. `validation.json`, the capture-proof
JSON files and the review manifests retain exact artifact hashes and scope.

Validation completed with 170 instrument-analysis tests, 14 live schema checks,
Python compilation and generated JavaScript syntax checks. Tests use synthetic
BRR/ROM layouts and mocks; no game ROM bytes or rendered audio were checked in.
Browser visual inspection remains unavailable because the browser tool blocks
local file URLs. No human choice has been entered and no legacy patch map changed.
