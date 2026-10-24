"""Queries over raw decoder usage bundles, independent of curated GM mappings."""

from __future__ import annotations

from typing import Any


def query_usage(
    bundle: dict[str, Any],
    *,
    source_instrument_id: int | str | None = None,
    sequence_id: int | str | None = None,
    track_id: int | str | None = None,
) -> list[dict[str, Any]]:
    """Return one row per matching instrument/sequence with selected raw notes.

    IDs use exact value and type matching: an integer ID and its string spelling
    are different source identities. Returned source/identity/occurrence objects
    reference the bundle; callers should treat the result as read-only.
    """
    def matches(value: Any, wanted: Any) -> bool:
        return type(value) is type(wanted) and value == wanted

    rows = []
    for record in bundle['records']:
        identity = record['identity']
        if source_instrument_id is not None and not matches(
            identity['source_instrument_id'], source_instrument_id
        ):
            continue
        for sequence in record['observation'].get('usage', {}).get('sequences', []):
            if sequence_id is not None and not matches(sequence['sequence_id'], sequence_id):
                continue
            tracks = sequence.get('track_ids', [])
            if track_id is not None:
                tracks = [t for t in tracks if matches(t, track_id)]
                if not tracks:
                    continue
            occurrences = sequence.get('note_occurrences', [])
            if track_id is not None:
                occurrences = [n for n in occurrences if matches(n.get('track_id'), track_id)]
            rows.append({
                'record_id': record['record_id'],
                'source': record['source'],
                'identity': identity,
                'sequence_id': sequence['sequence_id'],
                'track_ids': list(tracks),
                'note_occurrences': list(occurrences),
            })
    return rows
