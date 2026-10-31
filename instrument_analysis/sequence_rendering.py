"""Candidate GM rendering of explicit, provenance-bound sequence recipes."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import os
from pathlib import Path
import shutil
import wave

from .rendering import _run, _sha256, _staging_directory, _ticks, _vlq, _wav_info, read_soundfont_presets


def _number(value, label, minimum=None):
    if type(value) not in (int, float) or not math.isfinite(value) or (minimum is not None and value < minimum):
        raise ValueError(f"{label} must be a finite number in range")
    return value


def _curve(points, kind, duration):
    if not isinstance(points, list):
        raise ValueError(f"{kind} must be an array")
    value_key = "cents" if kind == "pitch_curve" else "value_0_127"
    previous = -1
    for point in points:
        if not isinstance(point, dict) or set(point) != {"time_seconds", value_key}:
            raise ValueError(f"invalid {kind} point")
        time = _number(point["time_seconds"], "curve time", 0)
        if time <= previous or time > duration:
            raise ValueError("curve times must ascend within event duration")
        value = _number(point[value_key], value_key)
        if value_key == "value_0_127" and (type(value) is not int or not 0 <= value <= 127):
            raise ValueError("expression values must be MIDI integers 0..127")
        previous = time
    return points


def make_sequence_midi(recipe, program_0based, octave_adjustment=0, component_offsets=None):
    """Return format-0 SMF and exact quantized event recipe.

    Channels are reserved through recipe end by default to protect unknown
    SoundFont release tails. An explicit release_guard_seconds permits reuse,
    with CC120 terminating old voices before new setup. CC11 follows the supplied
    expression curve scaled by the normalized relative-level coefficient; this
    is a MIDI policy, not a guarantee of acoustic amplitude ratios.
    """
    allowed = {"schema_version", "source_record_id", "source", "provenance", "events", "end_seconds", "correction"}
    if not isinstance(recipe, dict) or set(recipe) - allowed or recipe.get("schema_version") != "instrument-sequence-recipe/v1":
        raise ValueError("invalid sequence recipe")
    if not isinstance(recipe.get("source_record_id"), str) or not recipe["source_record_id"]:
        raise ValueError("source_record_id is required")
    source = recipe.get("source", {})
    if not isinstance(source, dict) or set(source) != {"path", "sha256"} or not isinstance(source["path"], str) or not isinstance(source["sha256"], str) or len(source["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in source["sha256"]):
        raise ValueError("source requires path and SHA256")
    provenance = recipe.get("provenance", {})
    if not isinstance(provenance, dict) or provenance.get("effects_complete") is not True or any(not isinstance(provenance.get(k), str) or not provenance[k] for k in ("context", "basis", "timing_basis", "pitch_basis")):
        raise ValueError("explicit complete effect and timing/pitch provenance is required")
    guard = provenance.get("release_guard_seconds")
    guard_ticks = None
    if "release_guard_seconds" in provenance:
        _number(guard, "release_guard_seconds", 0)
        guard_ticks = _ticks(guard, "release_guard_seconds", zero=True)
    if type(program_0based) is not int or not 0 <= program_0based <= 127 or type(octave_adjustment) is not int:
        raise ValueError("invalid program or octave adjustment")
    end_tick = _ticks(recipe.get("end_seconds"), "end_seconds")
    offsets = [{"semitone_offset": 0, "residual_cents": 0, "relative_level_db": 0}] if component_offsets is None else component_offsets
    if not isinstance(offsets, list) or not offsets:
        raise ValueError("component_offsets must be nonempty")
    for offset in offsets:
        if not isinstance(offset, dict) or set(offset) != {"semitone_offset", "residual_cents", "relative_level_db"} or type(offset["semitone_offset"]) is not int:
            raise ValueError("invalid component offset")
        _number(offset["residual_cents"], "component residual")
        _number(offset["relative_level_db"], "component level")
    events = recipe.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("events must be nonempty")
    expanded, ids = [], set()
    fields = {"event_id", "midi_note", "start_seconds", "duration_seconds", "velocity", "residual_cents", "relative_level_db", "pitch_curve", "expression_curve"}
    for event in events:
        if not isinstance(event, dict) or set(event) - fields or fields - {"pitch_curve", "expression_curve"} - set(event):
            raise ValueError("invalid or unsupported event fields")
        if not isinstance(event["event_id"], str) or not event["event_id"] or event["event_id"] in ids:
            raise ValueError("event_id must be a unique nonempty string")
        ids.add(event["event_id"])
        if type(event["midi_note"]) is not int or not 0 <= event["midi_note"] <= 127 or type(event["velocity"]) is not int or not 1 <= event["velocity"] <= 127:
            raise ValueError("invalid MIDI note or velocity")
        start = _number(event["start_seconds"], "event start", 0)
        duration = _number(event["duration_seconds"], "event duration", 0)
        if duration <= 0 or start + duration > recipe["end_seconds"] + 1e-12:
            raise ValueError("event duration exceeds recipe window")
        start_tick = _ticks(start, "event start", zero=True)
        off_tick = _ticks(start + duration, "event end")
        if off_tick <= start_tick:
            raise ValueError("event collapses at MIDI resolution")
        pitch = _curve(event.get("pitch_curve", []), "pitch_curve", duration)
        expression = _curve(event.get("expression_curve", []), "expression_curve", duration)
        for index, offset in enumerate(offsets):
            note = event["midi_note"] + 12 * octave_adjustment + offset["semitone_offset"]
            cents = _number(event["residual_cents"], "residual cents") + offset["residual_cents"]
            if not 0 <= note <= 127 or any(not -200 <= cents + delta <= 200 for delta in [0] + [p["cents"] for p in pitch]):
                raise ValueError("expanded pitch outside note or bend range")
            expanded.append({**deepcopy(event), "midi_note": note, "residual_cents": cents,
                "relative_level_db": _number(event["relative_level_db"], "relative level") + offset["relative_level_db"],
                "component_index": index, "start_tick": start_tick, "note_off_tick": off_tick,
                "pitch_curve": deepcopy(pitch), "expression_curve": deepcopy(expression)})
    peak = max(e["relative_level_db"] for e in expanded)
    scheduled = [(0, -1, b"\xff\x51\x03\x0f\x42\x40")]
    free_at = {channel: 0 for channel in range(16) if channel != 9}
    used_channels = set()
    output = []
    for event in sorted(expanded, key=lambda e: (e["start_tick"], e["event_id"], e["component_index"])):
        available = [c for c, tick in free_at.items() if tick <= event["start_tick"]]
        if not available:
            raise ValueError("more than 15 melodic voices under selected release policy")
        channel = available[0]
        reused = channel in used_channels
        used_channels.add(channel)
        free_at[channel] = end_tick + 1 if guard_ticks is None else event["note_off_tick"] + guard_ticks
        event["channel_0based"] = channel
        event["reuse_cut"] = reused
        coefficient = 10 ** ((event["relative_level_db"] - peak) / 20)
        event["normalized_relative_level_db"] = event["relative_level_db"] - peak
        def cc(tick, controller, value, priority=2):
            scheduled.append((tick, priority, bytes((0xB0 | channel, controller, value))))
        start = event["start_tick"]
        if reused:
            # All Sound Off kills releases on this channel before changing bend.
            cc(start, 120, 0)
        for controller, value in ((0, 0), (32, 0), (7, 100), (101, 0), (100, 0), (6, 2), (38, 0), (101, 127), (100, 127)):
            cc(start, controller, value)
        scheduled.append((start, 2, bytes((0xC0 | channel, program_0based))))
        pitch_points = [{"time_seconds": 0, "cents": 0}] + event["pitch_curve"]
        expression_points = [{"time_seconds": 0, "value_0_127": 127}] + event["expression_curve"]
        event["quantized_pitch_curve"], event["quantized_expression_curve"] = [], []
        for point in pitch_points:
            tick = _ticks(event["start_seconds"] + point["time_seconds"], "pitch time", zero=True)
            bend = max(0, min(16383, round(8192 + (event["residual_cents"] + point["cents"]) / 200 * 8192)))
            scheduled.append((tick, 2 if tick == start else 0, bytes((0xE0 | channel, bend & 127, bend >> 7))))
            event["quantized_pitch_curve"].append({"tick": tick, "pitch_bend_14bit": bend, "rendered_cents": (bend - 8192) * 200 / 8192})
        for point in expression_points:
            tick = _ticks(event["start_seconds"] + point["time_seconds"], "expression time", zero=True)
            value = round(point["value_0_127"] * coefficient)
            cc(tick, 11, value, 2 if tick == start else 0)
            event["quantized_expression_curve"].append({"tick": tick, "expression_cc11": value})
        scheduled.append((start, 3, bytes((0x90 | channel, event["midi_note"], event["velocity"]))))
        scheduled.append((event["note_off_tick"], 1, bytes((0x80 | channel, event["midi_note"], 0))))
        output.append(event)
    scheduled.append((end_tick, 4, b"\xff\x2f\x00"))
    track, previous = bytearray(), 0
    for tick, _, payload in sorted(scheduled, key=lambda entry: (entry[0], entry[1])):
        track.extend(_vlq(tick - previous) + payload)
        previous = tick
    return (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x03\xe8" + b"MTrk" + len(track).to_bytes(4, "big") + track), output


def render_sequence_preview(recipe_path, *, program_0based, soundfont_path,
                            fluidsynth_path, output_dir, octave_adjustment=0, component_offsets=None):
    """Render a candidate file only; no claim of native driver equivalence."""
    recipe_path = Path(recipe_path).resolve(strict=True)
    recipe_hash = _sha256(recipe_path)
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    midi_bytes, events = make_sequence_midi(recipe, program_0based, octave_adjustment, component_offsets)
    source_path = Path(recipe["source"]["path"])
    if not source_path.is_absolute():
        source_path = recipe_path.parent / source_path
    source_path = source_path.resolve(strict=True)
    if _sha256(source_path) != recipe["source"]["sha256"]:
        raise ValueError("recipe source hash mismatch")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if source.get("source_record_id") != recipe["source_record_id"] and not any(r.get("record_id") == recipe["source_record_id"] for r in source.get("records", [])):
        raise ValueError("recipe source record identity mismatch")
    soundfont = Path(soundfont_path).resolve(strict=True)
    found = shutil.which(os.fspath(fluidsynth_path))
    if found is None:
        raise FileNotFoundError("FluidSynth executable not found")
    executable = Path(found).resolve(strict=True)
    bound = [(recipe_path, recipe_hash), (source_path, recipe["source"]["sha256"]), (soundfont, _sha256(soundfont)), (executable, _sha256(executable))]
    audio_ref = recipe["provenance"].get("source_sequence_audio")
    if audio_ref is not None:
        if not isinstance(audio_ref, dict) or set(audio_ref) != {"path", "sha256"}:
            raise ValueError("source_sequence_audio requires path and SHA256")
        audio_path = Path(audio_ref["path"])
        if not audio_path.is_absolute():
            audio_path = recipe_path.parent / audio_path
        audio_path = audio_path.resolve(strict=True)
        if _sha256(audio_path) != audio_ref["sha256"]:
            raise ValueError("source sequence audio hash mismatch")
        context = recipe["provenance"].get("source_context")
        source_context = source.get("source_context")
        keys = {"capture_context_sha256", "source_audio_sha256"}
        if (not isinstance(context, dict) or not isinstance(source_context, dict)
                or not keys <= set(context) or not keys <= set(source_context)):
            raise ValueError("source sequence audio requires bound capture context")
        for key in keys:
            value = context[key]
            if (not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
                    or source_context[key] != value):
                raise ValueError("source sequence capture context mismatch")
        if context["source_audio_sha256"] != audio_ref["sha256"]:
            raise ValueError("source sequence context/audio hash mismatch")
        bound.append((audio_path, audio_ref["sha256"]))
    if (0, program_0based) not in read_soundfont_presets(soundfont):
        raise ValueError("SoundFont lacks requested bank-0 program")
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError("sequence preview output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    version = _run([str(executable), "-V"], timeout=15)
    manifest = {"schema_version": "instrument-sequence-preview/v1", "source_record_id": recipe["source_record_id"],
        "recipe": {"path": str(recipe_path), "sha256": recipe_hash}, "source": {"path": str(source_path), "sha256": bound[1][1]},
        "generator": {"name": "instrument_analysis.sequence_rendering", "executable": str(executable), "executable_sha256": bound[3][1], "version": (version.stdout or version.stderr).strip()},
        "soundfont": {"path": str(soundfont), "sha256": bound[2][1]}, "provenance": deepcopy(recipe["provenance"]),
        "conditions": {"candidate_only": True, "dry": True, "reverb": False, "chorus": False, "sample_rate": 44100,
            "program_0based": program_0based, "octave_adjustment": octave_adjustment, "component_offsets": component_offsets,
            "midi_ticks_per_second": 1000, "pitch_bend_range_semitones": 2,
            "level_policy": "CC11 = round(curve_value * 10 ** ((dB - max_dB) / 20)); acoustic dB ratios not guaranteed"}, "events": events}
    guard = recipe["provenance"].get("release_guard_seconds")
    manifest["conditions"]["release_policy"] = {
        "policy": "reserve_channels_through_recipe_end" if guard is None else "explicit_guard_then_cc120_before_reuse",
        "release_guard_seconds": guard,
        "release_guard_ticks": None if guard is None else _ticks(guard, "release_guard_seconds", zero=True),
        "quantization": "guard rounded to nearest 1ms; reuse at note_off_tick plus guard ticks",
        "cut_scope": "CC120 All Sound Off terminates lingering release voices on reused channel before new setup; explicit tail truncation"}
    with _staging_directory(output.parent) as workspace:
        stage = workspace / "publish"
        stage.mkdir()
        midi, wav, config = stage / "sequence.mid", stage / "sequence.wav", stage / "empty.cfg"
        midi.write_bytes(midi_bytes)
        config.write_text("", encoding="utf-8")
        _run([str(executable), "-i", "-n", "-q", "-f", str(config), "-r", "44100", "-C", "0", "-R", "0", "-o", "synth.midi-bank-select=mma", "-T", "wav", "-O", "s16", "-F", str(wav), str(soundfont), str(midi)], timeout=120)
        frames = (44100 * _ticks(recipe["end_seconds"], "end_seconds") + 999) // 1000
        raw = _wav_info(wav, 44100, frames)
        with wave.open(str(wav), "rb") as stream:
            params, pcm = stream.getparams(), stream.readframes(frames)
        with wave.open(str(wav), "wb") as stream:
            stream.setparams(params)
            stream.writeframes(pcm)
        info = _wav_info(wav, 44100, frames)
        if not 0 < info["peak_pcm16"] < 32767:
            raise RuntimeError("sequence WAV is silent or clipped")
        for path, digest in bound:
            if _sha256(path) != digest:
                raise RuntimeError(f"render input changed during rendering: {path}")
        manifest["conditions"]["output_window"] = {"raw_frames": raw["frames"], "published_frames": frames, "end_seconds_quantized": _ticks(recipe["end_seconds"], "end_seconds") / 1000}
        manifest["midi"] = {"file": midi.name, "sha256": _sha256(midi)}
        manifest["wav"] = {"file": wav.name, "sha256": _sha256(wav), **info}
        if "correction" in recipe:
            manifest["correction"] = deepcopy(recipe["correction"])
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        config.unlink()
        stage.rename(output)
    return manifest
