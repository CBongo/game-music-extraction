-- g3_soundtest.lua
-- Gradius 3 arcade sound test mode
-- Allows auditioning individual sound events and muting/soloing channels
-- Usage: mame gradius3 -autoboot_script g3_soundtest.lua

-- By Claude under direction from Chris Bongaarts 2026-03-19

-- ============================================================
-- Constants
-- ============================================================

-- Main CPU addresses
local SOUND_LATCH_ADDR = 0xE8000   -- main CPU: write event byte here
local SOUND_IRQ_ADDR   = 0xF0000   -- main CPU: write anything here to trigger Z80 IRQ

-- Audio CPU (Z80) RAM
local VSTATE_BASE  = 0xF800
local VSTATE_SIZE  = 0x50
local VSTATE_COUNT = 10

-- vstate field offsets (from eventstate.h)
local VS_VOICENUM  = 0x00
local VS_SONGNUM   = 0x01
local VS_LEVEL_ADJ = 0x0E
local VS_TRANSPOSE = 0x1A
local VS_PAN_FLAGS = 0x1F
local VS_PATCH     = 0x3E
local VS_MUTED     = 0x3F
local VS_K232_VOL  = 0x41

-- YM2151 Z80 addresses
local YM_ADDR_PORT = 0xF030
local YM_DATA_PORT = 0xF031

-- K007232 Z80 addresses
local K232_BASE    = 0xF020
local K232_END     = 0xF02D
local K232_CHA_START = 5    -- register offset: writing triggers ch A playback
local K232_CHB_START = 11   -- register offset: writing triggers ch B playback
local K232_VOL_REG   = 12   -- register offset: volume (high nibble=chA, low nibble=chB)

-- Colors (0xAARRGGBB)
local COL_TITLE    = 0xFFFFFF00
local COL_WHITE    = 0xFFFFFFFF
local COL_GREEN    = 0xFF44FF44
local COL_RED      = 0xFFFF4444
local COL_YELLOW   = 0xFFFFFF44
local COL_CYAN     = 0xFF44FFFF
local COL_GRAY     = 0xFF888888
local COL_BG       = 0xD0000000
local COL_BOX_LINE = 0xFF666666

-- Event name lookup (from latchcmds.txt)
local event_names = {
    [0x01] = "Player Shoot",
    [0x05] = "Enemy Shoot",
    [0x06] = "Enemy Multishoot",
    [0x0A] = "Enemy Hit",
    [0x0B] = "Enemy Hit 2",
    [0x0C] = "Enemy Hit 3",
    [0x0D] = "Enemy Hit 4",
    [0x0E] = "Weapon Select",
    [0x13] = "Enemy Self-Destruct",
    [0x40] = "Collect Powerup",
    [0x41] = "Confirm Initials",
    [0x45] = "Weapon Confirm",
    [0x47] = "Player Explode",
    [0x48] = "Coin Drop",
    [0x50] = '"Double"',
    [0x51] = '"Laser"',
    [0x52] = '"Missile"',
    [0x53] = '"Optional"',
    [0x54] = '"Force Field"',
    [0x55] = '"Two Way"',
    [0x56] = '"Shield"',
    [0x57] = '"Speed Up"',
    [0x58] = '"Destroy Them All"',
    [0x59] = '"Destroy The Core"',
    [0x5A] = '"Destroy The Eye"',
    [0x5B] = '"Destroy The Mouth"',
    [0x5C] = '"Destroy The Chest"',
    [0x5D] = '"Warning"',
    [0x5E] = '"Speech Line 1"',
    [0x5F] = '"Speech Line 2"',
    [0x60] = '"Arrrrrrrrrr"',
    [0x80] = "Stage Start BGM",
    [0x81] = "Stage 1 BGM",
    [0x87] = "Game Over",
    [0x88] = "High Score BGM",
    [0x89] = "Weaponry BGM",
    [0x8C] = "Title Theme"
}

-- ============================================================
-- State
-- ============================================================

local selected_event = 0x8C  -- start on Title Theme
local mute_state = {}        -- mute_state[0..9]: true = muted
local solo_ch = nil          -- nil = no solo; number = solo channel

-- YM2151 shadow register file
local ym_regs = {}
local ym_addr_reg = 0

-- Devices (set in init)
local maincpu, main_mem
local audiocpu, audio_mem
local screen, input_mgr

-- Tap handles (must be module-level to prevent GC from removing taps)
local tap_latch, tap_ym_addr, tap_ym_data, tap_k232

-- Input codes (set in init)
local KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT
local KEY_ENTER
local KEY_0, KEY_1, KEY_2, KEY_3, KEY_4
local KEY_5, KEY_6, KEY_7, KEY_8, KEY_9
local KEY_S, KEY_R

for i = 0, 9 do mute_state[i] = false end

-- ============================================================
-- Sound event triggering
-- ============================================================

local function trigger_event(event_byte)
    -- Write event byte to soundlatch, then trigger Z80 IRQ
    -- Uses the same hardware path as the main CPU: write to latch, then poke IRQ
    main_mem:write_u8(SOUND_LATCH_ADDR, event_byte)
    main_mem:write_u8(SOUND_IRQ_ADDR, 0xFF)
end

-- ============================================================
-- Mute/solo helpers
-- ============================================================

local function compute_mute_mask()
    -- Returns table of effective mute per channel considering solo mode
    local mask = {}
    if solo_ch ~= nil then
        for i = 0, 9 do
            mask[i] = (i ~= solo_ch)
        end
    else
        for i = 0, 9 do
            mask[i] = mute_state[i]
        end
    end
    return mask
end

local function is_ch_muted(ch)
    if solo_ch ~= nil then return ch ~= solo_ch end
    return mute_state[ch]
end

local function apply_mutes()
    local mask = compute_mute_mask()
    for ch = 0, VSTATE_COUNT - 1 do
        local addr = VSTATE_BASE + (ch * VSTATE_SIZE) + VS_MUTED
        audio_mem:write_u8(addr, mask[ch] and 1 or 0)
    end
end

local function toggle_mute(ch)
    if solo_ch ~= nil then
        -- In solo mode: exit solo, restore mute state
        solo_ch = nil
    end
    mute_state[ch] = not mute_state[ch]
end

local function toggle_solo(ch)
    if solo_ch == ch then
        solo_ch = nil  -- toggle off
    else
        solo_ch = ch
    end
end

local function reset_mutes()
    for i = 0, 9 do mute_state[i] = false end
    solo_ch = nil
end

-- ============================================================
-- Input handling
-- ============================================================

local function handle_input()
    if not input_mgr then return end

    -- Up/Down: change event byte
    if input_mgr:code_pressed_once(KEY_UP) then
        selected_event = (selected_event + 1) % 0x100
    elseif input_mgr:code_pressed_once(KEY_DOWN) then
        selected_event = (selected_event - 1 + 0x100) % 0x100
    end

    -- Left/Right: change by 0x10
    if input_mgr:code_pressed_once(KEY_RIGHT) then
        selected_event = (selected_event + 0x10) % 0x100
    elseif input_mgr:code_pressed_once(KEY_LEFT) then
        selected_event = (selected_event - 0x10 + 0x100) % 0x100
    end

    -- Enter: trigger selected event
    if input_mgr:code_pressed_once(KEY_ENTER) then
        trigger_event(selected_event)
        print(string.format("G3 SoundTest: triggered event %02X", selected_event))
    end

    -- 0-9: toggle mute for that channel
    local num_keys = {KEY_0,KEY_1,KEY_2,KEY_3,KEY_4,KEY_5,KEY_6,KEY_7,KEY_8,KEY_9}
    for i, key in ipairs(num_keys) do
        if input_mgr:code_pressed_once(key) then
            toggle_mute(i - 1)
        end
    end

    -- S: cycle solo (hold concept: toggle solo on last-pressed channel not yet convenient, so S solos ch 0 by default toggle)
    -- Better: S + number key would be ideal, but MAME input doesn't support combos easily.
    -- For now S toggles solo on the currently-hovered channel (use the selected event low nibble as proxy, or just cycle through)
    if input_mgr:code_pressed_once(KEY_S) then
        -- Solo the channel matching low nibble of selected_event mod 10
        local ch = selected_event % 10
        toggle_solo(ch)
    end

    -- R: reset all mutes
    if input_mgr:code_pressed_once(KEY_R) then
        reset_mutes()
    end

end

-- ============================================================
-- Voice state reading
-- ============================================================

local function read_vstate(ch)
    local base = VSTATE_BASE + ch * VSTATE_SIZE
    return {
        songnum   = audio_mem:read_u8(base + VS_SONGNUM),
        patch     = audio_mem:read_u8(base + VS_PATCH),
        level_adj = audio_mem:read_u8(base + VS_LEVEL_ADJ),
        transpose = audio_mem:read_u8(base + VS_TRANSPOSE),
        pan       = audio_mem:read_u8(base + VS_PAN_FLAGS),
        muted     = audio_mem:read_u8(base + VS_MUTED),
        k232_vol  = audio_mem:read_u8(base + VS_K232_VOL),
    }
end

-- ============================================================
-- Drawing
-- ============================================================

local function event_category(val)
    if val >= 0x80 then return "Music"
    elseif val >= 0x50 then return "Voice"
    elseif val >= 0x01 then return "SFX"
    else return "---"
    end
end

local function draw_ui()
    local container = screen.container

    -- Background
    container:draw_box(0.0, 0.0, 1.0, 1.0, 0xFF222222, COL_BG)

    -- Title
    container:draw_text(0.02, 0.01, "GRADIUS 3 SOUND TEST", COL_TITLE, 0x00000000)

    -- Event selector
    local ev_name = event_names[selected_event] or ""
    local ev_cat  = event_category(selected_event)
    container:draw_text(0.02, 0.10,
        string.format("Event: [%02X]  %-20s  Category: %s (Up/Dn=byte  Lf/Rt=+/-0x10  Enter=play)",
            selected_event, ev_name, ev_cat),
        COL_WHITE, 0x00000000)

    -- Channel boxes
    container:draw_text(0.02, 0.17, "Channels (0-9=mute  S=solo  R=reset):", COL_WHITE, 0x00000000)
    local mask = compute_mute_mask()
    for ch = 0, 9 do
        local bx = 0.02 + ch * 0.095
        local by = 0.21
        local bx2, by2 = bx + 0.085, by + 0.055

        local box_col, lbl_col
        if solo_ch == ch then
            box_col = 0xFF005500  -- dark green bg for solo
            lbl_col = COL_GREEN
        elseif mask[ch] then
            box_col = 0xFF550000  -- dark red bg for muted
            lbl_col = COL_RED
        else
            box_col = 0xFF003300
            lbl_col = COL_GREEN
        end

        container:draw_box(bx, by, bx2, by2, COL_BOX_LINE, box_col)
        local label = string.format("Ch %d", ch)
        local state = solo_ch == ch and "SOLO" or (mask[ch] and "MUTE" or "  ON")
        container:draw_text(bx + 0.005, by + 0.005, label, lbl_col, 0x00000000)
        container:draw_text(bx + 0.005, by + 0.030, state,  lbl_col, 0x00000000)
    end

    -- Voice state table
    container:draw_text(0.02, 0.30, "Voice State:", COL_YELLOW, 0x00000000)
    container:draw_text(0.02, 0.34,
        string.format("%-3s %-4s %-5s %-5s %-5s %-3s %-4s",
            "Ch", "Song", "Patch", "LvAdj", "Tsp", "Pan", "Mute"),
        COL_GRAY, 0x00000000)

    for ch = 0, VSTATE_COUNT - 1 do
        local vs = read_vstate(ch)
        local y = 0.38 + ch * 0.038
        local col = mask[ch] and COL_RED or COL_WHITE
        container:draw_text(0.02, y,
            string.format("%-3d %02X    %02X     %02X     %02X    %02X   %s",
                ch, vs.songnum, vs.patch, vs.level_adj,
                vs.transpose, vs.pan,
                vs.muted == 1 and "M" or "."),
            col, 0x00000000)
    end

    -- YM2151 key state (from shadow registers)
    container:draw_text(0.55, 0.30, "YM2151 State (shadow regs):", COL_YELLOW, 0x00000000)
    container:draw_text(0.55, 0.34,
        string.format("%-4s %-5s %-5s %-3s",
            "Ch", "KC", "Pan", "Algo"),
        COL_GRAY, 0x00000000)
    for ch = 0, 7 do
        local y = 0.38 + ch * 0.038
        local kc  = ym_regs[0x28 + ch] or 0
        local pan = ym_regs[0x20 + ch] or 0
        container:draw_text(0.55, y,
            string.format("%-4d %02X    %02X    %02X",
                ch, kc, pan, pan & 0x07),
            COL_WHITE, 0x00000000)
    end

    -- Key help at bottom
    container:draw_text(0.02, 0.96,
        "0-9=mute chan  S=solo  R=reset mutes  Up/Dn=event  Lf/Rt=+/-0x10  Enter=play",
        COL_GRAY, 0x00000000)
end

-- ============================================================
-- Initialization
-- ============================================================

local function init()
    maincpu = manager.machine.devices[":maincpu"]
    audiocpu = manager.machine.devices[":audiocpu"]
    screen = manager.machine.screens[":screen"]
    input_mgr = manager.machine.input

    if not maincpu or not audiocpu or not screen then
        print("G3 SoundTest ERROR: required devices not found")
        for tag, _ in pairs(manager.machine.devices) do
            print("  device: " .. tag)
        end
        return
    end

    main_mem  = maincpu.spaces["program"]
    audio_mem = audiocpu.spaces["program"]

    -- Resolve input codes
    KEY_UP    = input_mgr:code_from_token("KEYCODE_UP")
    KEY_DOWN  = input_mgr:code_from_token("KEYCODE_DOWN")
    KEY_LEFT  = input_mgr:code_from_token("KEYCODE_LEFT")
    KEY_RIGHT = input_mgr:code_from_token("KEYCODE_RIGHT")
    KEY_ENTER = input_mgr:code_from_token("KEYCODE_ENTER")
    KEY_0     = input_mgr:code_from_token("KEYCODE_0")
    KEY_1     = input_mgr:code_from_token("KEYCODE_1")
    KEY_2     = input_mgr:code_from_token("KEYCODE_2")
    KEY_3     = input_mgr:code_from_token("KEYCODE_3")
    KEY_4     = input_mgr:code_from_token("KEYCODE_4")
    KEY_5     = input_mgr:code_from_token("KEYCODE_5")
    KEY_6     = input_mgr:code_from_token("KEYCODE_6")
    KEY_7     = input_mgr:code_from_token("KEYCODE_7")
    KEY_8     = input_mgr:code_from_token("KEYCODE_8")
    KEY_9     = input_mgr:code_from_token("KEYCODE_9")
    KEY_S     = input_mgr:code_from_token("KEYCODE_S")
    KEY_R     = input_mgr:code_from_token("KEYCODE_R")

    -- Trap main CPU in an infinite watchdog loop.
    -- We watch for the audio init event (0x00) written to the sound latch
    -- at 0xE8000 (which happens at PC ~0x152e). When we see it, we know
    -- the ROM checksum has passed and the CPU hasn't yet reached 0x1544,
    -- so we can safely patch the ROM. The patched instruction at 0x1544
    -- becomes BRA.S 0x153c (opcode 0x60F6), looping the main CPU on the
    -- watchdog write forever.
    local maincpu_trapped = false
    tap_latch = main_mem:install_write_tap(SOUND_LATCH_ADDR, SOUND_LATCH_ADDR + 1,
        "g3_wait_for_init",
        function(offset, data, mask)
            -- print("G3ST: write tap fired for sound latch")
            if not maincpu_trapped then
                main_mem:write_direct_u16(0x1544, 0x60F6)
                maincpu_trapped = true
                print("G3 SoundTest: patched ROM at 0x1544 (BRA.S 0x153c) after audio init")
            end
            return data  -- pass the init event through to the audio CPU
        end
    )
    if tap_latch then
        print("G3 SoundTest: write tap installed at 0xE8000")
    else
        print("G3 SoundTest WARNING: write tap installation returned nil")
    end

    -- Shadow YM2151 register writes from Z80 + enforce mutes
    tap_ym_addr = audio_mem:install_write_tap(
        YM_ADDR_PORT, YM_ADDR_PORT,
        "g3_ym_addr",
        function(offset, data, mask)
            ym_addr_reg = data
            return data
        end
    )
    tap_ym_data = audio_mem:install_write_tap(
        YM_DATA_PORT, YM_DATA_PORT,
        "g3_ym_data",
        function(offset, data, mask)
            ym_regs[ym_addr_reg] = data
            -- Reg 0x08: key on/off. Bits 6-3 = operator mask, bits 2-0 = channel
            if ym_addr_reg == 0x08 then
                local ch = data & 0x07
                local ops = data & 0x78  -- operator bits
                if ops ~= 0 and is_ch_muted(ch) then
                    -- Convert key-on to key-off (strip operator bits)
                    return ch  -- key-off for this channel
                end
            end
            -- Reg 0x60-0x7F: total level (volume). Suppress for muted channels.
            if ym_addr_reg >= 0x60 and ym_addr_reg <= 0x7F then
                local ch = ym_addr_reg & 0x07
                if is_ch_muted(ch) then
                    return 0x7F  -- max attenuation = silence
                end
            end
            return data
        end
    )

    -- K007232 register tap: suppress triggers and volume for muted channels 8/9
    tap_k232 = audio_mem:install_write_tap(
        K232_BASE, K232_END,
        "g3_k232_mute",
        function(offset, data, mask)
            local reg = offset - K232_BASE
            -- Reg 5: ch A start trigger (channel 8)
            if reg == K232_CHA_START and is_ch_muted(8) then
                return nil  -- suppress sample trigger
            end
            -- Reg 11: ch B start trigger (channel 9)
            if reg == K232_CHB_START and is_ch_muted(9) then
                return nil  -- suppress sample trigger
            end
            -- Reg 12: volume (high nibble = ch A, low nibble = ch B)
            if reg == K232_VOL_REG then
                local vol = data
                if is_ch_muted(8) then vol = vol & 0x0F end  -- zero ch A volume
                if is_ch_muted(9) then vol = vol & 0xF0 end  -- zero ch B volume
                return vol
            end
            return data
        end
    )

    print("G3 SoundTest: initialized.")
    print("  Up/Down = select event  Enter = play  0-9 = mute channel  S = solo  R = reset")
end

-- ============================================================
-- Entry point - single top-level frame callback handles
-- both init (first frame) and main loop (subsequent frames).
-- Registering frame_done from within frame_done doesn't work
-- reliably in MAME, so we use one callback for everything.
-- ============================================================

local _init_done = false
emu.register_frame_done(function()
    if not _init_done then
        _init_done = true
        local ok, err = pcall(init)
        if not ok then
            print("G3 SoundTest INIT ERROR: " .. tostring(err))
        end
        return  -- skip main loop on init frame
    end

    local ok, err = pcall(function()
        handle_input()
        apply_mutes()
        draw_ui()
    end)
    if not ok then
        print("G3 SoundTest FRAME ERROR: " .. tostring(err))
    end
end, "g3_soundtest")
