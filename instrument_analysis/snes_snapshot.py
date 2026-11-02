"""Capture real cartridge ARAM/DSP for isolated voice replay, not song resumption."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from .native_rendering import _resolve_mame, _sha256
from .snes_rendering import _extract_ipl

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / 'snes' / 'capture_snapshot.lua'
ROM_SHA256 = '972aef8eb0568564da49d8a363ffec369681686b304d1cf2833d0ed1ca9b031e'
PAYLOAD_SHA256 = '04e43a75f491a79d408a315fd68a026371387381fc7c4597e2acb9a44758549f'


def _rom_payload(raw: bytes) -> tuple[bytes, dict]:
    if hashlib.sha256(raw).hexdigest() != ROM_SHA256:
        raise ValueError('ROM is not the approved FF5 cartridge fixture')
    payload = raw[512:]
    if len(raw) != 2621952 or hashlib.sha256(payload).hexdigest() != PAYLOAD_SHA256:
        raise ValueError('ROM payload size/hash mismatch')
    header = payload[0xffc0:0xffe0]
    return payload, {'copier_header_bytes': 512, 'payload_bytes': len(payload),
                     'payload_sha256': PAYLOAD_SHA256, 'header_offset': 0xffc0,
                     'header_hex': header.hex(), 'title': header[:21].decode('ascii'),
                     'map_mode': header[21], 'declared_checksum': int.from_bytes(header[30:32], 'little'),
                     'declared_complement': int.from_bytes(header[28:30], 'little'),
                     'observed_payload_sum16': sum(payload) & 0xffff,
                     'approval_basis': 'Exact file and payload hashes; header checksum is observed, not an approval check.'}


def _validate_receipt(receipt, *, request_id, frame_count, aram, dsp, aram_path, dsp_path):
    expected = {'schema_version': 'snes-snapshot-receipt/v1', 'status': 'ok',
                'request_id': request_id, 'rom_payload_sha256': PAYLOAD_SHA256,
                'frames': frame_count, 'device': ':soundcpu', 'f1_written': 0,
                'aram_read_bytes': 65536, 'dsp_read_bytes': 128,
                'aram_path': str(aram_path), 'dsp_path': str(dsp_path), 'dsp_hex': dsp.hex()}
    if len(aram) != 65536 or len(dsp) != 128:
        raise RuntimeError('capture requires exact ARAM/DSP block sizes')
    if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in expected.items()):
        raise RuntimeError('snapshot receipt does not match capture request/data')
    for name, maximum in [('pc', 65535), ('original_f2', 255)]:
        if type(receipt.get(name)) is not int or not 0 <= receipt[name] <= maximum:
            raise RuntimeError(f'invalid receipt {name}')
    time = receipt.get('time_seconds')
    if type(time) not in (int, float) or not math.isfinite(time) or time <= 0:
        raise RuntimeError('invalid capture time')


def _spc_bytes(aram, dsp, pc):
    if len(aram) != 65536 or len(dsp) != 128:
        raise ValueError('SPC requires exact ARAM/DSP block sizes')
    header = bytearray(256)
    signature = b'SNES-SPC700 Sound File Data v0.30'
    header[:len(signature)] = signature
    header[0x21:0x25] = bytes((0x1a, 0x1a, 0x1b, 30))
    header[0x25:0x27] = pc.to_bytes(2, 'little')
    return bytes(header) + aram + dsp + bytes(128)


def capture_snes_snapshot(rom_path, *, mame_path, ipl_source_path,
                          ipl_source_offset=0x89878, frame_count=600, output_dir,
                          source_record_id='snes:ff5:captured-session'):
    if type(frame_count) is not int or not 1 <= frame_count <= 36000:
        raise ValueError('frame_count must be an integer from 1 through 36000')
    if not isinstance(source_record_id, str) or not source_record_id.strip():
        raise ValueError('source_record_id must be nonempty')
    rom = Path(rom_path).resolve(strict=True)
    original = rom.read_bytes()
    payload, rom_info = _rom_payload(original)
    mame = _resolve_mame(mame_path)
    ipl_source = Path(ipl_source_path).resolve(strict=True)
    ipl, ipl_sha = _extract_ipl(ipl_source, ipl_source_offset)
    bindings = {str(p): _sha256(p) for p in (rom, mame, ipl_source, _SCRIPT)}
    output = Path(output_dir).resolve()
    if not output.is_relative_to(_ROOT):
        raise ValueError('output_dir must be inside the repository workspace')
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f'capture output already exists: {output}')
    request_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='snes-snapshot-') as name:
        scratch = Path(name)
        roms = scratch / 'roms' / 'snes'
        roms.mkdir(parents=True)
        (roms / 'spc700.rom').write_bytes(ipl)
        cart = scratch / 'ff5.sfc'
        cart.write_bytes(payload)
        aram_path, dsp_path, receipt_path = [scratch / n for n in ('aram.bin', 'dsp.bin', 'receipt.json')]
        env = os.environ.copy()
        env.update({'GME_CAPTURE_FRAMES': str(frame_count), 'GME_CAPTURE_ID': request_id,
                    'GME_CAPTURE_ROM_SHA': PAYLOAD_SHA256, 'GME_CAPTURE_ARAM': str(aram_path),
                    'GME_CAPTURE_DSP': str(dsp_path), 'GME_CAPTURE_RECEIPT': str(receipt_path)})
        base = [str(mame), '-noreadconfig', '-nowriteconfig']
        version = subprocess.run(base + ['-version'], cwd=scratch, capture_output=True, text=True, timeout=15, check=True)
        version_text = (version.stdout or version.stderr).strip()
        if not version_text:
            raise RuntimeError('MAME version query returned no text')
        argv = base + ['snes', '-rompath', str(roms.parent), '-cart', str(cart),
                       '-autoboot_script', str(_SCRIPT), '-autoboot_delay', '0',
                       '-video', 'none', '-sound', 'none', '-nothrottle']
        for flag in ('homepath', 'cfg_directory', 'nvram_directory', 'input_directory',
                     'state_directory', 'snapshot_directory', 'diff_directory', 'comment_directory'):
            directory = scratch / flag
            directory.mkdir()
            argv += ['-' + flag, str(directory)]
        result = subprocess.run(argv, cwd=scratch, env=env, capture_output=True, text=True,
                                timeout=max(90, frame_count // 10), check=False)
        if result.returncode or not receipt_path.is_file():
            raise RuntimeError(f'MAME snapshot failed: {result.stdout!r}; {result.stderr!r}')
        receipt = json.loads(receipt_path.read_text())
        aram, dsp = aram_path.read_bytes(), dsp_path.read_bytes()
        _validate_receipt(receipt, request_id=request_id, frame_count=frame_count,
                          aram=aram, dsp=dsp, aram_path=aram_path, dsp_path=dsp_path)
        if any(_sha256(Path(p)) != sha for p, sha in bindings.items()):
            raise RuntimeError('capture input changed during execution')
        spc = _spc_bytes(aram, dsp, receipt['pc'])
        files = {'aram.bin': aram, 'dsp.bin': dsp, 'capture.spc': spc,
                 'receipt.json': (json.dumps(receipt, indent=2) + '\n').encode()}
        manifest = {'schema_version': 'snes-cartridge-snapshot/v1', 'source_record_id': source_record_id,
                    'source': {'rom_path': str(rom), 'rom_sha256': ROM_SHA256, **rom_info,
                               'mame_executable': str(mame), 'mame_executable_sha256': bindings[str(mame)],
                               'mame_version': version_text, 'capture_script': str(_SCRIPT),
                               'capture_script_sha256': bindings[str(_SCRIPT)], 'ipl_source_path': str(ipl_source),
                               'ipl_source_sha256': ipl_sha, 'ipl_source_offset': ipl_source_offset},
                    'request_id': request_id, 'frame_count': frame_count, 'receipt': receipt,
                    'run_arguments': argv, 'spc_file': 'capture.spc',
                    'files': {n: {'sha256': hashlib.sha256(b).hexdigest(), 'bytes': len(b)} for n,b in files.items()},
                    'limitations': ['Isolated voice replay input; not a resumable full song or game snapshot.',
                                    'PC captured; A/X/Y/PSW/SP header fields unknown and written as zero.',
                                    'Frame count counts frame_done callbacks after Lua autoboot registration.',
                                    'F2 restored; F1 set to zero to expose top ARAM and disable timers.',
                                    'ARAM F0:FF are I/O reads, not underlying RAM; isolated renderer skips them.',
                                    'DSP registers read sequentially without CPU execution; internal DSP state is not serialized.',
                                    'ENVX/OUTX/ENDX are observed DSP readbacks; no DSP register writes except F2 selection.',
                                    'Reading ARAM I/O timer counters FD:FF can clear them; game execution is not resumed.']}
        files['snapshot.manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
        staging = output.parent / ('.snes-capture-' + uuid.uuid4().hex)
        staging.mkdir()
        try:
            for filename, data in files.items():
                with (staging / filename).open('xb') as stream:
                    stream.write(data)
            if output.exists():
                raise FileExistsError('capture output appeared during publication')
            staging.rename(output)
        finally:
            if staging.exists():
                if staging.resolve().parent != output.parent or not staging.name.startswith('.snes-capture-'):
                    raise RuntimeError('unsafe snapshot staging cleanup path')
                shutil.rmtree(staging)
    return manifest
