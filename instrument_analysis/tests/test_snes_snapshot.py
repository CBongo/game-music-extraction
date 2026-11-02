import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from instrument_analysis import snes_snapshot as capture
from instrument_analysis.snes_rendering import _read_spc


class SnapshotTests(unittest.TestCase):
    def receipt(self, dsp):
        return dict(schema_version='snes-snapshot-receipt/v1', status='ok', request_id='id',
                    rom_payload_sha256=capture.PAYLOAD_SHA256, frames=600, device=':soundcpu',
                    f1_written=0, aram_read_bytes=65536, dsp_read_bytes=128,
                    aram_path='aram', dsp_path='dsp', dsp_hex=dsp.hex(), pc=655,
                    original_f2=76, time_seconds=9.98)

    def validate(self, receipt, aram=bytes(65536), dsp=bytes(128)):
        capture._validate_receipt(receipt, request_id='id', frame_count=600,
                                  aram=aram, dsp=dsp, aram_path=Path('aram'), dsp_path=Path('dsp'))

    def test_exact_spc_round_trip(self):
        aram = bytes(range(256)) * 256
        dsp = bytes(range(128))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'capture.spc'
            path.write_bytes(capture._spc_bytes(aram, dsp, 655))
            raw, actual_aram, actual_dsp, pc = _read_spc(path)
        self.assertEqual(len(raw), 0x10200)
        self.assertEqual((actual_aram, actual_dsp, pc), (aram, dsp, 655))
        self.assertEqual(raw[0x27:0x2c], bytes(5))

    def test_incomplete_blocks_rejected(self):
        for aram, dsp in [(bytes(65535), bytes(128)), (bytes(65536), bytes(127))]:
            with self.assertRaises(ValueError):
                capture._spc_bytes(aram, dsp, 0)
            with self.assertRaises(RuntimeError):
                self.validate(self.receipt(dsp), aram, dsp)

    def test_receipt_is_bound_to_request_and_bytes(self):
        valid = self.receipt(bytes(128))
        self.validate(valid)
        for field, value in [('frames', 601), ('dsp_hex', '00'), ('pc', True),
                             ('original_f2', 256), ('request_id', 'other'),
                             ('time_seconds', float('nan')), ('aram_path', 'other'),
                             ('rom_payload_sha256', 'other'), ('device', ':maincpu')]:
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.validate({**valid, field: value})

    def test_unapproved_rom_rejected(self):
        with self.assertRaisesRegex(ValueError, 'approved'):
            capture._rom_payload(bytes(2621952))

    def test_request_validation_precedes_emulator(self):
        for count in (True, 0, -1, 36001, 1.5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                capture.capture_snes_snapshot('missing', mame_path='missing',
                                              ipl_source_path='missing', frame_count=count,
                                              output_dir='missing')

    def test_silent_arguments_and_input_mutation_guard(self):
        # Mock process execution while preserving actual staged file/receipt validation.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rom, mame, ipl = [root / n for n in ('rom', 'mame', 'ipl')]
            for path in (rom, mame, ipl):
                path.write_bytes(b'original')
            commands = []

            def run(argv, **kwargs):
                commands.append(argv)
                if '-version' in argv:
                    return type('Result', (), dict(stdout='MAME test', stderr='', returncode=0))()
                env = kwargs['env']
                aram_path, dsp_path = Path(env['GME_CAPTURE_ARAM']), Path(env['GME_CAPTURE_DSP'])
                aram_path.write_bytes(bytes(65536)); dsp_path.write_bytes(bytes(128))
                receipt = {**self.receipt(bytes(128)), 'request_id': env['GME_CAPTURE_ID'],
                           'aram_path': str(aram_path), 'dsp_path': str(dsp_path)}
                Path(env['GME_CAPTURE_RECEIPT']).write_text(json.dumps(receipt))
                rom.write_bytes(b'mutated')
                return type('Result', (), dict(stdout='', stderr='', returncode=0))()

            with patch.object(capture, '_rom_payload', return_value=(b'cart', {})), \
                    patch.object(capture, '_ROOT', root), \
                    patch.object(capture, '_resolve_mame', return_value=mame), \
                    patch.object(capture, '_extract_ipl', return_value=(bytes(64), 'sha')), \
                    patch.object(capture.subprocess, 'run', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'input changed'):
                    capture.capture_snes_snapshot(rom, mame_path=mame, ipl_source_path=ipl,
                                                  output_dir=root / 'output')
            argv = commands[-1]
            self.assertEqual(argv[argv.index('-video') + 1], 'none')
            self.assertEqual(argv[argv.index('-sound') + 1], 'none')
            self.assertIn('-nothrottle', argv)
            self.assertNotIn('-wavwrite', argv)
            self.assertFalse((root / 'output').exists())


if __name__ == '__main__':
    unittest.main()
