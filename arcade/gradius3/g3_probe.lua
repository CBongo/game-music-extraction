-- Batch FM-only Gradius III probe. KEYCODE is the raw YM2151 register byte.
dofile(debug.getinfo(1, "S").source:match("@?(.+[/\\\\])") .. "g3_common.lua")
local FM_PTR_TABLE, PATCH_COUNT = 0x27CD, 178
local PC_LO, PC_HI, STARTUP_LIMIT = 0x0615, 0x0639, 300
local OP_OFFSETS, REG_BASES = {0,16,8,24}, {0x40,0x60,0x80,0xA0,0xC0,0xE0}
local function env_int(name, lo, hi)
    local v=tonumber(os.getenv(name) or "")
    if not v or v~=math.floor(v) or v<lo or v>hi then error("invalid "..name) end
    return v
end
local patch_num=env_int("GME_G3_PATCH",0,PATCH_COUNT-1)
local keycode=env_int("GME_G3_KEYCODE",0,127)
local hold_frames=env_int("GME_G3_HOLD_FRAMES",1,3600)
local tail_frames=env_int("GME_G3_TAIL_FRAMES",0,3600)
local receipt_path=os.getenv("GME_G3_RECEIPT")
if not receipt_path or receipt_path=="" then error("GME_G3_RECEIPT is required") end
local machine=manager.machine
local main_mem,audio_mem,audiocpu
local phase,startup_frames,held,tailed="startup",0,0,0
local patch_pointer,patch_bytes,note_on,note_off
local function quote(s)
    local escaped={}
    s=tostring(s)
    for i=1,#s do
        local byte=s:byte(i)
        if byte==34 then escaped[#escaped+1]=string.char(92,34)
        elseif byte==92 then escaped[#escaped+1]=string.char(92,92)
        elseif byte<32 then escaped[#escaped+1]=string.char(92)..string.format('u%04x',byte)
        else escaped[#escaped+1]=string.char(byte) end
    end
    return '"'..table.concat(escaped)..'"'
end
local function receipt(status,failure)
    local f,err=io.open(receipt_path,"wb")
    if not f then print("G3 probe receipt error: "..tostring(err)); return end
    local a={'"schema_version":"gradius3-native-probe-receipt/v1"',
        '"status":'..quote(status),'"patch_id":'..quote(patch_num),
        '"patch_num":'..patch_num,'"keycode_raw_ym2151":'..keycode,
        '"hold_frames":'..hold_frames,'"tail_frames":'..tail_frames,
        '"startup_frames":'..startup_frames,
        '"frame_period_seconds":'..string.format("%.12f",machine.screens[":screen"].frame_period)}
    if patch_pointer then a[#a+1]='"patch_pointer":'..patch_pointer; a[#a+1]='"patch_bytes_hex":'..quote(patch_bytes) end
    if note_on then a[#a+1]='"note_on_time_seconds":'..string.format("%.12f",note_on) end
    if note_off then a[#a+1]='"note_off_time_seconds":'..string.format("%.12f",note_off) end
    if status=="ok" then a[#a+1]='"capture_end_time_seconds":'..string.format("%.12f",machine.time:as_double()) end
    if failure then a[#a+1]='"failure":'..quote(failure) end
    f:write("{"..table.concat(a,",").."}\n"); f:close()
end
local function ym_write(reg,data)
    audio_mem:write_u8(YM_ADDR_PORT,reg); audio_mem:write_u8(YM_DATA_PORT,data)
end
local function fail(err)
    phase="failed"
    if audio_mem then pcall(function()
        audio_mem:write_u8(K7232_BASE+K7232_VOL_REG,0)
        for ch=0,7 do ym_write(0x08,ch) end
    end) end
    print("G3 probe ERROR: "..tostring(err)); pcall(receipt,"failed",err); machine:exit()
end
local function read8(addr) return audio_mem:read_u8(addr) end
local function read16(addr) return read8(addr)|(read8(addr+1)<<8) end
local function quiet_other_audio()
    for ch=0,7 do
        ym_write(0x08,ch)
        for _,offset in ipairs(OP_OFFSETS) do ym_write(0x60+ch+offset,127) end
    end
    audio_mem:write_u8(K7232_BASE+K7232_VOL_REG,0)
    ym_write(0x0F,0); ym_write(0x19,0); ym_write(0x19,0x80)
end
local function load_patch()
    patch_pointer=read16(FM_PTR_TABLE+patch_num*2)
    if patch_pointer+25>0x10000 then error("FM patch pointer outside audio CPU ROM") end
    local b={}
    for i=0,24 do b[#b+1]=string.format("%02x",read8(patch_pointer+i)) end
    patch_bytes=table.concat(b)
    ym_write(0x20,read8(patch_pointer)|0xC0)
    for op=0,3 do
        local base=patch_pointer+1+op*6
        for field=0,5 do ym_write(REG_BASES[field+1]+OP_OFFSETS[op+1],read8(base+field)) end
    end
    ym_write(0x30,0); ym_write(0x28,keycode)
end
local function begin()
    local maincpu=machine.devices[":maincpu"]; audiocpu=machine.devices[":audiocpu"]
    if not maincpu or not audiocpu then error("required Gradius III CPUs not found") end
    main_mem=maincpu.spaces["program"]; audio_mem=audiocpu.spaces["program"]
    if not main_mem or not audio_mem then error("required CPU program spaces not found") end
    halt_main_cpu(main_mem); quiet_other_audio()
end
emu.register_frame_done(function()
    local ok,err=pcall(function()
        if phase=="startup" then
            if not audio_mem then begin() end
            startup_frames=startup_frames+1
            local pc=audiocpu.state["PC"].value
            if pc>=PC_LO and pc<=PC_HI then
                quiet_other_audio(); load_patch(); ym_write(0x08,0x78); note_on=machine.time:as_double(); phase="hold"
            elseif startup_frames>=STARTUP_LIMIT then error(string.format("Z80 startup timeout at PC=%04X",pc)) end
        elseif phase=="hold" then
            held=held+1
            if held>=hold_frames then
                ym_write(0x08,0); note_off=machine.time:as_double(); phase="tail"
                if tail_frames==0 then phase="done"; receipt("ok"); machine:exit() end
            end
        elseif phase=="tail" then
            tailed=tailed+1
            if tailed>=tail_frames then phase="done"; receipt("ok"); machine:exit() end
        end
    end)
    if not ok then fail(err) end
end)
