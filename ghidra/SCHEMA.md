# System Descriptor Schema — Design Notes (draft 1)

*Deliverable of bead `gib.1.2` (2026-07-03). Companion worked example:
[`descriptors/c64.yaml`](descriptors/c64.yaml). Schema is explicitly **evolving** —
`schema:` version field gates breaking changes; C64 is instance #1, NES is the stress
test that will force revisions.*

## Purpose

One YAML descriptor per system drives the loader with **zero hard-coded system knowledge
in Java**. The descriptor answers: what exists at every address, which addresses are
banked and by what mechanism, what the hardware registers are called and shaped like, and
where the symbol/type data came from.

## Design principles

1. **Data, not code — except mechanisms.** Regions, bank states, symbols, types, ROM
   slots are pure data. Bank-switch *mechanisms* are code-shaped: the descriptor selects
   a named strategy (implemented in the extension) and parameterizes it. This mirrors
   emulator architecture (mapper classes keyed by iNES number) — deliberate, per the
   emulators-as-oracle principle.
2. **Mechanism + initial state.** Per the `gib.1.2` design decision: the mechanism
   defines the state space and transitions; the initial state picks the start point.
   Container formats may override initial state (`.crt` EXROM/GAME bytes, iNES headers).
3. **Provenance on all imported knowledge.** Every symbol/type source records where it
   came from and its license. Prevents the c64_ghidra trap (great data, no license).
4. **Copyright-safe ROM slots.** System ROMs are declared as *slots* (size, location,
   known checksums) that users fill with their own dumps; symbols and entry points apply
   even when the slot is empty (uninitialized block + labels).
5. **The descriptor describes hardware truth; the loader decides representation.**
   E.g. mirrors are declared as facts (`repeat_to`); whether the loader models them as
   repeated structs, aliased blocks, or comments is loader policy, not descriptor data.

## Top-level shape

```yaml
schema: 1            # descriptor-schema version
system: {...}        # identity + CPU/language binding
memory:              # the address map
  regions: [...]     #   always-visible ranges
  windows: [...]     #   banked ranges with candidate occupants
banking: {...}       # mechanism + state space + initial state
rom_images: {...}    # copyright-safe slots for system ROMs
symbols: [...]       # label/entry-point sources with provenance
types: [...]         # register-struct sources with provenance
formats: {...}       # file formats the loader accepts for this system
validation: {...}    # emulator-oracle cross-check metadata
```

## Section semantics → Ghidra mapping

| Descriptor construct | Ghidra realization |
|---|---|
| `memory.regions[]` | one `MemoryBlock` each (initialized for loaded file content, uninitialized otherwise) |
| `memory.windows[].occupants[]` | one **overlay** `MemoryBlock` per occupant in the window's range; the state-selected default occupant may be the non-overlay "home" block |
| `banking.states[]` | values of the bank **context register** (lives in the bundled processor language, TMode-style); analyzer sets it flow-wise on mechanism writes |
| `rom_images` | uninitialized block + applied symbols by default; initialized from user-supplied file via loader option or File → Add To Program |
| `symbols[]` | labels; `kind: entry` additionally creates a function + external entry point (works on empty ROM slots) |
| `types[]` | `DataTypeManager` structs/enums; applied at declared addresses; `repeat_to` applies at each mirror |
| `formats` | `Loader` opinion + header parsing + placement rule |

## Banked windows and states

A **window** is an address range with multiple candidate **occupants** (RAM under ROM,
ROM, IO...). A **state** assigns one occupant to each window. Two ways to define the
state space:

- **`banking.states` (enumerated table)** — viable when the state space is small. C64
  has 8 PLA combinations of LORAM/HIRAM/CHAREN → 8 rows. This is pure data and trivially
  cross-checkable against the c64-wiki table and VICE.
- **Mechanism-computed** — NES mappers have state spaces far too large to enumerate
  (MMC1: shift-register loads select among dozens of banks per window independently).
  There the strategy code computes window→occupant from mechanism state, and
  `banking.states` is omitted. The schema allows either; C64 deliberately exercises the
  simple enumerated path first.

Initial state comes from `banking.initial_state`, overridable per-format (e.g. a future
`.crt` format entry derives EXROM/GAME from header bytes 0x18/0x19).

## Symbols: sets and provenance

Symbol sources are named sets the user can toggle at import (music-driver RE usually
wants KERNAL entry points but *not* BASIC zero-page variables — games reuse that RAM).
Each source carries `provenance` (upstream project, license, generation date). Bulk sets
are generated files (from mist64/c64ref via an adapted generator — clean license); small
critical sets (KERNAL jump table) may be inline in the descriptor.

`kind: entry` symbols (e.g. `CHROUT` at `$FFD2`) become functions with entry points even
when the KERNAL ROM slot is empty — calls from game code then resolve to named stubs
instead of dangling into the void.

## Scaling preview (what will force schema revisions)

- **NES**: mechanism-computed states (above); CHR/PPU address space is a *second* bus —
  schema will need multi-space support (`memory.spaces`?). Mapper registry = many
  mechanism parameterizations sharing strategies.
- **SNES**: mirroring at scale (LoROM mirrors across dozens of banks) — `repeat_to` may
  need a stride/pattern form. 65816 already has banked addressing in the language.
- **PS1**: no banking, but KSEG mirrors and a BIOS slot; mostly exercises `rom_images` +
  `symbols`.

## Open questions (for review)

1. **Struct source of truth**: inline YAML structs (current draft) vs generating from
   c64ref vs shipping Ghidra `.gdt` archives. Draft leans: YAML canonical (diffable,
   migrates with repo), loader builds `DataType`s at load time; KickAssembler's built-in
   structs used as offset cross-check.
2. **Symbol interchange**: draft uses YAML with provenance as canonical, with a VICE
   `.sym` importer as a convenience (de-facto community format). Alternative: `.sym`
   files as canonical. YAML preferred for provenance + `kind`/`comment` fields `.sym`
   can't carry.
3. **Where the bank context register is declared**: the bundled 6510 language must define
   it (Sleigh context field), and the descriptor references it by name
   (`banking.context_register`). Language and descriptor must agree — build-time check?

## Validation (emulator oracle)

- Bank state table cross-checked against VICE: monitor `bank` command names and the
  `$01` semantics in VICE source (`c64mem.c` / `c64pla.c`).
- IO struct offsets cross-checked against KickAssembler built-ins and mist64/c64ref
  `src/c64io/`.
- Future automated check: run a test PRG in VICE, dump memory/bank state at breakpoints,
  compare against what the static model predicts (`gib.1.3` acceptance material).
