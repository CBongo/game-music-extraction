"""Deterministic, label-free phase-aware audio features for PCM16 WAVs."""
from __future__ import annotations

import array
import math
import statistics
import sys
import wave
from pathlib import Path
from typing import Any

VERSION = "instrument-audio-features/v1"


def _fft(values: list[complex]) -> list[complex]:
    """In-place iterative radix-2 Cooley-Tukey FFT; input length must be a power of two."""
    n = len(values)
    if n < 2 or n & (n - 1):
        raise ValueError("FFT size must be a power of two")
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            values[i], values[j] = values[j], values[i]
    length = 2
    while length <= n:
        angle = -2.0 * math.pi / length
        root = complex(math.cos(angle), math.sin(angle))
        half = length >> 1
        for start in range(0, n, length):
            factor = 1 + 0j
            for offset in range(half):
                even = values[start + offset]
                odd = factor * values[start + offset + half]
                values[start + offset] = even + odd
                values[start + offset + half] = even - odd
                factor *= root
        length <<= 1
    return values


def _read_wav(path: Path) -> tuple[int, list[list[float]]]:
    with wave.open(str(path), "rb") as wav:
        channels, width = wav.getnchannels(), wav.getsampwidth()
        rate, frames = wav.getframerate(), wav.getnframes()
        if channels not in (1, 2) or width != 2 or rate < 8000 or frames < 1:
            raise ValueError("features require a nonempty mono/stereo PCM16 WAV")
        raw = wav.readframes(frames)
    if len(raw) != frames * channels * width:
        raise ValueError("truncated PCM16 WAV")
    values = array.array("h")
    values.frombytes(raw)
    if sys.byteorder != "little":
        values.byteswap()
    planes = [[v / 32768.0 for v in values[channel::channels]] for channel in range(channels)]
    return rate, planes


def _downsample(planes: list[list[float]], rate: int, target_rate: int) -> tuple[list[list[float]], float]:
    factor = max(1, round(rate / target_rate))
    reduced = []
    for plane in planes:
        out = []
        for start in range(0, len(plane), factor):
            block = plane[start:start + factor]
            out.append(sum(block) / len(block))
        reduced.append(out)
    return reduced, rate / factor


def _phase_features(planes: list[list[float]], rate: float, start: int, end: int,
                    *, fft_size: int, hop_size: int, band_count: int,
                    envelope_bins: int) -> dict[str, Any]:
    length = end - start
    channels = len(planes)
    phase_samples = [plane[start:end] for plane in planes]
    power_sums = [0.0] * (fft_size // 2 + 1)
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / (fft_size - 1)) for i in range(fft_size)]
    window_energy = sum(v * v for v in window)
    window_count = 0
    frame_rms = []
    frame_zcr = []
    for offset in range(0, max(1, length), hop_size):
        block_end = min(length, offset + fft_size)
        if block_end <= offset:
            continue
        count = block_end - offset
        if count < 32:
            continue
        averaged_power = [0.0] * (fft_size // 2 + 1)
        for plane in phase_samples:
            real = [0.0] * fft_size
            chunk = plane[offset:block_end]
            for i, value in enumerate(chunk):
                real[i] = value * window[i]
            transformed = _fft([complex(value, 0.0) for value in real])
            for k in range(len(averaged_power)):
                averaged_power[k] += (transformed[k].real ** 2 + transformed[k].imag ** 2) / (window_energy * channels)
        for k, value in enumerate(averaged_power):
            power_sums[k] += value
        window_count += 1
        rms_power = sum(sum(v * v for v in plane[offset:block_end]) for plane in phase_samples) / (count * channels)
        frame_rms.append(math.sqrt(rms_power))
        crossings = []
        for plane in phase_samples:
            piece = plane[offset:block_end]
            crossings.append(sum(1 for a, b in zip(piece, piece[1:]) if (a < 0 <= b) or (a >= 0 > b)) / max(1, count - 1))
        frame_zcr.append(statistics.mean(crossings))
    if window_count:
        power_sums = [p / window_count for p in power_sums]
    nyquist = rate / 2
    max_frequency = min(10000.0, nyquist)
    low_frequency = min(40.0, max_frequency / (band_count + 1))
    edges = [low_frequency * (max_frequency / low_frequency) ** (i / band_count) for i in range(band_count + 1)]
    bands = []
    for band in range(band_count):
        low_bin = max(1, math.floor(edges[band] * fft_size / rate))
        high_bin = min(len(power_sums), max(low_bin + 1, math.ceil(edges[band + 1] * fft_size / rate)))
        bands.append(sum(power_sums[low_bin:high_bin]))
    total_band_power = sum(bands)
    silence = total_band_power < 1e-12
    if silence:
        log_bands = [0.0] * band_count
    else:
        logs = [math.log10(max(power, total_band_power * 1e-12)) for power in bands]
        mean_log = statistics.mean(logs)
        centered = [value - mean_log for value in logs]
        norm = math.sqrt(sum(v * v for v in centered))
        log_bands = [v / norm for v in centered] if norm > 0 else [0.0] * band_count

    full_total = sum(power_sums[1:])
    if full_total > 0:
        centroid = sum((k * rate / fft_size) * power for k, power in enumerate(power_sums)) / full_total
        cumulative = 0.0
        rolloff = 0.0
        for k, power in enumerate(power_sums):
            cumulative += power
            if cumulative >= full_total * 0.85:
                rolloff = k * rate / fft_size
                break
        positive = [p for p in power_sums[1:] if p > 1e-20]
        flatness = math.exp(sum(math.log(p) for p in positive) / len(positive)) / (sum(positive) / max(1, len(positive))) if positive else 0.0
    else:
        centroid = rolloff = flatness = 0.0

    envelope_values = []
    for index in range(envelope_bins):
        a = start + length * index // envelope_bins
        b = start + length * (index + 1) // envelope_bins
        if b <= a:
            envelope_values.append(0.0)
            continue
        rms = math.sqrt(sum(sum(plane[i] * plane[i] for plane in planes) for i in range(a, b)) / ((b - a) * channels))
        envelope_values.append(rms)
    envelope_max = max(envelope_values, default=0.0)
    envelope = [value / envelope_max for value in envelope_values] if envelope_max > 1e-8 else [0.0] * envelope_bins
    phase_rms = math.sqrt(sum(sum(v * v for v in plane) for plane in phase_samples) / max(1, length * channels))
    peak = max((abs(v) for plane in phase_samples for v in plane), default=0.0)
    clipped = sum(1 for plane in phase_samples for v in plane if abs(v) >= 32767 / 32768)
    return {
        "start_seconds": start / rate,
        "end_seconds": end / rate,
        "window_count": window_count,
        "silent": silence,
        "log_spectral_bands_l2": log_bands,
        "envelope_shape": envelope,
        "spectral_centroid_hz": centroid,
        "spectral_centroid_normalized": centroid / nyquist if nyquist else 0.0,
        "spectral_rolloff_hz": rolloff,
        "spectral_rolloff_normalized": rolloff / nyquist if nyquist else 0.0,
        "spectral_flatness": max(0.0, min(1.0, flatness)),
        "zero_crossing_rate": statistics.mean(frame_zcr) if frame_zcr else 0.0,
        "rms_dbfs": 20 * math.log10(max(phase_rms, 1e-12)),
        "peak_dbfs": 20 * math.log10(max(peak, 1e-12)),
        "clipped_sample_count": clipped,
    }


def extract_features(wav_path: str | Path, *, note_on_seconds: float,
                     note_off_seconds: float, end_seconds: float,
                     attack_seconds: float = 0.12, target_sample_rate: int = 22050,
                     fft_size: int = 2048, hop_size: int | None = None,
                     band_count: int = 32, envelope_bins: int = 64) -> dict[str, Any]:
    """Extract normalized phase features without using source, preset, or label metadata.

    `note_on_seconds`, `note_off_seconds`, and `end_seconds` are boundaries in
    the input WAV time base. A comparison excerpt can therefore use onset=0,
    offset=matched hold duration, and end=hold+tail duration.
    """
    path = Path(wav_path).resolve()
    for name, value in (("note_on_seconds", note_on_seconds), ("note_off_seconds", note_off_seconds),
                        ("end_seconds", end_seconds), ("attack_seconds", attack_seconds)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if note_on_seconds < 0 or note_off_seconds <= note_on_seconds or end_seconds <= note_off_seconds or attack_seconds <= 0:
        raise ValueError("event boundaries must satisfy 0 <= note_on < note_off < end and attack_seconds > 0")
    if (type(target_sample_rate) is not int or type(band_count) is not int or type(envelope_bins) is not int
            or not 8000 <= target_sample_rate <= 48000 or band_count < 4 or envelope_bins < 4):
        raise ValueError("feature dimensions/sample rate are outside supported bounds")
    if type(fft_size) is not int or fft_size < 256 or fft_size > 8192 or fft_size & (fft_size - 1):
        raise ValueError("fft_size must be a power of two from 256 through 8192")
    if hop_size is None:
        hop_size = fft_size // 2
    if type(hop_size) is not int:
        raise ValueError("hop_size must be an integer")
    if not 1 <= hop_size <= fft_size:
        raise ValueError("hop_size must be between 1 and fft_size")
    source_rate, source_planes = _read_wav(path)
    source_duration = len(source_planes[0]) / source_rate
    if end_seconds > source_duration + 1 / source_rate:
        raise ValueError("end_seconds exceeds WAV duration")
    planes, rate = _downsample(source_planes, source_rate, target_sample_rate)
    onset = max(0, round(note_on_seconds * rate))
    offset = min(len(planes[0]), round(note_off_seconds * rate))
    end = min(len(planes[0]), round(end_seconds * rate))
    attack_end = min(offset, onset + round(attack_seconds * rate))
    if offset - onset <= round(attack_seconds * rate):
        raise ValueError("note hold is too short to provide separate attack and sustain phases")
    sustain_start = attack_end
    phase_bounds = {
        "attack": (onset, max(onset + 1, attack_end)),
        "sustain": (sustain_start, max(sustain_start + 1, offset)),
        "release": (offset, max(offset + 1, end)),
    }
    phase_results = {}
    for name, (start, finish) in phase_bounds.items():
        if finish > len(planes[0]) or start >= len(planes[0]):
            raise ValueError(f"{name} phase is empty or outside the WAV")
        phase_results[name] = _phase_features(planes, rate, start, finish, fft_size=fft_size,
                                               hop_size=hop_size, band_count=band_count,
                                               envelope_bins=envelope_bins)
    event_planes = [plane[onset:end] for plane in planes]
    event_frames = max(1, end - onset)
    whole_rms = math.sqrt(sum(sum(v * v for v in plane) for plane in event_planes) / (event_frames * len(planes)))
    event_clipped = sum(1 for plane in event_planes for v in plane if abs(v) >= 32767 / 32768)
    return {
        "schema_version": VERSION,
        "wav_path": str(path),
        "sample_rate_original": source_rate,
        "sample_rate_analysis": rate,
        "channels": len(source_planes),
        "frames_original": len(source_planes[0]),
        "duration_seconds": source_duration,
        "event_boundaries_seconds": {"note_on": note_on_seconds, "note_off": note_off_seconds, "end": end_seconds},
        "analysis": {"fft_size": fft_size, "hop_size": hop_size, "band_count": band_count,
                     "envelope_bins": envelope_bins, "attack_seconds": attack_seconds,
                     "downsample_method": "block-average decimation (simple box anti-alias approximation; not a high-fidelity resampler); stereo channels analyzed as mean power"},
        "valid": True,
        "silent": whole_rms < 1e-6,
        "event_rms_dbfs": 20 * math.log10(max(whole_rms, 1e-12)),
        "event_clipped_sample_count": event_clipped,
        "phases": phase_results,
    }
