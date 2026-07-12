# RFC Draft: One Game Image → Multiple VGMColls (Multi-Song Architecture) (8lo.8)

Design note + recommendation, drafted for vgmtrans maintainer feedback (to be posted
with the codec-seam RFC as part of the raw-disc-input initiative, bead 8lo.10).
Companion docs: `vgmtrans-disc-loader-design.md` (pipeline + loader contract),
`psx-akao-disc-formats.md` (byte-level formats), `rfc-vgmtrans-disc-codec-seam.md`
(the orthogonal decode-seam RFC).

Validated against the fork at `D:/git/vgmtrans`, branch
`akao-optional-sample-collection` @ `96840ecb` (upstream-synced; post PSFMetadataHints
#907).

## Problem

vgmtrans's canonical input is one ripped file = one song (a PSF holds a single
sequence + its samples). A disc or ROM holds **many** songs. Raw-disc input (bead
8lo.3/8lo.4) means one loaded image must surface as **N playable collections**.

The good news, and the reason this RFC is short: **the multi-collection *mechanism*
already exists and already works for AKAO.** What is missing is not plumbing but two
things a human actually notices — **naming** (the N collections are indistinguishable
soup) and **surfacing** (N flat entries with no tie back to the disc they came from).
This RFC asks maintainers to bless a naming channel and pick a surfacing shape *before*
the disc loaders land, so they land with usable UX rather than needing a follow-up
rework.

## What already works: the mechanism

One whole-file scan yields N collections with **zero** changes to scanner, matcher, or
Root:

1. `AkaoScanner` byte-scans the entire RawFile for the BE `"AKAO"` signature at every
   offset, emitting an `AkaoSeq` / `AkaoSampColl` per block, plus `AkaoInstrSet`s
   (`AkaoScanner.cpp`). N blocks in one file → N format objects.
2. `AkaoMatcher::onFinishedScan` (`AkaoMatcher.cpp:15`) iterates **every** scanned seq
   id and calls `tryCreateCollection(seq->seq_id)` for each — pairing seq ↔ instrset ↔
   sampcoll by id into one `AkaoColl` apiece (`:90-187`). N ids → N collections.
3. `VGMRoot::loadVGMColl` → `sinkVGMColl` (`Root.cpp:260-278`) loads each and calls
   `UI_addVGMColl`.

This is the same shape `FilegroupMatcher` / `GetIdMatcher` / `SimpleMatcher` give other
multi-song formats, and the same shape `KonamiArcadeScanner` uses to emit many
sequences from one MAME romset. **8lo.8 is therefore not a mechanism-gap bead** — the
disc loader (8lo.3) that hands the scanner a cooked, contiguous image gets N collections
for free. The remaining work is entirely UX.

## Gap 1 — Naming (concrete, and already solved for other formats)

Today every AKAO collection is named by its pairing id and nothing else:

- `AkaoScanner.cpp:35` — `name = fmt::format("Akao Seq {:02X}", id)`
- `AkaoMatcher.cpp:167` — `coll->setName(seq->name())`

So a loaded FF9 disc surfaces ~160 collections literally labelled *Akao Seq 00, Akao Seq
01, …* — technically distinct, musically meaningless. For a PSF this never mattered
(one file, one song, and the ripper embedded a title tag); for a disc it is the
difference between a usable tree and a wall of hex.

**The fix already exists in-tree and is upstream-blessed.** PSFMetadataHints (#907)
added `IndexedMetadataHintProvider`, a per-RawFile store of `VGMMetadataHint`s
(a `VGMTag` keyed by `targetFormat` + one of `songIndex | romAddress | fileOffset |
lookupKey`). Crucially, **consumers already exist beyond PSFLoader**: `NDSScanner` and
`MP2kScanner` read it. The NDS pattern is exactly what AKAO wants:

```cpp
// NDSScanner.cpp — findNDSMetadataHint(): try lookupKey, then songIndex, then fileOffset
// getNDSSeqName():
const auto* hint = findNDSMetadataHint(file, seqIndex, seqName, seqOffset);
if (hint && hint->tag.hasTitle())
    return hint->tag.title;      // human title when a hint is present…
return seqName;                  // …else fall back to the generated name
```

AKAO consumes **no** hints today (`grep` confirms none in `formats/Akao/`). Wiring it in
is a small, mechanical mirror of NDSScanner: query the provider by AKAO id (as
`songIndex` or `lookupKey`) or by block `fileOffset`, and use `tag.title` when present.
**GME's `akao/*.yaml` corpus is the ready-made source of those titles** — the per-song
names keyed by id that GME has curated for years.

## Gap 2 — Surfacing N collections (the genuine open question)

`sinkVGMColl` pushes every collection into one flat `m_vgmcolls` and calls the pure-
virtual `UI_addVGMColl` (`Root.h:110`). There is no representation of "these N
collections all came from `FF9.IMG`." For a handful of PSFs that is fine; for a disc
producing 100+ collections it is the whole UX problem. This is a maintainer-taste call,
not a mechanism gap — hence an open question rather than a proposal (options in
"Open questions" below). Note that even a hint-supplied title (`{disc} — {song}` style)
substantially mitigates a flat list, so **Gap 1's fix partially covers Gap 2** and can
ship first.

## Responsibility split (loader vs scanner vs matcher)

This is open question (1) from the bead. The recommendation follows directly from the
loader contract (8lo.2) and keeps each layer doing only what it already does:

| Layer | Responsibility for multi-song | Change needed |
|---|---|---|
| **Disc loader** (8lo.3) | Normalize disc → contiguous AKAO bytes; **attach an `IndexedMetadataHintProvider`** built from the per-game song table (id/offset → title). | New loader; hint attachment mirrors PSFLoader. |
| **Scanner** (`AkaoScanner`) | Find blocks by signature — *unchanged as the block finder*. Optionally read hints for the seq name (mirror NDSScanner), or defer naming to the matcher. | Small, additive, or none. |
| **Matcher** (`AkaoMatcher`) | Pair by id → N colls — *mechanism unchanged*. Set the coll name from the hint title when present instead of the raw seq name. | One-line change at `:167`. |
| **Root / UI** | Surface N colls (Gap 2). | Design-dependent (see open questions). |

The load-bearing principle, consistent with the codec-seam RFC: **all game-specific
data (song titles, block locations) enters as declarative loader-side data (hints +
game DB); the scanner and matcher stay game-agnostic.** No per-game C++.

## Per-game disc-definition record (shape)

This is open question (3), and it is the **same record** the codec-seam RFC (8lo.9) and
per-game-config bead (8lo.5) already need — they should share one game-DB entry, not
invent three. A single entry per game carries:

- container/codec steps (8lo.9's concern: which walker/codec by name at each path),
- non-signature pointers (8lo.2's FF7 `INSTR.ALL`/`INSTR.DAT` locations, version/album
  string), and
- **the song table this RFC needs: `id → title` (optionally `offset → title`)**, which
  becomes the metadata hints.

Precedent for the format: MAMELoader's JSON game DB already carries per-game rom groups
+ attributes + a codec name; the AKAO entry extends that idea. GME will populate it from
its YAML corpus regardless of the on-disk format chosen (JSON, per-loader config, etc.).

## Recommendation

1. **Name via hints, mirroring NDSScanner** (Gap 1). Disc loader attaches a hint
   provider from the game-DB song table; AKAO reads `tag.title` with fallback to the
   existing `"Akao Seq {id:02X}"`. Rides the #907 direction; no new concepts.
2. **Keep the existing multi-coll mechanism** — no matcher/scanner mechanism changes;
   only the name source changes.
3. **Share one game-DB record** across 8lo.5/8lo.8/8lo.9 rather than three.
4. **Defer the UI grouping decision to maintainers** (Gap 2), and ship the hint-based
   naming first since it stands alone and de-risks the flat-list problem on its own.

Upstream-friendliness argument: the naming path is *literally NDSScanner's already-
merged pattern* applied to a second format, fed through the *already-merged* #907
channel. Mechanism, matchers, Root, and other loaders are untouched.

## Open questions for maintainers

1. **Responsibility split** — is loader-attaches-hints / scanner-finds-blocks /
   matcher-names-from-hint the split you want, or would you rather the scanner own the
   hint lookup (as NDSScanner does) and the matcher stay naming-agnostic?
2. **Surfacing N collections in the UI/Root** — acceptable options, roughly in
   increasing effort:
   - (a) **Flat list, better names** — hint-supplied `{disc} — {song}` titles into the
     existing flat `m_vgmcolls`. Zero Root/UI structural change; ships now.
   - (b) **Group node per source file** — a disc/IMG appears as a parent with its N
     collections as children. Needs a Root/UI grouping concept (does one exist we
     should reuse?).
   - (c) **Collection-of-collections** — model the disc as a first-class container.
     Largest change; probably overkill.
   Is (a)-now / (b)-later acceptable, and does any grouping primitive already exist?
3. **Hint key for AKAO** — key song titles by AKAO pairing `id` (`songIndex`/`lookupKey`),
   by block `fileOffset`, or support both (NDSScanner supports all three)? Id is
   stable across dumps; offset is unambiguous within one image.
4. **Game-DB record ownership** — one shared per-game record across the disc-loader
   RFCs (codec/container steps + pointers + song table), or separate config surfaces?
   (GME feeds any of them from its YAML corpus.)

## Deliverable status

- [x] Design note (this doc)
- [x] Recommendation (hint-based naming, mirror NDSScanner; keep mechanism; defer UI
      grouping)
- [ ] Maintainer feedback (8lo.10 — post with the codec-seam RFC)
