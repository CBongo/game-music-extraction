# Moved: ghidra-retro-machines

The Ghidra extension that incubated in this directory (prior-art survey, descriptor
schema, C64 machine descriptor) graduated to its own repository at repo-birth on
2026-07-03:

**https://github.com/CBongo/ghidra-retro-machines** (local dev clone: `D:/git/ghidra-retro-machines`)

- `SURVEY.md` → `docs/SURVEY.md`
- `SCHEMA.md` → `docs/SCHEMA.md`
- `descriptors/c64.yaml` → `machines/c64.yaml`
- beads epic `gib.1` (+children) → `grm-1` in that repo's beads

Still tracked in **this** repo's beads: `gib.2` (Ghidra core banked-memory upstream
proposal) and `gib.3` (Ghidra MCP tooling) — plus any future standalone Ghidra
*scripts*, which live in GME per the three-track plan on epic `gib`.
