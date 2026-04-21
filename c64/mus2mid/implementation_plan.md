# Plan: Sidplayer .mus to MIDI Converter

## Context

The `c64/mus2mid/` directory has a [design.md](c64/mus2mid/design.md) specifying a converter from Sidplayer `.mus` files to MIDI. The .mus format is a C64 music format used by Compute's Gazette Sidplayer. Format specs:
- [CBongo fork](https://github.com/CBongo/ComputeSidPlayerC64Source/blob/74c961b0395a5de87fa307346154b7ae3605d16d/notes/musFileFormat.md) (updated with utility duration, triplets)
- [Modland doc](https://modland.com/pub/documents/format_documentation/Sidplayer%20type%20B%20%28.mus%29.txt) (detailed TPS/RTP encoding, dual CAL/DEF patterns)

Primary test file: `M:\Music\c64\CGSC\Harry_Bratt\For_the_Times.mus`

## Approach

Single Perl script `c64/mus2mid/mus2mid.pl` using the `MIDI` module, following patterns from existing converters ([acs2midi.pl](c64/acs/acs2midi.pl), [kikmidi.pl](c64/kik/kikmidi.pl), [rds2midi.pl](c64/rds/rds2midi.pl)).

## Key Design Decisions

### Timing: ticks_per_quarter = 480

The tempo byte T = whole note duration in jiffies (1/60s). BPM = 14400/T (from modland doc: "divided into 14,400"). With ticks_per_quarter=480, note durations in MIDI ticks are **constant regardless of tempo**:

| Duration | Ticks | Dotted | Dbl-Dotted | Triplet |
|----------|-------|--------|------------|---------|
| Whole    | 1920  | 2880   | 3360       | 1280    |
| Half     | 960   | 1440   | 1680       | 640     |
| Quarter  | 480   | 720    | 840        | 320     |
| 8th      | 240   | 360    | 420        | 160     |
| 16th     | 120   | 180    | 210        | 80      |
| 32nd     | 60    | 90     | 105        | 40      |
| 64th     | 30    | 45     | 52*        | 20      |

(*52.5 rounded -- 64th double-dotted is unlikely in practice)

Tempo changes emit `set_tempo = int(T * 125000/3 + 0.5)` on the conductor track.

Utility durations (in jiffies) convert to ticks: `int(jiffies * 1920 / T + 0.5)`.

### Duration Byte Decoding (corrected)

Bits 2-4 of command byte:
- 000 = 64th note
- 001 = **Utility duration** (bit 5 selects: 0=global UTL, 1=per-voice UTV)
- 010 = Whole
- 011 = Half
- 100 = Quarter
- 101 = 8th
- 110 = 16th
- 111 = 32nd

Modifiers (bits 5, 7):
- Bit 5=1, bit 7=0: **dotted** (duration * 3/2)
- Bit 5=1, bit 7=1: **double-dotted** (duration * 7/4)
- Bit 5=0, bit 7=1: **triplet** (duration * 2/3; only valid at certain tempos)
- Bit 6=1: **tie** (extends previous note's duration)

When duration=001 (Utility): the note uses the current UTL or UTV jiffy value as its duration. UTL/UTV commands **set** this value; they don't advance time themselves.

### Note Pitch Mapping

`midi_note = 12 * (octave + 1) + semitone_offset + accidental + transpose`

- Octave: bits 3-5 of option byte, XOR with 7
- Semitone: C=0, D=2, E=4, F=5, G=7, A=9, B=11
- Accidental: 01=sharp(+1), 10=natural(0), 11=flat(-1), 00=double-sharp/double-flat(+2 for GFDC, -2 for ABE)
- Note value 000 = rest (advance time, no MIDI event)

### TPS Encoding (from modland doc)

Not a simple signed byte. Bit 0 = sign (0=positive, 1=negative).
- **Positive**: octaves = 7 - bits[3:1], half-steps = bits[7:4]. Total = octaves*12 + half_steps.
- **Negative**: octaves = bits[3:1], half-steps = 11 - bits[7:4]. Total = -(octaves*12 + half_steps).

### RTP Encoding (from modland doc)

Bits 2-0: octaves = 3 - value. Bits 7-3: half-steps = value - 11. Total = octaves*12 + half_steps. (Can be negative when bits exceed the offset.)

### Repeats: HED/TAL (NOT nested)

Per the Sidplayer book: repeats **cannot be nested**. Each HED must be followed by TAL before another HED. Implementation uses a single per-voice `$repeat_pos` and `$repeat_count` (not a stack).

- HED: record position and count. Count 0 = infinite (cap at 2 with a warning).
- TAL without prior HED: repeat from voice start (also warn).
- TAL: decrement count; if >0, jump back. If 0, continue.

### Phrases: DEF/END/CALL (shared, executed on definition)

Key behaviors from the Sidplayer book:
1. **DEF executes on first encounter** -- notes/commands between DEF and END are both recorded and played.
2. **CALL replays** the recorded phrase.
3. **Phrases are shared across all 3 voices** -- a phrase defined in voice 1 can be called from voice 2 or 3.
4. **Nesting**: up to 5 levels of CALL nesting per voice. DEF inside a phrase counts as a nesting level.
5. **Undefined phrase CALL** should warn (Sidplayer errors with "UNDEFINED PHRASE CALL").

Implementation:
- Global `@phrases` array (indices 0-23), each value = byte offset of the first command after DEF in the combined voice data. Since voice data blocks don't overlap, a single offset suffices.
- Per-voice `@call_stack` (max depth 5) of return offsets.
- During DEF: record the offset of the next command as `$phrases[$n]`, set `$defining` flag, but **continue executing** commands normally.
- At END: if defining, clear the flag. If in a CALL, pop call stack and return.
- At CALL: warn if undefined. Push current position, jump to `$phrases[$n]`.

When calling a phrase defined in another voice: utility-voice durations use the **calling** voice's UTV, not the defining voice's.

### Two CAL/DEF Byte Patterns (from modland doc)

Each of CAL and DEF has two encodings to cover phrases 0-23:
- **CAL 0-15**: option byte `nnnn 0010` (low nibble 0x02), phrase = bits 7-4
- **CAL 16-23**: option byte `1nnn 1011` (low nibble 0x0B, bit 7 set), phrase = bits 7-4 + 8
- **DEF 0-15**: option byte `nnnn 0110` (low nibble 0x06), phrase = bits 7-4
- **DEF 16-23**: option byte `1nnn 0011` (low nibble 0x03, bit 7 set), phrase = bits 7-4 + 8

The modland doc's "8 less than the true value" means the encoded nibble value + 8 = actual phrase number (e.g., encoded 8 = phrase 16, encoded 15 = phrase 23).

### Volume: MIDI CC7 (Master Volume)

SID volume (0-15) is a global master volume, not per-note. Map to MIDI CC7 (channel volume) on all channels. SID volume is roughly linear; MIDI CC7 is also approximately linear in most implementations. Simple mapping: `cc7_value = vol * 8` (0-120 range) or `int(vol * 127 / 15)` for full range.

Emit CC7 on all active channels when VOL changes. BMP (bump +1/-1) adjusts the current volume.

### Future: Noise Waveform to Channel 10

Not implemented in first version, but WAV=0 (noise) should eventually map to MIDI percussion on channel 10 (9 zero-based).

## Implementation Steps

### Step 1: Script skeleton and argument parsing
- `#!/usr/bin/perl`, `use MIDI;`
- Parse `--quiet`/`-q` from @ARGV; remaining args = .mus filenames
- `sub vprintf { printf STDERR @_ unless $quiet }` 
- Main loop: for each file, parse -> process -> write MIDI
- Output filename: replace `.mus`/`.MUS` with `.mid`

### Step 2: File parsing
- Slurp file, unpack 2-byte load address + three 16-bit LE voice sizes
- Compute voice data offsets (starting at byte 8)
- Extract voice data blocks, verify HLT ($01 $4F) terminators
- Extract NULL-terminated description text (PETSCII: convert $0D to newlines)
- Log header info and description in verbose mode

### Step 3: Command decoder
Return a hash with `{type, ...params}` for each 2-byte pair.

**When bits 0-1 = 00 (note):**
- Decode duration (bits 2-4), dotted/triplet (bits 5,7), tie (bit 6)
- Decode note/rest, octave, accidental from option byte
- Special case: duration=001 → utility duration (bit 5 selects UTL vs UTV)

**When cmd byte = 0x01 (SID/structural commands):**
- Match option byte low nibble + specific bit patterns per the command table
- Handle dual encodings for CAL (n2 and nB) and DEF (n6 and n3)

**When bits 0-1 = 10 (register commands):**
- Match full command byte: 0x06=TEM, 0x16=UTL, 0x36=HED, 0xA6=TPS, 0x2E=RTP, 0xF6=UTV, etc.
- Decode TPS/RTP using their special signed encodings

**When bits 0-1 = 11 (portamento):**
- 14-bit value from cmd bits 7-2 + option byte

### Step 4: Voice processing main loop
For each voice 0-2:
- Init state: totaltime=0, transpose=0, velocity=100, utl_duration=0, utv_duration=0
- Walk voice data 2 bytes at a time, decode, dispatch
- Build score-format events into `@{$e[$v]}`

Process all 3 voices to completion before writing MIDI (phrases are shared, but since DEF records byte ranges, we can replay from any voice's data).

### Step 5: Note emission
- Compute MIDI pitch (handle rest → no note event, just advance time)
- Compute duration in MIDI ticks (constant values from table, or utility duration converted from jiffies)
- Apply dotted (*3/2), double-dotted (*7/4), triplet (*2/3)
- **Tie**: search backward for last note on this voice, extend duration (established pattern)
- Push `['note', $totaltime, $dur, $channel, $pitch, $velocity]`
- Advance totaltime by full duration (including dots/triplets)

### Step 6: Phrase system
- Global `@phrases` array (0-23), value = byte offset of first command after DEF
- Per-voice `@call_stack` (return offsets), max depth 5
- DEF: record start, set `$defining = phrase_num`, **continue executing** normally
- END: if `$defining >= 0`, record end boundary, clear. If in CALL, pop stack and return.
- CALL: warn if undefined. Push return position. Jump to phrase's byte range. When calling cross-voice, use the calling voice's UTV.

### Step 7: Repeat system (flat, not nested)
- Per-voice: `$repeat_pos`, `$repeat_count`
- HED: `$repeat_pos = next_cmd_offset; $repeat_count = opt` (0=infinite → cap at 2, emit warning)
- TAL: if `$repeat_count > 0`, decrement and jump to `$repeat_pos`. If no HED seen, jump to voice start and warn.
- After TAL completes (count exhausted), clear repeat state

### Step 8: Tempo and JIF
- Default tempo: $90 (MM 100)
- TEM: update `$current_tempo`, emit `['set_tempo', $totaltime, int($tempo * 125000/3 + 0.5)]` on conductor track
- For T=0, use T=256
- JIF: alters the effective jiffy length by performing a 16-bit add of the JIF value to the base timer value ($4295 for NTSC, $4025 for PAL). The JIF value's high byte is the option byte and low byte is the top 2 bits of the command byte. The resulting timer value changes the interrupt rate. Math check: both platforms give ~16,666 usec/jiffy at default (60 Hz), and JIF offsets produce <0.2% difference between NTSC/PAL -- so platform choice is irrelevant. Use NTSC values: `new_timer = 0x4295 + jif_value`, `usec_per_jiffy = new_timer * 1000000 / 1022727`, then re-emit `set_tempo = int(usec_per_jiffy * current_tempo / 4)`.

### Step 9: Volume
- VOL: set `$volume` (0-15), emit CC7 on all channels: `['control_change', $totaltime, $ch, 7, int($vol * 127/15)]`
- BMP: increment/decrement volume by 1, clamp 0-15, emit CC7
- Note velocity stays constant (e.g., 100)

### Step 10: Transpose
- TPS: decode per modland doc's signed format, set `$transpose` for current voice
- RTP: decode per modland doc, add to `$transpose` 

### Step 11: MIDI output
```perl
my $opus = MIDI::Opus->new({format => 1, ticks => 480});
# Conductor track: time_signature, set_tempo events
# Voice tracks: score events → MIDI::Score::score_r_to_events_r()
$opus->write_to_file($outfile);
```

### Step 12: SID-specific commands (log only)
WAV, ATK, DCY, SUS, REL, PNT, HLD, PW, P-S, PVD, PVR, SNC, RNG, POR, VDP, VRT, DTN, F-M, AUT, RES, FLT, F-C, F-S, F-X, LFO, RUP, RDN, SRC, DST, SCA, MAX, JIF, FLG, AUX, BMP, 3-O -- log command name + value in verbose mode, no MIDI output.

## Commands: Implement vs. Log-Only

**Implement (affect MIDI):** NOTE, TEM, JIF, UTL, UTV, VOL, BMP, TPS, RTP, HED, TAL, CALL, DEF, END, HLT, MS# (log measure number)

**Log only:** Everything else (SID register writes with no MIDI equivalent)

## Files

- **Create:** [mus2mid.pl](c64/mus2mid/mus2mid.pl)
- **Reference:** [acs2midi.pl](c64/acs/acs2midi.pl), [kikmidi.pl](c64/kik/kikmidi.pl), [rds2midi.pl](c64/rds/rds2midi.pl), [ctmus.pl](snes/ct/ctmus.pl)

## Verification

1. `perl mus2mid.pl "M:\Music\c64\CGSC\Harry_Bratt\For_the_Times.mus"`
2. Verbose output: header parsed, description displayed, all 3 voices processed, HLT reached for each
3. `.mid` file created, non-empty, opens in MIDI player
4. Correct tempo (~128 BPM if default tempo is $70)
5. Reasonable note pitches (not all clustered at extremes)
6. Phrases and repeats terminate (no infinite loops)
7. `--quiet` suppresses non-error output
8. Test additional CGSC files for robustness
