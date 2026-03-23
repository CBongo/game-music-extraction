-- g3_common.lua
-- Shared constants and helpers for Gradius 3 arcade MAME Lua scripts
-- Loaded via dofile() at the top of each script
-- by Claude under direction from Chris Bongaarts

-- ============================================================
-- Main CPU addresses
-- ============================================================

SOUND_LATCH_ADDR = 0xE8000   -- main CPU: write event byte here
SOUND_IRQ_ADDR   = 0xF0000   -- main CPU: write anything here to trigger Z80 IRQ

-- ============================================================
-- Audio CPU (Z80) RAM
-- ============================================================

VSTATE_BASE  = 0xF800
VSTATE_SIZE  = 0x50
VSTATE_COUNT = 10

-- vstate field offsets (from eventstate.h)
VS_VOICENUM  = 0x00
VS_SONGNUM   = 0x01
VS_LEVEL_ADJ = 0x0E
VS_TRANSPOSE = 0x1A
VS_PAN_FLAGS = 0x1F
VS_PATCH     = 0x3E
VS_MUTED     = 0x3F
VS_K7232_VOL = 0x41

-- ============================================================
-- YM2151 FM Synthesizer (Z80 address space)
-- ============================================================

YM_ADDR_PORT = 0xF030
YM_DATA_PORT = 0xF031

-- ============================================================
-- K007232 PCM Chip (Z80 address space)
-- ============================================================

K7232_BASE      = 0xF020
K7232_END       = 0xF02D
K7232_BANK_ADDR = 0xF000   -- bank register (written separately)
K7232_CHA_START = 5        -- register offset: writing/reading triggers ch A playback
K7232_CHB_START = 11       -- register offset: writing/reading triggers ch B playback
K7232_VOL_REG   = 12       -- register offset: volume (high nibble=chA, low nibble=chB)

-- ============================================================
-- Colors (0xAARRGGBB)
-- ============================================================

COL_TITLE    = 0xFFFFFF00
COL_WHITE    = 0xFFFFFFFF
COL_GREEN    = 0xFF44FF44
COL_RED      = 0xFFFF4444
COL_YELLOW   = 0xFFFFFF44
COL_CYAN     = 0xFF44FFFF
COL_GRAY     = 0xFF888888
COL_BG       = 0xD0000000
COL_BOX_LINE = 0xFF666666

-- ============================================================
-- Event name lookup (from latchcmds.txt)
-- ============================================================

event_names = {
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
    [0x82] = "Crystal Labyrinth BGM",
    [0x83] = "Boss on Parade 3 BGM",
    [0x84] = "Easter Stone BGM",
    [0x85] = "Aqua Illusion BGM",
    [0x86] = "In the Wind BGM",
    [0x87] = "Game Over",
    [0x88] = "High Score BGM",
    [0x89] = "Weaponry BGM",
    [0x8A] = "Mechanical Base BGM",
    [0x8B] = "Cosmo Plant BGM",
    [0x8C] = "Title Theme",
    [0x8D] = "Boss on Parade 1 BGM",
    [0x8E] = "Boss on Parade 2 BGM",
    [0x8F] = "A Long Time Ago BGM",
    [0x90] = "Unused 1 BGM",
    [0x91] = "Challenger 1985 BGM",
    [0x92] = "Power of Anger BGM",
    [0x93] = "Poison of Snake BGM",
    [0x94] = "Unused 2 BGM",
    [0x95] = "Dark Force BGM",
    [0x96] = "Aircraft Carrier BGM",
    [0x97] = "High Speed Dimension BGM",
    [0x98] = "Try to Star BGM",
    [0x99] = "Underground BGM",
    [0x9A] = "Escape to the Freedom BGM",
    [0x9B] = "Dead End Cell BGM",
    [0x9C] = "Final Shot BGM",
    [0x9D] = "Fire Scramble BGM",
    [0x9E] = "Return to the Star BGM",
    [0x9F] = "Congratulations BGM",
    [0xA0] = "Stage Start BGM (no intro)",
    [0xA1] = "Unused 3 BGM",
    [0xA2] = "Large Explosion SFX",
}

-- ============================================================
-- Main CPU halt helper
-- Patches main CPU ROM to trap it in a watchdog loop,
-- preventing it from interfering with our audio-only mode.
-- ============================================================

function halt_main_cpu(main_mem)
    main_mem:write_direct_u16(0x2f0, 0x60F6)  -- BRA.S 0x2e8 (infinite watchdog loop)
    print("G3 Common: patched main CPU ROM at 0x2f0 (BRA.S 0x2e8)")
end

-- ============================================================
-- YM2151 write helper (requires audio_mem to be set by caller)
-- ============================================================

-- Note: ym_write is defined per-script because it depends on
-- the script's local audio_mem variable.  Scripts should define:
--   function ym_write(reg, data)
--       audio_mem:write_u8(YM_ADDR_PORT, reg)
--       audio_mem:write_u8(YM_DATA_PORT, data)
--   end
