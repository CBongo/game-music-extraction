"""Version-pinned FF6 US ROM BRR extraction; no instrument-source inference."""
from __future__ import annotations

import hashlib
from pathlib import Path

SUPPORTED_PAYLOAD_SHA256 = "0f51b4fca41b7fd509e4b8f9d543151f68efa5e97b08493e4b2a0c06f5d8d5e2"
PAYLOAD_SIZE = 0x300000
POINTER_TABLE = 0x53C5F
LOOP_TABLE = 0x53D1C
TUNING_TABLE = 0x53D9A
ADSR_TABLE = 0x53E18
SAMPLE_COUNT = 63


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def parse_ff6_sample(data: bytes, sample_id: int, *, header_bytes: int = 0):
    """Parse the specified layout without approving a ROM version.

    This low-level parser exists for synthetic bounds/format tests. Production
    callers must use extract_ff6_sample, which pins the supported ROM payload.
    Entry offsets and BRR offsets are absolute file offsets including any header.
    """
    if type(data) is not bytes or type(header_bytes) is not int or header_bytes not in (0, 512):
        raise ValueError("data must be bytes and header_bytes must be 0 or 512")
    if type(sample_id) is not int or not 1 <= sample_id <= SAMPLE_COUNT:
        raise ValueError("global sample ID must be 1..63 (one-based)")
    index = sample_id - 1
    def entry(offset, length):
        offset += header_bytes
        if offset < header_bytes or offset + length > len(data):
            raise ValueError("sample descriptor or data extends beyond ROM")
        return {"offset": offset, "length": length, "bytes_hex": data[offset:offset + length].hex()}
    pointer = entry(POINTER_TABLE + 3 * index, 3)
    raw_pointer = int.from_bytes(bytes.fromhex(pointer["bytes_hex"]), "little")
    if not 0xC00000 <= raw_pointer <= 0xFFFFFF:
        raise ValueError("sample pointer is not a supported HiROM address")
    sample_offset = header_bytes + (raw_pointer & 0x3FFFFF)
    length_field = entry(sample_offset - header_bytes, 2)
    length = int.from_bytes(bytes.fromhex(length_field["bytes_hex"]), "little")
    if not length or length % 9:
        raise ValueError("BRR length must be positive and divisible by nine")
    payload_offset = sample_offset + 2
    if payload_offset + length > len(data):
        raise ValueError("BRR data extends beyond ROM")
    brr = data[payload_offset:payload_offset + length]
    loop = entry(LOOP_TABLE + 2 * index, 2)
    tuning = entry(TUNING_TABLE + 2 * index, 2)
    adsr = entry(ADSR_TABLE + 2 * index, 2)
    loop_offset = int.from_bytes(bytes.fromhex(loop["bytes_hex"]), "little")
    if loop_offset % 9 or not 0 <= loop_offset < length:
        raise ValueError("loop offset must identify an aligned BRR block")
    headers = brr[::9]
    if not headers[-1] & 1 or any(value & 1 for value in headers[:-1]):
        raise ValueError("BRR must contain exactly one END flag at the final block")
    descriptor = b"".join(bytes.fromhex(e["bytes_hex"]) for e in (pointer, length_field, loop, tuning, adsr))
    metadata = {
        "schema_version": "instrument-rom-sample/v1", "game": "Final Fantasy VI",
        "platform": "SNES", "region": "US", "driver": "FF6/FF3 US SPC700",
        "record_id": f"snes:ff6-us:global-sample-{sample_id:02x}",
        "rom": {"sha256": _hash(data), "payload_sha256": _hash(data[header_bytes:]),
                "size": len(data), "header_bytes": header_bytes, "version_approved": False},
        "instrument": {"global_sample_id": sample_id, "table_index": index,
            "pointer_entry": pointer, "length_field": length_field, "loop_entry": loop,
            "tuning_entry": tuning, "adsr_entry": adsr,
            "descriptor_sha256": _hash(descriptor),
            "descriptor_hash_recipe": "SHA256(raw pointer3 + raw length2 + raw loop2 + raw tuning2 + raw ADSR2); all words little-endian as stored"},
        "brr": {"file_offset": payload_offset, "length_bytes": length,
            "sha256": _hash(brr), "frame_count": length // 9,
            "decoded_sample_count": length // 9 * 16, "loop_relative_bytes": loop_offset,
            "looping": bool(headers[-1] & 2), "end_header": headers[-1]},
        "evidence": [
            {"path": "snes/ff3/f3-05.dis", "location": "C50341-C5035B", "fact": "global ID decremented, times three indexes C53C5F"},
            {"path": "snes/ff3/f3-05.dis", "location": "C50398-C503E1", "fact": "little-endian length precedes copied sample bytes"},
            {"path": "snes/ff3/f3-05.dis", "location": "C5040E-C5041B", "fact": "C53D1C loop offset added to loaded sample start"},
            {"path": "snes/ff3/f3-05.dis", "location": "C50495-C5049B; C504D7-C504DD", "fact": "global tuning and ADSR loaded to song-local APU tables"},
            {"path": "snes/ff3/ff3spc.dis", "location": "1502-1534", "fact": "DC command indexes local tuning/ADSR and SRCN; global ID is not physical SRCN"}
        ]}
    return metadata, brr


def extract_ff6_sample(rom_path, sample_id):
    """Extract a sample from the pinned US revision-0 ROM, headered or bare."""
    path = Path(rom_path).expanduser().resolve(strict=True)
    before = _hash(path.read_bytes())
    data = path.read_bytes()
    header_bytes = len(data) - PAYLOAD_SIZE
    if header_bytes not in (0, 512):
        raise ValueError("unsupported FF6 ROM size/version")
    payload = data[header_bytes:]
    title = payload[0xFFC0:0xFFD5]
    fields = payload[0xFFD5:0xFFE0]
    checksum = int.from_bytes(payload[0xFFDE:0xFFE0], "little")
    complement = int.from_bytes(payload[0xFFDC:0xFFDE], "little")
    mirrored_checksum = (sum(payload[:0x200000]) + 2 * sum(payload[0x200000:])) & 0xFFFF
    if (title != b"FINAL FANTASY 3      " or fields[0] != 0x31 or fields[4] != 1
            or fields[6] != 0 or checksum != 0x5F32 or complement != 0xA0CD
            or mirrored_checksum != checksum or _hash(payload) != SUPPORTED_PAYLOAD_SHA256):
        raise ValueError("unsupported FF6 ROM revision/hash/header/checksum")
    metadata, brr = parse_ff6_sample(data, sample_id, header_bytes=header_bytes)
    if _hash(data) != before or _hash(path.read_bytes()) != before:
        raise RuntimeError("ROM changed during sample extraction")
    metadata["rom"].update(path=str(path), version_approved=True,
        verified_title=title.decode("ascii"), verified_map_mode=0x31,
        verified_region_code=1, verified_revision=0,
        verified_checksum=checksum, verified_checksum_complement=complement,
        checksum_method="3MiB HiROM mirror: first2MiB + twice final1MiB modulo65536")
    return metadata, brr
