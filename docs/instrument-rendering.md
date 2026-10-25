# Instrument probe rendering

The GM reference harness is `instrument_analysis.rendering`. It emits the
versioned `instrument-render/v1` manifest in
`schemas/instrument-render-v1.schema.json`, with exact SoundFont/executable hashes,
engine version, render settings, MIDI hashes, canonical routes, and WAV properties.

It needs a runnable FluidSynth executable and an unpacked SoundFont `.sf2`/`.sf3`.
Live validation on 2026-10-25 used these installed tools:

- Native FluidSynth 2.4.7: `C:/tools/fluidsynth/bin/fluidsynth.exe`.
  `fluidsynth` on PATH resolves to a Chocolatey shim; passing the native executable
  records the actual engine binary hash. This Windows build lacks `getopt` and
  rejects long CLI options, so the harness uses supported short options, including
  separate `-C 0` / `-R 0` arguments.
- `D:/GeneralUser-GS/GeneralUser-GS.sf2`, whose embedded name is
  **GeneralUser GS 2.0.3 BETA**. All bank-0 programs and the standard drum preset
  were present; hashes identify this exact bank rather than its filename alone.
- MAME 0.285: `H:/emulators/Arcade/mame0285b_x64/mame.exe`, with the installed
  `roms/gradius3.zip`. Its audio ROM matches the repository's private audio ROM.

For another machine, official sources are [FluidSynth](https://www.fluidsynth.org/download/),
[GeneralUser GS](https://www.schristiancollins.com/generaluser.php), and
[MAME installation](https://docs.mamedev.org/initialsetup/installingmame.html).
[openmpt123](https://lib.openmpt.org/libopenmpt/download/) remains useful for
independent native playback validation of the original ProTracker fixtures.
The bank's embedded license is retained in the local asset; no bank or game data
is added to Git.

A two-probe smoke run after providing real paths:

```powershell
python -m instrument_analysis.rendering --fluidsynth C:/tools/fluidsynth/bin/fluidsynth.exe --soundfont D:/GeneralUser-GS/GeneralUser-GS.sf2 --output instrument-renders/gm-smoke --programs 0 --notes 60 --percussion 38
```

Omit the subset flags for all 128 programs at notes 48/60/72 plus all 47 standard
GM percussion keys 35..81: 431 probes at default velocity 96. Each probe is an
isolated FluidSynth process with explicit tempo, duration, note-off, and tail.
Velocity zero is rejected because MIDI interprets it as note-off. Dry rendering
disables chorus/reverb; `--wet` records their enabled state instead. The official
[FluidSynth manual](https://www.fluidsynth.org/wiki/UserManual/) documents the
fast-file-render CLI used by the harness.

The harness reads SoundFont preset headers first and rejects missing bank/program
pairs, preventing silent fallback to another preset. Channel-10 keys use the GM
default drum bank, distinct from normal-channel melodic percussion. Top-level
program fields on a drum probe select the kit; its canonical route is a percussion
key. `--banks` selects variants within one SoundFont; repeat runs with distinct
bank files/output directories for cross-bank comparisons. Record preset names,
actual WAV lengths, and hashes rather than assuming bit-identical output across
engine builds/platforms. WAVs must contain the requested note and tail interval;
silent output remains measurable via its recorded peak and should be reviewed.
Nothing is published as a successful manifest on a failed process or bad WAV.
Existing outputs are protected unless `--overwrite` is explicit.

## Source plans and native FM

`instrument_analysis.probes.plan_source_probes(bundle)` makes a **planned**, not
rendered, source manifest. It chooses usage quantiles in the real source register
and adds a small standard grid. Raw trigger, driver transpose, effect context,
identity, and expected sounding pitch remain separate. Curated GM maps do not
influence selection. Standard-grid triggers and unknown calibration do not claim
pitch alignment. Source record IDs link back to the canonical observation bundle.

`instrument_analysis.native_rendering.render_gradius3_probe` drives the new
`arcade/gradius3/g3_probe.lua` through MAME:

```python
from instrument_analysis.native_rendering import render_gradius3_probe

manifest = render_gradius3_probe(
    mame_path='H:/emulators/Arcade/mame0285b_x64/mame.exe',
    rompath='H:/emulators/Arcade/mame0285b_x64/roms',
    audio_rom_path='arcade/gradius3/rom/945_r05.d9',
    output_dir='instrument-renders/gradius3',
    source_record_id='gradius3-fm-3', patch_id=3, keycode=66,
    hold_frames=30, tail_frames=30,
)
```

This is a raw YM2151 keycode, **not a verified MIDI note**, and the native patch
uses fixed operator levels rather than a claimed MIDI velocity response. The
script waits for verified driver startup, programs the 25-byte FM patch including
operator envelopes, silences other channels, disables global LFO/noise for an
isolated dry patch probe, and records note-on/off/end times and frame period in a
receipt. Startup timeout fails instead of proceeding with uncertain state. The
wrapper checks patch bytes against the supplied audio ROM, validates timing and
PCM capture, trims boot audio, and records hashes and the exact run arguments.
The receipt/WAV/manifest are staged and published only after validation.
MAME scratch runs on the local temp volume; output-local publication stages use
inherited permissions and same-volume renames. Python's restrictive temporary
file/directory modes denied access on this SMB workspace, so publication avoids
those modes. MAME configuration reads/writes are disabled and its writable runtime
directories point into the isolated scratch folder.

The [MAME Lua device documentation](https://docs.mamedev.org/luascript/ref-devices.html)
defines screen frame period as seconds, while machine time is an attotime converted
using `as_double()` per the
[core API](https://docs.mamedev.org/luascript/ref-core.html). Live MAME playback now
passes twice through the public wrapper, with matching ROM bytes, successful
receipts, schema-valid manifests, and byte-identical PCM. The mixer WAV clock is
not proven to share the Lua time origin. Keep one video frame of pre-on lead,
permit at most one frame of end-buffer discrepancy, and record the actual sample
interval and uncertainty in `conditions.wav_alignment`. The real capture's end
shortfall is 7.145 ms within a 16.896 ms bound. First-nonzero time is diagnostic
only; trimming to it would remove slow attacks. This is bounded alignment, not
sample-exact note timing. No private ROM/audio assets are checked in.

At Gradius III's chip clock, raw keycode `0x42` nominally corresponds to
D-sharp4 / MIDI 63. This follows the MAME 0.285 YMFM key-code phase-step mapping,
not a measurement of the patch's audible fundamental. Operator ratios, detuning,
and FM routing can alter that fundamental, so the native manifest still leaves
its perceptual MIDI-note and velocity fields unset. See the source references and
actual results in [the validation record](instrument-render-validation.md).

## Native SNES and AKAO/SPU probes

The source-chip backends use MAME's native decoding, interpolation, and envelope
processing. They accept explicit raw pitch registers and preserve source sample
loops and envelope registers. They do not infer acoustic MIDI pitch or MIDI
velocity from those registers. Isolated dry captures disable echo/reverb, noise,
and pitch modulation; whole-song driver behavior and region-specific overrides
require separate context.

`instrument_analysis.snes_rendering.render_snes_spc_probe` selects a voice from a
standard SPC snapshot. It restores the snapshot's sample RAM and sample-directory
entry, SRCN, ADSR, and GAIN, with fixed voice/master volume. An original minimal
test cartridge stops the main CPU, while a verified scratch instruction stops the
SPC CPU outside the selected sample and directory. A local, hash-verified 64-byte
IPL block is supplied privately to MAME from an existing emulator installation;
it is never stored in the repository. The source executable path and offset are
explicit inputs, rather than a general firmware-discovery mechanism.

`instrument_analysis.psx_rendering.render_akao_spu_probe` selects one raw
articulation from an AKAO 3.1/3.2 sample bank. MAME 0.285's Konami GV board exposes
the PlayStation SPU without requiring an emulated game program. This is a named
arcade-board engine: the board swaps SPU output channels and applies 0.75 speaker
gain, and MAME marks its driver imperfect. An isolated CPU loop prevents the
board BIOS from modifying the probe. The renderer uploads the bounded ADPCM
payload and preserves the articulation's start, repeat address, and raw ADSR.
It does not apply unprovided instrument-region tuning or ADSR overrides.

Example calls with the installed assets (choose new output folders for each run):

```python
from instrument_analysis.snes_rendering import render_snes_spc_probe
from instrument_analysis.psx_rendering import render_akao_spu_probe

snes_manifest = render_snes_spc_probe(
    'snes/ff2/spcs/Airship.spc',
    mame_path='H:/emulators/Arcade/mame0285b_x64/mame.exe',
    output_dir='instrument-renders/snes-example',
    source_record_id='ff2-airship-voice0', voice=0, pitch_register=0x1000,
    ipl_source_path='H:/emulators/snes/snes9x/snes9xw128/snes9xw.exe',
    ipl_source_offset=0x89878,
)
spu_manifest = render_akao_spu_probe(
    'psx/ff8/ff8img_1e_1.bin', format_version='3.1',
    mame_path='H:/emulators/Arcade/mame0285b_x64/mame.exe',
    rom_path='H:/emulators/Arcade/mame0285b_x64/roms',
    output_dir='instrument-renders/spu-example',
    source_record_id='ff8-set10-art76', articulation_id=76,
    pitch_register=0x1000,
)
```

These backends publish `instrument-native-chip-render/v1` manifests, separate
from the existing FM manifest, with exact source/engine/script hashes, raw patch
state, requested frame counts, native receipts, and PCM properties. The receipt
and WAV clocks retain the bounded alignment limitation described above. A
successful capture is evidence of playback under those recorded conditions;
it does not establish physical-console equivalence or perceptual pitch alignment.

## Remaining acceptance work

Bead `gme-0r4.6` remains in progress. Real all-program GM audio and native FM
capture have now been validated; see [the validation record](instrument-render-validation.md).
The 431 raw FluidSynth captures retain engine-generated release/padding beyond
the requested 0.5 s note plus minimum 0.5 s tail. Their actual lengths are recorded
(about 3.007 to 41.993 s); use the declared MIDI phase/window when comparing,
rather than assuming that the entire files have matched lengths. Separate presets
in GeneralUser banks 0 and 1 verify bank selection within one SoundFont, not
agreement across different SoundFonts or synthesizer implementations.

Native AKAO/SPU and SNES DSP backends now have validated, byte-identical repeat
captures with source-memory and register readback. Bead `gme-0r4.6.3` tracks
connecting calibrated source pitches and usage-selected triggers to matched GM
comparison windows and manifest-linked previews. Raw-register playback does not
establish pitch alignment by itself.
Matching source/reference effects, acoustic calibration, and cross-SoundFont
evidence must use real render manifests before classifier evaluation. The tests
and live integrity checks do not establish perceptual similarity or hardware
fidelity beyond the named emulator/runtime conditions.
