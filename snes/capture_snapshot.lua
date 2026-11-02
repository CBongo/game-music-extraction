-- Read a running cartridge at one frame boundary, for isolated voice replay only.
local machine = manager.machine
local frames = 0
local target = assert(tonumber(os.getenv('GME_CAPTURE_FRAMES')))
local function q(s) return '"' .. tostring(s):gsub('\\','\\\\'):gsub('"','\\"') .. '"' end
local function save(path, data)
    local f = assert(io.open(path, 'wb')); f:write(data); f:close()
end
emu.register_frame_done(function()
    frames = frames + 1
    if frames ~= target then return end
    local ok, err = pcall(function()
        local cpu = assert(machine.devices[':soundcpu'])
        local space = assert(cpu.spaces['program'])
        local pc = cpu.state.PC.value
        local original_f2 = space:read_u8(0xF2)
        local dsp, hex = {}, {}
        -- This callback does not yield; neither CPU advances during these reads.
        for reg = 0, 127 do
            space:write_u8(0xF2, reg)
            local value = space:read_u8(0xF3)
            dsp[#dsp+1] = string.char(value); hex[#hex+1] = string.format('%02x', value)
        end
        space:write_u8(0xF2, original_f2)
        -- Expose underlying top 64 ARAM bytes instead of IPL ROM. Timers disabled.
        space:write_u8(0xF1, 0)
        local aram = {}
        for address = 0, 65535 do aram[#aram+1] = string.char(space:read_u8(address)) end
        save(os.getenv('GME_CAPTURE_ARAM'), table.concat(aram))
        save(os.getenv('GME_CAPTURE_DSP'), table.concat(dsp))
        save(os.getenv('GME_CAPTURE_RECEIPT'), '{"schema_version":"snes-snapshot-receipt/v1","status":"ok",' ..
            '"request_id":' .. q(os.getenv('GME_CAPTURE_ID')) .. ',"rom_payload_sha256":' .. q(os.getenv('GME_CAPTURE_ROM_SHA')) ..
            ',"frames":' .. frames .. ',"time_seconds":' .. string.format('%.12f',machine.time:as_double()) ..
            ',"device":":soundcpu","pc":' .. pc .. ',"original_f2":' .. original_f2 ..
            ',"f1_written":0,"aram_read_bytes":65536,"dsp_read_bytes":128,"dsp_hex":' .. q(table.concat(hex)) ..
            ',"aram_path":' .. q(os.getenv('GME_CAPTURE_ARAM')) .. ',"dsp_path":' .. q(os.getenv('GME_CAPTURE_DSP')) .. '}\n')
    end)
    if not ok then print('SNES capture failed: ' .. tostring(err)) end
    machine:exit()
end)
