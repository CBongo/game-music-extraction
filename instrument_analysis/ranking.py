"""Conservative, hash-bound audio ranking against GM reference probes."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import wave

from .features import VERSION as FEATURE_VERSION, extract_features
from . import features as feature_module
from .schema import make_bundle
from .comparisons import _hash, _read_json, _native_key, _calibration

INDEX_VERSION = "instrument-gm-reference-index/v1"
DIAGNOSTICS_VERSION = "instrument-ranking-diagnostics/v1"
PITCH_TOLERANCE_CENTS = 25.0
PHASE_WEIGHTS = {"attack": .30, "sustain": .45, "release": .25}


def _ref(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": _hash(path)}


def _number(value: object, name: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    value = float(value)
    if not lo <= value <= hi:
        raise ValueError(f"{name} outside [{lo}, {hi}]")
    return value


def _local_file(manifest: Path, name: object, label: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name or Path(name).is_absolute():
        raise ValueError(f"{label} must be a local file name")
    path = (manifest.parent / name).resolve()
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    return path


def _wav_info(path: Path, meta: dict, label: str) -> dict:
    if _hash(path) != meta.get("sha256"):
        raise ValueError(f"{label} WAV hash differs from manifest")
    try:
        with wave.open(str(path), "rb") as stream:
            info = {"sample_rate": stream.getframerate(), "channels": stream.getnchannels(),
                    "sample_width_bytes": stream.getsampwidth(), "frames": stream.getnframes()}
            if stream.getcomptype() != "NONE":
                raise ValueError(f"{label} WAV is compressed")
    except wave.Error as exc:
        raise ValueError(f"{label} is not readable PCM WAV") from exc
    if any(meta.get(k) != v for k, v in info.items()):
        raise ValueError(f"{label} WAV header differs from manifest")
    if info["channels"] not in (1, 2) or info["sample_width_bytes"] != 2 or info["frames"] <= 0:
        raise ValueError(f"{label} must be nonempty mono/stereo PCM16")
    return info


def _dry_gm(manifest: dict) -> None:
    c = manifest.get("conditions")
    if manifest.get("schema_version") != "instrument-render/v1":
        raise ValueError("unknown GM render manifest schema")
    if not isinstance(c, dict) or c.get("dry") is not True or c.get("reverb") is not False or c.get("chorus") is not False:
        raise ValueError("GM reference is not explicitly dry")
    if c.get("midi_format") != 0 or c.get("midi_ticks_per_second") != 1000:
        raise ValueError("GM MIDI event timing format is unsupported")


def _route(probe: dict) -> dict:
    route = probe.get("route")
    if not isinstance(route, dict):
        raise ValueError("GM probe route missing")
    if route.get("kind") == "gm_program":
        p = route.get("program_0based")
        if type(p) is not int or not 0 <= p <= 127 or route.get("program_1based") != p + 1:
            raise ValueError("GM program numbering disagrees")
        if probe.get("kind") != "melodic" or probe.get("program_0based") != p:
            raise ValueError("GM melodic route conflicts with probe")
        return {"kind": "gm_program", "program_0based": p, "program_1based": p + 1}
    if route.get("kind") == "gm_percussion":
        note = route.get("note")
        if probe.get("kind") != "percussion" or type(note) is not int or note != probe.get("note"):
            raise ValueError("GM channel-10 percussion route conflicts with probe")
        if probe.get("channel_0based") != 9 or probe.get("bank") != 128:
            raise ValueError("GM percussion must use channel 10 and percussion bank")
        return {"kind": "gm_percussion", "note": note}
    raise ValueError("unsupported GM route kind")


def _cached_features(wav: Path, wav_hash: str, hold: float, end: float, cache_dir: Path) -> dict:
    settings = {"wav_sha256": wav_hash, "note_on_seconds": 0.0,
                "note_off_seconds": hold, "end_seconds": end,
                "feature_code_sha256": _hash(Path(feature_module.__file__))}
    key = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
    cache = cache_dir / f"{key}.json"
    if cache.is_file():
        item = _read_json(cache, "feature cache")
        if item.get("cache_key") == key and item.get("settings") == settings:
            value = item.get("features")
            if isinstance(value, dict) and value.get("schema_version") == FEATURE_VERSION:
                return value
    value = extract_features(wav, note_on_seconds=0, note_off_seconds=hold, end_seconds=end)
    if value.get("schema_version") != FEATURE_VERSION:
        raise ValueError("audio feature schema changed")
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        with cache.open("x", encoding="utf-8") as stream:
            json.dump({"cache_key": key, "settings": settings, "features": value}, stream, sort_keys=True)
            stream.write("\n")
    except FileExistsError:
        pass
    return value


def build_gm_reference_index(
    gm_manifest_paths: list[str | Path] | tuple[str | Path, ...],
    output_path: str | Path, *, cache_dir: str | Path | None = None,
) -> dict:
    """Hash and featurize dry GM probes without using benchmark labels."""
    manifests = tuple(Path(p).expanduser().resolve(strict=True) for p in gm_manifest_paths)
    if not manifests or len(set(manifests)) != len(manifests):
        raise ValueError("GM manifest paths must be nonempty and unique")
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"reference index already exists: {output}")
    cache = Path(cache_dir).expanduser().resolve() if cache_dir else output.parent / ".gm-feature-cache"
    references, manifest_refs = [], []
    for manifest_path in manifests:
        manifest = _read_json(manifest_path, "GM manifest")
        _dry_gm(manifest)
        conditions, font = manifest["conditions"], manifest.get("soundfont")
        if (not isinstance(font, dict) or not isinstance(font.get("sha256"), str) or
                len(font["sha256"]) != 64 or not isinstance(font.get("path"), str)):
            raise ValueError("GM manifest lacks SoundFont hash/path")
        source_ref = _ref(manifest_path)
        manifest_refs.append({**source_ref, "soundfont": deepcopy(font),
                              "conditions": deepcopy(conditions)})
        probes = manifest.get("probes")
        if not isinstance(probes, list) or not probes:
            raise ValueError("GM manifest has no probes")
        ids = set()
        for probe in probes:
            if not isinstance(probe, dict):
                raise ValueError("GM probe must be an object")
            pid = probe.get("probe_id")
            if not isinstance(pid, str) or not pid or pid in ids:
                raise ValueError("GM probe IDs must be unique and nonempty")
            ids.add(pid)
            route = _route(probe)
            wav_meta = probe.get("wav")
            if not isinstance(wav_meta, dict):
                raise ValueError("GM probe WAV metadata missing")
            wav = _local_file(manifest_path, wav_meta.get("file"), "GM probe WAV")
            info = _wav_info(wav, wav_meta, "GM")
            if info["sample_rate"] != conditions.get("sample_rate"):
                raise ValueError("GM sample rate disagrees with manifest")
            hold = _number(conditions.get("duration_seconds"), "GM hold", .001, 600)
            tail = _number(conditions.get("tail_seconds"), "GM tail", .001, 600)
            if info["frames"] / info["sample_rate"] + 1 / info["sample_rate"] < hold + tail:
                raise ValueError("GM WAV is shorter than its note and tail")
            note, velocity, bank = probe.get("note"), probe.get("velocity"), probe.get("bank")
            if any(type(x) is not int for x in (note, velocity, bank)) or not (
                    0 <= note <= 127 and 1 <= velocity <= 127 and 0 <= bank <= 16383):
                raise ValueError("GM note, velocity, or bank invalid")
            features = _cached_features(wav, wav_meta["sha256"], hold, hold + tail, cache)
            references.append({
                "probe_id": pid, "manifest_sha256": source_ref["sha256"],
                "wav": {"path": str(wav), "sha256": wav_meta["sha256"], **info},
                "soundfont_sha256": font["sha256"], "route": route,
                "bank": bank, "note": note, "velocity": velocity,
                "preset_name": probe.get("preset_name", ""),
                "conditions": {"hold_seconds": hold, "tail_seconds": tail,
                               "sample_rate": info["sample_rate"]},
                "features": features,
            })
    result = {
        "schema_version": INDEX_VERSION,
        "generator": {"name": "instrument_analysis.ranking",
                      "feature_schema_version": FEATURE_VERSION,
                      "feature_code_sha256": _hash(Path(feature_module.__file__))},
        "manifests": manifest_refs, "references": references,
    }
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return result


def _load_index(index_path: Path) -> dict:
    index = _read_json(index_path, "GM reference index")
    if index.get("schema_version") != INDEX_VERSION:
        raise ValueError("unknown GM reference index schema")
    generator = index.get("generator", {})
    if (not isinstance(generator, dict) or generator.get("feature_schema_version") != FEATURE_VERSION or
            generator.get("feature_code_sha256") != _hash(Path(feature_module.__file__))):
        raise ValueError("GM index audio feature implementation differs")
    manifests, references = index.get("manifests"), index.get("references")
    if not isinstance(manifests, list) or not isinstance(references, list) or not references:
        raise ValueError("GM index is empty")
    by_hash = {}
    for item in manifests:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("GM index manifest reference invalid")
        path = Path(item["path"])
        if _hash(path) != item.get("sha256"):
            raise ValueError(f"GM source manifest changed: {path}")
        manifest = _read_json(path, "GM manifest")
        _dry_gm(manifest)
        if manifest.get("soundfont") != item.get("soundfont") or manifest.get("conditions") != item.get("conditions"):
            raise ValueError("GM SoundFont identity or render conditions changed")
        by_hash[item["sha256"]] = (manifest, path)
    for item in references:
        if not isinstance(item, dict) or item.get("manifest_sha256") not in by_hash:
            raise ValueError("GM index probe has no source manifest")
        manifest, manifest_path = by_hash[item["manifest_sha256"]]
        matches = [p for p in manifest["probes"] if p.get("probe_id") == item.get("probe_id")]
        if len(matches) != 1:
            raise ValueError("GM index probe identity changed")
        probe = matches[0]
        if (_route(probe) != item.get("route") or
                probe.get("wav", {}).get("sha256") != item.get("wav", {}).get("sha256") or
                manifest.get("soundfont", {}).get("sha256") != item.get("soundfont_sha256")):
            raise ValueError("GM index probe route or WAV identity changed")
        if any(item.get(field) != probe.get(field) for field in ("note", "velocity", "bank", "preset_name")):
            raise ValueError("GM index probe note, velocity, bank or preset identity changed")
        wav = Path(item["wav"]["path"])
        if wav != _local_file(manifest_path, probe["wav"]["file"], "GM WAV"):
            raise ValueError("GM index WAV path changed")
        actual_info = _wav_info(wav, probe["wav"], "GM")
        if any(item["wav"].get(field) != value for field, value in actual_info.items()):
            raise ValueError("GM index WAV properties changed")
        if item.get("features", {}).get("schema_version") != FEATURE_VERSION:
            raise ValueError("GM index feature schema changed")
        c = item.get("conditions", {})
        if (not isinstance(c, dict) or c.get("hold_seconds") != manifest["conditions"].get("duration_seconds") or
                c.get("tail_seconds") != manifest["conditions"].get("tail_seconds") or
                c.get("sample_rate") != manifest["conditions"].get("sample_rate")):
            raise ValueError("GM index phase conditions changed")
    return index

_PREPARED_TOKEN = object()


class PreparedReferenceIndex:
    """An active, entry-verified index scoped to one batch of queries."""

    __slots__ = ("path", "sha256", "_index", "_active")

    def __init__(self, path: Path, sha256: str, index: dict, token: object):
        if token is not _PREPARED_TOKEN:
            raise TypeError("use prepared_reference_index()")
        self.path = path
        self.sha256 = sha256
        self._index = index
        self._active = True


@contextmanager
def prepared_reference_index(index_path: str | Path):
    """Verify all GM inputs before and after a batch; reuse them only in scope.

    The index file's SHA256 is checked on every query. All bound reference WAVs
    and manifests are rehashed on entry and exit, so mid-batch changes fail at
    context exit even when the caller made no further queries.
    """
    path = Path(index_path).expanduser().resolve(strict=True)
    sha256 = _hash(path)
    index = _load_index(path)
    if _hash(path) != sha256:
        raise ValueError("GM index changed during initial verification")
    handle = PreparedReferenceIndex(path, sha256, index, _PREPARED_TOKEN)
    try:
        yield handle
    finally:
        handle._active = False
        if _hash(path) != sha256:
            raise ValueError("GM index changed during prepared batch")
        _load_index(path)


def _index_for_query(index_path: Path, prepared_index: PreparedReferenceIndex | None) -> dict:
    if prepared_index is None:
        return _load_index(index_path)
    if (not isinstance(prepared_index, PreparedReferenceIndex) or
            not prepared_index._active or prepared_index.path != index_path):
        raise ValueError("prepared GM index handle is closed or belongs to another path")
    if _hash(index_path) != prepared_index.sha256:
        raise ValueError("GM index changed during prepared batch")
    return prepared_index._index


def _cosine_distance(a: list, b: list) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("audio feature vector lengths differ")
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in a + b):
        raise ValueError("audio feature vector has nonfinite values")
    aa = math.sqrt(sum(x * x for x in a))
    bb = math.sqrt(sum(x * x for x in b))
    if aa == 0 or bb == 0:
        return 1.0
    return min(1.0, max(0.0, 1 - sum(x * y for x, y in zip(a, b)) / (aa * bb)))


def _distance(source: dict, reference: dict) -> tuple[float, dict]:
    components, total, weight = {}, 0.0, 0.0
    for phase, phase_weight in PHASE_WEIGHTS.items():
        a = source.get("phases", {}).get(phase)
        b = reference.get("phases", {}).get(phase)
        if not isinstance(a, dict) or not isinstance(b, dict) or a.get("silent") or b.get("silent"):
            continue
        spectrum = _cosine_distance(a["log_spectral_bands_l2"], b["log_spectral_bands_l2"])
        envelope = _cosine_distance(a["envelope_shape"], b["envelope_shape"])
        centroid = min(1.0, abs(a["spectral_centroid_normalized"] - b["spectral_centroid_normalized"]))
        rolloff = min(1.0, abs(a["spectral_rolloff_normalized"] - b["spectral_rolloff_normalized"]))
        flatness = min(1.0, abs(a["spectral_flatness"] - b["spectral_flatness"]))
        zcr = min(1.0, abs(a["zero_crossing_rate"] - b["zero_crossing_rate"]))
        d = .55 * spectrum + .20 * envelope + .10 * centroid + .08 * rolloff + .04 * flatness + .03 * zcr
        components[phase] = {
            "spectral_cosine_distance": spectrum, "envelope_cosine_distance": envelope,
            "centroid_distance": centroid, "rolloff_distance": rolloff,
            "flatness_distance": flatness, "zero_crossing_distance": zcr,
            "weighted_distance": d,
        }
        total += phase_weight * d
        weight += phase_weight
    if weight < .5:
        raise ValueError("fewer than half the phase features are comparable")
    return total / weight, components


def _record(record_id: str, source_facts: dict | None, engine: str | None = None) -> dict:
    if not isinstance(record_id, str) or not record_id:
        raise ValueError("record_id is required")
    if source_facts is not None:
        if not isinstance(source_facts, dict) or any(
                k not in {"source", "identity", "observation", "classification", "source_record_id", "wav_sha256", "native_manifest_sha256"} for k in source_facts):
            raise ValueError("source_facts include unsupported fields")
        source = deepcopy(source_facts.get("source"))
        identity = deepcopy(source_facts.get("identity"))
        observation = deepcopy(source_facts.get("observation", {}))
        if source is None or identity is None:
            fallback = _record(record_id, None, engine)
            source = fallback["source"] if source is None else source
            identity = fallback["identity"] if identity is None else identity
        if not isinstance(source, dict) or not isinstance(identity, dict) or not isinstance(observation, dict):
            raise ValueError("source_facts source/identity/observation must be objects")
        observation.pop("existing_mapping", None)
    else:
        platform = ("Sony PlayStation" if engine and "PlayStation" in engine else
                    "Super Nintendo" if engine and "SNES" in engine else
                    "Arcade" if engine and "YM2151" in engine else "unknown")
        source = {"platform": platform,
                  "game_or_module": record_id.split(":")[1] if ":" in record_id else "query",
                  "driver": engine or "WAV query"}
        identity = {"source_instrument_id": record_id}
        observation = {}
    return {"record_id": record_id, "source": source,
            "identity": identity, "observation": observation}


def _classification(source_facts: dict | None, record_id: str, wav_hash: str) -> str | None:
    value = source_facts.get("classification") if isinstance(source_facts, dict) else None
    if value is None:
        return None
    provenance = value.get("provenance") if isinstance(value, dict) else None
    if (source_facts.get("source_record_id") != record_id or
            source_facts.get("wav_sha256") != wav_hash or
            not isinstance(value, dict) or value.get("basis") not in ("source_fact", "hardware_fact") or
            value.get("kind") not in ("gm_percussion_trigger", "sfx", "composite") or
            not isinstance(value.get("evidence"), str) or not value["evidence"].strip() or
            not isinstance(provenance, dict) or
            not isinstance(provenance.get("method"), str) or not provenance["method"].strip()):
        raise ValueError("classification requires hash-bound source/hardware fact and provenance")
    if value["kind"] == "gm_percussion_trigger" and value.get("gm_channel_0based") != 9:
        raise ValueError("GM percussion trigger requires explicit channel-10 source fact")
    source_path = provenance.get("source_file_path")
    source_sha = provenance.get("source_file_sha256")
    if source_path is not None or source_sha is not None:
        if (not isinstance(source_path, str) or not isinstance(source_sha, str) or
                len(source_sha) != 64 or _hash(Path(source_path)) != source_sha):
            raise ValueError("classification source file hash is invalid")
    return value["kind"]


def _settings(top_k: int, distance_threshold: float, margin_threshold: float) -> dict:
    if type(top_k) is not int or not 1 <= top_k <= 128:
        raise ValueError("top_k must be 1..128")
    return {
        "top_k": top_k,
        "distance_threshold": _number(distance_threshold, "distance_threshold", .001, 1),
        "margin_threshold": _number(margin_threshold, "margin_threshold", .001, 1),
        "pitch_tolerance_cents": PITCH_TOLERANCE_CENTS,
    }


def _write_outputs(output_dir: Path | None, bundle: dict, diagnostics: dict) -> None:
    if output_dir is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {"predictions.json": bundle, "diagnostics.json": diagnostics}
    for name in files:
        if (output_dir / name).exists():
            raise FileExistsError(f"ranking output already exists: {output_dir / name}")
    for name, value in files.items():
        with (output_dir / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")


def _functional_coverage(record: dict, index: dict) -> dict:
    usage = record.get("observation", {}).get("usage", {})
    if not isinstance(usage, dict):
        return {"status": "not_scored", "reason": "No grounded sounding-note usage supplied."}
    histogram: dict[int, int] = {}
    for sequence in usage.get("sequences", []):
        if not isinstance(sequence, dict):
            continue
        for occurrence in sequence.get("note_occurrences", []):
            if not isinstance(occurrence, dict) or occurrence.get("pitch_context_complete") is not True:
                continue
            note = occurrence.get("sounding_midi_note")
            if (isinstance(note, (int, float)) and not isinstance(note, bool) and
                    math.isfinite(note) and 0 <= note <= 127 and
                    abs(note - round(note)) <= .25):
                key = round(note)
                histogram[key] = histogram.get(key, 0) + 1
    if not histogram:
        return {"status": "not_scored", "reason": "No occurrence-level sounding MIDI notes are available; raw triggers and source-tick effects are not converted."}
    routes: dict[tuple, set[int]] = {}
    for ref in index["references"]:
        route = ref["route"]
        if route["kind"] != "gm_program":
            continue
        key = (route["program_0based"], ref["bank"])
        routes.setdefault(key, set()).add(ref["note"])
    total = sum(histogram.values())
    scores = [{"route": {"kind": "gm_program", "program_0based": program,
                         "program_1based": program + 1}, "bank": bank,
               "indexed_probe_note_coverage": sum(count for note, count in histogram.items()
                                                  if note in notes) / total}
              for (program, bank), notes in sorted(routes.items())]
    return {"status": "coverage_only", "note_histogram": [
                {"note": note, "occurrences": count} for note, count in sorted(histogram.items())],
            "scores": scores, "blend_weight": 0.0,
            "note": "Coverage describes reference probe grid availability, not an instrument's physical range or functional role."}


def _abstain(record: dict, reason: str, diagnostics: dict,
             output_dir: Path | None) -> tuple[dict, dict]:
    record["analysis"] = {"routing": [{
        "rank": 1, "route": {"kind": "abstain", "reason": reason},
        "score": 0.0, "basis": "inferred",
        "provenance": {"method": "conservative ranking abstention",
                       "parameters": {"reason": reason}},
    }], "notes": [reason]}
    bundle = make_bundle([record], "instrument_analysis.ranking")
    diagnostics.update(status="abstain", reason=reason, candidates=[])
    _write_outputs(output_dir, bundle, diagnostics)
    return bundle, diagnostics


def _rank(
    record: dict, query_features: dict | None, sounding_note: float | None,
    index: dict, index_path: Path, settings: dict, query_ref: dict,
    classification: str | None, output_dir: Path | None,
) -> tuple[dict, dict]:
    diagnostics = {
        "schema_version": DIAGNOSTICS_VERSION,
        "query": query_ref, "index": _ref(index_path), "settings": settings,
        "excluded_counts": {"pitch": 0, "route": 0, "phase": 0, "invalid_features": 0},
        "consensus": {
            "distinct_soundfont_count": len({r["soundfont_sha256"] for r in index["references"]}),
            "note": "Only distinct SoundFont SHA256 values count as independent reference banks.",
        },
        "functional": _functional_coverage(record, index),
    }
    if classification in ("sfx", "composite"):
        record["analysis"] = {"routing": [{"rank": 1, "route": {"kind": classification,
            "description": f"Explicit source-fact {classification} handoff"}, "score": 1.0,
            "basis": "measured", "provenance": {"method": "hash-bound source classification fact",
            "parameters": {"classification": query_ref.get("classification"),
                           "source_facts": query_ref.get("source_facts"),
                           "score_note": "Explicit source-fact handoff, not acoustic similarity or probability."}},
            "evidence": ["Dedicated source-function analysis is required; no GM program is inferred."]}],
            "notes": ["This is a source-function handoff, not a chord or GM timbre reconstruction."]}
        bundle = make_bundle([record], "instrument_analysis.ranking")
        diagnostics.update(status="handoff", reason=f"explicit {classification} source fact",
                           candidates=[])
        _write_outputs(output_dir, bundle, diagnostics)
        return bundle, diagnostics
    if query_features is not None and query_features.get("event_clipped_sample_count", 0) > 0:
        return _abstain(record, "query audio is clipped", diagnostics, output_dir)
    if query_features is None or not query_features.get("valid") or query_features.get("silent"):
        return _abstain(record, "query audio features are silent or invalid", diagnostics, output_dir)
    if classification != "gm_percussion_trigger" and sounding_note is None:
        return _abstain(record, "calibrated sounding pitch unavailable", diagnostics, output_dir)
    groups: dict[tuple, dict] = {}
    for ref in index["references"]:
        route = ref["route"]
        if classification == "gm_percussion_trigger":
            if route["kind"] != "gm_percussion":
                diagnostics["excluded_counts"]["route"] += 1
                continue
        elif route["kind"] != "gm_program":
            diagnostics["excluded_counts"]["route"] += 1
            continue
        phase = query_ref.get("event_boundaries_seconds")
        ref_phase = ref.get("conditions", {})
        if isinstance(phase, dict):
            tolerance = max(.01, query_ref.get("alignment_uncertainty_seconds", 0))
            if (abs((phase["note_off"] - phase["note_on"]) - ref_phase.get("hold_seconds", -1)) > tolerance or
                    abs((phase["end"] - phase["note_off"]) - ref_phase.get("tail_seconds", -1)) > tolerance):
                diagnostics["excluded_counts"]["phase"] += 1
                continue
        if (route["kind"] == "gm_program" and
                abs((sounding_note - ref["note"]) * 100) > settings["pitch_tolerance_cents"]):
            diagnostics["excluded_counts"]["pitch"] += 1
            continue
        if (not ref["features"].get("valid") or ref["features"].get("silent") or
                ref["features"].get("event_clipped_sample_count", 0) > 0):
            diagnostics["excluded_counts"]["invalid_features"] += 1
            continue
        try:
            distance, components = _distance(query_features, ref["features"])
        except ValueError:
            diagnostics["excluded_counts"]["invalid_features"] += 1
            continue
        key = (route["kind"], route.get("program_0based", route.get("note")),
               ref["bank"], ref["soundfont_sha256"])
        choice = {"distance": distance, "components": components, "reference": ref}
        if key not in groups or (distance, ref["probe_id"]) < (
                groups[key]["distance"], groups[key]["reference"]["probe_id"]):
            groups[key] = choice
    if not groups:
        return _abstain(record, "no pitch-matched, comparable GM reference",
                        diagnostics, output_dir)
    by_route: dict[tuple, list[dict]] = {}
    for choice in groups.values():
        ref = choice["reference"]
        route = ref["route"]
        key = (route["kind"], route.get("program_0based", route.get("note")))
        by_route.setdefault(key, []).append(choice)
    rows = []
    for variants in by_route.values():
        variants.sort(key=lambda c: (c["distance"], c["reference"]["probe_id"]))
        per_font = {}
        for choice in variants:
            per_font.setdefault(choice["reference"]["soundfont_sha256"], choice)
        choices = list(per_font.values())
        representative = variants[0]
        ref = representative["reference"]
        rows.append({
            "distance": sum(c["distance"] for c in choices) / len(choices),
            "best_probe_distance": representative["distance"],
            "route": ref["route"], "bank": ref["bank"],
            "soundfont_sha256": ref["soundfont_sha256"],
            "probe_id": ref["probe_id"], "note": ref["note"], "velocity": ref["velocity"],
            "components": representative["components"],
            "distinct_soundfont_count": len(choices),
            "variant_count": len(variants),
            "variant_selection_note": "Best bank/note/velocity variant per SoundFont; variants do not count as independent consensus.",
            "font_distances": [{"soundfont_sha256": c["reference"]["soundfont_sha256"],
                                "distance": c["distance"]} for c in choices],
        })
    rows.sort(key=lambda r: (r["distance"], r["route"]["kind"],
                             r["route"].get("program_0based", r["route"].get("note")), r["bank"]))
    best = rows[0]["distance"]
    margin = rows[1]["distance"] - best if len(rows) > 1 else 1.0
    ambiguous = best > settings["distance_threshold"] or margin < settings["margin_threshold"]
    reason = ("best timbral distance exceeds threshold" if best > settings["distance_threshold"] else
              "top two route groups are too close" if ambiguous else None)
    displayed = rows[:settings["top_k"]]
    for i, row in enumerate(displayed):
        row["rank"] = i + 1
        row["margin_to_next"] = (rows[i + 1]["distance"] - row["distance"]
                                 if i + 1 < len(rows) else None)
        row["confidence_heuristic"] = round(
            max(0.0, 1 - row["distance"] / settings["distance_threshold"]) *
            min(1.0, max(0.0, margin) / settings["margin_threshold"]), 6)
    diagnostics.update(status="abstain" if ambiguous else "ranked",
                       reason=reason, candidates=displayed)
    provenance = {
        "method": "phase-aware gain-invariant audio-feature distance",
        "parameters": {
            "index_sha256": diagnostics["index"]["sha256"],
            "pitch_tolerance_cents": settings["pitch_tolerance_cents"],
            "distance_threshold": settings["distance_threshold"],
            "margin_threshold": settings["margin_threshold"],
            "confidence_note": "Distance and confidence heuristic are not probabilities.",
        },
    }
    candidates = []
    if ambiguous:
        candidates.append({"rank": 1, "route": {"kind": "abstain", "reason": reason},
                           "score": 0.0, "basis": "inferred", "provenance": deepcopy(provenance)})
    for row in displayed:
        candidates.append({
            "rank": len(candidates) + 1, "route": deepcopy(row["route"]),
            "score": round(max(0.0, min(1.0, 1 - row["distance"])), 6),
            "basis": "measured", "provenance": deepcopy(provenance),
            "candidate_audition_octave_adjustment": 0,
            "evidence": [f"GM reference {row['probe_id']} at MIDI note {row['note']}",
                         f"distance {row['distance']:.6f}; bank {row['bank']}; distinct SoundFonts {row['distinct_soundfont_count']}"],
        })
    record["analysis"] = {
        "routing": candidates,
        "notes": ["Acoustic similarity only; functional role was not inferred.",
                  "Candidate audition octave is separate from any observed source pitch offset."],
    }
    bundle = make_bundle([record], "instrument_analysis.ranking")
    _write_outputs(output_dir, bundle, diagnostics)
    return bundle, diagnostics

def rank_native_source(
    native_manifest_path: str | Path, pitch_calibration_path: str | Path,
    index_path: str | Path, *, output_dir: str | Path | None = None,
    top_k: int = 5, distance_threshold: float = .35, margin_threshold: float = .04,
    canonical_record_path: str | Path | None = None,
    source_facts_path: str | Path | None = None,
    prepared_index: PreparedReferenceIndex | None = None,
) -> tuple[dict, dict]:
    """Rank a hash-bound native capture; uncalibrated pitch abstains."""
    settings = _settings(top_k, distance_threshold, margin_threshold)
    index_path = Path(index_path).expanduser().resolve(strict=True)
    index = _index_for_query(index_path, prepared_index)
    native_path = Path(native_manifest_path).expanduser().resolve(strict=True)
    cal_path = Path(pitch_calibration_path).expanduser().resolve(strict=True)
    native = _read_json(native_path, "native manifest")
    if native.get("schema_version") not in ("instrument-native-chip-render/v1", "instrument-native-render/v1"):
        raise ValueError("unknown native render schema")
    from .comparisons import _native_dry
    _native_dry(native)
    record_id = native.get("source_record_id")
    if not isinstance(record_id, str) or not record_id:
        raise ValueError("native source record ID is missing")
    meta = native.get("wav")
    if not isinstance(meta, dict):
        raise ValueError("native WAV metadata missing")
    wav = _local_file(native_path, meta.get("file"), "native WAV")
    info = _wav_info(wav, meta, "native")
    cal = _read_json(cal_path, "pitch calibration")
    raw_pitch = _native_key(native)
    note = None
    if cal.get("status") == "calibrated":
        note, _ = _calibration(cal, cal_path, native_path, _hash(native_path),
                               wav, meta["sha256"], native, raw_pitch)
    elif cal.get("status") != "abstain":
        raise ValueError("unknown pitch calibration status")
    elif (cal.get("native_manifest_sha256") != _hash(native_path) or
          cal.get("native_wav_sha256") != meta["sha256"] or
          cal.get("source_record_id") != record_id or
          cal.get("native_raw_pitch") != raw_pitch):
        raise ValueError("abstaining calibration is not bound to native input")
    facts = None
    source_facts_ref = None
    if canonical_record_path is not None:
        obj = _read_json(Path(canonical_record_path).expanduser().resolve(strict=True), "canonical source record")
        records = obj.get("records") if obj.get("schema_version") == "instrument-analysis/v1" else [obj]
        matches = [r for r in records if isinstance(r, dict) and r.get("record_id") == record_id]
        if len(matches) != 1:
            raise ValueError("canonical source record does not match native source ID")
        facts = {k: deepcopy(matches[0].get(k)) for k in ("source", "identity", "observation")}
    if source_facts_path is not None:
        context_path = Path(source_facts_path).expanduser().resolve(strict=True)
        context = _read_json(context_path, "grounded source facts")
        source_facts_ref = {**_ref(context_path),
                            "source_record_id": context.get("source_record_id"),
                            "native_manifest_sha256": context.get("native_manifest_sha256"),
                            "wav_sha256": context.get("wav_sha256")}
        if (context.get("source_record_id") != record_id or
                context.get("native_manifest_sha256") != _hash(native_path) or
                context.get("wav_sha256") != meta["sha256"]):
            raise ValueError("grounded source facts are not hash-bound to native source")
        facts = {**(facts or {}), **context}
    record = _record(record_id, facts, native.get("engine"))
    classification = _classification(facts, record_id, meta["sha256"])
    receipt = _local_file(native_path, native.get("receipt_file"), "native receipt")
    rec = _read_json(receipt, "native receipt")
    if rec.get("status") != "ok":
        raise ValueError("native receipt did not succeed")
    conditions = native.get("conditions", {})
    alignment = conditions.get("wav_alignment", {}) if isinstance(conditions, dict) else {}
    if not isinstance(alignment, dict):
        raise ValueError("native timing conditions missing")
    start = _number(alignment.get("actual_trim_start_seconds"), "native trim start", 0, 1e6)
    on = _number(conditions.get("note_on_time_seconds"), "native note on", 0, 1e6) - start
    off = _number(conditions.get("note_off_time_seconds"), "native note off", 0, 1e6) - start
    end = min(info["frames"] / info["sample_rate"],
              _number(conditions.get("capture_end_time_seconds"), "native capture end", 0, 1e6) - start)
    if not 0 <= on < off < end:
        raise ValueError("native query has no valid hold/tail window")
    for field in ("note_on_time_seconds", "note_off_time_seconds", "capture_end_time_seconds"):
        if abs(conditions[field] - rec.get(field, -1)) > 1e-6:
            raise ValueError("native receipt timing differs from manifest")
    features = extract_features(wav, note_on_seconds=on, note_off_seconds=off, end_seconds=end)
    query = {
        "kind": "native", "source_record_id": record_id,
        "native_manifest": _ref(native_path), "native_wav": _ref(wav),
        "native_receipt": _ref(receipt), "pitch_calibration": _ref(cal_path),
        "raw_pitch": raw_pitch, "calibrated_midi_note": note,
        "feature_schema_version": FEATURE_VERSION,
        "event_boundaries_seconds": {"note_on": on, "note_off": off, "end": end},
        "alignment_uncertainty_seconds": alignment.get("alignment_uncertainty_seconds", 0),
        "classification": deepcopy(facts.get("classification")) if facts else None,
        "source_facts": source_facts_ref,
    }
    output = Path(output_dir).expanduser().resolve() if output_dir else None
    return _rank(record, features, note, index, index_path, settings,
                 query, classification, output)


def rank_wav_source(
    wav_path: str | Path, index_path: str | Path, *, record_id: str,
    note_on_seconds: float, note_off_seconds: float, end_seconds: float,
    calibration: dict | None = None, source_facts: dict | None = None,
    output_dir: str | Path | None = None, top_k: int = 5,
    distance_threshold: float = .35, margin_threshold: float = .04,
    prepared_index: PreparedReferenceIndex | None = None,
) -> tuple[dict, dict]:
    """Rank a neutral WAV with explicit event times and hash-bound pitch.

    Benchmark manifests, labels, display names, and existing mappings are never
    loaded. Missing audio or missing calibration emits a canonical abstention.
    """
    settings = _settings(top_k, distance_threshold, margin_threshold)
    index_path = Path(index_path).expanduser().resolve(strict=True)
    index = _index_for_query(index_path, prepared_index)
    record = _record(record_id, source_facts)
    output = Path(output_dir).expanduser().resolve() if output_dir else None
    path = Path(wav_path).expanduser().resolve()
    classification = _classification(source_facts, record_id, _hash(path)) if path.is_file() else None
    facts_ref = None
    if source_facts is not None:
        facts_ref = {"origin": "supplied_argument",
                     "sha256": hashlib.sha256(json.dumps(source_facts, sort_keys=True).encode()).hexdigest(),
                     "source_record_id": source_facts.get("source_record_id"),
                     "wav_sha256": source_facts.get("wav_sha256")}
    query = {
        "kind": "wav", "source_record_id": record_id,
        "wav": {"path": str(path), "sha256": _hash(path) if path.is_file() else None},
        "calibration": deepcopy(calibration), "feature_schema_version": FEATURE_VERSION,
        "event_boundaries_seconds": {"note_on": note_on_seconds,
                                      "note_off": note_off_seconds, "end": end_seconds},
        "alignment_uncertainty_seconds": 0,
        "classification": deepcopy(source_facts.get("classification")) if source_facts else None,
        "source_facts": facts_ref,
    }
    if not path.is_file():
        diagnostics = {"schema_version": DIAGNOSTICS_VERSION, "query": query,
                       "index": _ref(index_path), "settings": settings,
                       "excluded_counts": {"pitch": 0, "route": 0, "phase": 0,
                                           "invalid_features": 0},
                       "consensus": {"distinct_soundfont_count": len({r["soundfont_sha256"] for r in index["references"]}),
                                     "note": "Only distinct SoundFont SHA256 values count as independent reference banks."},
                       "functional": _functional_coverage(record, index)}
        return _abstain(record, "query WAV missing", diagnostics, output)
    on = _number(note_on_seconds, "note_on_seconds", 0, 1e6)
    off = _number(note_off_seconds, "note_off_seconds", 0, 1e6)
    end = _number(end_seconds, "end_seconds", 0, 1e6)
    if not on < off < end:
        raise ValueError("query event timing has no hold/tail window")
    note = None
    if calibration is not None:
        if not isinstance(calibration, dict):
            raise ValueError("query calibration must be an object")
        allowed_calibration = {"schema_version", "basis", "status", "source_record_id",
            "wav_path", "wav_sha256", "event_boundaries_seconds", "sounding_midi_note",
            "f0_hz", "confidence", "evidence", "method", "window_estimates",
            "median_absolute_deviation_cents", "provenance"}
        if set(calibration) - allowed_calibration:
            raise ValueError("query calibration contains unsupported fields")
        if calibration.get("status") == "calibrated":
            if (calibration.get("schema_version") != "instrument-query-pitch-calibration/v1" or
                    calibration.get("basis") not in ("acoustic_measurement", "verified_driver_reference") or
                    calibration.get("wav_sha256") != query["wav"]["sha256"] or
                    calibration.get("wav_path") != str(path) or
                    calibration.get("source_record_id") != record_id or
                    calibration.get("event_boundaries_seconds") != query["event_boundaries_seconds"] or
                    not isinstance(calibration.get("evidence"), str) or not calibration["evidence"].strip()):
                raise ValueError("query calibration lacks explicit hash-bound path, timing, and evidence")
            if _number(calibration.get("confidence"), "query calibration confidence", 0, 1) < .8:
                raise ValueError("query calibration confidence below 0.8")
            note = _number(calibration.get("sounding_midi_note"), "query sounding MIDI note", 0, 127)
            if calibration["basis"] == "verified_driver_reference":
                provenance = calibration.get("provenance")
                if (not isinstance(provenance, dict) or
                        not isinstance(provenance.get("method"), str) or not provenance["method"].strip() or
                        not isinstance(provenance.get("locator"), str) or not provenance["locator"].strip() or
                        not isinstance(provenance.get("source_file_path"), str) or
                        not isinstance(provenance.get("source_file_sha256"), str) or
                        len(provenance["source_file_sha256"]) != 64 or
                        _hash(Path(provenance["source_file_path"])) != provenance["source_file_sha256"]):
                    raise ValueError("verified driver reference lacks hash-checked source provenance")
            if calibration["basis"] == "acoustic_measurement":
                f0 = _number(calibration.get("f0_hz"), "query measured F0", 1, 20000)
                if abs(note - (69 + 12 * math.log2(f0 / 440))) > .001:
                    raise ValueError("query acoustic MIDI note disagrees with F0")
                windows = calibration.get("window_estimates")
                if (not isinstance(windows, list) or
                        sum(isinstance(w, dict) and isinstance(w.get("f0_hz"), (int, float)) and
                            w["f0_hz"] is not None and w["f0_hz"] > 0 for w in windows) < 2):
                    raise ValueError("query acoustic calibration lacks two measured windows")
        elif calibration.get("status") != "abstain":
            raise ValueError("unknown query calibration status")
    features = extract_features(path, note_on_seconds=on, note_off_seconds=off, end_seconds=end)
    return _rank(record, features, note, index, index_path, settings,
                 query, classification, output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_index = sub.add_parser("index", help="Build hash-bound GM reference index")
    p_index.add_argument("--gm-manifest", action="append", required=True)
    p_index.add_argument("--output", required=True)
    p_index.add_argument("--cache-dir")
    p_native = sub.add_parser("rank-native", help="Rank a native chip capture")
    p_native.add_argument("--native-manifest", required=True)
    p_native.add_argument("--pitch-calibration", required=True)
    p_native.add_argument("--index", required=True)
    p_native.add_argument("--output", required=True)
    p_native.add_argument("--canonical-record")
    p_native.add_argument("--source-facts")
    p_native.add_argument("--top-k", type=int, default=5)
    p_native.add_argument("--distance-threshold", type=float, default=.35)
    p_native.add_argument("--margin-threshold", type=float, default=.04)
    args = parser.parse_args(argv)
    if args.command == "index":
        result = build_gm_reference_index(args.gm_manifest, args.output, cache_dir=args.cache_dir)
        print(json.dumps({"schema_version": result["schema_version"],
                          "references": len(result["references"]),
                          "output": str(Path(args.output).resolve())}))
    else:
        _, diagnostic = rank_native_source(
            args.native_manifest, args.pitch_calibration, args.index,
            output_dir=args.output, canonical_record_path=args.canonical_record,
            source_facts_path=args.source_facts,
            top_k=args.top_k, distance_threshold=args.distance_threshold,
            margin_threshold=args.margin_threshold)
        print(json.dumps({"schema_version": diagnostic["schema_version"],
                          "status": diagnostic["status"], "reason": diagnostic["reason"],
                          "output": str(Path(args.output).resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
