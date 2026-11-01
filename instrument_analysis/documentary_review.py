"""Auditable union of documentary proposals and independent acoustic retrieval."""
from __future__ import annotations

from copy import deepcopy
import html
import json
from pathlib import Path

from .documentary_shortlist import _baseline, _bound, _hash, _origin, _read, _ref, _rule
from .ranking import prepared_reference_index, _index_for_query
from .comparisons import build_matched_comparison
from .review import build_review_package
from .rendering import _staging_directory


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def build_documentary_review(shortlist_path, *, output_dir, audio_top_k=3):
    """Publish matched candidate auditions without combining branch priorities.

    Documentary proposals are not human labels. Acoustic abstention retains its
    candidate list; source-function or unknown-pitch gates yield metadata only.
    """
    if type(audio_top_k) is not int or not 1 <= audio_top_k <= 128:
        raise ValueError("audio_top_k must be 1..128")
    shortlist_path = Path(shortlist_path).resolve(strict=True)
    shortlist_hash = _hash(shortlist_path)
    shortlist = _read(shortlist_path)
    if shortlist.get("schema_version") != "instrument-documentary-shortlist/v1":
        raise ValueError("unsupported documentary shortlist")
    source_id = shortlist.get("source_record_id")
    inputs = shortlist.get("inputs", {})
    if not isinstance(inputs, dict) or not {"bindings", "index", "catalog", "source_rom", "source_brr"} <= set(inputs):
        raise ValueError("shortlist lacks bound inputs")
    bound = {key: _bound(ref, shortlist_path.parent) for key, ref in inputs.items()}
    initial = {path: _hash(path) for path in bound.values()}
    initial[shortlist_path] = shortlist_hash
    binding, catalog_path = _origin(bound["bindings"], source_id)
    if (catalog_path != bound["catalog"] or binding.get("origin") != shortlist.get("origin")
            or binding["sample"]["rom"]["sha256"] != inputs["source_rom"]["sha256"]
            or binding["artifact"]["sha256"] != inputs["source_brr"]["sha256"]):
        raise ValueError("shortlist source/origin bindings differ")
    rule = _rule(binding)
    if shortlist.get("rule") != rule or [c.get("route", {}).get("program_0based") for c in shortlist.get("candidates", [])] != rule["predeclared_programs"]:
        raise ValueError("documentary shortlist differs from predeclared source rule")
    lead = binding["lead_key"]
    folder = (bound["bindings"].parent / lead).resolve()
    if folder.parent != bound["bindings"].parent:
        raise ValueError("invalid binding lead path")
    binding_path, record_path = folder / "binding.json", folder / "record.json"
    if _read(binding_path) != binding:
        raise ValueError("per-lead binding differs from binding report")
    record = _read(record_path)
    bundle_path = bound["bindings"].parent / "records.json"
    bundle = _read(bundle_path)
    records = [r for r in bundle.get("records", []) if r.get("record_id") == source_id]
    if len(records) != 1 or records[0] != record or record.get("record_id") != source_id:
        raise ValueError("canonical binding record identity mismatch")
    sample_hash = record.get("observation", {}).get("synthesis", {}).get("sample", {}).get("sha256", {}).get("value")
    if sample_hash != binding["sample"]["brr"]["sha256"]:
        raise ValueError("canonical record sample hash differs from binding")
    for path in (binding_path, record_path, bundle_path):
        initial[path] = _hash(path)
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with _staging_directory(output.parent) as workspace:
        stage = workspace / "publish"
        stage.mkdir()
        with prepared_reference_index(bound["index"]) as prepared:
            index = _index_for_query(bound["index"], prepared)
            baseline, gate = _baseline(bound.get("audio_diagnostics"), bound["index"], binding)
            if gate != shortlist.get("audition_gate"):
                raise ValueError("shortlist audition gate differs from verified baseline")
            if baseline is not None:
                if baseline.get("query", {}).get("source_record_id") != source_id:
                    raise ValueError("independent baseline source mismatch")
                for key, ref in baseline.get("query", {}).items():
                    if isinstance(ref, dict) and "path" in ref and "sha256" in ref:
                        path = _bound(ref, bound["audio_diagnostics"].parent)
                        initial[path] = _hash(path)
            rows = baseline.get("candidates", []) if baseline else []
            union = {}
            def select(ref, membership, documentary=None, acoustic=None):
                matches = [r for r in index["references"] if all(r.get(k) == ref.get(k)
                    for k in ("probe_id", "soundfont_sha256", "bank", "route"))]
                phase = baseline["query"].get("event_boundaries_seconds")
                if not isinstance(phase, dict):
                    raise ValueError("eligible baseline lacks phase boundaries")
                tolerance = max(.01, baseline["query"].get("alignment_uncertainty_seconds", 0))
                matches = [r for r in matches if
                    abs((gate["calibrated_midi_note"] - r["note"]) * 100) <= 25
                    and abs(phase["note_off"] - phase["note_on"] - r["conditions"]["hold_seconds"]) <= tolerance
                    and abs(phase["end"] - phase["note_off"] - r["conditions"]["tail_seconds"]) <= tolerance
                    and r["features"].get("valid") and not r["features"].get("silent")
                    and not r["features"].get("event_clipped_sample_count", 0)]
                if len(matches) != 1:
                    raise ValueError("candidate is absent or ambiguous in verified reference index")
                actual = matches[0]
                if membership == "documentary" and actual != ref:
                    raise ValueError("documentary reference variant differs from verified index")
                key = (actual["manifest_sha256"], actual["probe_id"])
                candidate = union.setdefault(key, {"reference": deepcopy(actual), "membership": [],
                    "documentary_priority": None, "acoustic": None})
                if membership not in candidate["membership"]:
                    candidate["membership"].append(membership)
                if documentary is not None:
                    candidate["documentary_priority"] = documentary["documentary_priority"]
                if acoustic is not None:
                    candidate["acoustic"] = {"rank": acoustic["rank"], "distance": acoustic["distance"]}
            if gate["status"] == "eligible":
                for documentary in shortlist.get("candidates", []):
                    for ref in documentary.get("reference_variants", []):
                        if ref.get("route") != documentary.get("route"):
                            raise ValueError("documentary candidate route differs from reference")
                        row = next((r for r in rows if r.get("route") == ref.get("route")), None)
                        if row is None or any(row.get(k) != ref.get(k) for k in ("probe_id", "soundfont_sha256", "bank")):
                            raise ValueError("documentary reference differs from independent baseline selection")
                        select(ref, "documentary", documentary, row)
                for row in sorted(rows, key=lambda r: r["rank"])[:audio_top_k]:
                    select(row, "audio_only", acoustic=row)
            report = {"schema_version": "instrument-documentary-review-report/v1", "source_record_id": source_id,
                "status": "playable" if union else "metadata_only", "inputs": {"shortlist": _ref(shortlist_path), **deepcopy(inputs)},
                "origin": deepcopy(binding["origin"]), "documentary_rule": deepcopy(shortlist["rule"]),
                "audition_gate": gate, "independent_audio_baseline": deepcopy(baseline),
                "documentary_shortlist": deepcopy(shortlist), "candidates": [], "no_fused_winner": True,
                "evaluation_scope": "Development pilot; split/cohort audit is external. No accuracy claim or threshold fitting.",
                "human_label_count": 0,
                "functional": deepcopy(baseline.get("functional", {"status": "not_scored"})) if baseline else {"status": "not_scored"}}
            specs = []
            manifest_map = {entry["sha256"]: entry for entry in index["manifests"]}
            for ordinal, candidate in enumerate(union.values()):
                ref = candidate["reference"]
                query = baseline["query"]
                native_path = _bound(query["native_manifest"], bound["audio_diagnostics"].parent)
                calibration_path = _bound(query["pitch_calibration"], bound["audio_diagnostics"].parent)
                gm_path = _bound(manifest_map[ref["manifest_sha256"]], bound["index"].parent)
                cid = f"candidate-{ordinal:03d}-p{ref['route']['program_0based']:03d}-b{ref['bank']:05d}"
                comparison_dir = stage / "comparisons" / cid
                build_matched_comparison(native_path, gm_path, gm_probe_id=ref["probe_id"],
                    pitch_calibration_path=calibration_path, output_dir=comparison_dir)
                comparisons = list(comparison_dir.glob("*.comparison.json"))
                if len(comparisons) != 1:
                    raise ValueError("matched comparison did not publish exactly one manifest")
                comparison_path = comparisons[0]
                evidence = {"membership": candidate["membership"], "documentary_priority": candidate["documentary_priority"],
                    "acoustic": candidate["acoustic"], "functional": deepcopy(report["functional"]),
                    "review_status": "proposed_unreviewed", "no_fused_winner": True}
                comparison_ref = {"path": comparison_path.relative_to(stage).as_posix(), "sha256": _hash(comparison_path)}
                report["candidates"].append({"candidate_id": cid, "route": deepcopy(ref["route"]),
                    "reference": deepcopy(ref), "comparison": comparison_ref, "evidence": evidence})
                specs.append({"candidate_id": cid, "comparison": comparison_ref, "evidence": evidence})
            _write(stage / "report.json", report)
            package = None
            if specs:
                spec = {"schema_version": "instrument-review-spec/v1", "source_record_id": source_id,
                    "case_id": lead + "-combined-branches", "blind": False, "ordering_seed": 0,
                    "source": deepcopy(baseline["query"]["native_manifest"]), "analysis": _ref(record_path),
                    "diagnostics": {"path": "report.json", "sha256": _hash(stage / "report.json")},
                    "origin_binding": _ref(binding_path), "candidates": specs}
                _write(stage / "spec.json", spec)
                package = build_review_package(stage / "spec.json", stage / "review")
        # The prepared context rechecks the entire reference corpus before publication.
        for path, digest in initial.items():
            if _hash(path) != digest:
                raise ValueError("documentary review input changed during construction")
        manifest = {"schema_version": "instrument-documentary-review/v1", "source_record_id": source_id,
            "status": report["status"], "inputs": {"shortlist": _ref(shortlist_path), **deepcopy(inputs)},
            "generator": {"module": "instrument_analysis.documentary_review", "module_sha256": _hash(Path(__file__))},
            "audio_top_k": audio_top_k, "candidate_count": len(specs), "human_label_count": 0,
            "no_fused_winner": True, "report": {"path": "report.json", "sha256": _hash(stage / "report.json")},
            "review": None if package is None else {"path": "review/manifest.json", "sha256": _hash(stage / "review/manifest.json"), "package_id": package["package_id"]}}
        _write(stage / "manifest.json", manifest)
        link = '<a href="review/index.html">Open combined review</a>' if specs else "Metadata only: calibrated playable comparisons unavailable."
        (stage / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>Documentary and acoustic review</title><h1>' + html.escape(source_id) + '</h1><p>' + link + '</p><p>Documentary priority and acoustic rank remain separate; no fused winner and no human labels.</p><a href="report.json">Auditable branch report</a>', encoding="utf-8")
        stage.rename(output)
    return manifest


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shortlist", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--audio-top-k", type=int, default=3)
    args = parser.parse_args(argv)
    result = build_documentary_review(args.shortlist, output_dir=args.output, audio_top_k=args.audio_top_k)
    print(json.dumps({"status": result["status"], "candidate_count": result["candidate_count"]}))


if __name__ == "__main__":
    main()
