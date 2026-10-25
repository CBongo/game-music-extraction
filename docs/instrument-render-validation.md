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

The 74-test instrument-analysis suite passes after the live Windows CLI,
SMB publication permissions, isolated MAME configuration, and bounded alignment
fixes. These results establish reproducible renders under named conditions;
they do not establish perceptual similarity or physical-hardware equivalence.
Native SNES DSP and AKAO/SPU captures are being added under bead gme-0r4.6.2.
