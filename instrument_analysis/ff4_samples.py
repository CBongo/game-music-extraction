"""Version-pinned FFII US / FFIV global BRR sample extraction, without playback."""
from __future__ import annotations
import hashlib
from pathlib import Path

PAYLOAD_SHA256 = '680535dc1c4196c53b40dc9c2c9bc159a77802ab8d4b474bef5dc0281c15ad06'
ROM_SIZE = 0x100000
VERSION = 'instrument-rom-sample/v1'
EVIDENCE = [
    {'path':'snes/ff2/ff2mus.pl','sha256':'f18896043b57276a43ad3bbc6326e4a6b927f65bf3701a6956c81e2ec16ea19a','lines':'78-90,114-115,249,354-390','role':'table and LoROM decoding mechanics; historical labels are not identity authority'},
    {'path':'snes/ff2/ffdis04.txt','sha256':'c2a1c167396cf55698c9662c04f8839c83caecd1206cd83b1300379924cc8f99','lines':'459-518,602-678','role':'CPU sample pointer relocation and sample directory loader'},
    {'path':'snes/ff2/ff2spc.txt','sha256':'f686e6b49b6093abfb48235579fae95b78cacd7ef125ebd7ad4d63124a8eb0fe','lines':'1591-1627','role':'SPC sample and directory upload destinations'},
]

def _sha(data):
    return hashlib.sha256(data).hexdigest()

def parse_ff4_sample(data: bytes, sample_id: int, *, header_bytes: int = 0,
                     source_path: str = '<bytes>') -> tuple[dict, bytes]:
    """Pure table parser for a specified byte layout; does not certify ROM identity.

    Global ID is its zero-based table index. Zero is a null entry in this driver.
    The public file extractor adds strict known-ROM identity validation.
    """
    if not isinstance(data, bytes) or type(header_bytes) is not int or header_bytes not in (0,512):
        raise ValueError('bytes and explicit zero/512-byte header required')
    if type(sample_id) is not int or not 0 <= sample_id < 0x17:
        raise ValueError('global sample_id must be integer 0..22')
    def read(offset, length, label):
        if offset < header_bytes or length < 0 or offset + length > len(data):
            raise ValueError(label + ' exceeds ROM bounds')
        return data[offset:offset+length]
    def integer(offset,length,label): return int.from_bytes(read(offset,length,label),'little')
    base=header_bytes+0x24000
    sample_table=base+integer(base+3,3,'sample table pointer')
    directory=base+integer(base+9,3,'directory table pointer')
    flags=base+integer(base+12,3,'sample flags table pointer')
    pointer_offset=sample_table+sample_id*3
    pointer=read(pointer_offset,3,'sample pointer')
    relative=int.from_bytes(pointer,'little')
    if relative == 0:
        raise ValueError('global sample is null')
    record_offset=base+relative
    length_field=read(record_offset,2,'sample length')
    length=int.from_bytes(length_field,'little')
    if length == 0 or length % 9:
        raise ValueError('BRR length must be a positive multiple of nine')
    audio_offset=record_offset+2
    brr=read(audio_offset,length,'BRR payload')
    directory_offset=directory+sample_id*4
    entry=read(directory_offset,4,'directory descriptor')
    start=int.from_bytes(entry[:2],'little');loop=int.from_bytes(entry[2:],'little')
    if start != 0 or not 0 <= loop < length or loop % 9:
        raise ValueError('invalid relative start/loop descriptor')
    headers=brr[::9]
    if not headers[-1]&1 or any(h&1 for h in headers[:-1]):
        raise ValueError('BRR must end exactly at declared final block')
    flag=read(flags+sample_id,1,'sample flag')
    descriptor=b'FF4-US-sample-descriptor/v1\0'+bytes([sample_id])+pointer+length_field+entry+flag
    header=read(header_bytes+0x7fc0,32,'cartridge header')
    metadata={'schema_version':VERSION,'game':'Final Fantasy IV','source_aliases':['Final Fantasy II (US)','Final Fantasy IV (SNES)'],
        'platform':'Super Nintendo','region':'US','driver':'FF2 SPC700 custom driver',
        'record_id':f'snes:ff4-us:global-sample-{sample_id:02x}',
        'rom':{'path':source_path,'sha256':_sha(data),'payload_sha256':_sha(data[header_bytes:]),'size':len(data),
            'header_bytes':header_bytes,'title':header[:21].decode('ascii',errors='replace').rstrip(),
            'map_mode':header[21],'region_code':header[25],'revision':header[27],
            'checksum':int.from_bytes(header[30:32],'little'),'checksum_complement':int.from_bytes(header[28:30],'little')},
        'instrument':{'global_sample_id':sample_id,'table_index':sample_id,
            'pointer_entry':{'offset':pointer_offset,'length':3,'bytes_hex':pointer.hex()},
            'length_field':{'offset':record_offset,'length':2,'bytes_hex':length_field.hex()},
            'directory_entry':{'offset':directory_offset,'length':4,'bytes_hex':entry.hex()},
            'driver_flag':{'offset':flags+sample_id,'length':1,'bytes_hex':flag.hex()},
            'descriptor_sha256':_sha(descriptor),
            'descriptor_hash_recipe':'SHA256(ASCII FF4-US-sample-descriptor/v1 + NUL + uint8 global_sample_id + pointer3 + length2 + directory4 + driver_flag1); offsets excluded'},
        'brr':{'file_offset':audio_offset,'length_bytes':length,'sha256':_sha(brr),'frame_count':length//9,
            'decoded_sample_count':length//9*16,'loop_relative_bytes':loop,'looping':bool(headers[-1]&2),'end_header':headers[-1]},
        'evidence':EVIDENCE,'identity_scope':'Byte binding only; documentary origin and sampled note do not establish game unity key or sounding offset.'}
    return metadata,brr

def extract_ff4_sample(rom_path, sample_id: int) -> tuple[dict,bytes]:
    """Extract only the pinned, checksum-consistent US revision-zero payload."""
    path=Path(rom_path).resolve()
    data=path.read_bytes()
    if len(data) not in (ROM_SIZE,ROM_SIZE+512):
        raise ValueError('unsupported FF4 ROM size')
    header_bytes=len(data)-ROM_SIZE
    payload=data[header_bytes:]
    if _sha(payload) != PAYLOAD_SHA256:
        raise ValueError('unsupported FF4 ROM payload hash/version')
    header=payload[0x7fc0:0x7fe0]
    checksum=int.from_bytes(header[30:32],'little');complement=int.from_bytes(header[28:30],'little')
    if (header[:21] != b'FINAL FANTASY II     ' or header[21] != 0x20 or header[25] != 1 or header[27] != 0 or
        checksum ^ complement != 0xffff or sum(payload)&0xffff != checksum):
        raise ValueError('FF4 cartridge title/map/region/revision/checksum mismatch')
    metadata,brr=parse_ff4_sample(data,sample_id,header_bytes=header_bytes,source_path=str(path))
    metadata['rom']['variant_verified']=True
    return metadata,brr

def resolve_ff4_song_slots(data: bytes, song_id: int, *, header_bytes: int = 0) -> list[dict]:
    """Return all sixteen unfiltered DB40..DB4F logical-patch bindings."""
    if type(song_id) is not int or not 0 <= song_id < 0x46 or header_bytes not in (0,512):
        raise ValueError('invalid song/header')
    base=header_bytes+0x24000
    if base+9>len(data):raise ValueError('song table pointer out of bounds')
    table=base+int.from_bytes(data[base+6:base+9],'little')
    offset=table+song_id*32
    if offset+32>len(data):raise ValueError('song instrument slots out of bounds')
    return [{'song_local_slot':i,'source_patch_command':0x40+i,'global_sample_id':int.from_bytes(data[offset+i*2:offset+i*2+2],'little'),
        'entry_offset':offset+i*2} for i in range(16)]
