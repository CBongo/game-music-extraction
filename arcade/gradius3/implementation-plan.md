# Plan: MAME Lua Sound Test & Event Listener for Gradius 3 Arcade

## Context

The Gradius 3 arcade sound driver (Z80 + YM2151 FM + K007232 PCM) has been partially reverse-engineered, with Perl extractors already producing MIDI and disassembly output. To complete the sound event identification and allow instrument isolation, two MAME Lua tools are needed:

1. **Sound Test Mode** — standalone tool to audition individual sound events and mute/solo channels
2. **Sound Event Listener** — in-game overlay showing sound events in real time during gameplay

These tools will accelerate mapping the ~35 song commands and ~50+ SFX/voice sample events to their descriptions, and allow isolating individual FM voices for transcription.

## Decision: Standalone Scripts, Not Plugins

Both tools should be **standalone autoboot scripts**, not MAME plugins.

- Single-file each, no directory structure or metadata boilerplate
- Full access to all MAME Lua APIs (memory, drawing, input, write taps)
- The two tools are mutually exclusive (sound test suspends normal gameplay; listener requires it)
- Natural for dev workflow: `mame gradius3 -autoboot_script g3_eventlog.lua`

## Files to Create

| File | Purpose |
|------|---------|
| `arcade/gradius3/g3_eventlog.lua` | Sound event listener (build first) |
| `arcade/gradius3/g3_soundtest.lua` | Sound test mode |

## Reference Files

| File | Contains |
|------|----------|
| `eventstate.h` | Voice state struct (10 × 0x50 bytes at Z80 RAM 0xF800). Muted flag at offset **0x3F**. Patch at 0x3E. |
| `latchcmds.txt` | Sound event byte values and voice command opcode docs |
| `ym2151 reg notes.txt` | YM2151 register map (from ymfm source) |
| `rommap.txt` | ROM-to-device mapping; audio ROM is `945_r05.d9` |
| [MAME gradius3.cpp](https://github.com/mamedev/mame/blob/master/src/mame/konami/gradius3.cpp) | MAME driver source — device tags, memory maps, IRQ setup |

## Hardware Summary (confirmed from MAME source)

### CPUs
- **Main CPU**: 68000 @ 10 MHz (`:maincpu`) — game logic
- **Sub CPU**: 68000 @ 10 MHz (`:sub`) — graphics coprocessor, not involved in audio
- **Audio CPU**: Z80 @ 3.579545 MHz (`:audiocpu`) — sound driver

### Sound Devices (confirmed tags)
- **`:ymsnd`**: YM2151 FM synthesizer, 8 channels
- **`:k007232`**: Konami K007232 PCM chip, 2 channels (voice samples)
- **`:soundlatch`**: `generic_latch_8_device` — inter-CPU sound command queue

### Sound Event Dispatch (two-step process)
1. Main CPU writes event byte to **0xE8000** (→ soundlatch device)
2. Main CPU writes to **0xF0000** (→ `sound_irq_w`) which calls `m_audiocpu->set_input_line_and_vector(0, HOLD_LINE, 0xff)` triggering Z80 INT

### Z80 Memory Map
| Address | Device |
|---------|--------|
| 0x0000–0xEFFF | ROM (single 64KB ROM, not banked) |
| 0xF000 | K007232 sample ROM bank selector |
| 0xF010 | Sound latch read |
| 0xF020–0xF02D | K007232 registers |
| 0xF030 | YM2151 address register |
| 0xF031 | YM2151 data register |
| 0xF800–0xFFFF | RAM (2 KB) — voice state array starts at 0xF800 |

### Voice State Array
10 structs × 0x50 bytes at 0xF800–0xFB1F. Key fields (from `eventstate.h`):
| Offset | Field | Notes |
|--------|-------|-------|
| 0x00 | voicenum | YM2151 channel offset |
| 0x01 | songnum | Current song |
| 0x0E | level_adj | Volume adjustment |
| 0x1A | transpose | Per-voice transpose |
| 0x1F | pan_channel_flags | L/R panning |
| 0x3E | patch | FM instrument index |
| **0x3F** | **muted** | **1=muted, 0=unmuted** |
| 0x41 | k007232_volume | PCM volume |

### Event Byte Categories
- `0x01–0x4F`: Sound effects
- `0x50–0x7F`: Voice samples (K007232)
- `0x80–0xFE`: Music/BGM
- `0x00`, `0xFF`: Cancel/stop (omit from display)

---

## Phase 1: Sound Event Listener (`g3_eventlog.lua`)

Build first — simpler, and validates core API assumptions (device tags, write taps, drawing).

### Implementation

1. **Install a write tap** on main CPU address 0xE8000 to passively monitor sound latch writes. The tap captures the event byte but passes it through unchanged (does not block gameplay).

2. **Maintain a circular buffer** of recent events (last ~20), each tagged with frame number and category (SFX/Voice/BGM). Filter out 0x00 and 0xFF.

3. **Draw overlay** on right side of screen each frame via `emu.register_frame_done()`:
   - Vertical list of recent events as `XX [Category] Name`
   - Color-coded: yellow for SFX, cyan for voice samples, green for BGM
   - Include known event names from a lookup table (derived from `latchcmds.txt`)
   - Fade older entries by reducing alpha

### Key API Calls
```lua
-- Device access
local maincpu = manager.machine.devices[":maincpu"]
local main_mem = maincpu.spaces["program"]
local screen = manager.machine.screens[":screen"]

-- Write tap (passive monitor)
main_mem:install_write_tap(0xE8000, 0xE8000, "event_monitor", function(offset, data, mask)
    -- log event to buffer
    return data  -- pass through unchanged
end)

-- Drawing (normalized 0.0-1.0 coords, 0xAARRGGBB colors)
screen.container:draw_text(x, y, text, fg_color, bg_color)
screen.container:draw_box(x0, y0, x1, y1, line_color, fill_color)

-- Frame callback
emu.register_frame_done(draw_function, "g3_eventlog")
```

### Known Event Name Table
Derived from `latchcmds.txt`:
```lua
local event_names = {
    [0x01] = "Player Shoot", [0x05] = "Enemy Shoot",
    [0x06] = "Enemy Multishoot", [0x0A] = "Enemy Hit",
    [0x40] = "Collect Powerup", [0x45] = "Weapon Confirm",
    [0x47] = "Player Explode", [0x48] = "Coin Drop",
    [0x50] = "\"Double\"", [0x51] = "\"Laser\"",
    [0x52] = "\"Missile\"", [0x53] = "\"Optional\"",
    [0x56] = "\"Shield\"", [0x57] = "\"Speed Up\"",
    [0x58] = "\"Destroy Them All\"",
    [0x81] = "Stage 1 BGM", [0x87] = "Game Over",
    [0x88] = "High Score", [0x89] = "Weaponry BGM",
    [0x8C] = "Title Theme",
}
```

### Validation
- Run `mame gradius3 -autoboot_script g3_eventlog.lua`
- Play through attract mode — events should appear on screen
- Cross-reference: 0x8C when title music starts, 0x48 on coin insert
- Verify 0x00/0xFF are filtered out

---

## Phase 2: Sound Test Mode (`g3_soundtest.lua`)

### Implementation

**Step 1: Block main CPU sound interference**

Install write taps on both the sound latch (0xE8000) and the IRQ trigger (0xF0000) to prevent the game from sending sound commands. On startup, allow event 0x00 through first so the Z80 driver initializes properly, then block all subsequent game writes:

```lua
local initialized = false

main_mem:install_write_tap(0xE8000, 0xE8000, "block_latch", function(offset, data, mask)
    if not initialized then
        -- Allow the first write (0x00 init) through
        if data == 0x00 then initialized = true end
        return data
    end
    -- silently discard game's sound latch writes after init
end)
main_mem:install_write_tap(0xF0000, 0xF0000, "block_irq", function(offset, data, mask)
    if not initialized then
        return data  -- allow IRQ during init
    end
    -- silently discard game's IRQ triggers after init
end)
```

If the Z80 driver doesn't self-initialize via event 0x00, we may need to explicitly trigger event 0x00 from Lua at startup before blocking.

> **Risk**: Write taps may be observation-only (writes still go through regardless of return value). If so, fallback: run with `-debug` flag and use `device:debug()` to suspend maincpu and subcpu entirely. The user confirmed `-debug` is acceptable.

**Step 2: Trigger sound events from Lua**

Use the soundlatch device directly, then trigger the Z80 IRQ — mirroring what `gradius3_state::sound_irq_w` does in the MAME C++ source:

```lua
function trigger_event(event_byte)
    -- Write command to soundlatch device
    local soundlatch = manager.machine.devices[":soundlatch"]
    soundlatch:write(event_byte)  -- API TBD: may be write(), latch(), etc.

    -- Trigger Z80 IRQ line 0, vector 0xFF (HOLD_LINE)
    local audiocpu = manager.machine.devices[":audiocpu"]
    -- API TBD: set_input_line_and_vector() or similar Lua binding
end
```

> **Risk**: The exact Lua methods for `soundlatch:write()` and `audiocpu:set_input_line()` need runtime verification. The `generic_latch_8_device` and CPU input line APIs may not be directly exposed in Lua. If not:
> - **Fallback A**: Write the event byte to Z80 address 0xF010 (soundlatch read port) and poke the Z80's interrupt vector table
> - **Fallback B**: Write the event byte directly to the Z80 RAM location where the IRQ handler stores the received command, and set any "new command" flag the driver uses
> - **Fallback C**: Use main CPU memory writes to 0xE8000 then 0xF0000 — if write taps are observation-only, our writes go through just like the game's would

**Step 3: Channel mute/solo**

Each frame, write the muted flag for all 10 voice states:

```lua
local audiocpu = manager.machine.devices[":audiocpu"]
local audio_mem = audiocpu.spaces["program"]

for ch = 0, 9 do
    local addr = 0xF800 + (ch * 0x50) + 0x3F  -- muted flag
    audio_mem:write_u8(addr, mute_state[ch] and 1 or 0)
end
```

Write every frame so the mute persists even as the driver reinitializes voices for new notes. The Z80 driver checks `muted` before playing: "if muted flag nonzero, do nothing" (per `latchcmds.txt`).

**Solo mode**: When solo is active for channel N, set `mute_state[ch] = (ch ~= N)` for all channels.

**Step 4: Keyboard input**

Poll each frame via `manager.machine.input`:
- **Up/Down**: Increment/decrement selected event byte (0x00–0xFF with wrapping)
- **Enter**: Trigger the selected event
- **0–9 keys**: Toggle mute on channels 0–9
- **S**: Toggle solo mode
- **R**: Reset all mutes (unmute all)

```lua
local KEY_UP = manager.machine.input:code_from_token("KEYCODE_UP")
if manager.machine.input:code_pressed_once(KEY_UP) then ... end
```

**Step 5: On-screen UI**

Layout:
```
GRADIUS 3 SOUND TEST
Event: [8C]  Title Theme       [Enter=play, Up/Down=select]
Category: Music (0x80-0xFE)

Channels:  [0] [1] [2] [3] [4] [5] [6] [7] [8] [9]
                M               M              <- red=muted, green=active

Voice State:
Ch Song Patch Dur  Lvl Tsp Pan Mute
 0  81   02   0C   7F  00  C0  .
 1  81   05   18   6A  00  C0  .
 ...

Keys: 0-9=mute  S=solo  R=reset  Up/Down=event  Enter=play
```

**Step 6 (stretch): YM2151 register display**

Shadow YM2151 registers by installing a write tap on Z80 memory addresses 0xF030 (address port) and 0xF031 (data port):

```lua
local ym_regs = {}
local ym_addr_reg = 0

audio_mem:install_write_tap(0xF030, 0xF030, "ym_addr", function(offset, data, mask)
    ym_addr_reg = data
    return data
end)
audio_mem:install_write_tap(0xF031, 0xF031, "ym_data", function(offset, data, mask)
    ym_regs[ym_addr_reg] = data
    return data
end)
```

Display key registers per YM2151 channel (0–7):
- Key code (regs 0x28–0x2F): octave + note
- Total level (regs 0x60–0x7F): volume per operator
- Pan/feedback/algorithm (regs 0x20–0x27)

### Validation
- Trigger 0x8C → title music should play
- Trigger 0x81 → stage 1 music
- Trigger 0x51 → "laser" voice sample
- Mute channel 0 during song → that instrument drops out
- Solo channel 2 → only channel 2 audible
- Unmute all → full playback resumes
- Game sound events should NOT interrupt user playback

---

## Implementation Order

| Step | Task | Validates |
|------|------|-----------|
| 1 | Skeleton `g3_eventlog.lua`: device refs + console print | Device tags (`:maincpu`, `:screen`) |
| 2 | Add write tap on 0xE8000, log to console | `install_write_tap` API, latch address |
| 3 | Add on-screen overlay drawing | `draw_text`, `draw_box`, coordinate system |
| 4 | Add event names and category coloring | Polish |
| 5 | Skeleton `g3_soundtest.lua`: blocking write taps on 0xE8000 + 0xF0000 | Write tap suppression semantics |
| 6 | Event triggering via `:soundlatch` device + Z80 IRQ | Core sound test functionality |
| 7 | Mute/solo via vstate 0x3F writes | Z80 RAM write from Lua |
| 8 | Keyboard input handling | `code_from_token`, `code_pressed_once` |
| 9 | Full UI overlay | Voice state table, channel boxes |
| 10 | YM2151 register shadow via write taps on 0xF030/0xF031 | Stretch goal |

Steps 5–6 are the highest-risk items and should be prototyped early.

---

## Open Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Write taps may be observation-only (can't suppress writes) | HIGH | Fallback: run with `-debug`, suspend maincpu/subcpu via `device:debug()` |
| Soundlatch device Lua API unknown (`write()` method may not exist) | HIGH | Fallback: write to main CPU 0xE8000 + 0xF0000 directly; or write to Z80 address 0xF010 + poke IRQ |
| Z80 `set_input_line_and_vector()` may not be exposed in Lua | HIGH | Fallback: write to Z80 RAM where driver stores command; or use main CPU path |
| MAME keyboard input tokens may differ from expected names | LOW | Enumerate available input codes at startup |
