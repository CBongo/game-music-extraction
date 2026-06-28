# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

## Project Vision & Relationship to vgmtrans

GME (game-music-extraction) extracts and converts video game music from many platforms
(NES, SNES, PSX, C64, arcade) into MIDI, MusicXML, and human-readable disassembly. The
ultimate goal is to automate production of **sheet music scores** so humans can play the
songs together. The main active effort is the AKAO sound format (Square/Enix driver:
FF7/8/9, Chrono Cross, plus SNES titles).

[vgmtrans](https://github.com/vgmtrans/vgmtrans) is a mature C++/Qt tool that parses game
music formats (including AKAO) and exports MIDI/SF2/DLS. GME's relationship with it is
**complementary, not competing**:

- **GME is the oracle/spec, not code to be migrated.** GME's Perl/Python extractors are
  *not* destined to be ported into vgmtrans. Their lasting value is as a *reference
  implementation and documentation of how the game code actually works* — used to improve
  conversion correctness/musicality and to cross-check vgmtrans's parsers for parity gaps.
  An oracle that can say "vgmtrans got this opcode wrong" is worth more than a second engine.

- **Three integration paths** (sequenced, not exclusive; not yet committed to any):
  - **Path A — Upstream the generically-useful pieces** (raw ROM/CD-image input loaders,
    optional-sample-collection loosening, AKAO parser parity fixes, data-driven scanner
    constants). Delivered as upstream PRs; this is how the `8lo` epic is scoped.
  - **Path B — Consume vgmtrans as a library/backend.** GME's sheet-music goal is
    mission-adjacent for vgmtrans (it targets playback/preservation, not notation), so the
    score layer naturally lives as a separate consumer on top of vgmtrans — no need to win
    that argument with the maintainers.
  - **Path C — Hard fork.** Only if we decide to add functionality the maintainers do not
    want. High solo-maintenance cost; a fallback, not a default. Trigger = maintainers
    rejecting the *broadly useful* pieces (Path A), not rejecting score generation.

- **Default: pursue A + B; hold C as a documented fallback.** The cheap experiment that
  decides A-vs-C is engaging the maintainers (bead `8lo.10`) before sinking large effort.

- **Operating rules:** vgmtrans dev happens in the fork clone (`origin` = CBongo/vgmtrans,
  `upstream` = vgmtrans/vgmtrans). Issue tracking (beads) **stays in the GME repo** even for
  vgmtrans work — keeping the fork beads-free keeps PR branches clean for upstream (which
  uses GitHub Issues). The *only* trigger to migrate beads into the fork is a committed Path
  C hard fork. Deliver vgmtrans changes as upstream PRs; record the PR link on the relevant
  bead (e.g. PR #914 ↔ bead `4tn`). Machine-specific paths and agent autonomy settings live
  in local config (`.claude/`), not here.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->


## Build & Test

_Add your build and test commands here_

```bash
# Example:
# npm install
# npm test
```

## Architecture Overview

_Add a brief overview of your project architecture_

## Conventions & Patterns

_Add your project-specific conventions here_
