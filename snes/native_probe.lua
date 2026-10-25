-- MAME headless S-SMP/S-DSP native voice probe.
local RECEIPT_VERSION = "snes-native-probe-receipt/v1"
local RAM_SIZE = 0x10000
local function integer_env(name, lo, hi)
    local v = tonumber(os.getenv(name) or "")
    if not v or v ~= math.floor(v) or v < lo or v > hi then error("invalid " .. name) end
    return v
end
local function read_all(name)
    local path = os.getenv(name)
    if not path or path == "" then error("missing " .. name) end
    local f, err = io.open(path, "rb")
    if not f then error("cannot open " .. name .. ": " .. tostring(err)) end
    local bytes = f:read("*a")
    f:close()
    return bytes
end
local function quote(value)
    local s, out = tostring(value), {}
    for i = 1, #s do
        local b = s:byte(i)
        if b == 34 then out[#out + 1] = '\\\"'
        elseif b == 92 then out[#out + 1] = '\\\\'
        elseif b < 32 then out[#out + 1] = string.format("\\u%04x", b)
        else out[#out + 1] = string.char(b) end
    end
    return '"' .. table.concat(out) .. '"'
end
local receipt_path = os.getenv("GME_SNES_RECEIPT")
if not receipt_path or receipt_path == "" then error("missing GME_SNES_RECEIPT") end
local machine = manager.machine
local phase = "startup"
local startup_frames, held_frames, tailed_frames = 0, 0, 0
local ram, dsp, source_ram_sha, source_dsp_sha
local voice, pitch, hold_frames, tail_frames, halt_pc
local soundcpu, program, pc_entry
local registers = {}
local srcn, sample_start, sample_loop, sample_end, brr_frames, looping
local note_on, note_off, capture_end_time
local aram_verified = 0
local kon_value, koff_value = 0, 0

local function receipt(status, failure)
    local f, err = io.open(receipt_path, "wb")
    if not f then print("SNES receipt write failed: " .. tostring(err)); return end
    local fields = {
        '"schema_version":' .. quote(RECEIPT_VERSION),
        '"status":' .. quote(status),
        '"source_aram_sha256":' .. quote(source_ram_sha or ""),
        '"source_dsp_sha256":' .. quote(source_dsp_sha or ""),
        '"aram_source_bytes":' .. tostring(ram and #ram or 0),
        '"aram_loaded_bytes":' .. tostring(status == "ok" and (RAM_SIZE - 16) or 0),
        '"aram_readback_verified_bytes":' .. tostring(aram_verified),
        '"aram_skipped_io_bytes":16',
        '"aram_loaded_readback_ok":' .. tostring(status == "ok" and aram_verified == RAM_SIZE - 16),
        '"aram_modified_bytes":1',
        '"stop_opcode":255',
        '"voice":' .. tostring(voice or -1), '"srcn":' .. tostring(srcn or -1),
        '"pitch_register":' .. tostring(pitch or -1),
        '"adsr1":' .. tostring(dsp and dsp:byte(voice * 16 + 6) or -1),
        '"adsr2":' .. tostring(dsp and dsp:byte(voice * 16 + 7) or -1),
        '"gain":' .. tostring(dsp and dsp:byte(voice * 16 + 8) or -1),
        '"dir_register":' .. tostring(dsp and dsp:byte(0x5E) or -1),
        '"sample_start_address":' .. tostring(sample_start or -1),
        '"sample_loop_address":' .. tostring(sample_loop or -1),
        '"sample_end_address_exclusive":' .. tostring(sample_end or -1),
        '"brr_frame_count":' .. tostring(brr_frames or -1),
        '"sample_looping":' .. tostring(looping or false),
        '"halt_pc":' .. tostring(halt_pc or -1),
        '"hold_frames":' .. tostring(hold_frames or -1),
        '"tail_frames":' .. tostring(tail_frames or -1),
        '"startup_frames":' .. tostring(startup_frames),
        '"kon_value":' .. tostring(kon_value),
        '"koff_value":' .. tostring(koff_value),
        '"flush_frames":1',
        '"frame_period_seconds":' .. string.format("%.12f", machine.screens[":screen"].frame_period)
    }
    if note_on then fields[#fields + 1] = '"note_on_time_seconds":' .. string.format("%.12f", note_on) end
    if note_off then fields[#fields + 1] = '"note_off_time_seconds":' .. string.format("%.12f", note_off) end
    if status == "ok" then
        fields[#fields + 1] = '"capture_end_time_seconds":' .. string.format("%.12f", capture_end_time)
        fields[#fields + 1] = '"capture_flush_end_time_seconds":' .. string.format("%.12f", machine.time:as_double())
        local keys = {}
        for key, value in pairs(registers) do
            keys[#keys + 1] = quote(key) .. ":" .. tostring(value)
        end
        fields[#fields + 1] = '"dsp_registers":{' .. table.concat(keys, ",") .. "}"
    end
    if failure then fields[#fields + 1] = '"failure":' .. quote(failure) end
    f:write("{" .. table.concat(fields, ",") .. "}\n")
    f:close()
end

local function dsp_write(reg, value)
    program:write_u8(0xF2, reg)
    program:write_u8(0xF3, value)
end
local function dsp_read(reg)
    program:write_u8(0xF2, reg)
    return program:read_u8(0xF3)
end

local function load_aram_and_halt()
    ram = read_all("GME_SNES_ARAM_PATH")
    dsp = read_all("GME_SNES_DSP_PATH")
    if #ram ~= RAM_SIZE or #dsp ~= 0x80 then error("input ARAM/DSP file has invalid length") end
    source_ram_sha = os.getenv("GME_SNES_ARAM_SHA256")
    source_dsp_sha = os.getenv("GME_SNES_DSP_SHA256")
    voice = integer_env("GME_SNES_VOICE", 0, 7)
    pitch = integer_env("GME_SNES_PITCH", 0, 0x3FFF)
    hold_frames = integer_env("GME_SNES_HOLD_FRAMES", 1, 3600)
    tail_frames = integer_env("GME_SNES_TAIL_FRAMES", 0, 3600)
    halt_pc = integer_env("GME_SNES_HALT_PC", 0x100, 0xFFEF)
    soundcpu = machine.devices[":soundcpu"]
    if not soundcpu then error("MAME SNES :soundcpu device not found") end
    program = soundcpu.spaces["program"]
    if not program then error("MAME S-SMP program space not found") end
    pc_entry = soundcpu.state["PC"]
    if not pc_entry or not pc_entry.writeable then error("MAME S-SMP PC register is not writable") end

    -- Restore snapshot ARAM through the CPU bus while avoiding mapped $F0-$FF I/O.
    for addr = 0, RAM_SIZE - 1 do
        if addr < 0xF0 or addr > 0xFF then program:write_u8(addr, ram:byte(addr + 1)) end
    end
    program:write_u8(0xF1, 0x00) -- disable timers and expose RAM under the IPL overlay
    for addr = 0, RAM_SIZE - 1 do
        if addr < 0xF0 or addr > 0xFF then
            if program:read_u8(addr) ~= ram:byte(addr + 1) then
                error(string.format("SPC RAM readback mismatch at %04X", addr))
            end
            aram_verified = aram_verified + 1
        end
    end

    local base = voice * 0x10
    srcn = dsp:byte(base + 5)
    local dirreg = dsp:byte(0x5E)
    local dir = dirreg * 0x100
    local dir_addr = dir + srcn * 4
    if dir_addr + 3 >= RAM_SIZE then error("selected DSP directory entry outside SPC RAM") end
    if dir_addr < 0x100 and dir_addr + 4 > 0xF0 then error("sample directory entry overlaps skipped SPC I/O") end
    sample_start = ram:byte(dir_addr + 1) + ram:byte(dir_addr + 2) * 0x100
    sample_loop = ram:byte(dir_addr + 3) + ram:byte(dir_addr + 4) * 0x100
    local pos, count, last_header = sample_start, 0, nil
    while pos + 8 < RAM_SIZE do
        last_header = ram:byte(pos + 1)
        count = count + 1
        pos = pos + 9
        if last_header & 1 ~= 0 then break end
    end
    if not last_header or last_header & 1 == 0 then error("selected BRR sample has no end frame") end
    looping = (last_header & 2) ~= 0
    sample_end, brr_frames = pos, count
    if sample_start < 0x100 and sample_end > 0xF0 then error("BRR sample overlaps skipped SPC I/O") end
    if looping and (sample_loop < sample_start or sample_loop >= sample_end or (sample_loop - sample_start) % 9 ~= 0) then
        error("selected BRR loop address is invalid")
    end
    if halt_pc >= sample_start and halt_pc < sample_end then error("SPC stop opcode overlaps selected BRR sample") end
    if halt_pc >= dir and halt_pc < dir + 0x400 then error("SPC stop opcode overlaps sample directory") end

    -- Stop all snapshot voices and effects, then restore the selected patch and its DIR base.
    dsp_write(0x4C, 0)
    dsp_write(0x5C, 0xFF)
    for ch = 0, 7 do
        local b = ch * 0x10
        if ch ~= voice then dsp_write(b, 0); dsp_write(b + 1, 0) end
    end
    dsp_write(0x2C, 0); dsp_write(0x3C, 0)
    dsp_write(0x2D, 0); dsp_write(0x3D, 0); dsp_write(0x4D, 0)
    dsp_write(0x6C, 0x20)
    dsp_write(0x0C, 127); dsp_write(0x1C, 127)
    dsp_write(0x5D, dirreg)
    dsp_write(0x5C, 0) -- clear KOFF before keying this voice on
    dsp_write(base, 127); dsp_write(base + 1, 127)
    dsp_write(base + 2, pitch & 0xFF); dsp_write(base + 3, (pitch >> 8) & 0x3F)
    dsp_write(base + 4, srcn)
    dsp_write(base + 5, dsp:byte(base + 6))
    dsp_write(base + 6, dsp:byte(base + 7))
    dsp_write(base + 7, dsp:byte(base + 8))

    registers = {
        srcn = dsp_read(base + 4), pitch_low = dsp_read(base + 2), pitch_high = dsp_read(base + 3),
        adsr1 = dsp_read(base + 5), adsr2 = dsp_read(base + 6), gain = dsp_read(base + 7),
        voice_volume_left = dsp_read(base), voice_volume_right = dsp_read(base + 1),
        master_volume_left = dsp_read(0x0C), master_volume_right = dsp_read(0x1C),
        dir_register = dsp_read(0x5D), echo_enable = dsp_read(0x4D),
        noise_enable = dsp_read(0x3D), pitch_mod_enable = dsp_read(0x2D),
        echo_volume_left = dsp_read(0x2C), echo_volume_right = dsp_read(0x3C),
        flg = dsp_read(0x6C)
    }
    -- STOP is a one-byte scratch mutation outside all selected sample/directory bounds.
    program:write_u8(halt_pc, 0xFF)
    pc_entry.value = halt_pc
    if pc_entry.value ~= halt_pc or program:read_u8(halt_pc) ~= 0xFF then
        error("SPC stop PC/opcode could not be installed")
    end
end

local function fail(err)
    phase = "failed"
    if program then pcall(function() dsp_write(0x4C, 0); dsp_write(0x5C, 0xFF) end) end
    print("SNES probe ERROR: " .. tostring(err))
    pcall(receipt, "failed", err)
    machine:exit()
end

emu.register_frame_done(function()
    local ok, err = pcall(function()
        if phase == "startup" then
            startup_frames = startup_frames + 1
            load_aram_and_halt()
            kon_value = 1 << voice
            dsp_write(0x4C, kon_value)
            note_on = machine.time:as_double()
            phase = "hold"
        elseif phase == "hold" then
            held_frames = held_frames + 1
            if held_frames >= hold_frames then
                dsp_write(0x4C, 0)
                koff_value = 1 << voice
                dsp_write(0x5C, koff_value)
                note_off = machine.time:as_double()
                if tail_frames == 0 then
                    capture_end_time = machine.time:as_double()
                    phase = "flush"
                else
                    phase = "tail"
                end
            end
        elseif phase == "tail" then
            tailed_frames = tailed_frames + 1
            if tailed_frames >= tail_frames then
                capture_end_time = machine.time:as_double()
                phase = "flush"
            end
        elseif phase == "flush" then
            phase = "done"
            receipt("ok")
            machine:exit()
        end
    end)
    if not ok then fail(err) end
end)
