"""ROM identity rejection and synthetic FF4 BRR table mechanics."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from instrument_analysis.ff4_samples import extract_ff4_sample, parse_ff4_sample, resolve_ff4_song_slots

class FF4SampleTests(unittest.TestCase):
    def fixture(self, header_bytes=0):
        b=bytearray(header_bytes+0x25000);base=header_bytes+0x24000
        def put(o,n,width):b[o:o+width]=n.to_bytes(width,'little')
        for offset,value in ((3,0x100),(6,0x200),(9,0x500),(12,0x600)):put(base+offset,value,3)
        put(base+0x100+7*3,0x700,3);put(base+0x700,18,2)
        b[base+0x702:base+0x714]=bytes([0])+bytes(8)+bytes([3])+bytes(8)
        put(base+0x500+7*4,0,2);put(base+0x500+7*4+2,9,2)
        put(base+0x200+32+0*2,7,2);put(base+0x200+32+2*2,7,2)
        b[header_bytes+0x7fc0:header_bytes+0x7fc0+21]=b'FINAL FANTASY II     '
        return b
    def test_zero_based_index_and_header_offsets(self):
        records=[]
        for h in (0,512):
            meta,brr=parse_ff4_sample(bytes(self.fixture(h)),7,header_bytes=h)
            self.assertEqual(meta['record_id'],'snes:ff4-us:global-sample-07')
            self.assertEqual(meta['instrument']['table_index'],7)
            self.assertEqual(meta['brr']['file_offset'],h+0x24702)
            self.assertEqual(meta['brr']['frame_count'],2)
            self.assertTrue(meta['brr']['looping'])
            self.assertEqual(meta['brr']['loop_relative_bytes'],9)
            self.assertEqual(meta['brr']['sha256'],hashlib.sha256(brr).hexdigest())
            records.append(meta['instrument']['descriptor_sha256'])
        self.assertEqual(records[0],records[1])
    def test_invalid_lengths_loop_end_and_bounds(self):
        base=0x24000
        for offset,value in ((base+0x700,17),(base+0x500+7*4+2,1),(base+0x500+7*4,9),(base+0x70b,0),(base+0x702,1)):
            b=self.fixture();b[offset]=value
            with self.assertRaises(ValueError):parse_ff4_sample(bytes(b),7)
        b=self.fixture();b[base+0x115:base+0x118]=b'\xff\xff\xff'
        with self.assertRaises(ValueError):parse_ff4_sample(bytes(b),7)
        for sample_id in (-1,23,True,0):
            with self.assertRaises(ValueError):parse_ff4_sample(bytes(self.fixture()),sample_id)
    def test_song_slots_preserve_holes(self):
        rows=resolve_ff4_song_slots(bytes(self.fixture()),1)
        self.assertEqual(len(rows),16)
        self.assertEqual([r['global_sample_id'] for r in rows[:3]],[7,0,7])
        self.assertEqual(rows[2]['source_patch_command'],0x42)
    def test_unknown_rom_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'unknown.sfc';p.write_bytes(bytes(0x100000))
            with self.assertRaisesRegex(ValueError,'payload hash'):extract_ff4_sample(p,7)
            p.write_bytes(b'tiny')
            with self.assertRaisesRegex(ValueError,'size'):extract_ff4_sample(p,7)
