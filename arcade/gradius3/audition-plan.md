# Implementation Plan: `g3_audition.lua` — Gradius III Instrument Audition Mode

## Context

To determine appropriate GM patch mappings and improve percussion rendering accuracy for the Gradius III arcade MIDI extractor, we need a way to audition the game's FM (YM2151) and sampled (K007232) instruments interactively. This will be a new MAME Lua script that directly programs the sound chips, bypassing the Z80 audio driver entirely.

## Approach

Create a new file `g3_audition.lua` following the same architecture as `g3_soundtest.lua`. The script halts the main CPU, waits for the Z80 to finish startup (PC reaches 0x0615), then lets the Z80 run idle while we program the sound chips directly from Lua. Factor out common code shared with the other Lua scripts where it makes sense.

---

## Shared Code: Factor out a common module

Extract shared constants and helpers into `g3_common.lua` (loaded via `dofile` or `require`):
- Hardware address constants (YM2151 ports, K7232 base/bank, main CPU latch/IRQ)
- Color constants
- `ym_write(reg, data)` helper
- Main CPU halt patch (`write_direct_u16(0x2f0, 0x60F6)`)
- Event name table (used by soundtest, potentially useful for audition reference)

Update `g3_soundtest.lua` and `g3_eventlog.lua` to import from `g3_common.lua` instead of duplicating these constants.

**Naming change**: Rename `K232_*` prefix to `K7232_*` across all Lua scripts for clarity (e.g. `K7232_BASE`, `K7232_BANK_ADDR`, `K7232_VOL_REG`, etc.).

---

## File: `g3_audition.lua` (~600-800 lines)

### Section 1: Constants

From `g3_common.lua`:
- YM2151: `YM_ADDR_PORT = 0xF030`, `YM_DATA_PORT = 0xF031`
- K007232: `K7232_BASE = 0xF020`, `K7232_BANK_ADDR = 0xF000`, vol/trigger reg offsets
- Main CPU: `SOUND_LATCH_ADDR = 0xE8000`, `SOUND_IRQ_ADDR = 0xF0000`

New ROM table constants (audition-specific):
- `FM_PTR_TABLE = 0x27CD` — 178 entries, 2-byte LE pointers to 25-byte fmprops structs
- `K7232_PROPS_TABLE = 0x3A44` — 7 entries, 9-byte k7232_props structs
- `K7232_CB7_TABLE = 0x3A83` — 7 tables x 15 entries x 9 bytes (callback7 note-specific props)
- `ADDR_STEP_TABLE = 0x1D1F` — word table for sample pitch (indexed by notenum)

YM2151 chromatic note codes (for direct key code computation):
```lua
local YM_NOTE_CODES = {0x0,0x1,0x2,0x4,0x5,0x6,0x8,0x9,0xA,0xC,0xD,0xE}
```

### Section 2: State Variables

```
mode            = "FM"       -- "FM" or "SAMPLE"
fm_patch_num    = 0          -- 0x00-0xB1 (0-177)
fm_patch_data   = {}         -- parsed 25-byte patch
k7232_patch_num = 0          -- 0x00-0x06
k7232_patch_data = {}        -- parsed 9-byte patch
vstate_1f       = 0          -- 0-7 (0=normal, 1-7=callback7 mode)
octave          = 4          -- 0-7
current_note    = nil        -- currently held note index (1-12) or nil
last_played_info = ""        -- display string for last note's keycode/addr_step
```

### Section 3: Input Mapping

**Piano keys** (use `code_pressed()` for held-note detection):

| Key | Note | Index |
|-----|------|-------|
| Q | C | 1 |
| 2 | C# | 2 |
| W | D | 3 |
| 3 | D# | 4 |
| E | E | 5 |
| R | F | 6 |
| 5 | F# | 7 |
| T | G | 8 |
| 6 | G# | 9 |
| Y | A | 10 |
| 7 | A# | 11 |
| U | B | 12 |

**Control keys** (use `code_pressed_once()`):

| Key | Action |
|-----|--------|
| Tab | Toggle FM / Sample mode |
| Up/Down | Instrument +/-1 |
| Left/Right | Instrument +/-0x10 |
| +/- | Octave up/down (clamp 0-7) |
| PgUp/PgDn | vstate_1f +/-1 (sample mode, clamp 0-7) |

### Section 4: ROM Data Reading Helpers

- `read_rom_u8(addr)` — reads a byte from audio CPU address space
- `read_rom_u16_le(addr)` — reads a little-endian word
- `load_fm_patch(patch_num)` — reads pointer from `FM_PTR_TABLE + patch_num*2`, then reads 25 bytes from the target address. Returns parsed table with `rl_fb_con` and `ops[1..4]` (each with 6 fields: `dt1_mul`, `tl`, `ks_ar`, `ame_d1r`, `dt2_d2r`, `d1l_rr`)
- `load_k7232_patch(patch_num)` — reads 9 bytes from `K7232_PROPS_TABLE + patch_num*9`. Returns `{bank, addr_hi, addr, vstate_34, addr_step, vstate_1f, vstate_2f}`
- `load_k7232_cb7_props(note_index, cb7_table)` — reads 9 bytes from `K7232_CB7_TABLE + note_index*9 + (cb7_table-1)*135`. Same fields as above.

### Section 5: FM (YM2151) Playback

**`ym_write(reg, data)`** — writes reg/data pair to YM2151 (from `g3_common.lua`)

**`fm_load_patch(patch_data, channel)`** — programs all 25 bytes to YM2151 for `channel`:
1. Write `rl_fb_con | 0xC0` to reg `0x20+ch` (force both L+R speakers on)
2. For each of 4 operators, write 6 params to regs `0x40..0xE0 + ch + op_offset`

Operator mapping (fmprops struct order -> YM2151 register offset):
- struct op1 (bytes 1-6) -> offset +0 (M1)
- struct op3 (bytes 7-12) -> offset +16 (C1)
- struct op2 (bytes 13-18) -> offset +8 (M2)
- struct op4 (bytes 19-24) -> offset +24 (C2)

**`fm_play_note(note_index)`**:
1. Key-off channel 0: `ym_write(0x08, 0x00)`
2. Compute keycode: `kc = (octave << 4) | YM_NOTE_CODES[note_index]`
3. Set pitch: `ym_write(0x28, kc)`, `ym_write(0x30, 0x00)` (no key fraction)
4. Key-on: `ym_write(0x08, 0x78)` (all 4 ops + channel 0)
5. Update `last_played_info` with note name, octave, keycode

**`fm_stop_note()`**: `ym_write(0x08, 0x00)` — key-off channel 0

### Section 6: K7232 (K007232) Playback

**`k7232_play_sample(bank, addr_hi, addr_lo, addr_step, volume)`** — programs K007232 channel A:
1. Write start address to regs 2, 3, 4
2. Write bank to `K7232_BANK_ADDR`
3. Write address step to regs 0, 1
4. Write volume to reg 12 (high nibble = ch A volume)
5. Read from reg 5 to trigger ch A playback

Note: Start address is written before bank, matching the game's ordering.

**`k7232_play_note_normal(note_index)`** — when vstate_1f == 0:
1. Compute `notenum = note_index + octave*12 - 2` (clamp >= 0)
2. Read addr_step from `ADDR_STEP_TABLE + notenum*2`
3. Use bank/addr from current `k7232_patch_data`
4. Call `k7232_play_sample(...)`
5. Update `last_played_info` with addr_step

**`k7232_play_note_cb7(note_index)`** — when vstate_1f >= 1:
1. Load props from callback7 table (note_index used directly, no octave/transpose)
2. Use bank, addr, addr_step all from loaded props
3. Call `k7232_play_sample(...)`
4. Update `last_played_info` with bank/addr/step

**`k7232_stop_note()`** — zeroes K7232 ch A volume (clear high nibble of vol reg)

**`sample_play_note(note_index)`** — dispatcher:
- vstate_1f == 0 -> `k7232_play_note_normal()`
- vstate_1f >= 1 -> `k7232_play_note_cb7()`

### Section 7: Input Handling

**`handle_input()`** — called each frame:
1. Check Tab for mode switch (reload patch on switch)
2. Check Up/Down/Left/Right for instrument change (reload and reprogram patch)
3. Check +/- for octave change
4. Check PgUp/PgDn for vstate_1f change (sample mode only)
5. Scan all piano keys with `code_pressed()`:
   - If a key is pressed and differs from `current_note`: stop old note, play new note
   - If no key pressed and `current_note ~= nil`: stop note (key-off for FM; zero K7232 volume for samples)

### Section 8: UI Drawing

Layout (normalized coordinates):

| Y | Content |
|---|---------|
| 0.01 | Title: "GRADIUS III INSTRUMENT AUDITION" |
| 0.06 | Mode: [FM] or [SAMPLE] (Tab=switch) |
| 0.10 | Octave: 4 (+/-) |
| 0.14 | Instrument: [XX] with nav hints |
| 0.18 | vstate_1f: X (PgUp/PgDn) — sample mode only |
| 0.22-0.55 | Instrument parameters (varies by mode) |
| 0.60 | Last played note info (note name, keycode/addr_step) |
| 0.90 | Piano key mapping help |
| 0.96 | Controls help |

**FM parameter display**: RL/FB/CON decoded, then 4 operator rows showing all 6 params as hex.

**Sample parameter display**: Bank, Addr (combined 17-bit), AddrStep, plus vstate fields. When vstate_1f != 0, note that props come from callback7 table.

### Section 9: Initialization

**Multi-phase init** using frame counter:

**Phase 1** (frame 1):
1. Get device refs (maincpu, audiocpu, screen, input_mgr, memory spaces)
2. Halt main CPU: `main_mem:write_direct_u16(0x2f0, 0x60F6)`
3. Resolve all key codes via `code_from_token()`
4. Load default FM patch from ROM

**Wait phase** (subsequent frames):
- Poll `audiocpu.state["PC"].value` each frame until it equals `0x0615` (Z80 main loop entry point), indicating the Z80 has finished its full startup and initialization of all registers.

**Phase 2** (once Z80 PC == 0x0615):
1. Z80 left running idle in its main loop
2. Program initial FM patch to YM2151 channel 0
3. Begin normal operation (handle_input + draw_ui)

### Section 10: Entry Point

```lua
local _init_phase = 0
local Z80_MAIN_LOOP_PC = 0x0615

emu.register_frame_done(function()
    if _init_phase == 0 then
        _init_phase = 1; pcall(init_phase1); return
    end
    if _init_phase == 1 then
        -- Wait for Z80 to reach main loop
        if audiocpu.state["PC"].value == Z80_MAIN_LOOP_PC then
            _init_phase = 2; pcall(init_phase2)
        end
        return
    end
    pcall(function() handle_input(); draw_ui() end)
end, "g3_audition")
```

---

## Technical Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Lua writes to YM/K7232 may not reach hardware | Test immediately; if `audio_mem:write_u8()` doesn't work, may need to find device-specific Lua APIs |
| FM operator ordering wrong (sounds bad) | Compare against known patches; easy to swap in the mapping table |
| K7232 addr_step table index off-by-one | Verify against g3arcmus.pl note formula; adjust |
| Z80 occasionally overwrites K7232 volume | Acceptable; we re-set volume on each note play |
| Z80 PC never reaches 0x0615 | Add timeout (e.g. 300 frames / 5 sec) with error message; fall through to phase 2 anyway |

## Verification Plan

1. Launch: `mame gradius3 -autoboot_script g3_audition.lua`
2. Confirm Z80 startup completes (script transitions to phase 2)
3. FM mode: select patch 0x00, press Q (C4) — should hear an FM tone
4. Change octave with +/-, verify pitch changes
5. Scroll through FM patches with Up/Down, verify parameter display updates and sound changes
6. Switch to Sample mode (Tab), select patch 0, press keys — should hear samples
7. Change vstate_1f with PgUp, press keys — should hear different samples from callback7 tables
8. Verify last-played-note info displays keycode (FM) or addr_step (sample)
9. Verify key release stops sound (FM key-off, sample volume zero)

## Files to Create/Modify

- **CREATE**: `g3_audition.lua` — main audition script
- **CREATE**: `g3_common.lua` — shared constants and helpers extracted from existing scripts
- **MODIFY**: `g3_soundtest.lua` — import from `g3_common.lua`, rename `K232_*` to `K7232_*`
- **MODIFY**: `g3_eventlog.lua` — import from `g3_common.lua`, rename `K232_*` to `K7232_*` if applicable
- **Reference only** (no modifications): `fmprops.h`, `k7232props.h`, `latchcmds.txt`
