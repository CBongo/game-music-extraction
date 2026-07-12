# PSX AKAO Games: On-Disc File Structure Reference

Standalone reference for the disc/archive formats of the PSX AKAO-driver games GME
targets. Distilled from this repo's legacy extraction tools (which encode decades of
reverse engineering) plus external documentation; structure layouts below are the
formats as actually consumed by working extractors, not speculation. Companion to
`docs/vgmtrans-disc-loader-design.md` (which covers the loader *contract*; this doc
covers the *formats*).

All multi-byte integers are little-endian unless noted. "Cooked sector" = 2048 (0x800)
data bytes; "raw sector" = 2352 (0x930) bytes as stored in .bin images.

## Sector format: an image property, not a game property

Whether an image has raw (2352-byte) or cooked (2048-byte) sectors depends on how the
disc was dumped (.bin/.img vs .iso), **not on the game** — the same title can arrive
either way. Loaders must autodetect per image and never bake a sector size into a
game description:

- **Size divisibility**: `size % 2352 == 0` vs `size % 2048 == 0`. Sufficient alone
  only when exactly one divides; collisions occur (gcd is 16), so confirm with:
- **Sync pattern**: raw sectors start with the 12-byte sync
  `00 FF FF FF FF FF FF FF FF FF FF 00` at offset 0 (and every 2352 bytes).
- **ISO9660 PVD probe**: `"CD001"` at sector 16 — offset 0x8001 in a cooked image,
  0x9330+0x19 in a raw one.
- Rarer variants exist (2336-byte Mode 2 sans sync/header dumps; audio-track-bearing
  cue sheets) — detect-and-reject with a clear message rather than misparse.

### Raw-sector cooking (Mode 2 Form 1)

Raw 2352-byte sector layout: 12 sync + 4 header + 8 subheader = **24 bytes to skip**,
then 0x800 data bytes (rest is EDC/ECC). Multi-sector payloads are the concatenation
of each sector's 0x800 data bytes.

Reference implementation: `psx/cc/extractakao.pl` (`substr($buf, 24, 0x800)` per
sector), `psx/*/readsectors.pl`.

## AKAO block header (common)

Found by scanning for big-endian magic `"AKAO"` (0x414B414F):

| Offset | Size | Field |
|---|---|---|
| +0 | 4 | `"AKAO"` |
| +4 | 2 | id (pairs sequence ↔ instrument set ↔ sample collection) |
| +6 | 2 | sequence length; **0 ⇒ block is a sample collection**, ≠0 ⇒ sequence |

In FF7 field files the stored length **excludes the 0x10-byte header** — total block
size = len + 0x10 (`psx/ff7/extract_akao.pl:40`). All internal pointers are relative
to the block start, so a block extracted as contiguous bytes parses standalone.

## Chrono Cross

- No archive, no compression: AKAO blocks live directly in the disc image, but they
  **span sector boundaries**, so raw dumps only yield contiguous blocks after cooking
  (cooked dumps are scan-ready as-is).
- Extraction = cook sectors → byte-scan for `"AKAO"`. The simplest target.
- Tool: `psx/cc/extractakao.pl` (sector lists), `psx/cc/findakao.pl` (scan).

## FF9 — `FF9.IMG` archive

Single archive file in the ISO filesystem; 0x800-byte sectors; all offsets in sectors.

**Header** (`psx/ff9/extract_akao.pl:20`):

| Offset | Size | Field |
|---|---|---|
| 0x0 | 4 | signature (4 ASCII bytes) |
| 0x4 | 4 | (skipped) |
| 0x8 | 4 | directory count (u32) |

**Directory entries** (16 bytes each, u32×4): `(type, nfiles, dir_sector,
first_file_sector)`. Type 4 = end-of-directory marker. Types: 2 = normal,
3 = hierarchic (music extraction skips type 3).

**File entries** within a directory (8 bytes each): `(id u16, type u16, sector u32)`;
id 0xFFFF terminates the list. File length = next entry's sector − this one's
(extractor computes from sector deltas).

**DB containers**: files beginning with byte 0xDB hold a typed object directory whose
type codes include **7 = Song data (AKAO sequence)** and **9 = Instrument data**;
also 2 = 3D model, 3 = 3D anim, 4 = TIM image, 5 = script, 10/11 = field tiles/walkmesh,
12 = battle scenes, 18 = CLUT/TPage (`psx/ff9/extract_akao.pl:11-14,110+`).

- A plain `"AKAO"` byte-scan over the cooked image also finds the blocks
  (`psx/ff9/findakao` confirmed) — the directory parse adds names/typing, not
  discoverability.

## FF8 — `FF8DISCn.IMG` monolith (n = 1..4)

Nearly all game data hides in one huge file; from
[nocash PSXSPX](https://problemkaputt.de/psxspx-cdrom-file-archive-ff8-img-final-fantasy-viii.htm)
and [FFRTT PlaystationMedia](https://wiki.ffrtt.ru/index.php/FF8/PlaystationMedia)
(not yet validated by a GME tool — no `psx/ff8/` exists yet):

- **Root directory**: at IMG offset 0 (NTSC) or 0x2800 (PAL); detect by whether the
  first entries look like `000003xx`-range sector numbers. Entries are 8 bytes:
  `(ISO sector number u32 — origin 00:02:00, i.e. relative to ISO start, NOT to the
  IMG file — filesize in bytes u32)`. Unsorted; zero-padded to a 0x800 boundary.
- **Music: root files 0x1E–0x7F are "PADBUG" archives, each containing two AKAO
  files** (sequence + instrument/sample data). Exception: file 0x4B holds one AKAO +
  one TXT.
- Compression: LZ5 and LZ5-variants used within the archive (which root files are
  compressed needs verification during implementation); GZIP reportedly appears too.
- **Fields directory** (not needed for music): root file 0x0002 is LZS-compressed
  `FIELD.BIN` (~190KB decompressed) containing the field-file directory in the same
  entry format; movies live outside both directories at the end of the IMG.

## FF7 — field-script DAT files + world map

Music is *embedded* in other game files rather than stored standalone:

- **Field DAT files** (ISO9660 filesystem, one per field location): **LZS-compressed**
  (`psx/ff7/unlzs.pl` is the reference decoder). After decompression
  (`psx/ff7/extract_akao.pl:10-40`):

  | Offset | Size | Field |
  |---|---|---|
  | 0x0 | 4 | nExtraOffsets (u32) |
  | 0x4 | 4×n | extraOffsets table (u32 each, absolute within decompressed file) |

  Each extraOffset may point at an `"AKAO"` block (check magic; id at +4, len at +6,
  total = len + 0x10). Non-AKAO entries occur — validate the magic per offset.
- **World map**: AKAO inside `.TXZ` container(s), same offset-table idea.
- **Instrument/sample tables** (`INSTR.ALL`/`INSTR.DAT` images in RAM): **not
  AKAO-tagged** — no signature to scan for. vgmtrans hard-codes RAM offsets
  0xE0000/0x156000 for PSF images ≥ 0x1A8000; a disc loader needs explicit per-game
  pointers (they are ordinary files in the ISO filesystem: `INSTR.ALL`, `INSTR.DAT`).
- Tools: `psx/ff7/extract_akao.pl`, `unlzs.pl`, `dump_iso_dir.pl`,
  `dump_path_table.pl`, `readsectors.pl`.

## ISO9660 basics used above

FF7 (and FF8's outer shell) use standard ISO9660: path table + directory records over
cooked sectors. GME reference implementations: `psx/ff7/dump_iso_dir.pl`,
`psx/ff7/dump_path_table.pl`, `psx/cc/listfat.pl` (CC's variant). Sector numbers have
origin 00:02:00 (LBA 0 = MM:SS:FF 00:02:00 — the 150-sector pregap).

## Difficulty ladder (for loader work)

1. **CC** — cook sectors, scan. No archive, no compression.
2. **FF9** — FF9.IMG directory parse (structures above), cooked sectors.
3. **FF8** — IMG root-dir parse + PADBUG/LZ5 decode (formats documented externally,
   unvalidated here — budget verification time).
4. **FF7** — ISO9660 walk + LZS decompress + extraOffset tables + non-tagged
   instrument files.
