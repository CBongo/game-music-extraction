-- Isolated AKAO articulation probe on MAME 0.285 Konami GV's PlayStation SPU.
local function env_integer(name, lo, hi)
    local value = tonumber(os.getenv(name) or "")
    if not value or value ~= math.floor(value) or value < lo or value > hi then
        error("invalid " .. name)
    end
    return value
end
local payload_path = assert(os.getenv("GME_PSX_PAYLOAD"), "missing payload path")
local receipt_path = assert(os.getenv("GME_PSX_RECEIPT"), "missing receipt path")
local dest = env_integer("GME_PSX_DEST", 0, 0x7ffff)
local payload_length = env_integer("GME_PSX_PAYLOAD_LENGTH", 16, 0x80000)
local sample_start = env_integer("GME_PSX_START", 0, 0x7ffff)
local loop_start = env_integer("GME_PSX_LOOP", 0, 0x7ffff)
local sample_end = env_integer("GME_PSX_END", 0, 0x7ffff)
local adsr1 = env_integer("GME_PSX_ADSR1", 0, 65535)
local adsr2 = env_integer("GME_PSX_ADSR2", 0, 65535)
local pitch = env_integer("GME_PSX_PITCH", 1, 0x3fff)
local hold_frames = env_integer("GME_PSX_HOLD", 1, 3600)
local tail_frames = env_integer("GME_PSX_TAIL", 0, 3600)
local machine = manager.machine
local phase, held, tailed = "start", 0, 0
local note_on, note_off, capture_end, frame_period
local program, cpu
local verification = {}

local function quote(s)
    return '"' .. tostring(s):gsub('\\', '\\\\'):gsub('"', '\\"') .. '"'
end
local function receipt(status, failure)
    local fields = {
        '"schema_version":"akao-spu-native-probe-receipt/v1"',
        '"status":' .. quote(status),
        '"payload_length":' .. payload_length,
        '"spu_destination":' .. dest,
        '"sample_start":' .. sample_start,
        '"loop_start":' .. loop_start,
        '"sample_end":' .. sample_end,
        '"adsr1":' .. adsr1,
        '"adsr2":' .. adsr2,
        '"pitch_register_raw_spu":' .. pitch,
        '"hold_frames":' .. hold_frames,
        '"tail_frames":' .. tail_frames,
        '"frame_period_seconds":' .. string.format("%.12f", frame_period or 0),
        '"cpu_idle_pc":2684420096'
    }
    if note_on then fields[#fields+1] = '"note_on_time_seconds":' .. string.format("%.12f", note_on) end
    if note_off then fields[#fields+1] = '"note_off_time_seconds":' .. string.format("%.12f", note_off) end
    if status == "ok" then
        fields[#fields+1] = '"capture_end_time_seconds":' .. string.format("%.12f", capture_end)
    end
    if status == "ok" then
        for _, name in ipairs({
            "words_written", "verified_payload_bytes", "dma_readback_base",
            "first_word", "last_word",
            "voice_start_register_readback", "voice_pitch_register_readback",
            "adsr1_readback", "adsr2_readback", "spu_control_readback",
            "cpu_pc_after_upload"
        }) do fields[#fields+1] = quote(name) .. ":" .. assert(verification[name], "missing " .. name) end
    end
    if failure then fields[#fields+1] = '"failure":' .. quote(failure) end
    local f = assert(io.open(receipt_path, "wb"))
    f:write("{" .. table.concat(fields, ",") .. "}\n")
    f:close()
end
local function write16(addr, value) program:write_u16(addr, value) end
local function fail(err)
    phase = "failed"
    if program then pcall(write16, 0x1f801d8c, 1) end
    print("AKAO SPU probe ERROR: " .. tostring(err))
    pcall(receipt, "failed", tostring(err))
    machine:exit()
end
local function begin()
    cpu = assert(machine.devices[":maincpu"], "Konami GV main CPU unavailable")
    assert(machine.devices[":spu"], "Konami GV SPU unavailable")
    program = assert(cpu.spaces["program"], "main CPU program space unavailable")
    frame_period = assert(machine.screens[":screen"], "screen unavailable").frame_period

    -- The uncached KSEG1 RAM loop stops the board BIOS from modifying SPU state.
    program:write_u32(0xa0010000, 0x1000ffff) -- beq zero,zero,-1
    program:write_u32(0xa0010004, 0)          -- delay slot nop
    cpu.state["SR"].value = 0                 -- mask external interrupts
    cpu.state["pc"].value = 0xa0010000
    if program:read_u32(0xa0010000) ~= 0x1000ffff then error("CPU idle RAM write did not stick") end

    -- MAME 0.285 writes each 16-bit data register word immediately.
    local file = assert(io.open(payload_path, "rb"))
    local bytes = assert(file:read("*a"))
    file:close()
    if #bytes ~= payload_length then error("payload length changed") end
    if dest % 8 ~= 0 or #bytes % 2 ~= 0 or dest + #bytes > 0x80000 then
        error("unaligned or overflowing SPU transfer")
    end
    write16(0x1f801da6, dest // 8)
    for i = 1, #bytes, 2 do
        write16(0x1f801da8, bytes:byte(i) | (bytes:byte(i+1) << 8))
    end
    verification.words_written = #bytes // 2
    verification.first_word = bytes:byte(1) | (bytes:byte(2) << 8)
    verification.last_word = bytes:byte(#bytes-1) | (bytes:byte(#bytes) << 8)
    verification.cpu_pc_after_upload = cpu.state["pc"].value
    -- DMA channel 4 copies SPU RAM back into unused PSX main RAM.
    -- Compare every byte, including the selected ADPCM end block.
    local dma_base = 0x20000
    write16(0x1f801da6, dest // 8)
    program:write_u32(0x1f8010f0, program:read_u32(0x1f8010f0) | 0x00080000)
    program:write_u32(0x1f8010c0, dma_base)
    program:write_u32(0x1f8010c4, #bytes // 4)
    program:write_u32(0x1f8010c8, 0x01000000)
    for i = 1, #bytes do
        if program:read_u8(0xa0000000 + dma_base + i - 1) ~= bytes:byte(i) then
            error(string.format("SPU DMA readback differs at payload byte %d", i - 1))
        end
    end
    verification.verified_payload_bytes = #bytes
    verification.dma_readback_base = dma_base
    if cpu.state["pc"].value ~= 0xa0010000 and cpu.state["pc"].value ~= 0xa0010004 then
        error("CPU left idle loop during SPU transfer")
    end
    -- Full stereo, no reverb/FM/noise, and one native voice.
    write16(0x1f801d8c, 0xffff)
    write16(0x1f801d8e, 0x00ff)
    write16(0x1f801d80, 0x3fff)
    write16(0x1f801d82, 0x3fff)
    write16(0x1f801d90, 0)
    write16(0x1f801d92, 0)
    write16(0x1f801d94, 0)
    write16(0x1f801d96, 0)
    write16(0x1f801d98, 0)
    write16(0x1f801d9a, 0)
    write16(0x1f801daa, 0xc000)
    write16(0x1f801c00, 0x3fff)
    write16(0x1f801c02, 0x3fff)
    write16(0x1f801c04, pitch)
    write16(0x1f801c06, (dest + sample_start) // 8)
    write16(0x1f801c08, adsr1)
    write16(0x1f801c0a, adsr2)
    write16(0x1f801c0e, (dest + loop_start) // 8)
    write16(0x1f801d88, 1)
    verification.voice_start_register_readback = program:read_u16(0x1f801c06)
    verification.voice_pitch_register_readback = program:read_u16(0x1f801c04)
    verification.adsr1_readback = program:read_u16(0x1f801c08)
    verification.adsr2_readback = program:read_u16(0x1f801c0a)
    verification.spu_control_readback = program:read_u16(0x1f801daa)
    note_on = machine.time:as_double()
    phase = "hold"
end

emu.register_frame_done(function()
    local ok, err = pcall(function()
        if phase == "start" then begin()
        elseif phase == "hold" then
            held = held + 1
            if held >= hold_frames then
                write16(0x1f801d8c, 1)
                note_off = machine.time:as_double()
                phase = "tail"
                if tail_frames == 0 then capture_end = machine.time:as_double(); phase = "flush" end
            end
        elseif phase == "tail" then
            tailed = tailed + 1
            if tailed >= tail_frames then capture_end = machine.time:as_double(); phase = "flush" end
        elseif phase == "flush" then receipt("ok"); machine:exit()
        end
    end)
    if not ok then fail(err) end
end)

