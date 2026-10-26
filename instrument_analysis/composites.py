"""Conservative baked-chord inference from hash-verified isolated WAV probes.

This module distinguishes independent tonal components from a single rich
harmonic series. Its decisions describe these captures and remain uncertain
when harmonics, missing fundamentals, or effects admit competing models.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import statistics
import wave

from .features import _fft, _read_wav, _downsample
from .schema import make_bundle

DIAGNOSTICS_VERSION = "instrument-composite-diagnostics/v1"
SUMMARY_VERSION = "instrument-tracker-sample-probes/v1"
TARGET_RATE = 22050
FFT_SIZE = 32768
PEAK_LIMIT = 72
MAX_HARMONIC = 10
CENTS_TOLERANCE = 12.0
ROOT_PENALTY = .09


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value, label, lo=None, hi=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    value = float(value)
    if lo is not None and value < lo or hi is not None and value > hi:
        raise ValueError(f"{label} is outside allowed bounds")
    return value


def _hz(note):
    return 440.0 * 2 ** ((note - 69.0) / 12.0)


def _midi(frequency):
    return 69.0 + 12.0 * math.log2(frequency / 440.0)


def _peak_spectrum(path: Path, on: float, analysis_end: float) -> tuple[dict, list[dict]]:
    source_rate, planes = _read_wav(path)
    planes, rate = _downsample(planes, source_rate, TARGET_RATE)
    begin = round((on + .14) * rate)
    finish = round(analysis_end * rate)
    if finish - begin < 4096:
        raise ValueError("isolated sustain window is shorter than 4096 analysis samples")
    segments = [plane[begin:finish] for plane in planes]
    count = len(segments[0])
    if count > FFT_SIZE:
        begin_offset = (count - FFT_SIZE) // 2
        segments = [segment[begin_offset:begin_offset + FFT_SIZE] for segment in segments]
        count = FFT_SIZE
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / (count - 1))
              for i in range(count)]
    power = [0.0] * (FFT_SIZE // 2 + 1)
    for segment in segments:
        values = [complex(v * window[i], 0) for i, v in enumerate(segment)]
        values.extend([0j] * (FFT_SIZE - count))
        transformed = _fft(values)
        for k in range(len(power)):
            power[k] += abs(transformed[k]) ** 2 / len(segments)
    low_bin = max(1, math.floor(55 * FFT_SIZE / rate))
    high_bin = min(len(power) - 2, math.ceil(5500 * FFT_SIZE / rate))
    band = power[low_bin:high_bin + 1]
    maximum = max(band, default=0.0)
    mean_power = sum(band) / max(1, len(band))
    floor = max(maximum * 1e-14, 1e-20)
    flatness = math.exp(sum(math.log(max(x, floor)) for x in band) / len(band)) / max(mean_power, floor)
    rms = math.sqrt(sum(sum(v * v for v in segment) for segment in segments) /
                    (count * len(segments)))
    peaks = []
    if maximum > 0:
        for k in range(low_bin + 1, high_bin):
            if power[k] < maximum * 1e-5 or power[k] < power[k-1] or power[k] <= power[k+1]:
                continue
            left, center, right = (math.log(max(power[j], floor)) for j in (k-1, k, k+1))
            denominator = left - 2 * center + right
            delta = .5 * (left - right) / denominator if denominator else 0.0
            delta = max(-.5, min(.5, delta))
            frequency = (k + delta) * rate / FFT_SIZE
            peaks.append({"frequency_hz": frequency, "amplitude": math.sqrt(power[k]),
                          "relative_db": 10 * math.log10(power[k] / maximum)})
    peaks.sort(key=lambda p: (-p["amplitude"], p["frequency_hz"]))
    independent = []
    for candidate in peaks:
        if any(abs(1200 * math.log2(candidate["frequency_hz"] / kept["frequency_hz"])) < 30
               for kept in independent):
            continue
        independent.append(candidate)
        if len(independent) >= PEAK_LIMIT:
            break
    peaks = sorted(independent, key=lambda p: p["frequency_hz"])
    quality = {"sample_rate_original": source_rate, "sample_rate_analysis": rate,
               "channels": len(planes), "fft_size": FFT_SIZE,
               "clipped_sample_count": sum(abs(v) >= 32767 / 32768
                                           for segment in segments for v in segment),
               "sustain_start_seconds": begin / rate,
               "sustain_end_seconds": finish / rate,
               "rms_pcm16_normalized": rms, "spectral_flatness": flatness,
               "peak_count": len(peaks)}
    return quality, peaks


def _root_candidates(peaks: list[dict], trigger: int, rate: float) -> list[dict]:
    if not peaks:
        return []
    strongest = max(p["amplitude"] for p in peaks)
    candidates = []
    for offset in range(-24, 25):
        if not 0 <= trigger + offset <= 127:
            continue
        nominal = _hz(trigger + offset)
        cent_samples = []
        for peak in peaks:
            if peak["amplitude"] < strongest * .035:
                continue
            for harmonic in range(1, MAX_HARMONIC + 1):
                delta = 1200 * math.log2(peak["frequency_hz"] / (nominal * harmonic))
                if abs(delta) <= 35:
                    cent_samples.append((delta, peak["amplitude"] / harmonic))
        if not cent_samples:
            continue
        cent_samples.sort()
        total = sum(w for _, w in cent_samples)
        cumulative = 0.0
        cents = 0.0
        for value, weight in cent_samples:
            cumulative += weight
            if cumulative >= total / 2:
                cents = value
                break
        frequency = nominal * 2 ** (cents / 1200)
        supported = 0
        matched = 0.0
        for harmonic in range(1, MAX_HARMONIC + 1):
            target = frequency * harmonic
            if target > 5500:
                break
            tolerance_hz = max(rate / FFT_SIZE * 2, target * (2 ** (CENTS_TOLERANCE / 1200) - 1))
            possible = [p for p in peaks if abs(p["frequency_hz"] - target) <= tolerance_hz]
            if possible:
                selected = max(possible, key=lambda p: p["amplitude"])
                if selected["amplitude"] >= strongest * .045:
                    supported += 1
                    matched += selected["amplitude"] ** 2
        if supported >= 2 or (supported == 1 and matched > strongest ** 2 * .3):
            candidates.append({"offset": offset, "root_midi": trigger + offset + cents / 100,
                               "root_hz": frequency, "residual_cents": cents,
                               "harmonic_support": supported, "matched_power": matched})
    return candidates


def _columns(peaks: list[dict], roots: list[dict], rate: float) -> list[tuple[int, list[float]]]:
    result = []
    for index, root in enumerate(roots):
        for harmonic in range(1, MAX_HARMONIC + 1):
            target = root["root_hz"] * harmonic
            if target > 5500:
                break
            tolerance_hz = max(rate / FFT_SIZE * 1.5, target * (2 ** (8 / 1200) - 1))
            # A free nonnegative amplitude must not compensate for a mistuned
            # harmonic. Peaks outside the frequency uncertainty are excluded.
            col = [1.0 if abs(p["frequency_hz"] - target) <= tolerance_hz else 0.0
                   for p in peaks]
            if max(col, default=0) > .05:
                result.append((index, col))
    return result


def _fit(peaks: list[dict], roots: list[dict], rate: float) -> dict:
    columns = _columns(peaks, roots, rate)
    y = [p["amplitude"] for p in peaks]
    norm = math.sqrt(sum(v * v for v in y))
    if norm == 0 or not columns:
        return {"cost": 1.0, "explained_fraction": 0.0,
                "root_levels": [0.0] * len(roots)}
    y = [v / norm for v in y]
    residual = y[:]
    coefficients = [0.0] * len(columns)
    for _ in range(18):
        change = 0.0
        for j, (_, col) in enumerate(columns):
            denom = sum(v * v for v in col)
            if denom < 1e-12:
                continue
            new = max(0.0, coefficients[j] + sum(v * r for v, r in zip(col, residual)) / denom)
            delta = new - coefficients[j]
            if delta:
                coefficients[j] = new
                residual = [r - delta * v for r, v in zip(residual, col)]
                change = max(change, abs(delta))
        if change < 1e-6:
            break
    cost = min(1.0, max(0.0, sum(v * v for v in residual)))
    levels = [0.0] * len(roots)
    for (index, col), coefficient in zip(columns, coefficients):
        levels[index] += coefficient * coefficient * sum(v * v for v in col)
    return {"cost": cost, "explained_fraction": 1 - cost,
            "root_levels": [math.sqrt(v) for v in levels]}


def _models(peaks: list[dict], candidates: list[dict], rate: float) -> tuple[dict, dict]:
    if not candidates:
        empty = {"roots": [], "cost": 1.0, "explained_fraction": 0.0,
                 "root_levels": []}
        return empty, empty
    singles = [{**_fit(peaks, [c], rate), "roots": [c]} for c in candidates]
    minimum_cost = min(x["cost"] for x in singles)
    near_optimal = [x for x in singles if x["cost"] <= minimum_cost + .003]
    strongest = max(p["amplitude"] for p in peaks)
    def fundamental_strength(root):
        target = root["root_hz"]
        tolerance = max(rate / FFT_SIZE * 1.5, target * (2 ** (8 / 1200) - 1))
        return max((p["amplitude"] for p in peaks if abs(p["frequency_hz"] - target) <= tolerance),
                   default=0.0)
    near_optimal.sort(key=lambda x: (-fundamental_strength(x["roots"][0]),
                                     -x["roots"][0]["harmonic_support"],
                                     abs(x["roots"][0]["offset"]), x["cost"]))
    single = near_optimal[0]
    # Components may only be emitted where a strong fundamental is audible.
    # Hidden subharmonics remain competing single-source explanations.
    fundamentals = [c for c in candidates if fundamental_strength(c) >= strongest * .12]
    fundamentals.sort(key=lambda c: (-fundamental_strength(c), c["offset"]))
    fundamentals = fundamentals[:8]
    from itertools import combinations
    alternatives = []
    for count in (2, 3):
        for roots in combinations(fundamentals, count):
            fit = _fit(peaks, list(roots), rate)
            alternatives.append({**fit, "roots": list(roots)})
    if not alternatives:
        return single, single
    alternatives.sort(key=lambda x: (x["cost"] + ROOT_PENALTY * (len(x["roots"]) - 1),
                                     [r["offset"] for r in x["roots"]]))
    best = alternatives[0]
    if best["cost"] + ROOT_PENALTY * (len(best["roots"]) - 1) < single["cost"] - .01:
        return single, best
    return single, single


def _competing_three_note_fit(peaks: list[dict], candidates: list[dict],
                              selected: dict, rate: float) -> dict | None:
    """Find an observed third fundamental that a dyad may absorb as a harmonic."""
    if len(selected["roots"]) != 2 or not peaks:
        return None
    strongest = max(p["amplitude"] for p in peaks)
    selected_offsets = {r["offset"] for r in selected["roots"]}
    alternatives = []
    for candidate in candidates:
        if candidate["offset"] in selected_offsets:
            continue
        target = candidate["root_hz"]
        tolerance = max(rate / FFT_SIZE * 1.5,
                        target * (2 ** (8 / 1200) - 1))
        fundamental = max((p["amplitude"] for p in peaks
                           if abs(p["frequency_hz"] - target) <= tolerance), default=0.0)
        if fundamental < strongest * .12:
            continue
        roots = selected["roots"] + [candidate]
        offsets = sorted(r["offset"] for r in roots)
        quality, _ = _quality(offsets)
        if quality not in ("major", "minor", "sus2", "sus4"):
            continue
        fit = _fit(peaks, roots, rate)
        # A dyad may explain an observed third fundamental as a harmonic.
        if fit["cost"] <= selected["cost"] + .005:
            alternatives.append({"offsets": offsets, "quality": quality,
                                 "dyad_cost": selected["cost"],
                                 "three_note_cost": fit["cost"],
                                 "third_fundamental_relative_db": 20 * math.log10(
                                     fundamental / strongest)})
    return min(alternatives, key=lambda a: (a["three_note_cost"], a["offsets"])) if alternatives else None


def _missing_fundamental(root: dict, fit: dict, peaks: list[dict], rate: float) -> dict | None:
    strongest = max((p["amplitude"] for p in peaks), default=0)
    observed = []
    residuals = []
    for h in range(1, MAX_HARMONIC + 1):
        target = root["root_hz"] * h
        tolerance = max(rate / FFT_SIZE * 2.5, target * (2 ** (25 / 1200) - 1))
        nearby = [p for p in peaks if abs(p["frequency_hz"] - target) <= tolerance and
                  p["amplitude"] > strongest * .04]
        if nearby:
            selected = max(nearby, key=lambda p: p["amplitude"])
            cents = 1200 * math.log2(selected["frequency_hz"] / target)
            observed.append(h)
            residuals.append({"harmonic": h, "peak_frequency_hz": selected["frequency_hz"],
                              "residual_cents": cents, "relative_db": selected["relative_db"]})
    if not observed or observed[0] == 1:
        return None
    weights = [10 ** (item["relative_db"] / 10) for item in residuals]
    weighted_rms = math.sqrt(sum(w * item["residual_cents"] ** 2
                                 for w, item in zip(weights, residuals)) / sum(weights))
    return {"root_midi": root["root_midi"], "cost": fit["cost"],
            "lowest_observed_harmonic": observed[0],
            "observed_harmonics": observed,
            "harmonic_peak_residuals": residuals,
            "weighted_rms_residual_cents": weighted_rms,
            "note": "A single tone with an absent fundamental can explain some observed peaks."}


def _best_missing_fundamental(peaks: list[dict], candidates: list[dict], rate: float) -> dict | None:
    alternatives = []
    for root in candidates:
        fit = _fit(peaks, [root], rate)
        alt = _missing_fundamental(root, fit, peaks, rate)
        if alt is not None:
            alternatives.append(alt)
    return min(alternatives, key=lambda a: (a["cost"], a["weighted_rms_residual_cents"])) if alternatives else None


def _quality_alternatives(offsets: list[int]) -> list[dict]:
    """Enumerate chord names without assuming an unobserved musical root."""
    if len(offsets) != 3:
        return []
    pcs = {o % 12 for o in offsets}
    if len(pcs) != 3:
        return []
    bass = min(offsets) % 12
    patterns = {frozenset((0, 4, 7)): "major", frozenset((0, 3, 7)): "minor",
                frozenset((0, 2, 7)): "sus2", frozenset((0, 5, 7)): "sus4"}
    alternatives = []
    for root in sorted(pcs):
        intervals = frozenset((pc - root) % 12 for pc in pcs)
        name = patterns.get(intervals)
        if name is None:
            continue
        inversion = ("root position" if bass == root else
                     "first inversion" if (bass - root) % 12 in (2, 3, 4, 5) else
                     "second inversion")
        alternatives.append({"root_pc_relative_trigger": root, "quality": name,
                             "inversion_or_voicing": inversion})
    # Prefer the trigger's pitch class as the label when it is a possible
    # root. This is a convention, not evidence that the sequencer meant it.
    alternatives.sort(key=lambda a: (a["root_pc_relative_trigger"] != 0,
                                     a["root_pc_relative_trigger"], a["quality"]))
    return alternatives


def _quality(offsets: list[int]) -> tuple[str, str]:
    if not offsets:
        return "unknown", "no stable tonal components"
    if len(offsets) == 1:
        return "unknown", "single tonal component"
    if len(offsets) == 2:
        return "other", "dyad; harmonic-series ambiguity assessed separately"
    alternatives = _quality_alternatives(offsets)
    if alternatives:
        return alternatives[0]["quality"], alternatives[0]["inversion_or_voicing"]
    return "other", "unclassified multi-tone voicing"


def _harmonically_ambiguous(offsets: list[int]) -> bool:
    if len(offsets) < 2:
        return False
    pcs = {(o - min(offsets)) % 12 for o in offsets}
    if pcs <= {0, 7}:
        return True
    if len(offsets) == 2 and abs(offsets[1] - offsets[0]) in (7, 12, 19, 24):
        return True
    return False


def _probe(path: Path, expected_hash: str, trigger: int, on: float, off: float,
           end: float, analysis_end: float, conditions: dict) -> dict:
    if not path.is_file():
        raise ValueError(f"probe WAV missing: {path}")
    if _sha(path) != expected_hash:
        raise ValueError(f"probe WAV SHA256 mismatch: {path}")
    with wave.open(str(path), "rb") as stream:
        sample_rate, channels, frames, width = (
            stream.getframerate(), stream.getnchannels(), stream.getnframes(), stream.getsampwidth())
        if channels not in (1, 2) or width != 2 or stream.getcomptype() != "NONE":
            raise ValueError("probe WAV must be mono/stereo PCM16")
    if end > frames / sample_rate + 1 / sample_rate:
        raise ValueError("probe event end is beyond WAV")
    try:
        quality, peaks = _peak_spectrum(path, on, analysis_end)
        quality["analysis_issue"] = None
    except ValueError as exc:
        if "sustain window is shorter" not in str(exc):
            raise
        quality = {"sample_rate_original": sample_rate,
                   "sample_rate_analysis": sample_rate, "channels": channels,
                   "fft_size": FFT_SIZE, "sustain_start_seconds": on + .14,
                   "sustain_end_seconds": analysis_end, "rms_pcm16_normalized": 0.0,
                   "spectral_flatness": 1.0, "clipped_sample_count": 0,
                   "peak_count": 0, "analysis_issue": str(exc)}
        peaks = []
    candidates = _root_candidates(peaks, trigger, quality["sample_rate_analysis"])
    single, multi = _models(peaks, candidates, quality["sample_rate_analysis"])
    selected_missing = (_missing_fundamental(single["roots"][0], single, peaks,
                                            quality["sample_rate_analysis"])
                        if single["roots"] else None)
    return {
        "wav": {"path": str(path), "sha256": expected_hash, "sample_rate": sample_rate,
                "channels": channels, "frames": frames},
        "trigger_midi_note": trigger,
        "event_boundaries_seconds": {"note_on": on, "note_off": off, "end": end},
        "analysis_end_seconds": analysis_end,
        "render_conditions": deepcopy(conditions),
        "quality": quality,
        "peaks": [{"frequency_hz": p["frequency_hz"], "relative_db": p["relative_db"]}
                  for p in peaks],
        "single": {"root_midi": single["roots"][0]["root_midi"] if single["roots"] else None,
                   "root_offset": single["roots"][0]["offset"] if single["roots"] else None,
                   "cost": single["cost"], "explained_fraction": single["explained_fraction"]},
        "multi": {"root_midi_notes": [r["root_midi"] for r in multi["roots"]],
                  "offsets": [r["offset"] for r in multi["roots"]],
                  "cost": multi["cost"], "explained_fraction": multi["explained_fraction"],
                  "root_levels": multi["root_levels"]},
        "single_missing_fundamental_alternative": selected_missing or _best_missing_fundamental(
            peaks, candidates, quality["sample_rate_analysis"]),
        "competing_three_note_fit": _competing_three_note_fit(
            peaks, candidates, multi, quality["sample_rate_analysis"]),
        "_selected_missing": selected_missing,
        "_raw_single": single, "_raw_multi": multi,
    }


def _sample_hash_in_probe_mod(path: Path, sample_number: int, expected_length: int) -> str:
    data = path.read_bytes()
    if len(data) < 1084 or data[1080:1084] != b"M.K." or not 1 <= sample_number <= 31:
        raise ValueError("probe MOD is not a strict four-channel M.K. module")
    song_length = data[950]
    if not 1 <= song_length <= 128:
        raise ValueError("probe MOD has invalid song length")
    patterns = max(data[952:952 + song_length]) + 1
    base = 1084 + patterns * 1024
    sizes = [2 * int.from_bytes(data[20 + i * 30 + 22:20 + i * 30 + 24], "big")
             for i in range(31)]
    start = base + sum(sizes[:sample_number - 1])
    length = sizes[sample_number - 1]
    if length != expected_length or start + length > len(data):
        raise ValueError("probe MOD selected sample bounds differ from summary")
    return hashlib.sha256(data[start:start + length]).hexdigest()


def _load_input(probes_or_summary, source_record_id: str | None):
    if isinstance(probes_or_summary, (str, Path)):
        summary_path = Path(probes_or_summary).expanduser().resolve(strict=True)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("schema_version") != SUMMARY_VERSION:
            raise ValueError("unsupported tracker probe summary")
        rid = summary.get("source_record_id")
        if source_record_id is not None and source_record_id != rid:
            raise ValueError("source_record_id differs from probe summary")
        source = summary.get("source", {})
        module_hash = source.get("module_sha256")
        if not isinstance(module_hash, str) or len(module_hash) != 64:
            raise ValueError("probe summary lacks source module SHA256")
        # Neutral probe manifests intentionally omit descriptive source paths.
        # Their preserved sample bytes are bound through every probe MOD hash.
        renderer = summary.get("renderer", {})
        if _sha(Path(renderer.get("path", ""))) != renderer.get("sha256"):
            raise ValueError("renderer executable SHA256 differs from probe summary")
        sample_hash = source.get("sample_bytes_sha256")
        if not isinstance(sample_hash, str) or len(sample_hash) != 64:
            raise ValueError("probe summary lacks source sample SHA256")
        items = summary.get("probes")
        if not isinstance(items, list) or not items:
            raise ValueError("probe summary has no probes")
        for item in items:
            module_name = item.get("probe_module_file")
            wav_name = item.get("wav_file")
            if (not isinstance(module_name, str) or Path(module_name).name != module_name or
                    not isinstance(wav_name, str) or Path(wav_name).name != wav_name):
                raise ValueError("probe MOD/WAV names must be local basenames")
            path = (summary_path.parent / module_name).resolve(strict=True)
            wav = (summary_path.parent / wav_name).resolve(strict=True)
            if item.get("probe_module_path") is not None and Path(item["probe_module_path"]).resolve() != path:
                raise ValueError("probe MOD path disagrees with local basename")
            if item.get("wav_path") is not None and Path(item["wav_path"]).resolve() != wav:
                raise ValueError("probe WAV path disagrees with local basename")
            if _sha(path) != item.get("probe_module_sha256"):
                raise ValueError("probe MOD SHA256 mismatch")
            if (_sample_hash_in_probe_mod(path, source.get("sample_number"),
                                          source.get("sample_length_bytes")) != sample_hash or
                    item.get("render_conditions", {}).get("source_sample_sha256") != sample_hash):
                raise ValueError("probe does not preserve the selected source sample bytes")
            item["probe_module_path"] = str(path)
            item["wav_path"] = str(wav)
        return rid, items, {"path": str(summary_path), "sha256": _sha(summary_path)}, {
            **source, "source_identity_kind": "sample_bytes",
            "source_identity_sha256": sample_hash}
    if not isinstance(probes_or_summary, (list, tuple)) or not probes_or_summary:
        raise ValueError("probes must be a nonempty sequence or a verified summary path")
    if not isinstance(source_record_id, str) or not source_record_id:
        raise ValueError("direct probes require source_record_id")
    items = list(probes_or_summary)
    if not all(isinstance(p, dict) for p in items):
        raise ValueError("direct probes must be objects")
    identities = {(p.get("render_conditions", {}).get("source_identity_kind"),
                   p.get("render_conditions", {}).get("source_identity_sha256"))
                  for p in items}
    if len(identities) != 1:
        raise ValueError("direct probes must share source identity kind and SHA256")
    kind, identity_hash = next(iter(identities))
    if (kind not in ("sample_bytes", "controlled_recipe") or
            not isinstance(identity_hash, str) or len(identity_hash) != 64 or
            any(ch not in "0123456789abcdef" for ch in identity_hash.lower())):
        raise ValueError("direct probes need a valid source identity kind and SHA256")
    if kind == "sample_bytes" and any(
            p["render_conditions"].get("source_sample_sha256") != identity_hash for p in items):
        raise ValueError("sample_bytes identity must match source_sample_sha256")
    return source_record_id, items, None, {
        "sample_bytes_sha256": identity_hash if kind == "sample_bytes" else None,
        "source_identity_kind": kind, "source_identity_sha256": identity_hash}


def _component_consensus(results: list[dict]) -> tuple[list[dict], float, list[str]]:
    valid = [p for p in results if p["quality"]["rms_pcm16_normalized"] > 1e-5 and
             p["quality"]["spectral_flatness"] < .25 and
             p["quality"]["clipped_sample_count"] == 0 and p["multi"]["offsets"]]
    problems = []
    if len(valid) < 2:
        problems.append("fewer than two tonal probe captures")
        return [], 0.0, problems
    if len({p["trigger_midi_note"] for p in valid}) < 2:
        problems.append("trigger pitches are not distinct")
    reference = sorted(valid[0]["multi"]["offsets"])
    if any(sorted(p["multi"]["offsets"]) != reference for p in valid[1:]):
        problems.append("component intervals disagree across trigger pitches")
        return [], 0.0, problems
    components = []
    max_deviation = 0.0
    for offset in reference:
        roots = []
        levels = []
        for probe in valid:
            model = probe["_raw_multi"]
            idx = next(i for i, r in enumerate(model["roots"]) if r["offset"] == offset)
            roots.append(model["roots"][idx])
            levels.append(model["root_levels"][idx])
        residuals = [r["residual_cents"] for r in roots]
        median_residual = statistics.median(residuals)
        deviation = max(abs(c - median_residual) for c in residuals)
        max_deviation = max(max_deviation, deviation)
        positive_levels = [max(1e-8, v) for v in levels]
        level = statistics.median(positive_levels)
        components.append({"semitone_offset": offset,
                           "residual_cents": median_residual,
                           "_level": level,
                           "confidence": max(0.0, min(1.0, 1 - deviation / 50))})
    peak = max(c["_level"] for c in components)
    for c in components:
        c["relative_level_db"] = 20 * math.log10(c.pop("_level") / peak)
    if max_deviation > 25:
        problems.append("component tuning differs across trigger pitches")
    return components, max_deviation, problems


def analyze_baked_chord(
    probes_or_summary, *, source_record_id: str | None = None,
    source_context: dict | None = None, output_dir: str | Path | None = None,
) -> tuple[dict, dict]:
    """Infer source-relative tonal components; abstain on non-identifiable audio."""
    rid, items, summary_ref, source_meta = _load_input(probes_or_summary, source_record_id)
    if not isinstance(rid, str) or not rid:
        raise ValueError("probe summary source_record_id is missing")
    observed = []
    trigger_notes = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("probe entry must be an object")
        path = Path(item.get("wav_path", "")).expanduser().resolve(strict=True)
        expected_hash = item.get("wav_sha256")
        trigger = item.get("trigger_midi_note")
        if type(trigger) is not int or not 0 <= trigger <= 127:
            raise ValueError("probe trigger_midi_note must be an integer 0..127")
        trigger_notes.append(trigger)
        on = _number(item.get("note_on_seconds"), "note_on_seconds", 0, 600)
        off = _number(item.get("note_off_seconds"), "note_off_seconds", on + .1, 600)
        end = _number(item.get("end_seconds"), "end_seconds", off + .001, 600)
        analysis_end = _number(item.get("analysis_end_seconds", off - .04),
                               "analysis_end_seconds", on + .01, off)
        conditions = item.get("render_conditions")
        if not isinstance(conditions, dict) or not conditions:
            raise ValueError("probe render_conditions are required")
        observed.append(_probe(path, expected_hash, trigger, on, off, end,
                               analysis_end, conditions))
    if len(set(trigger_notes)) < 2:
        trigger_warning = "only one distinct verified trigger pitch"
    else:
        trigger_warning = None
    components, max_deviation, problems = _component_consensus(observed)
    if trigger_warning:
        problems.append(trigger_warning)
    tonal = [p for p in observed if p["quality"]["rms_pcm16_normalized"] > 1e-5 and
             p["quality"]["spectral_flatness"] < .25 and
             p["quality"]["clipped_sample_count"] == 0 and p["multi"]["offsets"]]
    single_cost = statistics.mean(p["single"]["cost"] for p in tonal) if tonal else 1.0
    multi_cost = statistics.mean(p["multi"]["cost"] for p in tonal) if tonal else 1.0
    improvement = max(0.0, (single_cost - multi_cost) / max(single_cost, 1e-9))
    if len(tonal) != len(observed):
        problems.append("one or more probes are silent, noisy, short, or inharmonic")
    offsets = [c["semitone_offset"] for c in components]
    if _harmonically_ambiguous(offsets):
        problems.append("power/octave components are also explainable by one harmonic series")
    if len(offsets) > 1 and improvement < .18:
        problems.append("multi-note fit does not sufficiently improve on one rich tone")
    alternatives = [p["single_missing_fundamental_alternative"] for p in observed
                    if p["single_missing_fundamental_alternative"] is not None]
    if len(offsets) > 1 and alternatives and len(alternatives) == len(observed) and all(
            alt["cost"] - probe["multi"]["cost"] <= .045
            for alt, probe in zip(alternatives, observed)):
        problems.append("missing-fundamental single-tone alternative fits within residual tolerance")
    three_note_alternatives = [p["competing_three_note_fit"] for p in observed]
    competing_three_note = None
    if (len(offsets) == 2 and all(alt is not None for alt in three_note_alternatives)
            and len({tuple(alt["offsets"]) for alt in three_note_alternatives}) == 1):
        competing_three_note = {
            "offsets": three_note_alternatives[0]["offsets"],
            "quality": three_note_alternatives[0]["quality"],
            "per_probe": three_note_alternatives,
        }
        problems.append("observed third root yields a comparably fitting common triad")
    if len(offsets) == 1 and all(p["_selected_missing"] is not None and
                                 len(p["_selected_missing"]["observed_harmonics"]) >= 2
                                 for p in observed):
        problems.append("inferred single root has no observed fundamental; separate notes remain possible")
    if multi_cost > .32:
        problems.append("tonal harmonic model leaves too much residual energy")
    if len(offsets) > 3:
        problems.append("more than three unresolved tonal components")
    quality, voicing = _quality(offsets)
    quality_alternatives = _quality_alternatives(offsets)
    if len(offsets) >= 3 and quality not in ("major", "minor", "sus2", "sus4"):
        problems.append("multi-tone intervals do not form a supported tonal chord quality")
    supported = not problems and len(components) >= 1
    tentative = not supported and components and all("only one" in p for p in problems)
    status = "supported" if supported else "tentative" if tentative else "abstain"
    reason = None if supported else "; ".join(problems) or "insufficient identifiable evidence"
    confidence = max(0.0, min(1.0, (1 - multi_cost) *
                     (improvement if len(offsets) > 1 else 1) *
                     (1 - max_deviation / 100))) if components else 0.0
    compact = []
    for probe in observed:
        compact.append({key: value for key, value in probe.items() if not key.startswith("_")})
    probe_identity = [{"wav_sha256": p["wav"]["sha256"], "trigger_midi_note": p["trigger_midi_note"],
                       "event_boundaries_seconds": p["event_boundaries_seconds"]}
                      for p in observed]
    diagnostics = {
        "schema_version": DIAGNOSTICS_VERSION,
        "source_record_id": rid,
        "input": {"probe_summary": summary_ref,
                  "probes_sha256": hashlib.sha256(json.dumps(probe_identity, sort_keys=True).encode()).hexdigest(),
                  "source_sample_sha256": source_meta["sample_bytes_sha256"],
                  "source_identity_kind": source_meta["source_identity_kind"],
                  "source_identity_sha256": source_meta["source_identity_sha256"]},
        "analysis_settings": {"fft_size": FFT_SIZE, "target_sample_rate": TARGET_RATE,
                              "peak_limit": PEAK_LIMIT, "cents_tolerance": CENTS_TOLERANCE,
                              "model_penalty": ROOT_PENALTY},
        "status": status, "reason": reason,
        "probes": compact,
        "model": {"single_cost": single_cost, "multi_cost": multi_cost,
                  "relative_improvement": improvement,
                  "cross_trigger_max_deviation_cents": max_deviation,
                  "ambiguity": problems,
                  "competing_three_note_fit": competing_three_note,
                  "single_missing_fundamental_alternative": next(
                      (p["single_missing_fundamental_alternative"] for p in observed
                       if p["single_missing_fundamental_alternative"] is not None), None)},
        "components": components, "confidence_heuristic": confidence,
        "quality": quality, "inversion_or_voicing": voicing,
        "quality_alternatives": quality_alternatives,
    }
    if source_context is not None:
        if not isinstance(source_context, dict) or set(source_context) - {"source", "identity", "observation"}:
            raise ValueError("source_context may contain only source, identity, observation")
        source = deepcopy(source_context.get("source"))
        identity = deepcopy(source_context.get("identity"))
        observation = deepcopy(source_context.get("observation", {}))
        if not isinstance(source, dict) or not isinstance(identity, dict) or not isinstance(observation, dict):
            raise ValueError("source_context source/identity/observation must be objects")
        observation.pop("existing_mapping", None)
    else:
        if summary_ref is not None:
            source = {"platform": "tracker", "game_or_module": "isolated sample",
                      "driver": "verified isolated tracker probe"}
        elif source_meta['source_identity_kind'] == 'controlled_recipe':
            source = {"platform": "synthetic audio", "game_or_module": "controlled source",
                      "driver": "authored oscillator recipe"}
        else:
            source = {"platform": "unknown", "game_or_module": "isolated sample",
                      "driver": "caller-supplied PCM probes"}
        identity = {"source_instrument_id": source_meta.get("sample_number", rid)}
        observation = {}
    record = {"record_id": rid, "source": source, "identity": identity,
              "observation": observation, "analysis": {"notes": [
                  "Spectral evidence describes these isolated renders, not every use of the source sample."]}}
    if supported:
        if len(quality_alternatives) > 1:
            record["analysis"]["notes"].append(
                "Multiple chord roots name this pitch set; the trigger-centered quality is a labeling convention.")
        if len(components) == 1:
            c = components[0]
            record["observation"].setdefault("pitch", {})["observed_pitch_offset"] = {
                "semitones": c["semitone_offset"], "residual_cents": c["residual_cents"],
                "basis": "measured", "confidence": c["confidence"],
                "provenance": {"method": "cross-trigger isolated harmonic analysis",
                               "parameters": {"probe_summary_sha256": summary_ref["sha256"] if summary_ref else None}}}
        else:
            record["analysis"]["routing"] = [{
                "rank": 1, "route": {"kind": "composite",
                    "description": "Isolated source contains multiple tonal components"},
                "score": confidence, "basis": "measured",
                "provenance": {"method": "cross-trigger harmonic-template nonnegative fit",
                               "parameters": {"probe_summary_sha256": summary_ref["sha256"] if summary_ref else None}},
                "evidence": [f"multi-note residual {multi_cost:.5f} vs single-tone {single_cost:.5f}",
                             f"cross-trigger deviation {max_deviation:.2f} cents"]}]
        record["analysis"]["composite"] = {
            "is_harmonic_composite": len(components) > 1, "score": confidence,
            "basis": "measured", "provenance": {
                "method": "cross-trigger harmonic-template nonnegative fit",
                "parameters": {"probe_summary_sha256": summary_ref["sha256"] if summary_ref else None}},
            "trigger_reference": {"number": min(trigger_notes)},
            "components": components,
            "quality": quality, "inversion_or_voicing": voicing,
        }
    else:
        record["analysis"]["notes"].append(reason)
        record["analysis"]["routing"] = [{
            "rank": 1, "route": {"kind": "abstain", "reason": reason},
            "score": 0.0, "basis": "inferred",
            "provenance": {"method": "cross-trigger harmonic-model identifiability gate",
                           "parameters": {"probe_summary_sha256": summary_ref["sha256"] if summary_ref else None}},
        }]
    bundle = make_bundle([record], "instrument_analysis.composites")
    if output_dir is not None:
        out = Path(output_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        for name in ("analysis.json", "diagnostics.json"):
            if (out / name).exists():
                raise FileExistsError(f"composite output already exists: {out / name}")
        for name, obj in (("analysis.json", bundle), ("diagnostics.json", diagnostics)):
            with (out / name).open("x", encoding="utf-8") as stream:
                json.dump(obj, stream, indent=2, sort_keys=True)
                stream.write("\n")
    return bundle, diagnostics


def expand_composite_events(events: list[dict], analysis: dict, diagnostics: dict) -> list[dict]:
    """Expand accepted component notes, preserving every event's bend and timing.

    Expansion is all-or-nothing. Residual cents and relative level are explicit
    attributes and never silently folded into MIDI note numbers or velocity.
    """
    if diagnostics.get("status") != "supported":
        raise ValueError("only supported composite analyses may be expanded")
    records = analysis.get("records", [])
    if len(records) != 1:
        raise ValueError("analysis must contain exactly one record")
    composite = records[0].get("analysis", {}).get("composite")
    if not isinstance(composite, dict) or not composite.get("components"):
        raise ValueError("analysis has no accepted components")
    if (diagnostics.get("source_record_id") != records[0].get("record_id") or
            diagnostics.get("components") != composite["components"]):
        raise ValueError("diagnostics and accepted components disagree")
    components = composite["components"]
    output = []
    for event in events:
        if not isinstance(event, dict) or type(event.get("midi_note")) is not int or not 0 <= event["midi_note"] <= 127:
            raise ValueError("each event must contain an integer MIDI note 0..127")
        if event.get("source_record_id", records[0]['record_id']) != records[0]['record_id']:
            raise ValueError("event source_record_id differs from accepted analysis")
        if any(k not in event for k in ("start", "duration", "velocity")):
            raise ValueError("event requires start, duration, and velocity")
        _number(event["start"], "event start", 0)
        _number(event["duration"], "event duration", 1e-9)
        if type(event["velocity"]) is not int or not 1 <= event["velocity"] <= 127:
            raise ValueError("event velocity must be MIDI 1..127")
        staged = []
        for index, component in enumerate(components):
            offset = component.get("semitone_offset")
            cents = component.get("residual_cents")
            if type(offset) is not int or type(cents) not in (int, float) or not math.isfinite(cents) or not -50 <= cents <= 50:
                raise ValueError("component offset/cents is invalid")
            note = event["midi_note"] + offset
            if not 0 <= note <= 127:
                raise ValueError("expanded component is outside MIDI range")
            item = deepcopy(event)
            item["midi_note"] = note
            item["component_index"] = index
            item["residual_cents"] = cents
            item["relative_level_db"] = component.get("relative_level_db")
            staged.append(item)
        output.extend(staged)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-summary", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    _, diagnostic = analyze_baked_chord(args.probe_summary, output_dir=args.output)
    print(json.dumps({"schema_version": diagnostic["schema_version"],
                      "source_record_id": diagnostic["source_record_id"],
                      "status": diagnostic["status"], "reason": diagnostic["reason"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
