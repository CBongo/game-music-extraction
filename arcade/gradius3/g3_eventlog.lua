-- g3_eventlog.lua
-- Gradius 3 arcade sound event listener
-- Monitors sound commands sent from main CPU to audio CPU in real time
-- Usage: mame gradius3 -autoboot_script g3_eventlog.lua

-- by Claude under direction from Chris Bongaarts 2026-03-19

-- Load shared constants and helpers
dofile(debug.getinfo(1,"S").source:match("@?(.+[/\\])") .. "g3_common.lua")

-- ============================================================
-- Constants
-- ============================================================

local MAX_LOG = 20       -- max entries to display
local FADE_FRAMES = 300  -- frames before entry fully fades (~5s at 60fps)

-- Colors specific to event log (category colors)
local COL_HEADER = 0xFFFFFF00   -- yellow header
local COL_SFX    = 0xFFFFFF44   -- yellow for SFX
local COL_VOICE  = 0xFF44FFFF   -- cyan for voice samples
local COL_BGM    = 0xFF44FF44   -- green for BGM

-- ============================================================
-- State
-- ============================================================

local event_log = {}   -- {value, frame, category, color}
local frame_count = 0
local tap_handle = nil

-- ============================================================
-- Helpers
-- ============================================================

local function categorize(val)
    if val >= 0x80 then
        return "BGM", COL_BGM
    elseif val >= 0x50 then
        return "Voice", COL_VOICE
    else
        return "SFX", COL_SFX
    end
end

local function log_event(val)
    if val == 0x00 or val == 0xFF then return end
    local cat, col = categorize(val)
    table.insert(event_log, 1, {
        value    = val,
        frame    = frame_count,
        category = cat,
        color    = col,
    })
    if #event_log > MAX_LOG then
        table.remove(event_log)
    end
    print(string.format("G3 Sound Event: %02X [%s] %s", val, cat, event_names[val] or ""))
end

-- ============================================================
-- Drawing
-- ============================================================

local function draw_overlay(screen)
    local container = screen.container

    -- Background panel on the right
    local px = 0.70
    container:draw_box(px - 0.01, 0.02, 1.00, 0.98, 0xFF444444, COL_BG)

    -- Header
    container:draw_text(px, 0.03, "SOUND EVENTS", COL_HEADER, 0x00000000)

    local y = 0.08
    local line_h = 0.040

    for _, entry in ipairs(event_log) do
        local age = frame_count - entry.frame
        -- Fade alpha from FF down to 40 over FADE_FRAMES
        local alpha = math.max(0x40, 0xFF - math.floor(age * (0xFF - 0x40) / FADE_FRAMES))
        local col = (alpha * 0x01000000) + (entry.color % 0x01000000)

        local name = event_names[entry.value] or ""
        local text = string.format("%02X %-5s %s", entry.value, entry.category, name)
        container:draw_text(px, y, text, col, 0x00000000)
        y = y + line_h
        if y > 0.95 then break end
    end
end

-- ============================================================
-- Initialization
-- ============================================================

local function init()
    local maincpu = manager.machine.devices[":maincpu"]
    if not maincpu then
        print("G3 EventLog ERROR: :maincpu device not found")
        return
    end

    local main_mem = maincpu.spaces["program"]
    if not main_mem then
        print("G3 EventLog ERROR: maincpu program space not found")
        return
    end

    local screen = manager.machine.screens[":screen"]
    if not screen then
        print("G3 EventLog ERROR: :screen not found")
        return
    end

    -- Install passive write tap on sound latch address
    tap_handle = main_mem:install_write_tap(
        SOUND_LATCH_ADDR, SOUND_LATCH_ADDR + 1,
        "g3_event_monitor",
        function(_offset, data, _mask)
            log_event(data & 0xFF)
            return data  -- pass through unchanged
        end
    )

    if tap_handle then
        print("G3 EventLog: write tap installed at 0xE8000")
    else
        print("G3 EventLog WARNING: write tap installation returned nil")
    end

    -- Register frame callback
    emu.register_frame_done(function()
        frame_count = frame_count + 1
        draw_overlay(screen)
    end, "g3_eventlog_frame")

    print("G3 EventLog: initialized. Sound events will appear on screen right side.")
end

-- ============================================================
-- Entry point
-- ============================================================

-- Delay init one frame so machine is fully started
local _init_done = false
emu.register_frame_done(function()
    if not _init_done then
        _init_done = true
        init()
    end
end, "g3_eventlog_init")
