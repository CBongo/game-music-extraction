# Instrument renderer live validation

Validated on 2026-10-25 with installed Windows executables and local source
assets. Binary assets, captures, and manifests remain in ignored
`instrument-renders/`; this record preserves the results without distributing
soundfonts, game data, or firmware. See [renderer usage](instrument-rendering.md).

## GM reference bank

FluidSynth 2.4.7 (`C:/tools/fluidsynth/bin/fluidsynth.exe`) rendered GeneralUser GS
2.0.3 BETA (`D:/GeneralUser-GS/GeneralUser-GS.sf2`). The actual engine executable
SHA-256 is `12dd94bde278491385efd6cd2546f12ad77f3f186635517361031e97949562a0`;
the bank SHA-256 is
`9575028c7a1f589f5770fccc8cff2734566af40cd26ed836944e9a5152688cfe`.

The full run, `generaluser-full-20261024/manifest.json`, contains 431 probes:
128 melodic programs at MIDI notes 48, 60, and 72, plus drum keys 35 through 81.
All use velocity 96, note-off at 0.5 seconds, and a minimum 0.5-second tail.
All 431 WAV hashes, PCM headers, and input hashes were independently checked.
Every capture was nonzero; PCM16 peaks ranged from 40 to 2260, with no samples
at either clipping edge. Total WAV size was 353,307,156 bytes.

The full manifest SHA-256 is
`555475cadfbd0c90fd855628b12c43d3ae0561c109e3fd303d296dcd72de1a0a`.
Actual raw lengths range from 132608 to 1851904 sample frames at 44100 Hz
(3.006984 to 41.993288 seconds). FluidSynth appends release/padding beyond the
requested MIDI interval; comparison must use a declared phase/window rather
than whole-file duration. The longest capture is drum key 41.

Independent smoke/full renders of program 0 at note 60 and drum key 35 have
identical MIDI and WAV hashes. A separate bank-selection run at program 38,
note 60, selected bank 0 Synth Bass 1 and bank 1 Synth Bass 101, with different
nonzero WAVs. Their hashes are respectively
`2d2a1923296601111c21bc06399133525b385a00aeb21afa087b007091fceaa8` and
`a72199c9026befaf80fcf6a37b5fbddf2c8f6c9098558fb4f2d4398134b9c0a0`.
This establishes bank selection within this soundfont. Independent soundfont or
synthesizer agreement has not been measured.

## Native Gradius III FM

MAME 0.285 rendered patch 3, raw YM2151 keycode 0x42, with 30 video frames held
and 30 frames of release. The supplied audio ROM SHA-256 is
`cd4affd7447b631d22fada28a7a0fd8a235f322cfa3f2687db08d597398b9e28`.
The receipt confirms patch pointer 10620 and raw patch bytes
`f801121e1f000601111e17052601111e1f000603041e9f0606`.

Independent runs in `gradius3-live-20261024-run2/` and
`gradius3-live-20261024-repeat/` produced byte-identical mono PCM16 WAVs:
45137 frames at 44100 Hz, peak 7177, RMS 2079.0743, no clipping-edge samples.
Both WAV SHA-256 values are
`c19e872f922d35451fb2928740f731f9ba1d7f0ffd686d5b6b63e0751c796ccb`.
Their manifests validate against `instrument-native-render-v1.schema.json`.

Lua note-on/off/end times are 1.233408, 1.740288, and 2.247168 seconds.
The retained PCM interval is 1.216507937 through 2.240022676 seconds, including
745 sample frames of pre-on lead. The 7.145 ms capture-end shortfall is below
one video frame (16.896 ms). The WAV mixer and Lua clocks have no verified shared
origin: `clock_origin_verified` remains false and the uncertainty is recorded.
First-nonzero time is diagnostic only; it does not determine trimming.

The nominal keycode mapping comes from pinned MAME 0.285
[Gradius III clock configuration](https://raw.githubusercontent.com/mamedev/mame/mame0285/src/mame/konami/gradius3.cpp),
[YMFM OPM mapping](https://raw.githubusercontent.com/mamedev/mame/mame0285/3rdparty/ymfm/src/ymfm_opm.h), and
[phase-step implementation](https://raw.githubusercontent.com/mamedev/mame/mame0285/3rdparty/ymfm/src/ymfm_fm.ipp).
At 3.579545 MHz, keycode 0x42 with fractional pitch zero gives approximately
311.075 Hz, nominal D-sharp4/MIDI 63. This is a chip-key calculation, not an
acoustic fundamental measurement; operator ratios, detuning, and routing matter.
The manifest correctly keeps perceptual MIDI pitch and velocity unset.

## Verification scope

The 82-test instrument-analysis suite passes after the live Windows CLI,
SMB publication permissions, isolated MAME configuration, and bounded alignment
fixes. These results establish reproducible renders under named conditions;
they do not establish perceptual similarity or physical-hardware equivalence.
Native SNES DSP and AKAO/SPU capture evidence follows below. Calibrated source/GM
comparison previews remain tracked by gme-0r4.6.3; no acoustic pitch match is claimed.


## Native SNES S-DSP

The final source is `snes/ff2/spcs/Airship.spc`, voice 0, with an explicit raw
S-DSP pitch register of 0x1000. MAME 0.285 restores SRCN 0x40, DIR 0x1E,
BRR start 0x3000 and repeat 0x301B, 672 BRR blocks with an end/loop flag, and
raw ADSR1/ADSR2/GAIN FF/E0/A3. The snapshot's original pitch register is 1831;
the probe's explicit 4096 is recorded separately.

The receipt confirms 65,520 ARAM bytes loaded and read back, with the mapped
F0-FF I/O range skipped. One STOP byte at 0x0200 halts the SPC CPU outside the
sample and directory. The minimal test cartridge stops the main CPU. Native
DSP decode/interpolation and envelopes remain active; echo, noise, and pitch
modulation are disabled and voice/master volume is fixed at 127.

Final captures are in `snes-spc-live-20261025-final/` and
`snes-spc-live-20261025-final-repeat/`. Both WAVs are byte-identical stereo
PCM16 at 44100 Hz, 44762 frames, peak 29695, RMS 5064.5711, and no clipping-edge
samples. Both WAV SHA-256 values are
`b1d9a20d0de1319443d472171ca9579d0581ac2961ed300201b0c707597ce291`.
The final Lua script SHA-256 is
`de2d126147a5077231b70709fb864ff6fd0e13cdba619f724c30301b953aa346`.

The requested 30-frame hold and 30-frame release end at 1.015000788 seconds;
a further frame flushes the mixer before exit. The retained WAV endpoint is
1.015011338 seconds, with zero tail shortfall. Clock-origin verification remains
false and the recorded uncertainty is one 16.639 ms video frame. The firmware
block is verified against the required 64-byte IPL SHA-1
`97e352553e94242ae823547cd853eecda55c20f0` and extracted only into temporary
private runtime storage from the existing emulator executable. All input and
script hashes were independently checked against the final manifests.

## Native AKAO PlayStation SPU

The final source is the explicitly identified AKAO **3.1** bank
`psx/ff8/ff8img_1e_1.bin`, sample set 10, articulation 76. FF8's format version
is supplied by the caller; magic bytes alone do not determine it. The raw pitch
register is 0x1000, with raw ADSR1 0x35FF and ADSR2 0x5FCE. The raw articulation
is `80990200e0e2020023912400ff35ce5f`; no source-region tuning or envelope override
is inferred.

The source declares 247104 payload bytes but physically contains 247056 bytes.
The manifest marks the bank incomplete and records the 48-byte discrepancy.
Only the selected sample is proved complete: its final 16-byte ADPCM block at
payload offset 247040 has end/repeat flags 3. The wrapper bounds uploads to the
available declared payload and rejects a selected sample lacking a complete end
block or a valid repeat address.

The Lua receipt proves all 247056 uploaded bytes by DMA4 readback from SPU RAM
into main RAM and a byte-for-byte comparison before key-on. It also records
native voice start, pitch, ADSR, and control-register readbacks. A main-CPU RAM
loop prevents BIOS writes during playback. The named engine is MAME 0.285's
Konami GV arcade board: its 0.75 speaker gain, swapped output routing, and
imperfect driver status are recorded rather than equated to a retail console.
Reverb, FM, and noise are disabled for this isolated dry probe.

Final captures are in `akao-spu-ff8-v31-live-20261025/` and
`akao-spu-ff8-v31-repeat-20261025/`. Both WAVs are byte-identical stereo PCM16
at 44100 Hz, 44965 frames, peak 15954, RMS 3235.7425, and no clipping-edge samples.
Both WAV SHA-256 values are
`df0f54d2de173c82c8b1fca2898032f2d298f5f850859a59ad51315d423654e7`.
The final Lua script SHA-256 is
`5f2260a109bf08bf64627db27d4ce7c1defced85cb753a627b7aff7bd743e053`.
The 30-frame hold and 30-frame release are validated against the receipt clock;
end rounding leaves a 7.395 microsecond shortfall, within the recorded one-frame
alignment uncertainty of 16.715 ms.

All four final SNES/SPU manifests validate against
`schemas/instrument-native-chip-render-v1.schema.json`. Independent auditing
rechecked source/executable/script hashes, raw source fields, receipts, frame
counts, requested timings, WAV hashes, and PCM metrics. A missing uploaded-payload
hash fails schema validation. The local audit summary is preserved in ignored
`instrument-renders/native-chip-validation-summary.json`.
