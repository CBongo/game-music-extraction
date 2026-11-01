"""Documentary scope, uncertainty and immutable byte-binding pilot tests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from instrument_analysis.origin_pilot import build_origin_pilot, _canonical

ROOT=Path(__file__).resolve().parents[2]

class OriginPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.ff4=self.root/'four.smc';self.ff4.write_bytes(b'private-four')
        self.ff6=self.root/'six.smc';self.ff6.write_bytes(b'private-six')
        self.catalog=json.loads((ROOT/'docs/data/instrument-source-references.json').read_text())
        self.path=self.root/'catalog.json';self.save()
    def save(self): self.path.write_bytes(_canonical(self.catalog))
    def extract(self, game, rom, sample_id):
        audio=bytes([sample_id])*9
        sha=hashlib.sha256(audio).hexdigest()
        return {'schema_version':'instrument-rom-sample/v1','game':game,'record_id':f'snes:{"ff4" if game=="Final Fantasy IV" else "ff6"}-us:global-sample-{sample_id:02x}',
            'rom':{'path':str(rom),'payload_sha256':'a'*64,'sha256':hashlib.sha256(Path(rom).read_bytes()).hexdigest(),'header_bytes':512,'size':1049088},
            'instrument':{'global_sample_id':sample_id,'table_index':sample_id if game=='Final Fantasy IV' else sample_id-1,'descriptor_sha256':'c'*64},
            'brr':{'file_offset':0x123,'length_bytes':9,'sha256':sha,'frame_count':1,'decoded_sample_count':16,'loop_relative_bytes':0,'looping':False,'end_header':1},'evidence':[]},audio
    def build(self):
        with patch('instrument_analysis.origin_pilot.extract_ff4_sample',side_effect=lambda p,n:self.extract('Final Fantasy IV',p,n)) as ff4, patch('instrument_analysis.origin_pilot.extract_ff6_sample',side_effect=lambda p,n:self.extract('Final Fantasy VI',p,n)) as ff6:
            result=build_origin_pilot(self.path,ff4_rom_path=self.ff4,ff6_rom_path=self.ff6,output_dir=self.root/'output')
            self.assertEqual([c.args[1] for c in ff4.call_args_list],[7])
            self.assertEqual([c.args[1] for c in ff6.call_args_list],[7,0x16,0x1f,0x30])
            return result
    def test_five_bindings_keep_origins_distinct_and_tuning_unknown(self):
        before=self.path.read_bytes();report=self.build()
        self.assertEqual(before,self.path.read_bytes())
        rows={b['lead_key']:b for b in report['bindings']}
        self.assertEqual(len(rows),5)
        self.assertNotEqual(rows['ff4-flute-07']['record_id'],rows['ff6-flute-07']['record_id'])
        for lead,b in rows.items():
            self.assertEqual(b['status'],'bound')
            self.assertEqual(b['origin']['authority'],'preferred_documentary')
            original=next(c for c in self.catalog['pilot_claims'] if c['lead_key']==lead)
            self.assertEqual(b['origin']['claim'],original)
            self.assertEqual(b['origin']['external_report_state'],original['external_report_state'])
            self.assertIsNone(b['tuning']['game_unity_key']);self.assertIsNone(b['tuning']['observed_pitch_offset'])
            self.assertIsNone(b['physical_dsp_srcn'])
            self.assertTrue((self.root/'output'/b['artifact']['file']).is_file())
            self.assertEqual(json.loads((self.root/'output'/lead/'sample-metadata.json').read_text()),b['sample'])
            if 'flute' in lead:self.assertEqual(b['candidate_replacement']['status'],'proposed_unreviewed')
            else:self.assertNotIn('candidate_replacement',b)
        records=json.loads((self.root/'output/records.json').read_text())
        for r in records['records']:
            self.assertNotIn('pitch',r['observation']);self.assertNotIn('analysis',r)
        self.assertNotIn('.pl',json.dumps(records))
        self.assertIn('Probably not it',json.dumps(rows['ff6-whistle-1f']))
        self.assertIn('6.5 semitones',json.dumps(rows['ff6-claves-30']))
        try:from jsonschema import Draft202012Validator
        except ImportError:return
        schema=json.loads((ROOT/'schemas/instrument-analysis-v1.schema.json').read_text())
        Draft202012Validator(schema).validate(records)
    def test_tamper_and_rehashed_scope_mismatch_fail_before_extraction(self):
        for key,value in [('raw_cells',['Flute','$07','tamper','','','','']),('game_title','Final Fantasy VI'),('game_section','Section 2: remake')]:
            original=deepcopy(self.catalog)
            self.catalog['pilot_claims'][0][key]=value;self.save()
            with patch('instrument_analysis.origin_pilot.extract_ff4_sample') as extract:
                with self.assertRaises(ValueError):build_origin_pilot(self.path,ff4_rom_path=self.ff4,ff6_rom_path=self.ff6,output_dir=self.root/'output')
                extract.assert_not_called()
            self.catalog=original
        self.catalog['pilot_claims'][0]['raw_cells'][1]='$08'
        self.rehash_first();self.save()
        with self.assertRaises(ValueError):self.build()
    def rehash_first(self):
        c=self.catalog['pilot_claims'][0]
        selected={k:c[k] for k in ('sheet_id','game_title','game_section','raw_cells')}
        selected['spreadsheet_id']=next(s for s in self.catalog['sources'] if s['key']==c['source_key'])['spreadsheet_id']
        c['selection_sha256']=hashlib.sha256(_canonical(selected)).hexdigest()
    def test_card_escapes_documentary_cells_and_refuses_overwrite(self):
        self.catalog['pilot_claims'][0]['raw_cells'][4]='<script>alert("quoted")</script>'
        self.rehash_first();self.save();self.build()
        card=(self.root/'output/ff4-flute-07/index.html').read_text()
        self.assertNotIn('<script>',card);self.assertIn('&lt;script&gt;',card)
        self.assertNotIn('autoplay',card)
        snapshot=(self.root/'output/bindings.json').read_bytes()
        with self.assertRaises(FileExistsError):self.build()
        self.assertEqual(snapshot,(self.root/'output/bindings.json').read_bytes())
    def test_extractor_hash_mismatch_leaves_no_publication(self):
        metadata,audio=self.extract('Final Fantasy IV',self.ff4,7)
        with patch('instrument_analysis.origin_pilot.extract_ff4_sample',return_value=(metadata,b'bad')):
            with self.assertRaises(ValueError):build_origin_pilot(self.path,ff4_rom_path=self.ff4,ff6_rom_path=self.ff6,output_dir=self.root/'output')
        self.assertFalse((self.root/'output').exists())
        self.assertFalse(list(self.root.glob('.origin-pilot-*')))

    def test_source_rom_changes_during_later_extraction_reject_publication(self):
        def later(rom,n):
            result=self.extract('Final Fantasy VI',rom,n)
            if n==0x30:self.ff4.write_bytes(b'changed-after-extraction')
            return result
        with patch('instrument_analysis.origin_pilot.extract_ff4_sample',side_effect=lambda p,n:self.extract('Final Fantasy IV',p,n)), patch('instrument_analysis.origin_pilot.extract_ff6_sample',side_effect=later):
            with self.assertRaisesRegex(ValueError,'ROM changed'):
                build_origin_pilot(self.path,ff4_rom_path=self.ff4,ff6_rom_path=self.ff6,output_dir=self.root/'output')
        self.assertFalse((self.root/'output').exists())
