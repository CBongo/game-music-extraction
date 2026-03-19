# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Directory Overview

This directory contains tools for extracting music from the Gradius III arcade game (Konami, 1989). The hardware uses a **Z80 audio CPU** driving a **YM2151 FM synthesizer** and **K007232 PCM chip**.

## ROM Layout

The audio CPU ROM (`rom/945_r05.d9`) is a single 64KB Z80 ROM. The main/sub CPUs use separate interleaved ROMs (even/odd byte pairs). See [rommap.txt](rommap.txt) for the full ROM-to-chip mapping from MAME.

To merge interleaved ROM pairs into a flat binary, use [zippermerge.py](zippermerge.py):
```bash
python zippermerge.py 945_r13.f15 945_r12.e15 maincpu.bin
```

## Music Data Format (YM2151 driver)

### Song Table
- Song voice pointer table: `0x3db6` in the audio ROM
- 10 voices per song, each voice has a 2-byte pointer (little-endian)
- Songs are triggered by commands `0x80`–`0xa3` sent from the main CPU via latch

### Note Encoding (`0x10`–`0xdf`)
- High nibble `>> 4` = duration index into `durtbl` (at ROM offset `0x1839`, 14 entries, 1-based)
- Low nibble = note number (0 = rest, 1–12 = chromatic notes C through B)
- `0x00`–`0x0f` = set octave (low 3 bits = octave 0–7)

### Commands (`0xe0`–`0xff`)
See [latchcmds.txt](latchcmds.txt) for full opcode documentation. Key commands:
- `0xe2` — FM patch change (index into FM register table)
- `0xe5` — Utility duration: accumulates duration from multiple note nibbles for tied notes; variable-length operand
- `0xe8` / `0xe9` — Global / per-voice transpose (bit 7 = sign, bits 6–4 = octaves, bits 3–0 = semitones)
- `0xf0` — Goto (2-byte little-endian dest); used as loop-end or conditional halt
- `0xf1`, `0xf8` — Repeat with jump-on-last (op1=count, op2/3=dest address L/H)
- `0xf2`/`0xf3` — Begin/end repeat block (F2 style)
- `0xf4`/`0xf5` — Begin/end repeat block (F4 style)
- `0xf6`/`0xf7` — Call subroutine / return (op1=repeat count, op2/3=dest L/H)
- `0xff` — Halt voice

### Note-to-MIDI Conversion
```
mnote = 12 * octave + note_nibble + global_transpose + voice_transpose - 2
mnote += 13   # convert YM2151 keycode space to MIDI
```

## Scripts

### [g3arcmus.pl](g3arcmus.pl) — MIDI extractor
Reads the audio ROM and writes MIDI files to `mid/`. Executes repeat/loop/subroutine control flow to produce playable output. Patch maps are commented out (not yet mapped to GM instruments).

```bash
perl g3arcmus.pl           # extract all songs
perl g3arcmus.pl 0x88 0x8c # extract specific songs by command number
```

### [g3arctxt.pl](g3arctxt.pl) — Text disassembler
Same structure as `g3arcmus.pl` but writes human-readable disassembly to `txt/`. Uses a linearizing strategy for control flow (pushes branch targets onto a `@todo` stack) rather than actually simulating loops, to enumerate all code paths without infinite loops.

```bash
perl g3arctxt.pl           # disassemble all songs to txt/
perl g3arctxt.pl 0x88 0x8c # disassemble specific songs
```

### [zippermerge.py](zippermerge.py) — ROM interleave merger
Merges two interleaved ROM files (even/odd byte split) into a single flat binary.

```bash
python zippermerge.py <even_rom> <odd_rom> <output>
```

## Key Differences Between the Two Perl Scripts

- **`g3arcmus.pl`**: Simulates actual game control flow (executes repeats, loops, subroutines). Used for MIDI output.
- **`g3arctxt.pl`**: Linearizes control flow using a `@todo` stack to visit all branch targets without looping. Used for disassembly (avoids infinite loops in output).

## Output Directories

- `mid/` — MIDI output from `g3arcmus.pl`
- `txt/` — Text disassembly output from `g3arctxt.pl`
- `rom/` — Audio CPU ROM file (`945_r05.d9`) must be placed here
