"""Audit explicit sample and source-lineage relationships across data splits."""
from __future__ import annotations

import re
import hashlib
from pathlib import Path

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_FAMILY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def validate_origin_splits(cases, *, lineage_links=None):
    """Reject development/held-out leakage in known connected relationships.

    Family IDs describe explicitly curated source lineage, never GM classes.
    This does not discover unknown shared samples or fit ranking thresholds.
    """
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a nonempty list")
    if lineage_links is None:
        lineage_links = []
    if not isinstance(lineage_links, list):
        raise ValueError("lineage_links must be a list")
    parent = {}
    def find(key):
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key
    def union(left, right):
        a, b = find(left), find(right)
        parent[max(a, b)] = min(a, b)
    ids, families = set(), set()
    required = {"case_id", "record_id", "split", "sample_sha256", "origin_family_id"}
    for case in cases:
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("each case requires exactly the split audit fields")
        for field in ("case_id", "record_id"):
            if not isinstance(case[field], str) or not case[field].strip():
                raise ValueError(f"{field} must be a nonempty string")
        if case["case_id"] in ids:
            raise ValueError(f"duplicate case_id: {case['case_id']}")
        ids.add(case["case_id"])
        if case["split"] not in ("development", "held_out"):
            raise ValueError("unknown split")
        if not isinstance(case["sample_sha256"], str) or not _SHA.fullmatch(case["sample_sha256"]):
            raise ValueError("sample_sha256 must be a lowercase SHA256")
        family = case["origin_family_id"]
        if not isinstance(family, str) or not _FAMILY.fullmatch(family) or family in {"unknown", "unbound", "none"}:
            raise ValueError("origin_family_id requires explicit normalized curated lineage")
        families.add(family)
        union("case:" + case["case_id"], "record:" + case["record_id"])
        union("case:" + case["case_id"], "family:" + family)
        union("case:" + case["case_id"], "sample:" + case["sample_sha256"])
    evidence_rows = []
    for link in lineage_links:
        if not isinstance(link, dict) or set(link) != {"left_family_id", "right_family_id", "evidence"}:
            raise ValueError("lineage link requires two families and explicit evidence")
        left, right = link["left_family_id"], link["right_family_id"]
        if not isinstance(left, str) or not isinstance(right, str) or left not in families or right not in families:
            raise ValueError("lineage link references an unknown family")
        if left == right:
            raise ValueError("lineage link must join distinct families")
        evidence = link["evidence"]
        if not isinstance(evidence, dict):
            raise ValueError("lineage evidence must be an object")
        if set(evidence) == {"path", "sha256"}:
            if not isinstance(evidence["path"], str) or not evidence["path"].strip() or not isinstance(evidence["sha256"], str) or not _SHA.fullmatch(evidence["sha256"]):
                raise ValueError("file evidence requires path and lowercase SHA256")
            proof = Path(evidence["path"]).resolve()
            if not proof.is_file() or hashlib.sha256(proof.read_bytes()).hexdigest() != evidence["sha256"]:
                raise ValueError("lineage file evidence is missing or changed")
        elif set(evidence) == {"url", "locator", "reason"}:
            if any(not isinstance(evidence[k], str) or not evidence[k].strip() for k in evidence) or not evidence["url"].startswith(("https://", "http://")):
                raise ValueError("URL evidence requires URL, locator and reason")
        else:
            raise ValueError("unsupported lineage evidence fields")
        union("family:" + left, "family:" + right)
        evidence_rows.append({"left_family_id": left, "right_family_id": right, "evidence": dict(evidence)})
    connected = {}
    for case in cases:
        connected.setdefault(find("case:" + case["case_id"]), []).append(case)
    groups = []
    for members in connected.values():
        case_ids = sorted(c["case_id"] for c in members)
        splits = sorted({c["split"] for c in members})
        if len(splits) > 1:
            raise ValueError(f"split leakage in connected group {case_ids}: {splits}")
        groups.append({"case_ids": case_ids, "record_ids": sorted({c["record_id"] for c in members}),
            "sample_sha256s": sorted({c["sample_sha256"] for c in members}),
            "origin_family_ids": sorted({c["origin_family_id"] for c in members}), "splits": splits})
    groups.sort(key=lambda g: g["case_ids"])
    return {"schema_version": "instrument-origin-split-audit/v1", "status": "valid",
        "provenance": {"scope": "Explicit canonical source identities, known sample hashes, curated origin families and documented lineage links only; unknown sharing is not discovered.",
            "family_semantics": "Source sample lineage, not GM instrument class.",
            "threshold_fitting": False, "baseline_input_policy": "Keep lineage and documentary claims outside blind audio-ranking inputs."},
        "groups": groups, "lineage_links": evidence_rows,
        "counts": {"cases": len(cases), "groups": len(groups),
            "development_cases": sum(c["split"] == "development" for c in cases),
            "eligible_held_out_cases": sum(c["split"] == "held_out" for c in cases),
            "held_out_groups": sum(g["splits"] == ["held_out"] for g in groups)}}
