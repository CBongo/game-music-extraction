"""Deterministic Standard MIDI probes and isolated FluidSynth GM renders."""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from typing import Iterable

MANIFEST_VERSION = "instrument-render/v1"
TICKS_PER_SECOND = 1000  # PPQ 1000, explicit tempo 1,000,000 us/quarter


def _values(values: Iterable[int], name: str) -> tuple[int, ...]:
    values = tuple(values)
    if any(type(v) is not int or not 0 <= v <= 127 for v in values):
        raise ValueError(f"{name} must contain MIDI integers from 0 through 127")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} contains duplicates")
    return values


def plan_gm_reference_probes(
    *, notes: Iterable[int] = (48, 60, 72), velocities: Iterable[int] = (96,),
    programs: Iterable[int] = range(128),
    percussion_keys: Iterable[int] = range(35, 82),
    banks: Iterable[int] = (0,),
) -> tuple[dict, ...]:
    """Plan GM presets and channel-10 drum keys in stable order.

    Banks use 14-bit MIDI CC0/CC32 in FluidSynth's mma bank-select mode.
    Channel 10 drums send no bank CC and use the default GM percussion route.
    """
    notes = _values(notes, "notes")
    velocities = _values(velocities, "velocities")
    if any(velocity == 0 for velocity in velocities):
        raise ValueError("velocities must be 1 through 127; note-on velocity 0 is note-off")
    programs = _values(programs, "programs")
    percussion_keys = _values(percussion_keys, "percussion_keys")
    banks = tuple(banks)
    if any(type(b) is not int or not 0 <= b <= 16383 for b in banks):
        raise ValueError("banks must contain integers from 0 through 16383")
    if len(banks) != len(set(banks)):
        raise ValueError("banks contains duplicates")
    if not velocities or (programs and (not notes or not banks)):
        raise ValueError("velocities, melodic notes, and melodic banks cannot be empty")
    probes: list[dict] = []
    for bank in banks:
        for program in programs:
            for note in notes:
                for velocity in velocities:
                    probes.append({
                        "probe_id": f"gm-b{bank:05d}-p{program:03d}-n{note:03d}-v{velocity:03d}",
                        "kind": "melodic", "source_record_id": None,
                        "route": {"kind": "gm_program", "program_0based": program,
                                  "program_1based": program + 1},
                        "channel_0based": 0, "channel_1based": 1,
                        "bank": bank, "bank_source": "midi_cc0_cc32", "bank_select_msb": bank >> 7,
                        "bank_select_lsb": bank & 127,
                        "program_0based": program, "program_1based": program + 1,
                        "note": note, "velocity": velocity,
                    })
    for note in percussion_keys:
        for velocity in velocities:
            probes.append({
                "probe_id": f"gm-drums-p000-n{note:03d}-v{velocity:03d}",
                "kind": "percussion", "source_record_id": None,
                "route": {"kind": "gm_percussion", "note": note},
                "channel_0based": 9, "channel_1based": 10,
                "bank": 128, "bank_source": "channel_10_gm_default", "bank_select_msb": None, "bank_select_lsb": None,
                "program_0based": 0, "program_1based": 1,
                "note": note, "velocity": velocity,
            })
    if not probes:
        raise ValueError("the probe plan is empty")
    return tuple(probes)


def _ticks(seconds: float, label: str, *, zero: bool = False) -> int:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        raise ValueError(f"{label} must be a finite number")
    ticks = round(seconds * TICKS_PER_SECOND)
    if ticks < (0 if zero else 1) or ticks > 0x0FFFFFFF:
        raise ValueError(f"{label} is outside MIDI tick range")
    return ticks


def _vlq(value: int) -> bytes:
    result = [value & 127]
    value >>= 7
    while value:
        result.insert(0, (value & 127) | 128)
        value >>= 7
    return bytes(result)


def make_probe_midi(probe: dict, *, duration_seconds: float = 0.5,
                    tail_seconds: float = 0.5) -> bytes:
    """Return a format-0 SMF with tempo, note off, and timed end of track."""
    duration = _ticks(duration_seconds, "duration_seconds")
    tail = _ticks(tail_seconds, "tail_seconds", zero=True)
    channel = probe["channel_0based"]
    program, note, velocity = (probe[k] for k in ("program_0based", "note", "velocity"))
    if (type(channel) is not int or channel not in (0, 9) or
            any(type(x) is not int or not 0 <= x <= 127 for x in (program, note, velocity))
            or velocity == 0):
        raise ValueError("invalid MIDI probe route or note")
    track = bytearray(b"\x00\xff\x51\x03\x0f\x42\x40")
    if channel == 0:
        msb, lsb = probe["bank_select_msb"], probe["bank_select_lsb"]
        if any(type(x) is not int or not 0 <= x <= 127 for x in (msb, lsb)):
            raise ValueError("invalid bank select data")
        track.extend(bytes((0, 0xB0, 0, msb, 0, 0xB0, 32, lsb)))
    elif probe["bank_select_msb"] is not None or probe["bank_select_lsb"] is not None:
        raise ValueError("percussion probes cannot contain bank select CC")
    track.extend(bytes((0, 0xC0 | channel, program, 0, 0x90 | channel, note, velocity)))
    track.extend(_vlq(duration))
    track.extend(bytes((0x80 | channel, note, 0)))
    track.extend(_vlq(tail))
    track.extend(b"\xff\x2f\x00")
    return (b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x03\xe8"
            + b"MTrk" + len(track).to_bytes(4, "big") + track)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_soundfont_presets(soundfont_path: str | Path) -> dict[tuple[int, int], str]:
    """Read bank/program names from a RIFF sfbk (SF2 or SF3) phdr table.

    Only RIFF chunk headers and the preset headers are read; sample payloads
    are skipped. The final EOP record is a sentinel, not an audible preset.
    """
    path = Path(soundfont_path)
    size = path.stat().st_size
    with path.open("rb") as source:
        header = source.read(12)
        if (len(header) != 12 or header[:4] != b"RIFF" or
                header[8:12] != b"sfbk"):
            raise ValueError(f"not an SF2/SF3 RIFF sfbk SoundFont: {path}")
        end = 8 + struct.unpack_from("<I", header, 4)[0]
        if end > size or end < 12:
            raise ValueError(f"truncated SoundFont RIFF: {path}")
        offset = 12
        phdr = None
        while offset + 8 <= end:
            source.seek(offset)
            chunk_id, chunk_size = struct.unpack("<4sI", source.read(8))
            payload_end = offset + 8 + chunk_size
            next_offset = payload_end + (chunk_size & 1)
            if next_offset > end:
                raise ValueError(f"truncated SoundFont chunk: {path}")
            if chunk_id == b"LIST":
                if chunk_size < 4:
                    raise ValueError(f"invalid SoundFont LIST: {path}")
                list_type = source.read(4)
                if list_type == b"pdta":
                    sub_offset = offset + 12
                    while sub_offset + 8 <= payload_end:
                        source.seek(sub_offset)
                        sub_id, sub_size = struct.unpack("<4sI", source.read(8))
                        sub_end = sub_offset + 8 + sub_size
                        if sub_end + (sub_size & 1) > payload_end:
                            raise ValueError(f"truncated SoundFont pdta chunk: {path}")
                        if sub_id == b"phdr":
                            if phdr is not None:
                                raise ValueError("SoundFont has duplicate phdr tables")
                            phdr = source.read(sub_size)
                        sub_offset = sub_end + (sub_size & 1)
            offset = next_offset
    if phdr is None or len(phdr) < 38 or len(phdr) % 38:
        raise ValueError(f"SoundFont has no valid phdr table: {path}")
    entries = []
    for offset in range(0, len(phdr), 38):
        name_bytes, program, bank, *_ = struct.unpack_from("<20sHHHIII", phdr, offset)
        name = name_bytes.split(b"\x00", 1)[0].decode("latin-1").strip()
        entries.append((bank, program, name))
    if entries[-1][2] != "EOP":
        raise ValueError(f"SoundFont phdr lacks EOP sentinel: {path}")
    presets: dict[tuple[int, int], str] = {}
    for bank, program, name in entries[:-1]:
        key = (bank, program)
        if key in presets:
            raise ValueError(f"SoundFont has duplicate bank {bank}, program {program}")
        presets[key] = name
    return presets

def _wav_info(path: Path, sample_rate: int, minimum_frames: int) -> dict:
    try:
        with wave.open(str(path), "rb") as stream:
            channels = stream.getnchannels()
            rate = stream.getframerate()
            frames = stream.getnframes()
            width = stream.getsampwidth()
            compression = stream.getcomptype()
            pcm = stream.readframes(frames)
    except (OSError, EOFError, wave.Error) as exc:
        raise RuntimeError(f"not a readable PCM WAV: {path}") from exc
    if (rate != sample_rate or channels not in (1, 2) or frames <= 0
            or frames < minimum_frames or width != 2 or compression != "NONE"
            or len(pcm) != frames * channels * width):
        raise RuntimeError(f"invalid WAV properties: {path}")
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return {"sample_rate": rate, "channels": channels, "sample_width_bytes": width,
            "frames": frames, "duration_seconds": frames / rate,
            "peak_pcm16": max((abs(sample) for sample in samples), default=0)}


def _run(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(argv, shell=False, capture_output=True,
                                text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"FluidSynth invocation failed: {argv[0]}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()[-2000:]
        raise RuntimeError(f"FluidSynth exited {result.returncode}: {detail}")
    return result


def render_gm_reference_set(
    soundfont_path: str | Path, output_dir: str | Path, *,
    fluidsynth_path: str | Path = "fluidsynth", sample_rate: int = 44100,
    notes: Iterable[int] = (48, 60, 72), velocities: Iterable[int] = (96,),
    duration_seconds: float = 0.5, tail_seconds: float = 0.5,
    programs: Iterable[int] = range(128),
    percussion_keys: Iterable[int] = range(35, 82), dry: bool = True,
    banks: Iterable[int] = (0,), overwrite: bool = False,
) -> dict:
    """Render isolated reference WAVs; publish a manifest on complete success."""
    soundfont = Path(soundfont_path).expanduser().resolve(strict=True)
    if not soundfont.is_file():
        raise ValueError("soundfont_path must name a file")
    found = shutil.which(os.fspath(fluidsynth_path))
    if found is None:
        raise FileNotFoundError(f"FluidSynth executable not found: {fluidsynth_path}")
    executable = Path(found).resolve(strict=True)
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
        raise ValueError("sample_rate must be an integer from 8000 through 192000")
    if type(dry) is not bool or type(overwrite) is not bool:
        raise ValueError("dry and overwrite must be booleans")
    duration = _ticks(duration_seconds, "duration_seconds")
    tail = _ticks(tail_seconds, "tail_seconds", zero=True)
    probes = plan_gm_reference_probes(notes=notes, velocities=velocities,
        programs=programs, percussion_keys=percussion_keys, banks=banks)
    presets = read_soundfont_presets(soundfont)
    missing = sorted({(probe["bank"], probe["program_0based"]) for probe in probes}
                     - presets.keys())
    if missing:
        examples = ", ".join(f"bank={bank} program={program}"
                             for bank, program in missing[:8])
        raise ValueError(f"SoundFont lacks {len(missing)} requested presets: {examples}")
    expected_frames = (sample_rate * (duration + tail) + TICKS_PER_SECOND - 1) // TICKS_PER_SECOND
    minimum_frames = expected_frames - min(64, expected_frames // 100)
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths = [output / f"{probe['probe_id']}.wav" for probe in probes]
    manifest_path = output / "manifest.json"
    if not overwrite:
        collisions = [path for path in (*paths, manifest_path) if path.exists()]
        if collisions:
            raise FileExistsError(f"reference output already exists: {collisions[0]}")
    version = _run([str(executable), "--version"], timeout=15)
    manifest = {
        "schema_version": MANIFEST_VERSION,
        "generator": {"name": "instrument_analysis.rendering",
                      "executable": str(executable), "executable_sha256": _sha256(executable),
                      "version": (version.stdout or version.stderr).strip()},
        "soundfont": {"path": str(soundfont), "sha256": _sha256(soundfont),
                      "preset_count": len(presets)},
        "conditions": {"sample_rate": sample_rate,
                       "duration_seconds": duration / TICKS_PER_SECOND,
                       "tail_seconds": tail / TICKS_PER_SECOND,
                       "dry": dry, "reverb": not dry, "chorus": not dry,
                       "midi_bank_select": "mma", "midi_format": 0,
                       "midi_ticks_per_second": TICKS_PER_SECOND},
        "probes": [],
    }
    with tempfile.TemporaryDirectory(prefix=".gm-render-", dir=output) as stage_name:
        stage = Path(stage_name)
        config = stage / "empty.cfg"
        config.write_text("", encoding="utf-8")
        for probe in probes:
            stem = probe["probe_id"]
            midi = stage / f"{stem}.mid"
            wav = stage / f"{stem}.wav"
            midi.write_bytes(make_probe_midi(probe,
                duration_seconds=duration / TICKS_PER_SECOND,
                tail_seconds=tail / TICKS_PER_SECOND))
            command = [str(executable), "--no-shell", "--no-midi-in", "--quiet",
                       f"--load-config={config}", f"--sample-rate={sample_rate}",
                       f"--chorus={0 if dry else 1}",
                       f"--reverb={0 if dry else 1}",
                       "-o", "synth.midi-bank-select=mma",
                       "--audio-file-type=wav", "--audio-file-format=s16",
                       f"--fast-render={wav}", str(soundfont), str(midi)]
            try:
                _run(command, timeout=120)
                info = _wav_info(wav, sample_rate, minimum_frames)
            except RuntimeError as exc:
                raise RuntimeError(f"probe {stem} failed: {exc}") from exc
            manifest["probes"].append({**probe,
                "preset_name": presets[(probe["bank"], probe["program_0based"])],
                "midi_sha256": _sha256(midi),
                "wav": {"file": wav.name, "sha256": _sha256(wav), **info}})
            midi.unlink()
        for path in paths:
            (stage / path.name).replace(path)
        temp_manifest = stage / "manifest.json"
        temp_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
        temp_manifest.replace(manifest_path)
    return manifest


def _numbers(text: str) -> tuple[int, ...]:
    result: list[int] = []
    try:
        for piece in text.split(","):
            if "-" in piece:
                first, last = (int(x) for x in piece.split("-", 1))
                if first > last:
                    raise ValueError("range must ascend")
                result.extend(range(first, last + 1))
            else:
                result.append(int(piece))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return tuple(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render isolated GM SoundFont references")
    parser.add_argument("--soundfont", required=True, type=Path)
    parser.add_argument("--fluidsynth", default="fluidsynth")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-rate", type=int, default=44100)
    parser.add_argument("--notes", type=_numbers, default=(48, 60, 72))
    parser.add_argument("--velocities", type=_numbers, default=(96,))
    parser.add_argument("--duration", type=float, default=0.5)
    parser.add_argument("--tail", type=float, default=0.5)
    parser.add_argument("--programs", type=_numbers, default=tuple(range(128)))
    parser.add_argument("--percussion", type=_numbers, default=tuple(range(35, 82)))
    parser.add_argument("--banks", type=_numbers, default=(0,))
    parser.add_argument("--wet", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    render_gm_reference_set(args.soundfont, args.output,
        fluidsynth_path=args.fluidsynth, sample_rate=args.sample_rate,
        notes=args.notes, velocities=args.velocities,
        duration_seconds=args.duration, tail_seconds=args.tail,
        programs=args.programs, percussion_keys=args.percussion,
        dry=not args.wet, banks=args.banks, overwrite=args.overwrite)
    print(args.output.resolve() / "manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
