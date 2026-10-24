# Instrument probe rendering

The GM reference harness is `instrument_analysis.rendering`. It emits the
versioned `instrument-render/v1` manifest in
`schemas/instrument-render-v1.schema.json`, with exact SoundFont/executable hashes,
engine version, render settings, MIDI hashes, canonical routes, and WAV properties.

It needs a runnable FluidSynth executable and an unpacked SoundFont `.sf2`/`.sf3`.
The inventory for this work found neither on PATH or in the checked audio/tool
folders; `FluidR3_GM.sfArk` in Downloads is compressed and cannot be supplied
as an SF2. Useful official sources:

- [FluidSynth Windows binaries](https://www.fluidsynth.org/download/).
- [GeneralUser GS](https://www.schristiancollins.com/generaluser.php), as a concrete
  GM bank choice; retain the chosen archive's own license and version locally.
- [MAME installation](https://docs.mamedev.org/initialsetup/installingmame.html).
  The downloaded `mame0285b_x64.exe` can be extracted to a directory.
- [openmpt123](https://lib.openmpt.org/libopenmpt/download/) for independent native
  module playback validation of the original ProTracker fixtures.

A two-probe smoke run after providing real paths:

```powershell
python -m instrument_analysis.rendering --fluidsynth C:/tools/fluidsynth/bin/fluidsynth.exe --soundfont C:/soundfonts/GeneralUser.sf2 --output instrument-renders/gm-smoke --programs 0 --notes 60 --percussion 38
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
    mame_path='C:/tools/mame/mame.exe',
    rompath='arcade/gradius3/rom',
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

The [MAME Lua device documentation](https://docs.mamedev.org/luascript/ref-devices.html)
defines screen frame period as seconds, while machine time is an attotime converted
using `as_double()` per the
[core API](https://docs.mamedev.org/luascript/ref-core.html). The script has a syntax
check and mocked process/receipt tests; **live MAME playback is not yet validated**.
Capture alignment, ROM-set compatibility, and calibrated MIDI pitch require a
real run. No private ROM/audio assets are checked in.

## Remaining acceptance work

Bead `gme-0r4.6` remains in progress. Real all-program GM audio and MAME captures
have not been generated in this environment because the executable/bank setup is
missing. Native AKAO/SPU and SNES DSP probe backends also remain to be implemented
and validated; re-exporting their samples to SoundFont is not claimed as native
hardware playback. Matching source/reference effects, acoustic calibration, and
cross-bank evidence must use real render manifests before classifier evaluation.
The current tests verify planning, file formats, process boundaries, failure
handling, and metadata integrity, not perceptual similarity or emulator fidelity.
