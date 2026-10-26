"""Conservative, provenance-bound acoustic pitch measurement for native renders.

This module estimates the sounding pitch of isolated monophonic WAV captures. It
never converts a chip pitch register into a MIDI note; MIDI pitch is derived only
from the measured PCM fundamental. Ambiguous or non-periodic material abstains.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import wave
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _decode_pcm(path: Path) -> tuple[int, list[float]]:
    with wave.open(str(path), "rb") as wav:
        channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
        if channels not in (1, 2) or width != 2 or rate < 8000 or frames < 1:
            raise ValueError("pitch calibration requires nonempty mono/stereo PCM16 WAV")
        raw = wav.readframes(frames)
    if len(raw) != frames * channels * 2:
        raise ValueError("truncated PCM16 WAV")
    count = len(raw) // 2
    import array
    values = array.array("h")
    values.frombytes(raw)
    if __import__("sys").byteorder != "little":
        values.byteswap()
    mono = [sum(values[i:i + channels]) / channels for i in range(0, count, channels)]
    return rate, mono


def _resample_average(signal: list[float], rate: int, target_rate: int = 11025) -> tuple[list[float], int]:
    if rate <= target_rate:
        return signal, rate
    factor = max(1, round(rate / target_rate))
    out = [sum(signal[i:i + factor]) / len(signal[i:i + factor]) for i in range(0, len(signal), factor)]
    return out, rate / factor


def _estimate_window(samples: list[float], rate: float) -> tuple[float | None, float, float, float]:
    """Return F0, periodicity and runner-up ambiguity for one window."""
    n = len(samples)
    if n < 512:
        return None, 0.0, 0.0, 0.0
    mean = sum(samples) / n
    x = [v - mean for v in samples]
    rms = math.sqrt(sum(v * v for v in x) / n)
    if rms < 80.0:  # PCM16; conservatively reject very quiet/noise-floor windows.
        return None, 0.0, 0.0, 0.0
    min_lag = max(4, int(rate / 1500.0))
    max_lag = min(n // 3, int(rate / 55.0))
    if max_lag <= min_lag + 2:
        return None, 0.0, 0.0, 0.0
    correlations: list[float] = []
    # Mean-removed normalized autocorrelation, with overlap-specific energy.
    for lag in range(min_lag, max_lag + 1):
        left = x[:-lag]
        right = x[lag:]
        e1 = sum(v * v for v in left)
        e2 = sum(v * v for v in right)
        if e1 <= 0 or e2 <= 0:
            correlations.append(-1.0)
        else:
            correlations.append(sum(a * b for a, b in zip(left, right)) / math.sqrt(e1 * e2))
    peaks = []
    for idx in range(1, len(correlations) - 1):
        c = correlations[idx]
        if c >= correlations[idx - 1] and c > correlations[idx + 1]:
            peaks.append((c, idx + min_lag))
    if not peaks:
        return None, 0.0, 0.0, 0.0
    peaks.sort(reverse=True)
    strongest = peaks[0][0]
    # Prefer the earliest strong periodic peak to avoid selecting an octave
    # subharmonic. If a sharper peak is far stronger, retain the actual maximum.
    eligible = [p for p in peaks if p[0] >= max(0.68, strongest * 0.90)]
    chosen = min(eligible, key=lambda p: p[1]) if eligible else peaks[0]
    periodicity, lag = chosen
    if periodicity < 0.72:
        return None, max(0.0, periodicity), 0.0, 0.0
    # Parabolic peak interpolation reduces integer-lag quantization bias.
    peak_index = lag - min_lag
    fractional = 0.0
    if 0 < peak_index < len(correlations) - 1:
        left, center, right = correlations[peak_index - 1:peak_index + 2]
        denominator = left - 2.0 * center + right
        if abs(denominator) > 1e-12:
            fractional = max(-0.5, min(0.5, 0.5 * (left - right) / denominator))
    refined_lag = lag + fractional
    frequency = rate / refined_lag
    if not 55.0 <= frequency <= 1500.0:
        return None, periodicity, 0.0, 0.0
    # Similar-strength unrelated peaks indicate a competing tone or ambiguity.
    alternatives = [c for c, l in peaks if abs(l - lag) > max(2, lag * 0.035) and not (abs((l / lag) - round(l / lag)) <= 0.06 * max(1, round(l / lag))) and not (abs((lag / l) - round(lag / l)) <= 0.06 * max(1, round(lag / l)))]
    runner = max(alternatives, default=-1.0)
    ambiguity = max(0.0, runner)
    segment = max(64, n // 11)
    envelope = []
    for start in range(0, n, segment):
        chunk = x[start:start + segment]
        if len(chunk) >= segment // 2:
            envelope.append(math.sqrt(sum(v * v for v in chunk) / len(chunk)))
    envelope_mean = statistics.mean(envelope) if envelope else 0.0
    envelope_modulation = (statistics.pstdev(envelope) / envelope_mean) if envelope_mean > 0 else 1.0
    return frequency, periodicity, ambiguity, envelope_modulation


def _raw_pitch(manifest: dict[str, Any]) -> tuple[str, int]:
    key = manifest.get("key")
    if not isinstance(key, dict):
        raise ValueError("manifest has no raw-pitch key object")
    for name, value in key.items():
        if name.startswith("pitch_register_raw_") or name.startswith("keycode_raw_"):
            if isinstance(value, int) and not isinstance(value, bool):
                return name, value
    raise ValueError("manifest does not expose a supported raw pitch register/keycode")


def _measure_pitch(wav_path: Path, note_on: float, note_off: float, *, trim_start: float = 0.0) -> dict[str, Any]:
    """Shared sustain-window acoustic estimator for manifest and plain WAV inputs."""
    for name, value in (("note_on_seconds", note_on), ("note_off_seconds", note_off), ("trim_start_seconds", trim_start)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if note_on < 0 or note_off <= note_on or trim_start < 0:
        raise ValueError("pitch event times are invalid")
    rate, pcm = _decode_pcm(wav_path)
    reduced, reduced_rate = _resample_average(pcm, rate)
    first = float(note_on) - float(trim_start) + 0.14
    last = float(note_off) - float(trim_start) - 0.07
    wav_duration = len(pcm) / rate
    if first < 0 or last > wav_duration + 1.0 / rate:
        raise ValueError("event times do not align with WAV trim bounds")
    window_seconds, hop_seconds = 0.18, 0.08
    windows = []
    t = first
    while t + window_seconds <= last + 1e-9 and len(windows) < 12:
        a = int(round(t * reduced_rate))
        b = int(round((t + window_seconds) * reduced_rate))
        f0, periodicity, ambiguity, envelope_modulation = _estimate_window(reduced[a:b], reduced_rate)
        windows.append({"start_seconds": round(t, 9), "end_seconds": round(t + window_seconds, 9),
                        "f0_hz": round(f0, 5) if f0 is not None else None,
                        "confidence": round(max(0.0, min(1.0, periodicity)), 5),
                        "periodicity": round(periodicity, 5), "ambiguity": round(ambiguity, 5),
                        "envelope_modulation": round(envelope_modulation, 5)})
        t += hop_seconds
    valid = [w for w in windows if w["f0_hz"] is not None]
    status, reason, f0_median, midi, confidence, mad_cents = "abstain", None, None, None, 0.0, None
    if len(valid) < 2:
        reason = "fewer than two periodic sustain windows"
    else:
        freqs = [float(w["f0_hz"]) for w in valid]
        f0_median = statistics.median(freqs)
        cents = [1200.0 * math.log2(f / f0_median) for f in freqs]
        mad_cents = statistics.median(abs(c) for c in cents)
        confidence = statistics.median(float(w["confidence"]) for w in valid)
        median_ambiguity = statistics.median(float(w["ambiguity"]) for w in valid)
        median_modulation = statistics.median(float(w["envelope_modulation"]) for w in valid)
        if mad_cents > 35.0:
            reason = "sustain-window pitch is unstable or competing"
        elif median_ambiguity > 0.65:
            reason = "competing non-harmonic periodic peaks make the fundamental ambiguous"
        elif median_modulation > 0.40:
            reason = "strong sustain-envelope beating suggests competing pitches"
        elif confidence < 0.80:
            reason = "periodicity/confidence below conservative threshold"
        else:
            status = "calibrated"
            midi = 69.0 + 12.0 * math.log2(f0_median / 440.0)
            reason = "stable monophonic acoustic fundamental measured across sustain windows"
    return {"status": status, "f0_hz": round(f0_median, 5) if f0_median is not None else None,
            "sounding_midi_note": round(midi, 5) if midi is not None else None,
            "confidence": round(confidence, 5), "evidence": reason,
            "window_estimates": windows,
            "median_absolute_deviation_cents": round(mad_cents, 5) if mad_cents is not None else None}

def calibrate_manifest_pitch(manifest_path: str | Path, *, output_path: str | Path | None = None) -> dict[str, Any]:
    """Measure isolated capture F0 and bind the result to manifest and WAV hashes."""
    manifest_path = Path(manifest_path).resolve()
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    source = manifest.get("source", {})
    wav_meta = manifest.get("wav", {})
    wav_path = (manifest_path.parent / wav_meta.get("file", "")).resolve()
    if not wav_path.is_file():
        raise ValueError("manifest WAV file is missing")
    wav_hash = _sha256(wav_path)
    if wav_hash != wav_meta.get("sha256"):
        raise ValueError("WAV hash does not match source manifest")
    raw_field, raw_value = _raw_pitch(manifest)
    conditions = manifest.get("conditions", {})
    note_on = conditions.get("note_on_time_seconds")
    note_off = conditions.get("note_off_time_seconds")
    if not isinstance(note_on, (int, float)) or isinstance(note_on, bool) or not isinstance(note_off, (int, float)) or isinstance(note_off, bool) or note_off <= note_on:
        raise ValueError("manifest lacks valid note-on/off times for isolated sustain analysis")
    alignment = conditions.get("wav_alignment", {})
    trim_start = alignment.get("actual_trim_start_seconds", 0.0) if isinstance(alignment, dict) else 0.0
    measurement = _measure_pitch(wav_path, note_on, note_off, trim_start=trim_start)
    result = {
        "schema_version": "instrument-pitch-calibration/v1",
        "basis": "acoustic_measurement",
        "status": measurement["status"],
        "source_record_id": manifest.get("source_record_id"),
        "native_manifest_path": str(manifest_path),
        "native_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "native_wav_path": str(wav_path),
        "native_wav_sha256": wav_hash,
        "engine": manifest.get("engine"),
        "native_raw_pitch": {"key": raw_field, "value": raw_value},
        "sounding_midi_note": measurement["sounding_midi_note"],
        "f0_hz": measurement["f0_hz"],
        "confidence": measurement["confidence"],
        "evidence": measurement["evidence"],
        "method": "mean-removed normalized autocorrelation across fixed sustain windows; 55-1500 Hz search; no raw-register-to-MIDI mapping",
        "window_estimates": measurement["window_estimates"],
        "median_absolute_deviation_cents": measurement["median_absolute_deviation_cents"],
    }
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("x", encoding="utf-8", newline="") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write("\n")
    return result


def calibrate_wav_pitch(wav_path: str | Path, *, source_record_id: str,
                        note_on_seconds: float, note_off_seconds: float,
                        end_seconds: float, output_path: str | Path | None = None) -> dict[str, Any]:
    """Measure and hash-bind an ordinary isolated WAV query, without renderer metadata."""
    path = Path(wav_path).resolve()
    if not path.is_file():
        raise ValueError("query WAV file is missing")
    if not isinstance(source_record_id, str) or not source_record_id.strip():
        raise ValueError("source_record_id must be a nonempty string")
    for name, value in (("note_on_seconds", note_on_seconds), ("note_off_seconds", note_off_seconds), ("end_seconds", end_seconds)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if note_on_seconds < 0 or note_off_seconds <= note_on_seconds or end_seconds <= note_off_seconds:
        raise ValueError("event boundaries must satisfy 0 <= note_on < note_off < end")
    with wave.open(str(path), "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()
        rate = wav.getframerate()
    if end_seconds > duration + 1 / rate:
        raise ValueError("end_seconds exceeds WAV duration")
    measurement = _measure_pitch(path, note_on_seconds, note_off_seconds)
    result = {
        "schema_version": "instrument-query-pitch-calibration/v1",
        "basis": "acoustic_measurement",
        "status": measurement["status"],
        "source_record_id": source_record_id,
        "wav_path": str(path),
        "wav_sha256": _sha256(path),
        "event_boundaries_seconds": {"note_on": note_on_seconds, "note_off": note_off_seconds, "end": end_seconds},
        "sounding_midi_note": measurement["sounding_midi_note"],
        "f0_hz": measurement["f0_hz"],
        "confidence": measurement["confidence"],
        "evidence": measurement["evidence"],
        "method": "mean-removed normalized autocorrelation across fixed sustain windows; 55-1500 Hz search; MIDI derived only from measured acoustic F0",
        "window_estimates": measurement["window_estimates"],
        "median_absolute_deviation_cents": measurement["median_absolute_deviation_cents"],
    }
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("x", encoding="utf-8", newline="") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write("\n")
    return result

def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Measure native render pitch from a hash-verified manifest/WAV pair.")
    parser.add_argument("native_manifest", type=Path)
    parser.add_argument("--output", type=Path, help="Write calibration JSON; existing paths are never overwritten.")
    args = parser.parse_args(argv)
    result = calibrate_manifest_pitch(args.native_manifest, output_path=args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())