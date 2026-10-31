"""Dry, file-only auditions of accepted isolated composite chord probes.

This deliberately does not synthesize source sequence usage or modulation.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import wave

from .composites import expand_composite_events
from .rendering import (_run, _sha256, _staging_directory, _ticks, _vlq,
                        _wav_info, read_soundfont_presets)
from .schema import validate_semantics

MANIFEST_VERSION = "instrument-composite-preview/v1"


def make_composite_midi(events: list[dict], *, program_0based: int,
                        tail_seconds: float) -> tuple[bytes, list[dict]]:
    """Encode one simultaneous chord with independent bend/expression channels.

    Relative dB maps to rounded CC11 expression, normalized to the loudest voice.
    SoundFont synthesis need not preserve the requested acoustic dB ratios.
    Both bend and expression quantization are recorded in the returned recipe.
    """
    if type(program_0based) is not int or not 0 <= program_0based <= 127:
        raise ValueError("program_0based must be MIDI 0..127")
    if not 1 <= len(events) <= 15:
        raise ValueError("isolated chord requires 1..15 melodic voices")
    tail = _ticks(tail_seconds, "tail_seconds", zero=True)
    levels = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("each event must be an object")
        if set(event) - {"midi_note", "start", "duration", "velocity", "source_record_id",
                         "component_index", "residual_cents", "relative_level_db"}:
            raise ValueError("unsupported event effects; only isolated static chords are supported")
        level = event.get("relative_level_db")
        if type(level) not in (int, float) or not math.isfinite(level):
            raise ValueError("relative_level_db must be finite")
        levels.append(level)
    peak = max(levels)
    track = bytearray(b"\x00\xff\x51\x03\x0f\x42\x40")
    recipe = []
    end = None
    for channel, event, level in zip((c for c in range(16) if c != 9), events, levels):
        note, velocity, cents = (event.get(k) for k in ("midi_note", "velocity", "residual_cents"))
        if (type(note) is not int or not 0 <= note <= 127 or type(velocity) is not int
                or not 1 <= velocity <= 127 or type(cents) not in (int, float)
                or not math.isfinite(cents) or not -50 <= cents <= 50):
            raise ValueError("invalid composite MIDI note, velocity, or residual cents")
        if event.get("start") != 0 or type(event.get("start")) is bool:
            raise ValueError("only a simultaneous chord starting at zero is supported")
        duration = _ticks(event.get("duration"), "duration")
        if end is not None and duration != end:
            raise ValueError("all isolated chord voices must have equal duration")
        end = duration
        bend = round(8192 + cents / 200 * 8192)
        expression = max(0, min(127, round(127 * 10 ** ((level - peak) / 20))))
        def cc(controller, value):
            track.extend(bytes((0, 0xB0 | channel, controller, value)))
        cc(0, 0)
        cc(32, 0)
        track.extend(bytes((0, 0xC0 | channel, program_0based)))
        cc(7, 100)
        cc(11, expression)
        cc(101, 0)
        cc(100, 0)
        cc(6, 2)
        cc(38, 0)
        cc(101, 127)
        cc(100, 127)
        track.extend(bytes((0, 0xE0 | channel, bend & 127, bend >> 7)))
        track.extend(bytes((0, 0x90 | channel, note, velocity)))
        recipe.append({**event, "channel_0based": channel, "pitch_bend_14bit": bend,
                       "rendered_residual_cents": (bend - 8192) * 200 / 8192,
                       "expression_cc11": expression, "volume_cc7": 100,
                       "normalized_relative_level_db": level - peak,
                       "start_tick": 0, "note_off_tick": duration})
    for index, event in enumerate(recipe):
        track.extend(_vlq(end if index == 0 else 0))
        track.extend(bytes((0x80 | event["channel_0based"], event["midi_note"], 0)))
    track.extend(_vlq(tail) + b"\xff\x2f\x00")
    return (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x03\xe8" + b"MTrk"
            + len(track).to_bytes(4, "big") + track), recipe


def render_composite_preview(analysis_path, diagnostics_path, *, trigger_midi_note,
                             duration_seconds, tail_seconds, program_0based,
                             soundfont_path, fluidsynth_path, output_dir) -> dict:
    """Publish MIDI, dry WAV, and hashed provenance for an explicit probe."""
    analysis_path = Path(analysis_path).resolve(strict=True)
    diagnostics_path = Path(diagnostics_path).resolve(strict=True)
    analysis_hash, diagnostics_hash = _sha256(analysis_path), _sha256(diagnostics_path)
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    validate_semantics(analysis)
    duration = _ticks(duration_seconds, "duration_seconds")
    tail = _ticks(tail_seconds, "tail_seconds", zero=True)
    expanded = expand_composite_events([{"midi_note": trigger_midi_note, "start": 0,
        "duration": duration / 1000, "velocity": 96}], analysis, diagnostics)
    midi_bytes, recipe = make_composite_midi(expanded, program_0based=program_0based,
                                            tail_seconds=tail / 1000)
    soundfont = Path(soundfont_path).resolve(strict=True)
    executable_found = shutil.which(os.fspath(fluidsynth_path))
    if executable_found is None:
        raise FileNotFoundError(f"FluidSynth executable not found: {fluidsynth_path}")
    executable = Path(executable_found).resolve(strict=True)
    soundfont_hash, executable_hash = _sha256(soundfont), _sha256(executable)
    presets = read_soundfont_presets(soundfont)
    if (0, program_0based) not in presets:
        raise ValueError("SoundFont lacks requested bank-0 program")
    output = Path(output_dir).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    names = ("composite.mid", "composite.wav", "manifest.json")
    if output.exists():
        raise FileExistsError("composite preview output already exists")
    version = _run([str(executable), "-V"], timeout=15)
    manifest = {"schema_version": MANIFEST_VERSION,
        "source_record_id": diagnostics["source_record_id"],
        "input": {"analysis": {"path": str(analysis_path), "sha256": analysis_hash},
                  "diagnostics": {"path": str(diagnostics_path), "sha256": diagnostics_hash}},
        "generator": {"name": "instrument_analysis.review_rendering", "executable": str(executable),
                      "executable_sha256": executable_hash,
                      "version": (version.stdout or version.stderr).strip()},
        "soundfont": {"path": str(soundfont), "sha256": soundfont_hash},
        "conditions": {"scope": "isolated_simultaneous_chord_probe", "trigger_midi_note": trigger_midi_note,
            "duration_seconds": duration / 1000, "tail_seconds": tail / 1000,
            "velocity": 96, "program_0based": program_0based, "bank": 0,
            "sample_rate": 44100, "dry": True, "reverb": False, "chorus": False,
            "midi_ticks_per_second": 1000, "pitch_bend_range_semitones": 2,
            "level_policy": "CC11 = round(127 * 10 ** ((dB - max_dB) / 20)); acoustic dB ratios are not guaranteed"},
        "events": recipe}
    with _staging_directory(output.parent) as workspace:
        stage = workspace / "publish"
        stage.mkdir()
        midi, wav = stage / names[0], stage / names[1]
        midi.write_bytes(midi_bytes)
        config = stage / "empty.cfg"
        config.write_text("", encoding="utf-8")
        _run([str(executable), "-i", "-n", "-q", "-f", str(config), "-r", "44100",
              "-C", "0", "-R", "0", "-o", "synth.midi-bank-select=mma",
              "-T", "wav", "-O", "s16", "-F", str(wav), str(soundfont), str(midi)], timeout=120)
        frames = (44100 * (duration + tail) + 999) // 1000
        info = _wav_info(wav, 44100, frames - min(64, frames // 100))
        raw_frames = info["frames"]
        if raw_frames < frames:
            raise RuntimeError("FluidSynth output is shorter than requested probe window")
        if raw_frames > frames:
            with wave.open(str(wav), "rb") as stream:
                params = stream.getparams()
                pcm = stream.readframes(frames)
            with wave.open(str(wav), "wb") as stream:
                stream.setparams(params)
                stream.writeframes(pcm)
            info = _wav_info(wav, 44100, frames)
        manifest["conditions"]["output_window"] = {
            "policy": "retain first requested duration plus tail; discard FluidSynth trailing frames",
            "raw_frames": raw_frames, "published_frames": frames}
        if not 0 < info["peak_pcm16"] < 32767:
            raise RuntimeError("composite WAV is silent or clipped")
        for path, expected in ((analysis_path, analysis_hash), (diagnostics_path, diagnostics_hash),
                               (soundfont, soundfont_hash), (executable, executable_hash)):
            if _sha256(path) != expected:
                raise RuntimeError(f"render input changed during rendering: {path}")
        manifest["midi"] = {"file": midi.name, "sha256": _sha256(midi)}
        manifest["wav"] = {"file": wav.name, "sha256": _sha256(wav), **info}
        (stage / names[2]).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        config.unlink()
        # Publish the complete directory; rename refuses an existing output on Windows.
        stage.rename(output)
    return manifest
