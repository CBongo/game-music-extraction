# RFC Draft: Extension Point for Game-Dependent On-Disc Compression/Encoding (8lo.9)

Design note + recommendation, drafted for vgmtrans maintainer feedback (to be posted
as part of the raw-disc-input initiative, bead 8lo.10). Companion docs:
`vgmtrans-disc-loader-design.md` (pipeline + loader contract),
`psx-akao-disc-formats.md` (byte-level formats).

## Problem

Music data on real game discs is frequently wrapped in game-specific compression or
container formats that must be decoded **before the scanner can even find it**:

| Game | Needs codec? | Needs container walk? |
|---|---|---|
| Chrono Cross | no | no (byte-scan after sector cook) |
| FF9 | no | yes — `FF9.IMG` directory + DB containers |
| FF7 | yes — LZS on field DATs | ISO9660 only |
| FF8 | yes — LZ5-variant | yes — `FF8DISCn.IMG` root dir + PADBUG archives |

PSF sidesteps this because ripping already produced a flat RAM image. Disc input
cannot. And the pattern is a *family* pattern, not one game's quirk — nearly every
PS1-era publisher had a house packer.

vgmtrans today has **no general seam** for this; three ad-hoc precedents exist
(§Prior art). This RFC proposes one.

## Constraints (from the loader contract, 8lo.2)

1. AKAO (or any format) blocks must reach the scanner as contiguous bytes — decode
   happens loader-side or not at all.
2. A loader that extracts anything disables scanning of its parent
   (`Root.cpp:106-111`) — extraction must be complete, so decode failures must be
   loud, not silent skips.
3. Format scanners should stay codec-free: the byte-scan + relative-parse model is
   what makes AkaoScanner work unchanged on anything.
4. Out of scope by design: raw-vs-cooked sector handling. That is a property of the
   *dump*, autodetected per image — never named in game data (see
   `psx-akao-disc-formats.md`).

## Prior art in-tree

- **MAMELoader — the strong precedent.** A JSON game DB describes, per game, rom
  groups with `load_method`, an **`encryption` codec name**, and free-form
  `attributes` (e.g. kabuki keys). The loader assembles bytes, applies the named
  codec (`MAMELoader.cpp:480-493` dispatches `"kabuki"` → `KabukiDecrypter`,
  `"cps3"` → CPS3 decrypt), and enqueues clean VirtFiles. The *data* is fully
  declarative; only the name→implementation dispatch is a hardcoded if/else.
- **TriAcePS1Scanner — the counter-example.** Scans for `"SLZ"` signatures and
  decompresses inline in the scanner. Defensible only because SLZ is
  self-identifying and owned by that format; it couples a codec to a format and
  cannot serve game-level packaging that wraps arbitrary data.
- **CHDLoader** — decode-whole-image → one VirtFile → enqueue. Proof that
  "loader normalizes, scanner scans" is the accepted shape.

## Design distinction: codecs vs containers

The FF9/FF7/FF8 spread shows two orthogonal needs the seam should not conflate:

- **Stream codec**: bytes → bytes (LZS, LZ5, gzip, kabuki). Stateless, reusable
  across games and containers.
- **Container walker**: bytes → N (name, bytes) members (FF9.IMG directory, PADBUG
  archive, ISO9660 itself). Game- or publisher-specific structure; members may
  themselves need a codec.

MAMELoader's rom-group layer is the container analog; its `encryption` field is the
codec analog. The two compose: FF8 = IMG root dir (container) → PADBUG (container)
→ LZ5 (codec) → AKAO bytes.

## Options

**A. Loader applies per-game codecs, emits decoded VirtFiles.**
Scanners stay clean; matches MAMELoader/CHDLoader shape exactly. As literally
implemented in MAMELoader, dispatch is an if/else chain inside the loader — adding
a codec means editing loader code.

**B. Pluggable codec/container provider interface, keyed from the game DB.**
Same runtime shape as A, but name→implementation goes through a small registry
(the `LoaderManager`/`ScannerManager` pattern that already exists), so new codecs
are additive: register implementation + reference it by name in data.

**C. Decode inside the scanner** (TriAcePS1 style).
Rejected: couples game packaging to format code; FF7's LZS wraps field scripts,
walkmeshes, *and* music — it is not an AKAO concern. Also multiplies work per
format instead of per codec.

## Recommendation: A's architecture with B's dispatch ("A+B-lite")

1. **Keep decode entirely loader-side** (A). The disc loader walks containers,
   applies codecs, and enqueues flat VirtFiles; scanners are untouched.
2. **Introduce two small interfaces + a registry** (B-lite), mirroring existing
   manager patterns:
   - `DiscCodec { name(); decode(span) -> bytes }`
   - `ContainerWalker { name(); members(span) -> [(name, tag-hints, bytes-ref)] }`
3. **Declarative game DB entry** (the 8lo.5 config), mirroring MAMELoader's JSON:
   a game entry names which files/paths to visit, which walker/codec (by name) at
   each step, and per-game attributes (offsets, table pointers — e.g. FF7's
   `INSTR.ALL`/`INSTR.DAT` locations). No game logic in C++ beyond the registered
   codecs/walkers themselves.
4. **Initial inventory** (all with reference implementations in GME):
   codecs `lzs` (FF7; `psx/ff7/unlzs.pl`), `ff8-lz5` (+variants);
   walkers `iso9660`, `ff9-img` (+DB containers), `ff8-img-rootdir`, `padbug`.
5. **Failure mode**: a named codec/walker that is missing or fails aborts that
   game entry with a logged error (constraint 2 — never silently emit less).

Upstream-friendliness argument: this is not a new idea, it is MAMELoader's existing
design *generalized one notch* (registry instead of if/else; containers as a named
concept) and made available to disc loaders. CLI/GUI behavior, scanners, matchers,
and existing loaders are untouched.

## Open questions for maintainers

1. Registry location/ownership: alongside `LoaderManager`/`ScannerManager`, or
   internal to the disc loader until a second loader wants it?
2. Game DB format: extend the MAME JSON approach, or per-loader config files? (GME
   will feed either from its YAML corpus.)
3. Appetite for `ContainerWalker` as a first-class concept vs folding container
   walking into per-game loader code initially and extracting the interface when a
   third container appears?
4. Codec implementations: prefer in-tree minimal C++ ports (LZS is ~50 lines) over
   external deps — agreed?

## Deliverable status

- [x] Design note (this doc)
- [x] Recommended option (A+B-lite)
- [ ] Maintainer feedback (8lo.10 — post with the multi-song RFC)
