"""Benchmark assets are original or private-input recipes; never bundled game rips."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import wave

from instrument_analysis.benchmark import evaluate_benchmark
from instrument_analysis.fixtures import SYNTHETIC_CASES, generate_fixtures

ROOT=Path(__file__).resolve().parents[2]


class BenchmarkAssetTests(unittest.TestCase):
    def test_original_fixtures_are_reproducible_and_modules_are_structured(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first=generate_fixtures(a)
            second=generate_fixtures(b)
            self.assertEqual(first,second)
            self.assertEqual(set(SYNTHETIC_CASES),{x['case_id'] for x in first['fixtures']})
            for item in first['fixtures']:
                path=Path(a)/item['wav']['path']
                self.assertEqual(item['wav']['sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
                with wave.open(str(path),'rb') as wav:
                    self.assertEqual((1,2,22050,22050),(wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),wav.getnframes()))
                if 'module' in item:
                    data=(Path(a)/item['module']['path']).read_bytes()
                    self.assertEqual(b'M.K.',data[1080:1084])
                    self.assertEqual(8192,struct.unpack_from('>H',data,42)[0]*2)
                    self.assertEqual(1084+1024+8192,len(data))
                    self.assertEqual(bytes([1,0xac,0x10,0]),data[1084:1088])
                self.assertEqual('CC0-1.0',item['license'])
            with self.assertRaises(FileExistsError):
                generate_fixtures(a)

    def test_pilot_manifest_keeps_unknowns_and_sanity_splits_explicit(self):
        manifest=json.loads((ROOT/'benchmarks/instruments/pilot-v1.json').read_text())
        by_id={x['case_id']:x for x in manifest['cases']}
        self.assertEqual({},by_id['gradius3-fm-ambiguous']['labels'])
        self.assertEqual('historical_curated',by_id['ff4-flute']['label_basis'])
        self.assertEqual('same-bank-sanity',by_id['gm-melodic-percussion-control']['split'])
        self.assertEqual([-5,0,4],by_id['tracker-major-inversion']['labels']['component_offsets'])
        self.assertTrue(by_id['abstain-silence']['labels']['abstain'])
        report=evaluate_benchmark(manifest,{'schema_version':'instrument-analysis/v1','records':[]})
        self.assertEqual(0,report['coverage']['predicted_cases'])
        self.assertIn('unlabeled-review',report['by_split'])
        try:
            from jsonschema import Draft202012Validator
            from referencing import Registry,Resource
        except ImportError:
            return
        observation=json.loads((ROOT/'schemas/instrument-analysis-v1.schema.json').read_text())
        schema=json.loads((ROOT/'schemas/instrument-benchmark-v1.schema.json').read_text())
        registry=Registry().with_resource(observation['$id'],Resource.from_contents(observation))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema,registry=registry).validate(manifest)
        report_schema=json.loads((ROOT/'schemas/instrument-benchmark-report-v1.schema.json').read_text())
        Draft202012Validator.check_schema(report_schema)
        Draft202012Validator(report_schema).validate(report)


if __name__=='__main__':
    unittest.main()
