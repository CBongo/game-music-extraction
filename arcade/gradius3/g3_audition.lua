-- g3_audition.lua
-- Gradius 3 arcade instrument audition mode
-- Allows playing FM (YM2151) and sample (K007232) instruments directly
-- by programming the sound chips from Lua, independent of the Z80 driver.
-- Usage: mame gradius3 -autoboot_script g3_audition.lua

-- By Claude under direction from Chris Bongaarts 2026-03-22

-- Load shared constants and helpers
dofile(debug.getinfo(1,"S").source:match("@?(.+[/\\])") .. "g3_common.lua")

-- ============================================================
-- ROM table addresses
-- ============================================================

local FM_PTR_TABLE      = 0x27CD   -- 178 x 2-byte LE pointers to fmprops structs
local FM_PATCH_COUNT    = 178
local K7232_PROPS_TABLE = 0x3A44   -- 7 x 9-byte k7232_props structs (patch change mode)
local K7232_PATCH_COUNT = 7
local K7232_CB7_TABLE   = 0x3A83   -- callback7 tables: 7 tables x 15 entries x 9 bytes
local K7232_CB7_NOTES   = 15       -- entries per callback7 table
local ADDR_STEP_TABLE   = 0x1D1F   -- word table: sample address step indexed by notenum

-- ============================================================
-- YM2151 note codes for chromatic scale
-- Index 1=C, 2=C#, 3=D, 4=D#, 5=E, 6=F, 7=F#, 8=G, 9=G#, 10=A, 11=A#, 12=B
-- ============================================================

local YM_NOTE_CODES = {0x0,0x1,0x2,0x4,0x5,0x6,0x8,0x9,0xA,0xC,0xD,0xE}

-- Operator register offsets within a channel (YM2151 uses 4 operators)
-- M1=+0, M2=+8, C1=+16, C2=+24
-- fmprops struct order: op1(M1), op3(C1), op2(M2), op4(C2)
local OP_REG_OFFSETS = {0, 16, 8, 24}   -- indexed 1-4, matching struct op order

-- fmprops field -> YM2151 register base mapping (add ch + op_offset)
local OP_REG_BASES = {0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0}
-- fields: dt1_mul, tl, ks_ar, ame_d1r, dt2_d2r, d1l_rr

-- Note name strings
local NOTE_NAMES = {"C","C#","D","D#","E","F","F#","G","G#","A","A#","B"}

-- Z80 main loop PC range (audition waits for Z80 to reach this range before proceeding)
local Z80_MAIN_LOOP_PC_LO = 0x0615   -- inclusive
local Z80_MAIN_LOOP_PC_HI = 0x0639   -- inclusive

-- Audition uses YM2151 channel 0 and K007232 channel A
local FM_CHANNEL  = 0
local K7232_CH_A  = 0   -- channel A index (regs 0-5)

-- ============================================================
-- State
-- ============================================================

local mode            = "FM"    -- "FM" or "SAMPLE"
local fm_patch_num    = 0       -- 0x00-0xB1 (0 to FM_PATCH_COUNT-1)
local fm_patch_data   = nil     -- parsed patch table or nil
local k7232_patch_num = 0       -- 0x00-0x06
local k7232_patch_data = nil    -- parsed patch table or nil
local vstate_1f       = 0       -- 0=normal addr_step mode, 1-7=callback7 mode
local octave          = 4       -- 0-7
local current_note    = nil     -- currently held note index (1-12) or nil
local last_played_info = ""     -- info string for last played note
local k7232_vol_shadow = 0      -- shadow of K007232 vol reg (reg 12)

-- Devices (set in init)
local maincpu, main_mem
local audiocpu, audio_mem
local screen, input_mgr

-- Input codes (set in init)
local KEY_TAB, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT
local KEY_PLUS, KEY_MINUS, KEY_PGUP, KEY_PGDN

-- Piano key definitions: {keycode_token, note_index, display_label}
local PIANO_KEY_DEFS = {
    {"KEYCODE_Q", 1,  "C"},
    {"KEYCODE_2", 2,  "C#"},
    {"KEYCODE_W", 3,  "D"},
    {"KEYCODE_3", 4,  "D#"},
    {"KEYCODE_E", 5,  "E"},
    {"KEYCODE_R", 6,  "F"},
    {"KEYCODE_5", 7,  "F#"},
    {"KEYCODE_T", 8,  "G"},
    {"KEYCODE_6", 9,  "G#"},
    {"KEYCODE_Y", 10, "A"},
    {"KEYCODE_7", 11, "A#"},
    {"KEYCODE_U", 12, "B"},
}
local piano_keys = {}   -- {code, note_index, label} populated in init

-- Init phase tracking
local _init_phase = 0   -- 0=pre-init, 1=waiting for Z80, 2=ready, 3=timeout fallback
local _wait_frames = 0
local WAIT_TIMEOUT = 300   -- frames (~5s at 60fps)

-- ============================================================
-- ROM helpers
-- ============================================================

local function read_u8(addr)
    return audio_mem:read_u8(addr)
end

local function read_u16_le(addr)
    return read_u8(addr) | (read_u8(addr + 1) << 8)
end

-- ============================================================
-- YM2151 helpers
-- ============================================================

local function ym_write(reg, data)
    audio_mem:write_u8(YM_ADDR_PORT, reg)
    audio_mem:write_u8(YM_DATA_PORT, data)
end

-- ============================================================
-- FM patch loading
-- ============================================================

local function load_fm_patch(patch_num)
    -- Read 2-byte pointer from FM_PTR_TABLE
    local ptr = read_u16_le(FM_PTR_TABLE + patch_num * 2)
    -- Read 25-byte fmprops struct from ptr
    local rl_fb_con = read_u8(ptr)
    local ops = {}
    for i = 1, 4 do
        local base = ptr + 1 + (i - 1) * 6
        ops[i] = {
            dt1_mul  = read_u8(base + 0),
            tl       = read_u8(base + 1),
            ks_ar    = read_u8(base + 2),
            ame_d1r  = read_u8(base + 3),
            dt2_d2r  = read_u8(base + 4),
            d1l_rr   = read_u8(base + 5),
        }
    end
    return {ptr = ptr, rl_fb_con = rl_fb_con, ops = ops}
end

local function fm_program_patch(patch, channel)
    -- Write RL/FB/CON with L+R enabled (bits 7-6 set)
    ym_write(0x20 + channel, patch.rl_fb_con | 0xC0)
    -- Write 4 operators; fmprops order is op1,op3,op2,op4 -> reg offsets 0,16,8,24
    local fields = {"dt1_mul","tl","ks_ar","ame_d1r","dt2_d2r","d1l_rr"}
    for i = 1, 4 do
        local op = patch.ops[i]
        local op_off = OP_REG_OFFSETS[i]
        local vals = {op.dt1_mul, op.tl, op.ks_ar, op.ame_d1r, op.dt2_d2r, op.d1l_rr}
        for f = 1, 6 do
            ym_write(OP_REG_BASES[f] + channel + op_off, vals[f])
        end
    end
end

-- ============================================================
-- FM note playback
-- ============================================================

local function fm_key_off(channel)
    ym_write(0x08, channel)   -- operator mask=0 = key off
end

local function fm_play_note(note_index)
    fm_key_off(FM_CHANNEL)
    -- Set key code and fraction
    local kc = (octave << 4) | YM_NOTE_CODES[note_index]
    ym_write(0x28 + FM_CHANNEL, kc)
    ym_write(0x30 + FM_CHANNEL, 0x00)   -- no key fraction
    -- Key on: all 4 operators
    ym_write(0x08, 0x78 | FM_CHANNEL)
    last_played_info = string.format("%s%d  KC=%02X",
        NOTE_NAMES[note_index], octave, kc)
end

local function fm_stop_note()
    fm_key_off(FM_CHANNEL)
end

-- ============================================================
-- K7232 patch loading
-- ============================================================

local function parse_k7232_props(base_addr)
    -- Read 9-byte k7232_props struct
    local bank
    local addr_hi
    local addr_lo
    local addr_step
    local vstate_36
    local v1f
    local v2f

    if base_addr == K7232_PROPS_TABLE then
        bank      = read_u8(base_addr + 0)
        addr_hi   = read_u8(base_addr + 1)
        addr_lo   = read_u16_le(base_addr + 2)   -- 16-bit LE word
        addr_step = read_u16_le(base_addr + 4)   -- 16-bit LE word
        vstate_36 = read_u8(base_addr + 6)
        v1f       = read_u8(base_addr + 7)
        v2f       = read_u8(base_addr + 8)
    else
        bank      = read_u8(base_addr + 0)
        addr_hi   = read_u8(base_addr + 1)
        addr_lo   = read_u16_le(base_addr + 2)   -- 16-bit LE word
        -- addr_step is weird in this case
        vstate_36 = read_u8(base_addr + 4)
        addr_step = read_u8(base_addr + 5) * 0x100 + read_u8(base_addr + 6)
        v1f       = read_u8(base_addr + 7)
        v2f       = read_u8(base_addr + 8)
    end
    return {
        bank      = bank,
        addr_hi   = addr_hi,
        addr_lo   = addr_lo,
        addr_step = addr_step,
        vstate_36 = vstate_36,
        vstate_1f = v1f,
        vstate_2f = v2f,
    }
end

local function load_k7232_patch(patch_num)
    return parse_k7232_props(K7232_PROPS_TABLE + patch_num * 9)
end

local function load_k7232_cb7_props(note_index, cb7_table)
    -- Index into callback7 table: note * 9 + (cb7_table-1) * 135
    local offset = note_index * 9 + (cb7_table - 1) * 135
    return parse_k7232_props(K7232_CB7_TABLE + offset)
end

-- ============================================================
-- K7232 sample playback
-- ============================================================

local function k7232_play_sample(bank, addr_hi, addr_lo, addr_step, volume)
    -- Write start address first (regs 2,3,4), then bank
    audio_mem:write_u8(K7232_BASE + 2, addr_lo & 0xFF)
    audio_mem:write_u8(K7232_BASE + 3, (addr_lo >> 8) & 0xFF)
    audio_mem:write_u8(K7232_BASE + 4, addr_hi & 0x01)
    audio_mem:write_u8(K7232_BANK_ADDR, bank)
    -- Address step (regs 0,1)
    audio_mem:write_u8(K7232_BASE + 0, addr_step & 0xFF)
    audio_mem:write_u8(K7232_BASE + 1, (addr_step >> 8) & 0x0F)
    -- Volume: high nibble = ch A
    local vol_byte = ((volume & 0x0F) << 4) | (k7232_vol_shadow & 0x0F)
    audio_mem:write_u8(K7232_BASE + K7232_VOL_REG, vol_byte)
    k7232_vol_shadow = vol_byte
    -- Trigger ch A playback (read from reg 5)
    audio_mem:read_u8(K7232_BASE + K7232_CHA_START)
end

local function k7232_stop_note()
    -- Zero ch A volume (clear high nibble)
    local vol_byte = k7232_vol_shadow & 0x0F
    audio_mem:write_u8(K7232_BASE + K7232_VOL_REG, vol_byte)
    k7232_vol_shadow = vol_byte
end

local function k7232_play_note_normal(note_index)
    -- vstate_1f == 0: address step from ADDR_STEP_TABLE, bank/addr from patch
    local notenum = note_index + octave * 12 - 2
    if notenum < 0 then notenum = 0 end
    local addr_step = read_u16_le(ADDR_STEP_TABLE + notenum * 2)
    local p = k7232_patch_data
    local full_addr = ((p.addr_hi & 0x01) << 16) | p.addr_lo
    k7232_play_sample(p.bank, p.addr_hi, p.addr_lo, addr_step, 0x0F)
    last_played_info = string.format("%s%d  NoteNum=%02X  Step=%04X  Addr=%05X",
        NOTE_NAMES[note_index], octave, notenum, addr_step, full_addr)
end

local function k7232_play_note_cb7(note_index)
    -- vstate_1f != 0: all props from callback7 table, no octave adjustment
    local props = load_k7232_cb7_props(note_index, vstate_1f)
    local full_addr = ((props.addr_hi & 0x01) << 16) | props.addr_lo
    k7232_play_sample(props.bank, props.addr_hi, props.addr_lo, props.addr_step, 0x0F)
    last_played_info = string.format("%s  Bank=%02X  Addr=%05X  Step=%04X  (cb7=%d)",
        NOTE_NAMES[note_index], props.bank, full_addr, props.addr_step, vstate_1f)
end

local function sample_play_note(note_index)
    if vstate_1f == 0 then
        k7232_play_note_normal(note_index)
    else
        k7232_play_note_cb7(note_index)
    end
end

-- ============================================================
-- Input handling
-- ============================================================

local function handle_input()
    -- Mode switch
    if input_mgr:code_pressed_once(KEY_TAB) then
        mode = (mode == "FM") and "SAMPLE" or "FM"
        current_note = nil
        last_played_info = ""
    end

    -- Instrument selection
    local inst_delta = 0
    if input_mgr:code_pressed_once(KEY_UP)    then inst_delta =  1 end
    if input_mgr:code_pressed_once(KEY_DOWN)  then inst_delta = -1 end
    if input_mgr:code_pressed_once(KEY_RIGHT) then inst_delta =  0x10 end
    if input_mgr:code_pressed_once(KEY_LEFT)  then inst_delta = -0x10 end

    if inst_delta ~= 0 then
        if mode == "FM" then
            fm_patch_num = (fm_patch_num + inst_delta) % FM_PATCH_COUNT
            fm_patch_data = load_fm_patch(fm_patch_num)
            fm_program_patch(fm_patch_data, FM_CHANNEL)
        else
            k7232_patch_num = (k7232_patch_num + inst_delta) % K7232_PATCH_COUNT
            k7232_patch_data = load_k7232_patch(k7232_patch_num)
        end
        current_note = nil
        last_played_info = ""
    end

    -- Octave control
    if input_mgr:code_pressed_once(KEY_PLUS) then
        octave = math.min(7, octave + 1)
    end
    if input_mgr:code_pressed_once(KEY_MINUS) then
        octave = math.max(0, octave - 1)
    end

    -- vstate_1f control (sample mode)
    if mode == "SAMPLE" then
        if input_mgr:code_pressed_once(KEY_PGUP) then
            vstate_1f = math.min(7, vstate_1f + 1)
            current_note = nil
            last_played_info = ""
        end
        if input_mgr:code_pressed_once(KEY_PGDN) then
            vstate_1f = math.max(0, vstate_1f - 1)
            current_note = nil
            last_played_info = ""
        end
    end

    -- Piano keys (held detection)
    local pressed_note = nil
    for _, pk in ipairs(piano_keys) do
        if input_mgr:code_pressed(pk.code) then
            pressed_note = pk.note_index
            break   -- only play one note at a time
        end
    end

    if pressed_note ~= nil then
        if pressed_note ~= current_note then
            -- New note or changed note: stop previous, play new
            if mode == "FM" then
                fm_play_note(pressed_note)
            else
                sample_play_note(pressed_note)
            end
            current_note = pressed_note
        end
    else
        if current_note ~= nil then
            -- Key released: stop note
            if mode == "FM" then
                fm_stop_note()
            else
                k7232_stop_note()
            end
            current_note = nil
        end
    end
end

-- ============================================================
-- Drawing
-- ============================================================

local function draw_fm_params()
    local p = fm_patch_data
    if not p then
        screen.container:draw_text(0.02, 0.22, "(no patch loaded)", COL_GRAY, 0x00000000)
        return
    end

    local rl = (p.rl_fb_con >> 7) & 1
    local lr = (p.rl_fb_con >> 6) & 1   -- note: YM2151 uses bit7=L, bit6=R
    local fb = (p.rl_fb_con >> 3) & 0x7
    local con = p.rl_fb_con & 0x7

    screen.container:draw_text(0.02, 0.22,
        string.format("FM Patch %02X  (ROM ptr=%04X)", fm_patch_num, p.ptr),
        COL_YELLOW, 0x00000000)
    screen.container:draw_text(0.02, 0.26,
        string.format("RL/FB/CON: %02X  (L=%d R=%d FB=%d CON=%d)",
            p.rl_fb_con, rl, lr, fb, con),
        COL_WHITE, 0x00000000)
    screen.container:draw_text(0.02, 0.30,
        string.format("%-4s  %s  %s  %s  %s  %s  %s",
            "Op", "DT1/MUL", "TL    ", "KS/AR ", "AME/D1R", "DT2/D2R", "D1L/RR"),
        COL_GRAY, 0x00000000)

    local op_names = {"M1","C1","M2","C2"}   -- display order matches struct order
    for i = 1, 4 do
        local op = p.ops[i]
        local y = 0.34 + (i - 1) * 0.038
        screen.container:draw_text(0.02, y,
            string.format("%-4s  %02X       %02X      %02X      %02X       %02X       %02X",
                op_names[i],
                op.dt1_mul, op.tl, op.ks_ar, op.ame_d1r, op.dt2_d2r, op.d1l_rr),
            COL_WHITE, 0x00000000)
    end
end

local function draw_sample_params()
    local p = k7232_patch_data
    local y = 0.22

    if vstate_1f == 0 then
        screen.container:draw_text(0.02, y,
            string.format("K7232 Patch %02X  (mode: addr_step from table)", k7232_patch_num),
            COL_YELLOW, 0x00000000)
        y = y + 0.04
        if p then
            local full_addr = ((p.addr_hi & 0x01) << 16) | p.addr_lo
            screen.container:draw_text(0.02, y,
                string.format("Bank=%02X  Addr=%05X  Step=%04X",
                    p.bank, full_addr, p.addr_step),
                COL_WHITE, 0x00000000)
            y = y + 0.04
            screen.container:draw_text(0.02, y,
                string.format("vstate_36=%02X  vstate_1f=%02X  vstate_2f=%02X",
                    p.vstate_36, p.vstate_1f, p.vstate_2f),
                COL_WHITE, 0x00000000)
        end
        y = y + 0.04
        screen.container:draw_text(0.02, y,
            "(Note pitch from addr_step table at ROM 1D1F; octave affects pitch)",
            COL_GRAY, 0x00000000)
    else
        screen.container:draw_text(0.02, y,
            string.format("K7232 callback7 mode=%d  (patch ignored; note selects props)",
                vstate_1f),
            COL_YELLOW, 0x00000000)
        y = y + 0.04
        screen.container:draw_text(0.02, y,
            string.format("Props table base: ROM %04X + note*9 + %d*135",
                K7232_CB7_TABLE, vstate_1f - 1),
            COL_GRAY, 0x00000000)
        y = y + 0.04
        -- Show all 12 note entries for the current cb7 table
        screen.container:draw_text(0.02, y,
            string.format("%-4s  %s  %s  %s", "Note", "Bank", "Addr ", "Step"),
            COL_GRAY, 0x00000000)
        y = y + 0.034
        for ni = 1, math.min(12, K7232_CB7_NOTES) do
            local props = load_k7232_cb7_props(ni, vstate_1f)
            local full_addr = ((props.addr_hi & 0x01) << 16) | props.addr_lo
            local highlight = (current_note == ni) and COL_GREEN or COL_WHITE
            screen.container:draw_text(0.02, y,
                string.format("%-4s  %02X     %05X  %04X",
                    NOTE_NAMES[ni], props.bank, full_addr, props.addr_step),
                highlight, 0x00000000)
            y = y + 0.034
        end
    end
end

local function draw_ui()
    local container = screen.container

    -- Background
    container:draw_box(0.0, 0.0, 1.0, 1.0, 0xFF222222, COL_BG)

    -- Title
    container:draw_text(0.02, 0.01, "GRADIUS III INSTRUMENT AUDITION", COL_TITLE, 0x00000000)

    -- Mode indicator
    local mode_col = (mode == "FM") and COL_CYAN or COL_YELLOW
    container:draw_text(0.02, 0.05,
        string.format("Mode: [%s]  (Tab=switch)", mode),
        mode_col, 0x00000000)

    -- Octave
    container:draw_text(0.40, 0.05,
        string.format("Octave: %d  (+/- to change)", octave),
        COL_WHITE, 0x00000000)

    -- Instrument selector
    if mode == "FM" then
        container:draw_text(0.02, 0.09,
            string.format("FM Instrument: [%02X]  (%d/%d)  Up/Dn=+/-1  Lf/Rt=+/-0x10",
                fm_patch_num, fm_patch_num, FM_PATCH_COUNT - 1),
            COL_WHITE, 0x00000000)
    else
        container:draw_text(0.02, 0.09,
            string.format("Sample Patch: [%02X]  (%d/%d)  Up/Dn=+/-1  Lf/Rt=+/-0x10",
                k7232_patch_num, k7232_patch_num, K7232_PATCH_COUNT - 1),
            COL_WHITE, 0x00000000)
        container:draw_text(0.02, 0.13,
            string.format("vstate_1f: %d  (PgUp/PgDn)  -- 0=pitch table mode, 1-7=callback7 mode",
                vstate_1f),
            COL_WHITE, 0x00000000)
    end

    -- Instrument parameters
    if mode == "FM" then
        draw_fm_params()
    else
        draw_sample_params()
    end

    -- Last played note info
    local info_y = 0.72
    if mode == "SAMPLE" and vstate_1f ~= 0 then
        info_y = 0.82   -- shift down to avoid overlap with note table
    end
    local note_col = current_note and COL_GREEN or COL_GRAY
    container:draw_text(0.02, info_y,
        string.format("Last: %s", last_played_info ~= "" and last_played_info or "(none)"),
        note_col, 0x00000000)

    -- Piano keyboard help
    container:draw_text(0.02, 0.90,
        "Piano: Q=C  2=C#  W=D  3=D#  E=E  R=F  5=F#  T=G  6=G#  Y=A  7=A#  U=B",
        COL_GRAY, 0x00000000)

    -- Controls help
    container:draw_text(0.02, 0.94,
        "Tab=FM/Sample  Up/Dn=instrument  Lf/Rt=+/-0x10  +/-=octave  PgUp/PgDn=vstate_1f",
        COL_GRAY, 0x00000000)

    -- Init status overlay
    if _init_phase < 2 then
        local pc = audiocpu and audiocpu.state["PC"].value or 0
        container:draw_text(0.02, 0.50,
            string.format("Waiting for Z80 startup... PC=%04X (target=%04X-%04X)  frame %d/%d",
                pc, Z80_MAIN_LOOP_PC_LO, Z80_MAIN_LOOP_PC_HI, _wait_frames, WAIT_TIMEOUT),
            COL_YELLOW, 0x00000000)
    end
end

-- ============================================================
-- Initialization
-- ============================================================

local function init_phase1()
    maincpu  = manager.machine.devices[":maincpu"]
    audiocpu = manager.machine.devices[":audiocpu"]
    screen   = manager.machine.screens[":screen"]
    input_mgr = manager.machine.input

    if not maincpu or not audiocpu or not screen then
        print("G3 Audition ERROR: required devices not found")
        for tag, _ in pairs(manager.machine.devices) do
            print("  device: " .. tag)
        end
        return
    end

    main_mem  = maincpu.spaces["program"]
    audio_mem = audiocpu.spaces["program"]

    -- Halt main CPU
    halt_main_cpu(main_mem)

    -- Resolve control key codes
    KEY_TAB   = input_mgr:code_from_token("KEYCODE_TAB")
    KEY_UP    = input_mgr:code_from_token("KEYCODE_UP")
    KEY_DOWN  = input_mgr:code_from_token("KEYCODE_DOWN")
    KEY_LEFT  = input_mgr:code_from_token("KEYCODE_LEFT")
    KEY_RIGHT = input_mgr:code_from_token("KEYCODE_RIGHT")
    KEY_PLUS  = input_mgr:code_from_token("KEYCODE_EQUALS")   -- = / + key
    KEY_MINUS = input_mgr:code_from_token("KEYCODE_MINUS")
    KEY_PGUP  = input_mgr:code_from_token("KEYCODE_PGUP")
    KEY_PGDN  = input_mgr:code_from_token("KEYCODE_PGDN")

    -- Resolve piano key codes
    for _, def in ipairs(PIANO_KEY_DEFS) do
        table.insert(piano_keys, {
            code       = input_mgr:code_from_token(def[1]),
            note_index = def[2],
            label      = def[3],
        })
    end

    -- Load default patches from ROM (will be programmed in phase 2)
    fm_patch_data    = load_fm_patch(fm_patch_num)
    k7232_patch_data = load_k7232_patch(k7232_patch_num)

    print("G3 Audition: phase 1 done, waiting for Z80 startup...")
end

local function init_phase2()
    -- Z80 has reached its main loop; program initial FM patch to channel 0
    fm_program_patch(fm_patch_data, FM_CHANNEL)
    -- Set initial volume shadow for K7232
    k7232_vol_shadow = 0xFF   -- both channels max vol initially (we'll manage ch A)
    print(string.format("G3 Audition: ready. FM patch %02X loaded.", fm_patch_num))
    print("  Tab=mode  Up/Dn=instrument  +/-=octave  Piano keys=play notes")
end

-- ============================================================
-- Entry point
-- ============================================================

emu.register_frame_done(function()
    local ok, err

    if _init_phase == 0 then
        _init_phase = 1
        ok, err = pcall(init_phase1)
        if not ok then
            print("G3 Audition INIT ERROR: " .. tostring(err))
        end
        return
    end

    if _init_phase == 1 then
        -- Wait for Z80 to reach main loop PC
        _wait_frames = _wait_frames + 1
        local pc = audiocpu and audiocpu.state["PC"].value or -1
        if (pc >= Z80_MAIN_LOOP_PC_LO and pc <= Z80_MAIN_LOOP_PC_HI) or _wait_frames >= WAIT_TIMEOUT then
            if _wait_frames >= WAIT_TIMEOUT and not (pc >= Z80_MAIN_LOOP_PC_LO and pc <= Z80_MAIN_LOOP_PC_HI) then
                print(string.format(
                    "G3 Audition: Z80 timeout (PC=%04X), proceeding anyway", pc))
            end
            _init_phase = 2
            ok, err = pcall(init_phase2)
            if not ok then
                print("G3 Audition INIT2 ERROR: " .. tostring(err))
            end
        end
        -- Draw waiting screen
        if screen then
            pcall(draw_ui)
        end
        return
    end

    -- Normal operation
    ok, err = pcall(function()
        handle_input()
        draw_ui()
    end)
    if not ok then
        print("G3 Audition FRAME ERROR: " .. tostring(err))
    end
end, "g3_audition")
