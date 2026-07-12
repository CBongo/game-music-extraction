# vgmtrans Disc-Loader Design: PSFLoader→AkaoScanner Pipeline & Contract (8lo.2)

Deliverable of bead `8lo.2`. Documents how vgmtrans turns input bytes into AKAO
collections today, the minimal contract a PS1 disc-image loader must satisfy so the
**existing AkaoScanner fires unchanged**, and where AKAO data physically lives on each
target game's disc. Feeds 8lo.3 (disc loader), 8lo.5 (per-game config), 8lo.8
(multi-song RFC), 8lo.9 (compression-seam RFC).

Validated against the fork at `D:/git/vgmtrans`, branch `akao-optional-sample-collection`
@ `96840ecb` (upstream-synced; post `vgmtrans-shell` transition, post PSFMetadataHints #907).

## 1. The pipeline as it exists today

**Root chain** (`src/main/Root.cpp`, `VGMRoot::loadRawFile` :94): for each RawFile, run
every registered `FileLoader`; each loader may extract child files via `enqueue()`,
which recurse through `loadRawFile`. Then, **only if no loader extracted anything**,
run scanners — extension-registered subset if one exists, else all scanners — and each
scanner's format matcher.

> **Contract-critical change since the original draft:** if a loader extracts *any*
> children, the parent gets `setUseScanners(false)` (`Root.cpp:106-111`) and is never
> byte-scanned itself. A disc loader therefore must emit VirtFiles covering **all**
> AKAO-bearing content — partial extraction silently hides whatever it didn't emit.

**PSFLoader** (`src/main/loaders/PSFLoader.cpp`): detects `PSF`, builds a RAM `Image`
by `overlay()`ing the EXE at its header load address plus recursive `_lib`/`_libN`
files, then enqueues one flat `VirtFile` (offset 0 = lowest load address) with the PSF
tag attached. New since #907: it also collects **metadata hints** and attaches an
`IndexedMetadataHintProvider` to the RawFile (see §4).

**AkaoScanner** (`src/main/formats/Akao/AkaoScanner.cpp`):
- `determineVersionFromTag` (:88-105) switches on `file->tag.album` strings; falls back
  to per-block `guessVersion` when unrecognized.
- Byte-scans the whole file for BE `"AKAO"` at every offset; `readShort(off+6)`
  (seq_length) `!= 0` → `AkaoSeq`, `== 0` → `AkaoSampColl`; pairing id at
  `readShort(off+4)`. All parsing is **relative to the block offset** — blocks must be
  contiguous bytes.
- Hard-coded FF7 quirk (:63-65): if `size >= 0x1A8000`, loads the non-AKAO-tagged
  instrument tables from fixed RAM offsets `AkaoInstrDatLocation(0xe0000, 0x156000, 0, 128)`
  (instrAll/instrDat). Exactly the per-game constant GME externalizes to YAML.

**AkaoMatcher** pairs seqs↔instrSets↔sampColls **by id** into `AkaoColl`s; with PR #914
(this branch), a sample collection is no longer mandatory.

**Multi-song is already solved mechanically:** whole-file scan + id-pairing means one
RawFile with N AKAO blocks yields N collections with zero scanner changes. 8lo.8 is
about generality and UX (naming, surfacing N colls), not an AKAO mechanism gap.

## 2. The disc-loader contract

A disc loader (8lo.3) must produce VirtFile(s) such that:

1. **Contiguity** — every AKAO seq/sampcoll block appears as contiguous bytes: sectors
   cooked (2352→2048, subheaders stripped), archives unpacked, game compression decoded.
2. **Version tagging** — set `tag.album` to a recognized string, or accept
   `guessVersion`. Recognized strings for our targets (AkaoScanner.cpp:88-105):
   `Final Fantasy 7` / `Final Fantasy VII`, `Final Fantasy 8` / `Final Fantasy VIII`,
   `Final Fantasy 9` / `Final Fantasy IX`, `Chrono Cross`. (Also recognized, same
   driver family: SaGa Frontier 1/2, Front Mission 2/3, Parasite Eve, Chocobo titles,
   Legend of Mana, Vagrant Story, Racing Lagoon, FF Origins-FF2.)
3. **All-or-nothing extraction** — because extraction disables scanning of the parent,
   emit VirtFiles for *everything* AKAO-bearing (or don't claim the file).
4. **Non-AKAO-tagged data needs explicit pointers** — FF7's instrAll/instrDat tables
   have no signature; today a size-heuristic hack, target state is per-game config
   (8lo.5) supplying what GME's YAMLs already encode.
5. **(Optional, forward-looking) metadata hints** — attach an
   `IndexedMetadataHintProvider` with per-song tags (see §4).

## 3. Where AKAO lives, per game

(Byte-level structure layouts for everything in this table live in the standalone
reference `docs/psx-akao-disc-formats.md`.)

| Game | Container | What the loader must do | Sources |
|---|---|---|---|
| **Chrono Cross** | AKAO blocks directly in the image, spanning sector boundaries | Cook raw sectors if the dump is raw (strip 24-byte header+subheader, keep 0x800) → byte-scan just works | `psx/cc/extractakao.pl`, `readsectors.pl` |
| **FF9** | `FF9.IMG` custom archive, typed directory (type 7 = song, 9 = instrument), cooked sectors | Parse FF9.IMG directory, emit song/instrument entries; plain AKAO byte-scan confirmed working on cooked image | `psx/ff9/extract_akao.pl`, `findakao` |
| **FF8** | `FF8DISCn.IMG` monolith (n=1-4). Root dir at 0h (NTSC) / 2800h (PAL); entries = ISO sector number + byte size, positions relative to ISO start. **Root files 0x1E–0x7F are PADBUG archives, each holding two AKAO files** (0x4B: one AKAO + one TXT). LZ5-variant compression in the archive; fields dir hidden in LZS `FIELD.BIN` (root file 2) — not needed for music | Locate IMG (name match), parse root dir, take entries 0x1E–0x7F, unpack PADBUG containers (+LZ5 where applied), emit AKAO pairs | [nocash FF8 IMG docs](https://problemkaputt.de/psxspx-cdrom-file-archive-ff8-img-final-fantasy-viii.htm), [FFRTT PlaystationMedia](https://wiki.ffrtt.ru/index.php/FF8/PlaystationMedia) |
| **FF7** | AKAO embedded inside LZS-compressed field-script DAT files (ISO9660 FS) + world-map TXZ, located via per-file extraOffset tables; instrument tables non-AKAO-tagged | ISO9660 walk → LZS decompress (`psx/ff7/unlzs.pl`) → offset-table walk → emit blocks; per-game pointers for instrAll/instrDat | `psx/ff7/extract_akao.pl` |

**Revised difficulty order** (FF8 survey moves it *below* FF7 — music lives in dedicated
root archives, not field-embedded):

1. **Chrono Cross** — sector-cook only. First target.
2. **FF9** — archive directory parse over cooked sectors.
3. **FF8** — monolith root-dir parse + PADBUG/LZ5 decode (new decode work, but
   music-dedicated files; no field entanglement).
4. **FF7** — ISO9660 + LZS + embedded offset tables; the case that motivates 8lo.9.

## 4. New upstream mechanism: metadata hints (#907)

`VGMMetadataHint` (`src/main/components/VGMMetadataHint.h`) carries a `VGMTag` targeted
by `(targetFormat, songIndex | romAddress | fileOffset | lookupKey)`;
`IndexedMetadataHintProvider` indexes them and hangs off the RawFile. Today only
PSFLoader populates it and consumers are nascent — but this is the upstream-blessed
channel for exactly what GME's YAML configs contain (per-song titles keyed by
index/address). A disc loader that attaches YAML-derived hints, plus a small PR wiring
AKAO ids to hint lookups, rides the current upstream direction rather than fighting it.
Directly de-risks 8lo.8's naming/UX story.

## 5. CHDLoader: pattern, not parent

`CHDLoader` handles only CHD (`MComprHD` magic): decompress all hunks → one flat
VirtFile → enqueue → signature scan. It parses no filesystem, so it is not a base for
.bin/.cue/.iso — but it is the in-tree template for the Tier-1 "cooked image" path,
and proof that "loader emits one big VirtFile, scanner does the rest" is an accepted
upstream shape.

## 6. Implications for the epic

- **8lo.3 (disc loader): two tiers.** Tier 1 mounts .cue/.bin/.iso and produces a
  logical (cooked) image (CC and FF9-via-byte-scan work immediately; mirrors
  CHDLoader). **Sector format (2352 raw vs 2048 cooked) is a property of the dump,
  not the game — autodetect it per image** (size divisibility, confirmed by
  sync-pattern/PVD probe; see `docs/psx-akao-disc-formats.md`) and keep it out of
  per-game config (8lo.5).
  Tier 2 walks ISO9660 / game archives with per-game decode (FF9.IMG dir; FF8 root dir
  + PADBUG/LZ5; FF7 LZS + extraOffset), emitting per-file VirtFiles. Tier 2 is where
  8lo.9 and 8lo.5 plug in.
- **8lo.9 (compression seam) now has two customers**: FF7 LZS *and* FF8 LZ5/PADBUG —
  strengthens the RFC from "one game's need" to "a family pattern".
- **8lo.8 (multi-song RFC)**: mechanism already works for AKAO; the RFC should focus on
  surfacing/naming N collections, with metadata hints (§4) as the naming channel.
- **8lo.5 (per-game config)**: contract items 2 (album string), 4 (instrument-table
  pointers), and 3's completeness list (which archives to walk) are precisely the
  per-game data — same information GME's YAMLs hold today.
