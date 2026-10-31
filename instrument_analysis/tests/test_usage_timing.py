import json
from pathlib import Path
import tempfile
import unittest
from instrument_analysis.usage_timing import audit_usage_timing


class UsageTimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "usage.json"
        self.note = {"raw_sequence_note": 60, "track_id": 0, "duration": 100,
            "time": {"value": 48, "unit": "midi_tick", "timebase": 96},
            "pitch_context_complete": True,
            "provenance": {"parameters": {"render_context": {"gate_duration_ticks": 96,
                "velocity_0_127": 100, "effects_complete": False}}}}
        self.context = {"sequence_id": 1, "ticks_per_quarter": 96,
            "default_microseconds_per_quarter": 500000,
            "tempo_events": [{"tick": 96, "microseconds_per_quarter": 1000000}],
            "basis": "legacy_ff3_midi_conversion", "effects_complete": False}
        self.bundle = {"schema_version": "instrument-analysis/v1", "records": [{
            "record_id": "source-1", "source": {}, "identity": {}, "observation": {"usage": {
                "provenance": {"parameters": {"sequence_render_contexts": [self.context]}},
                "sequences": [{"sequence_id": 1, "track_ids": [0], "note_occurrences": [self.note]}]}}}]}
    def audit(self, **kwargs):
        self.path.write_text(json.dumps(self.bundle))
        return audit_usage_timing(self.path, source_record_id="source-1", sequence_id=kwargs.get("sequence_id", 1))
    def test_cross_tempo_gate_and_raw_duration_are_separate(self):
        report = self.audit()
        event = report["events"][0]
        self.assertEqual(event["start_seconds"], .25)
        self.assertEqual(event["duration_seconds"], .75)
        self.assertEqual(event["source_step_duration_ticks"], 100)
        self.assertEqual(event["converter_gate_ticks"], 96)
        self.assertIsNone(event["sounding_midi_note"])
        self.assertEqual(report["status"], "blocked")
        self.assertIn("sounding_pitch_not_grounded", report["blocking_reasons"])
        self.assertIn("source_effects_not_fully_normalized", report["blocking_reasons"])
    def test_missing_context_typed_ids_and_tempo_conflicts_reject(self):
        with self.assertRaises(ValueError): self.audit(sequence_id="1")
        self.context["tempo_events"].append({"tick": 96, "microseconds_per_quarter": 250000})
        with self.assertRaises(ValueError): self.audit()
        self.context["tempo_events"].pop()
        self.note["provenance"]["parameters"]["render_context"].pop("gate_duration_ticks")
        with self.assertRaises(ValueError): self.audit()
    def test_known_timeline_still_requires_source_audio(self):
        self.context.update(basis="verified_timing", effects_complete=True)
        self.note["sounding_midi_note"] = 61
        self.note["provenance"]["parameters"]["render_context"]["effects_complete"] = True
        self.assertEqual(self.audit()["status"], "timeline_available_requires_source_audio_binding")


if __name__ == "__main__": unittest.main()
