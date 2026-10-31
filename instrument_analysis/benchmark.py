"""Evaluate explicit instrument labels against instrument-analysis/v1 predictions.

Missing labels are unknown. Missing routing predictions score as incorrect for
classification metrics; numeric metrics report paired coverage separately.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any

MANIFEST_VERSION = "instrument-benchmark/v1"
PREDICTION_VERSION = "instrument-analysis/v1"
REPORT_VERSION = "instrument-benchmark-report/v1"


def _obj(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    return value


def _arr(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be an array")
    return value


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where} must be a nonempty string")
    return value


def _int(value: Any, where: str, low: int | None = None,
         high: int | None = None) -> int:
    if (isinstance(value, bool) or not isinstance(value, int)
            or (low is not None and value < low)
            or (high is not None and value > high)):
        raise ValueError(f"{where} must be an integer in range")
    return value


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{where} must be a finite number")
    return float(value)


def _route_key(value: Any, where: str) -> tuple:
    """Canonical route identity; prose descriptions and reasons are not classes."""
    route = _obj(value, where)
    kind = route.get("kind")
    allowed = {
        "gm_program": {"kind", "program_0based", "program_1based", "melodic_percussion"},
        "gm_percussion": {"kind", "note", "name"},
        "sfx": {"kind", "description"},
        "composite": {"kind", "description"},
        "abstain": {"kind", "reason"},
    }
    if kind not in allowed:
        raise ValueError(f"{where}.kind is unsupported: {kind!r}")
    extra = set(route) - allowed[kind]
    if extra:
        raise ValueError(f"{where} has unsupported fields: {sorted(extra)!r}")
    for optional_text in ("name", "description"):
        if optional_text in route and not isinstance(route[optional_text], str):
            raise ValueError(f"{where}.{optional_text} must be a string")
    if kind == "gm_program":
        program = _int(route.get("program_0based"), f"{where}.program_0based", 0, 127)
        one = _int(route.get("program_1based"), f"{where}.program_1based", 1, 128)
        if one != program + 1:
            raise ValueError(f"{where}: GM program numbering disagrees")
        percussion = route.get("melodic_percussion", False)
        if not isinstance(percussion, bool):
            raise ValueError(f"{where}.melodic_percussion must be boolean")
        return (kind, program)  # melodic_percussion annotates the same concrete GM preset
    if kind == "gm_percussion":
        return (kind, _int(route.get("note"), f"{where}.note", 0, 127))
    if kind in ("sfx", "composite"):
        return (kind,)
    if kind == "abstain":
        _str(route.get("reason"), f"{where}.reason")
        return (kind,)
    raise ValueError(f"{where}.kind is unsupported: {kind!r}")


def _offsets(value: Any, where: str) -> Counter[int]:
    # A multiset preserves duplicate unison components if the annotator meant
    # two sounding layers at the same pitch.
    return Counter(_int(x, f"{where}[{i}]") for i, x in enumerate(_arr(value, where)))


def _pitch(record: dict, candidates: list[dict]) -> float | None:
    # Prefer the selected candidate's proposed offset over source observation.
    proposed = candidates[0].get("observed_pitch_offset") if candidates else None
    observed = _obj(record.get("observation", {}), "observation").get("pitch", {})
    observed = _obj(observed, "observation.pitch").get("observed_pitch_offset")
    for value in (proposed, observed):
        if value is None:
            continue
        value = _obj(value, "predicted observed_pitch_offset")
        semitones = _int(value.get("semitones"), "predicted offset semitones")
        cents = _number(value.get("residual_cents", 0), "predicted offset residual_cents")
        if not -50 <= cents <= 50:
            raise ValueError("predicted offset residual_cents must be within ±50")
        return semitones + cents / 100
    return None


def _predictions(bundle: Any) -> dict[str, tuple[dict, list[dict]]]:
    bundle = _obj(bundle, "predictions bundle")
    if bundle.get("schema_version") != PREDICTION_VERSION:
        raise ValueError("unsupported predictions schema_version")
    result = {}
    for record in _arr(bundle.get("records"), "predictions.records"):
        record = _obj(record, "prediction record")
        record_id = _str(record.get("record_id"), "prediction record_id")
        if record_id in result:
            raise ValueError(f"duplicate prediction record_id: {record_id!r}")
        analysis = _obj(record.get("analysis", {}), f"prediction {record_id}.analysis")
        routing = _arr(analysis.get("routing", []), f"prediction {record_id}.routing")
        ranks = set()
        for candidate in routing:
            candidate = _obj(candidate, f"prediction {record_id} candidate")
            rank = _int(candidate.get("rank"), f"prediction {record_id} rank", 1)
            if rank in ranks:
                raise ValueError(f"prediction {record_id}: duplicate rank {rank}")
            ranks.add(rank)
            _route_key(candidate.get("route"), f"prediction {record_id} rank {rank} route")
        if ranks != set(range(1, len(routing) + 1)):
            raise ValueError(f"prediction {record_id}: ranks must be consecutive from 1")
        result[record_id] = (record, sorted(routing, key=lambda c: c["rank"]))
    return result


def _cases(manifest: Any) -> list[dict]:
    manifest = _obj(manifest, "manifest")
    if manifest.get("schema_version") != MANIFEST_VERSION:
        raise ValueError("unsupported manifest schema_version")
    cases = _arr(manifest.get("cases"), "manifest.cases")
    ids, records = set(), set()
    for case in cases:
        case = _obj(case, "manifest case")
        case_id = _str(case.get("case_id"), "case_id")
        record_id = _str(case.get("record_id"), "record_id")
        _str(case.get("split"), f"{case_id}.split")
        if case_id in ids:
            raise ValueError(f"duplicate case_id: {case_id!r}")
        if record_id in records:
            raise ValueError(f"duplicate manifest record_id: {record_id!r}")
        ids.add(case_id)
        records.add(record_id)
        labels = _obj(case.get("labels", {}), f"{case_id}.labels")
        if "routes" in labels:
            routes = _arr(labels["routes"], f"{case_id}.labels.routes")
            if not routes:
                raise ValueError(f"{case_id}.labels.routes must contain an acceptable route")
            keys = [_route_key(r, f"{case_id}.labels.routes") for r in routes]
            if len(keys) != len(set(keys)):
                raise ValueError(f"{case_id}: duplicate acceptable route")
        if "gm_families" in labels:
            families = _arr(labels["gm_families"], f"{case_id}.labels.gm_families")
            if not families:
                raise ValueError(f"{case_id}.labels.gm_families must not be empty")
            values = [_int(f, f"{case_id}.gm_family", 0, 15) for f in families]
            if len(values) != len(set(values)):
                raise ValueError(f"{case_id}: duplicate GM family")
        if "observed_pitch_offset_semitones" in labels:
            _number(labels["observed_pitch_offset_semitones"], f"{case_id}.pitch label")
        if "component_offsets" in labels:
            _offsets(labels["component_offsets"], f"{case_id}.component_offsets")
        if "abstain" in labels and not isinstance(labels["abstain"], bool):
            raise ValueError(f"{case_id}.labels.abstain must be boolean")
    return cases


class _Metric:
    def __init__(self) -> None:
        self.numerator = 0.0
        self.denominator = 0
        self.eligible_cases = 0
        self.predicted_cases = 0

    def add(self, amount: float, *, predicted: bool = True) -> None:
        self.numerator += amount
        self.denominator += 1
        self.eligible_cases += 1
        self.predicted_cases += int(predicted)

    def missing(self) -> None:
        self.eligible_cases += 1

    def report(self) -> dict:
        return {
            "value": self.numerator / self.denominator if self.denominator else None,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "eligible_cases": self.eligible_cases,
            "predicted_cases": self.predicted_cases,
        }


def _score(cases: list[dict], predictions: dict, top_k: tuple[int, ...],
           reviews: list[dict]) -> dict:
    names = ("route_accuracy", "gm_family_accuracy", "abstention_accuracy",
             "abstention_precision", "abstention_recall", "ood_false_accept_rate",
             "pitch_mae_semitones", "chord_note_precision", "chord_note_recall",
             "human_blinded_preference_agreement")
    metrics = {name: _Metric() for name in names}
    metrics.update({f"top_{k}_candidate_accuracy": _Metric() for k in top_k})
    first_by_case = {}
    predicted_count = 0

    for case in cases:
        case_id = case["case_id"]
        labels = case.get("labels", {})
        entry = predictions.get(case["record_id"])
        record, candidates = entry if entry else ({}, [])
        predicted = bool(candidates)
        predicted_count += predicted
        first = _route_key(candidates[0]["route"], f"{case_id} top route") if predicted else None
        first_by_case[case_id] = first

        if "routes" in labels:
            acceptable = {_route_key(r, f"{case_id} acceptable route") for r in labels["routes"]}
            acceptable_kinds = {route[0] for route in acceptable}
            metrics["route_accuracy"].add(
                float(first is not None and first[0] in acceptable_kinds), predicted=predicted)
            for k in top_k:
                keys = {_route_key(c["route"], f"{case_id} candidate route") for c in candidates[:k]}
                metrics[f"top_{k}_candidate_accuracy"].add(float(bool(keys & acceptable)),
                                                            predicted=predicted)

        if "gm_families" in labels:
            family = first[1] // 8 if first is not None and first[0] == "gm_program" else None
            metrics["gm_family_accuracy"].add(float(family in labels["gm_families"]),
                                               predicted=predicted)

        if "abstain" in labels:
            truth = labels["abstain"]
            called = first == ("abstain",)
            metrics["abstention_accuracy"].add(float(predicted and called == truth),
                                                predicted=predicted)
            if called:
                metrics["abstention_precision"].add(float(truth))
            if truth:
                metrics["abstention_recall"].add(float(called), predicted=predicted)
                if predicted:
                    metrics["ood_false_accept_rate"].add(float(not called))
                else:
                    metrics["ood_false_accept_rate"].missing()

        if "observed_pitch_offset_semitones" in labels:
            offset = _pitch(record, candidates) if entry else None
            if offset is None:
                metrics["pitch_mae_semitones"].missing()
            else:
                metrics["pitch_mae_semitones"].add(
                    abs(offset - labels["observed_pitch_offset_semitones"]))

        if "component_offsets" in labels:
            composite = _obj(record.get("analysis", {}), f"{case_id}.analysis").get("composite") if entry else None
            if composite is None:
                metrics["chord_note_precision"].missing()
                metrics["chord_note_recall"].missing()
            else:
                components = _arr(_obj(composite, f"{case_id}.composite").get("components"),
                                  f"{case_id}.components")
                actual = Counter(_int(_obj(c, "component").get("semitone_offset"),
                                      f"{case_id} component offset") for c in components)
                expected = _offsets(labels["component_offsets"], f"{case_id}.component_offsets")
                hits = sum((actual & expected).values())
                precision = hits / sum(actual.values()) if actual else float(not expected)
                recall = hits / sum(expected.values()) if expected else float(not actual)
                metrics["chord_note_precision"].add(precision)
                metrics["chord_note_recall"].add(recall)

    for review in reviews:
        case_id = review["case_id"]
        if case_id in first_by_case:
            first = first_by_case[case_id]
            metrics["human_blinded_preference_agreement"].add(
                float(first == review["preferred_route_key"]), predicted=first is not None)

    return {
        "metrics": {name: metric.report() for name, metric in metrics.items()},
        "coverage": {"cases": len(cases), "predicted_cases": predicted_count,
                     "records_present": sum(c['record_id'] in predictions for c in cases)},
    }


def evaluate_benchmark(manifest: dict, predictions_bundle: dict, *,
                       top_k: tuple[int, ...] = (1, 3, 5),
                       reviews: list[dict] | None = None) -> dict:
    """Score explicit labels, returning null values for empty metric cohorts.

    A blinded review is an object with case_id, blinded: true, and a
    preferred_route route object. Unblinded reviews are ignored.
    """
    if not isinstance(top_k, (tuple, list)) or not top_k:
        raise ValueError("top_k must be a nonempty sequence")
    top_k = tuple(_int(k, "top_k", 1) for k in top_k)
    if len(set(top_k)) != len(top_k):
        raise ValueError("top_k contains duplicates")
    cases = _cases(manifest)
    predictions = _predictions(predictions_bundle)
    by_id = {c["case_id"]: c for c in cases}
    parsed_reviews = []
    for review in _arr(reviews if reviews is not None else [], "reviews"):
        review = _obj(review, "review")
        case_id = _str(review.get("case_id"), "review.case_id")
        if case_id not in by_id:
            raise ValueError(f"review refers to unknown case_id: {case_id!r}")
        if review.get("blinded") is not True:
            continue
        parsed_reviews.append({"case_id": case_id,
                               "preferred_route_key": _route_key(
                                   review.get("preferred_route"), "review.preferred_route")})
    overall = _score(cases, predictions, top_k, parsed_reviews)
    return {
        "schema_version": REPORT_VERSION,
        **overall,
        "by_split": {
            split: _score([c for c in cases if c["split"] == split], predictions, top_k,
                          [r for r in parsed_reviews if by_id[r["case_id"]]["split"] == split])
            for split in sorted({c["split"] for c in cases})
        },
    }


def load_review_preferences(path: Path) -> list[dict]:
    """Read an explicit JSON array or the review workflow's preference JSONL."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    value = json.loads(text)
    if not isinstance(value, list):
        raise ValueError("review preferences must be a JSON array or JSONL records")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    reviews = load_review_preferences(args.reviews) if args.reviews else None
    report = evaluate_benchmark(manifest, predictions, reviews=reviews)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
