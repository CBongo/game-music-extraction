#!/usr/bin/env python3
"""Test pass 1 parsing only."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from extractor import SequenceExtractor

def test_pass1():
    """Test pass 1 IR generation for FF2 song 1."""
    # Construct paths relative to akao directory
    akao_dir = os.path.join(os.path.dirname(__file__), '..')
    config_path = os.path.join(akao_dir, 'ff2.yaml')
    rom_path = os.path.join(akao_dir, '..', 'snes', 'ff2', 'ff2.smc')
    extractor = SequenceExtractor(config_path, rom_path)

    # Get the format handler
    handler = extractor.format_handler

    # Read song data for song 1 (Prelude)
    song_id = 1
    print(f"\nTesting song {song_id}")

    # Get song data - need to find the song offset
    # For FF2, songs are at master_ptrs[0] + song_id * 2
    master_ptrs_offset = extractor.config.get('master_ptrs_offset', 0)
    # Check if ROM has SMC header
    has_header = len(extractor.rom_data) % 1024 == 512
    master_ptrs_abs = master_ptrs_offset + (0x200 if has_header else 0)

    # Read master pointers
    song_table_ptr_bytes = extractor.rom_data[master_ptrs_abs:master_ptrs_abs + 2]
    song_table_ptr = int.from_bytes(song_table_ptr_bytes, 'little')
    song_table_offset = handler.snes_to_file_offset(song_table_ptr, extractor.rom_data)

    # Read song pointer
    song_ptr_offset = song_table_offset + song_id * 2
    song_ptr_bytes = extractor.rom_data[song_ptr_offset:song_ptr_offset + 2]
    song_ptr = int.from_bytes(song_ptr_bytes, 'little')
    song_offset = handler.snes_to_file_offset(song_ptr, extractor.rom_data)

    # Read voice count and voice pointers
    voice_count = extractor.rom_data[song_offset]
    print(f"Voice count: {voice_count}")

    # Test voice 0
    voice_ptr_offset = song_offset + 1
    voice_ptr_bytes = extractor.rom_data[voice_ptr_offset:voice_ptr_offset + 2]
    voice_ptr = int.from_bytes(voice_ptr_bytes, 'little')
    voice_offset = handler.snes_to_file_offset(voice_ptr, extractor.rom_data)

    print(f"Voice 0 offset: 0x{voice_offset:04X}")

    # Create a buffer with the song data (mimic how it's loaded)
    # Add 2-byte size prefix as expected by parser
    song_data_size = 0x4000  # Arbitrary large size
    song_data_start = song_offset
    buffer = b'\x00\x40' + extractor.rom_data[song_data_start:song_data_start + song_data_size]

    # Calculate offset into buffer for this voice
    buffer_offset = (voice_offset - song_data_start) + 2

    print(f"Buffer offset: 0x{buffer_offset:04X}")

    # Get instrument table
    instrument_table = handler._read_song_instrument_table(song_id)
    print(f"Instrument table: {len(instrument_table)} instruments")

    # Call pass 1 only
    print("\n=== Running Pass 1 ===")
    disasm, ir_events = handler._parse_track_pass1(
        buffer, buffer_offset, 0, instrument_table
    )

    print(f"\nPass 1 Results:")
    print(f"  Disassembly lines: {len(disasm)}")
    print(f"  IR events: {len(ir_events)}")

    # Show first 20 disassembly lines
    print(f"\nFirst 20 disassembly lines:")
    for i, line in enumerate(disasm[:20]):
        print(line)

    # Show IR event summary
    print(f"\nIR Event Summary:")
    event_types = {}
    for event in ir_events:
        event_type = event.type.value
        event_types[event_type] = event_types.get(event_type, 0) + 1

    for event_type, count in sorted(event_types.items()):
        print(f"  {event_type}: {count}")

    # Show first 10 IR events
    print(f"\nFirst 10 IR events:")
    for i, event in enumerate(ir_events[:10]):
        print(f"  {i}: {event.type.value} at offset 0x{event.offset:04X}")
        if event.type.value == 'note':
            # NOTE: octave not in metadata - it's tracked as state in Pass 2
            print(f"     note={event.note_num}, dur={event.duration}")

    return len(disasm), len(ir_events)

if __name__ == '__main__':
    try:
        disasm_count, ir_count = test_pass1()
        print(f"\n[OK] Pass 1 completed successfully!")
        print(f"  Generated {disasm_count} disassembly lines and {ir_count} IR events")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
