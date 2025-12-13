#!/usr/bin/env python3
"""Simple test of pass 1 parsing."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from extractor import SequenceExtractor
from format_base import SongMetadata

# Test song 1 (Prelude)
config_test = {
    'console_type': 'snes',
    'rom_file': '../snes/ff2/ff2.smc',
    'master_ptrs_offset': 0xD0000,
    'songs': [
        {'id': 1, 'title': 'Prelude'}
    ]
}

def test_pass1_simple():
    """Test pass 1 with a single song."""
    print("Initializing extractor...")
    # Construct paths relative to akao directory
    akao_dir = os.path.join(os.path.dirname(__file__), '..')
    config_path = os.path.join(akao_dir, 'ff2.yaml')
    rom_path = os.path.join(akao_dir, '..', 'snes', 'ff2', 'ff2.smc')
    extractor = SequenceExtractor(config_path, rom_path)

    # Get song 1
    songs = [SongMetadata(**s) for s in extractor.config.get('songs', [])]
    song = [s for s in songs if s.id == 1][0]

    print(f"\nExtracting song {song.id:02X}: {song.title}")

    # Extract sequence data
    try:
        data = extractor.extract_sequence_data(song)
        print(f"Extracted {len(data)} bytes of sequence data")
    except Exception as e:
        print(f"Error extracting sequence data: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Parse voices - get voice offsets
    voice_count = data[0]
    print(f"Voice count: {voice_count}")

    # Test parsing voice 0 only
    voice_num = 0
    voice_ptr_offset = 1 + voice_num * 2
    voice_ptr = int.from_bytes(data[voice_ptr_offset:voice_ptr_offset + 2], 'little')

    # Voice data starts after voice table
    voice_table_size = 1 + voice_count * 2
    # Voice pointer is relative to start of song data (SPC address 0x2000)
    # Data buffer starts at offset 2 (2-byte size prefix)
    voice_offset = (voice_ptr - 0x2000) + 2

    print(f"Voice {voice_num} pointer: 0x{voice_ptr:04X}, offset in buffer: 0x{voice_offset:04X}")

    # Get instrument table for this song
    instrument_table = extractor.format_handler._read_song_instrument_table(song.id)
    print(f"Instrument table has {len(instrument_table)} entries")

    # Call pass 1 ONLY
    print("\n=== Calling _parse_track_pass1 ===")
    try:
        disasm, ir_events = extractor.format_handler._parse_track_pass1(
            data, voice_offset, voice_num, instrument_table
        )

        print(f"\n[SUCCESS] Pass 1 completed!")
        print(f"  Disassembly lines: {len(disasm)}")
        print(f"  IR events: {len(ir_events)}")

        # Show first 15 disassembly lines
        print(f"\nFirst 15 disassembly lines:")
        for line in disasm[:15]:
            print(line)

        # Count event types
        event_counts = {}
        for event in ir_events:
            t = event.type.value
            event_counts[t] = event_counts.get(t, 0) + 1

        print(f"\nIR Event counts:")
        for event_type, count in sorted(event_counts.items()):
            print(f"  {event_type:20s}: {count:4d}")

        return True

    except Exception as e:
        print(f"\n[ERROR] Pass 1 failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = test_pass1_simple()
    sys.exit(0 if success else 1)
