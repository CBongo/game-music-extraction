"""
PSX music format handler for AKAO-based games.
Supports FF7, FF8, FF9, Chrono Cross via config-driven architecture.
"""

import io
import struct
import sys
from typing import List, Tuple, Dict, Optional

# Import base classes
from format_base import SequenceFormat, NOTE_NAMES

# Import IR event classes
from ir_events import (
    IREvent, IREventType,
    make_note, make_rest, make_tie, make_tempo, make_tempo_fade,
    make_octave_set, make_octave_inc, make_octave_dec,
    make_volume, make_volume_fade, make_pan_fade,
    make_patch_change, make_loop_start, make_loop_end, make_goto,
    make_slur_on, make_slur_off, make_roll_on, make_roll_off,
    make_staccato, make_utility_duration, make_master_volume, make_volume_multiplier,
    make_percussion_mode_on, make_percussion_mode_off, make_halt
)


class Raw2352FileWrapper(io.BufferedIOBase):
    """File-like object that converts 2352-byte raw CD sectors to 2048-byte ISO on-the-fly."""

    def __init__(self, filepath: str):
        self.file = open(filepath, 'rb')
        self.sector_size = 2352
        self.data_size = 2048
        self.header_size = 24  # Skip sync pattern + header

        # Calculate the logical size (as if it were a 2048-byte ISO)
        self.file.seek(0, 2)  # Seek to end
        raw_size = self.file.tell()
        self.num_sectors = raw_size // self.sector_size
        self.logical_size = self.num_sectors * self.data_size

        self.file.seek(0)
        self.current_pos = 0

    def read(self, size: Optional[int] = -1) -> bytes:  # type: ignore[override]
        """Read and translate data from raw CD format."""
        if size is None or size == -1:
            size = self.logical_size - self.current_pos

        if size <= 0 or self.current_pos >= self.logical_size:
            return b''

        # Limit to available data
        size = min(size, self.logical_size - self.current_pos)

        result = bytearray()
        bytes_read = 0

        while bytes_read < size:
            # Which logical sector are we in?
            sector_num = self.current_pos // self.data_size
            offset_in_sector = self.current_pos % self.data_size

            # How much to read from this sector
            bytes_from_sector = min(self.data_size - offset_in_sector, size - bytes_read)

            # Seek to the raw sector and skip header
            raw_offset = sector_num * self.sector_size + self.header_size + offset_in_sector
            self.file.seek(raw_offset)

            # Read the data
            data = self.file.read(bytes_from_sector)
            result.extend(data)

            bytes_read += len(data)
            self.current_pos += len(data)

            if len(data) < bytes_from_sector:
                break

        return bytes(result)

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to a position in the logical file."""
        if whence == 0:  # SEEK_SET
            self.current_pos = offset
        elif whence == 1:  # SEEK_CUR
            self.current_pos += offset
        elif whence == 2:  # SEEK_END
            self.current_pos = self.logical_size + offset

        self.current_pos = max(0, min(self.current_pos, self.logical_size))
        return self.current_pos

    def tell(self) -> int:
        """Return current position in logical file."""
        return self.current_pos

    def readable(self) -> bool:
        return True

    def close(self):
        """Close the underlying file."""
        if self.file:
            self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AKAONewStyle(SequenceFormat):
    """Handler for 'new style' AKAO format (Chrono Cross, etc.)"""

    # Opcode names
    OPCODE_NAMES = {
        0xA0: 'Halt', 0xA1: 'Program Change',
        0xA2: 'Utility Duration', 0xA3: 'Set Track Vol',
        0xA5: 'Set Octave', 0xA6: 'Inc Octave', 0xA7: 'Dec Octave',
        0xA8: 'Set Expression', 0xA9: 'Expression Fade',
        0xAA: 'Set Pan', 0xAB: 'Pan Fade',
        0xAD: 'Set Attack', 0xAE: 'Set Decay',
        0xAF: 'Set Sustain Level', 0xB0: 'Set Decay and SusLvl',
        0xB1: 'Set Sustain Rate', 0xB2: 'Set Release',
        0xB3: 'Reset ADSR',
        0xB4: 'Vibrato On', 0xB5: 'Vibrato Depth', 0xB6: 'Vibrato Off',
        0xB7: 'Set Attack Mode',
        0xB8: 'Tremolo On', 0xB9: 'Tremolo Depth', 0xBA: 'Tremolo Off',
        0xBB: 'Set Sustain Rate Mode',
        0xBC: 'Ping-pong On', 0xBD: 'Ping-pong Depth', 0xBE: 'Ping-pong Off',
        0xBF: 'Set Release Mode',
        0xC0: 'trance(?) absolute', 0xC1: 'trance(?) relative',
        0xC2: 'Reverb On', 0xC3: 'Reverb Off',
        0xC4: 'Noise On', 0xC5: 'Noise Off',
        0xC6: 'FM On', 0xC7: 'FM Off',
        0xC8: 'Begin Repeat', 0xC9: 'End Repeat', 0xCA: 'Repeat Always',
        0xCC: 'Slur on', 0xCD: 'Slur off',
        0xD4: 'Vibrato Active', 0xD5: 'Vibrato Inactive',
        0xD6: 'Tremolo Active', 0xD7: 'Tremolo Inactive',
        0xD8: 'Set Pitch Bend', 0xD9: 'Add Pitch Bend',
        0xDD: 'Vibrato Fade', 0xDE: 'Tremolo Fade',
        0xDF: 'Ping-pong Fade',
        0xFF: 'Halt'
    }

    FE_OPCODE_NAMES = {
        0x00: 'Set Tempo', 0x01: 'Tempo Fade',
        0x02: 'Set Reverb Vol', 0x03: 'Reverb Vol Fade',
        0x04: 'Perc Mode On', 0x05: 'Perc Mode Off',
        0x06: 'Goto Relative', 0x07: 'Branch if vblk+6c >= op1',
        0x08: 'Goto if arg == rptcount',
        0x09: 'Goto if arg == rptcount w/pop',
        0x0e: 'Call Sub', 0x0f: 'Return From Sub',
        0x10: 'Set NextAvailPV', 0x11: 'Clear NextAvailPV',
        0x12: 'Track Vol Fade',
        0x14: 'Program Change', 0x15: 'Time Signature',
        0x16: 'Measure #'
    }

    # Default values for Pass 2 execution
    default_octave = 4
    default_velocity = 100
    default_tempo = 120

    # Override specific lengths
    def __init__(self, config: Dict, rom_data: bytes, exe_iso_reader=None):
        """Initialize with game-specific config and ROM data."""
        self.config = config
        self.rom_data: bytes = rom_data
        self.exe_iso_reader = exe_iso_reader  # Optional: SequenceExtractor instance for reading from ISO

        # Read duration table from ROM if specified
        if 'duration_table' in config:
            table_cfg = config['duration_table']
            self.duration_table = self._read_rom_table(
                table_cfg.get('address'),
                table_cfg.get('size', 11),
                table_cfg.get('type', 'B')
            )
        else:
            # Fallback to hardcoded table
            self.duration_table = [0xC0, 0x60, 0x30, 0x18, 0x0C, 0x06,
                                   0x03, 0x20, 0x10, 0x08, 0x04]

        # Read opcode length table from ROM if specified
        if 'opcode_table' in config:
            table_cfg = config['opcode_table']
            self.oplen = self._read_rom_table(
                table_cfg.get('address'),
                table_cfg.get('size', 96),
                table_cfg.get('type', 'B')
            )
        else:
            # Fallback to hardcoded table
            self.oplen = [
                0,2,2,2,3,2,1,1,  # A0-A7
                2,3,2,3,2,2,2,2,  # A8-AF
                3,2,2,1,4,2,1,2,  # B0-B7
                4,2,1,2,3,2,1,2,  # B8-BF
                2,2,1,1,1,1,1,1,  # C0-C7
                1,0,0,0,1,0,2,2,  # C8-CF
                1,0,2,2,1,1,1,1,  # D0-D7
                2,2,2,0,2,3,3,3,  # D8-DF
                1,2,1,0,3,3,3,0,  # E0-E7
            ]

        # Read FE opcode length table from ROM if specified
        if 'fe_opcode_table' in config:
            table_cfg = config['fe_opcode_table']
            self.fe_oplen = self._read_rom_table(
                table_cfg.get('address'),
                table_cfg.get('size', 32),
                table_cfg.get('type', 'B')
            )
        else:
            # Fallback to hardcoded table
            self.fe_oplen = [
                3,4,3,4,1,1,3,4,  # 00-07
                4,4,2,2,0,0,3,1,  # 08-0F
                2,1,3,1,2,3,3,0,  # 10-17
                0,4,1,1,2,1,1,0,  # 18-1F
            ]

        # Apply overrides
        self.oplen[0xA0-0xA0] = 1  # halt
        self.oplen[0xC9-0xA0] = 2  # end repeat
        self.oplen[0xCA-0xA0] = 1  # repeat always
        self.oplen[0xCB-0xA0] = 1
        self.oplen[0xCD-0xA0] = 1  # nop
        # E3-EF, FF all = 1
        for i in range(0xE3, 0xF0):
            if i - 0xA0 < len(self.oplen):
                self.oplen[i-0xA0] = 1
        if 0xFF-0xA0 < len(self.oplen):
            self.oplen[0xFF-0xA0] = 1

        # FE opcode overrides
        self.fe_oplen[0x06] = 3
        self.fe_oplen[0x07] = 4
        self.fe_oplen[0x0e] = 3
        self.fe_oplen[0x0f] = 1

        # Get state defaults from config
        self.default_tempo = config.get('default_tempo', 255)
        self.default_octave = config.get('default_octave', 4)
        self.default_velocity = config.get('default_velocity', 100)

        # Calculate tempo_factor from component values
        # tempo_factor = timer_period_us * timer_count * tempo_resolution
        timer_period_us = config.get('timer_period_us', 0.2362)
        timer_count = config.get('timer_count', 846720)
        tempo_resolution = config.get('tempo_resolution', 65536)
        self.tempo_factor = timer_period_us * timer_count * tempo_resolution

        # Initialize patch map (populated from config if provided)
        self.patch_map = {}

    def _read_rom_table(self, address: int, size: int, data_type: str) -> List[int]:
        """Read a table from the ROM image."""
        if not self.rom_data and not self.exe_iso_reader:
            raise ValueError("ROM data not provided for reading tables")

        # For PSX, handle RAM addresses and convert to file offsets
        if self.config.get('console_type') == 'psx':
            file_offset = self._psx_ram_to_file_offset(address)
        else:
            file_offset = address

        # Convert single char types to full names for clarity
        type_map = {
            'byte': 'B', 'B': 'B',
            'ubyte': 'B', 'unsigned char': 'B',
            'short': 'H', 'H': 'H', 'ushort': 'H',
            'int': 'I', 'I': 'I', 'uint': 'I'
        }
        format_char = type_map.get(data_type, data_type)

        item_size = struct.calcsize(format_char)
        bytes_to_read = size * item_size

        # Read from ISO if we have an ISO reader (more efficient than loading whole executable)
        if self.exe_iso_reader:
            data = self.exe_iso_reader._read_executable_bytes(file_offset, bytes_to_read)
        else:
            # Fall back to in-memory data if available
            data = self.rom_data[file_offset:file_offset + bytes_to_read]

        return list(struct.unpack(f'{size}{format_char}', data))

    def _psx_ram_to_file_offset(self, ram_address: int) -> int:
        """Convert PSX RAM address to file offset in the executable."""
        # PSX executables have a header at the start
        # Read the load address from the header (at offset 0x18)
        if len(self.rom_data) < 0x800:
            raise ValueError("PSX executable too small to have valid header")

        load_address = struct.unpack('<I', self.rom_data[0x18:0x1C])[0]

        original_address = ram_address

        # Strip 0x80000000 if present (KSEG0 address space marker)
        if ram_address >= 0x80000000:
            ram_address -= 0x80000000

        # Also strip load_address 0x80000000 marker if present
        if load_address >= 0x80000000:
            load_address -= 0x80000000

        # The executable code starts at offset 0x800 in the file
        # So: file_offset = (ram_address - load_address) + 0x800
        file_offset = (ram_address - load_address) + 0x800

        print(f"  Address conversion: RAM {original_address:08X} -> Load {load_address:08X} -> File offset {file_offset:08X}")

        return file_offset

    def parse_header(self, data: bytes, song_id: int = 0, use_alternate_pointers: bool = False) -> Dict:
        """Parse AKAO sequence header."""
        if len(data) < 0x40:
            raise ValueError("Data too short for AKAO header")

        # First 0x20 bytes are the file header
        magic = data[0:4].decode('ascii', errors='ignore')
        seq_id = struct.unpack('<H', data[4:6])[0]
        length = struct.unpack('<H', data[6:8])[0]

        # Voice mask (channel usage) at 0x20
        voice_mask = struct.unpack('<I', data[0x20:0x24])[0]
        voice_count = bin(voice_mask).count('1')

        # Patch/perc data offsets at 0x30 and 0x34 (relative to file start)
        patch_offset = struct.unpack('<I', data[0x30:0x34])[0]
        perc_offset = struct.unpack('<I', data[0x34:0x38])[0]

        return {
            'magic': magic,
            'id': seq_id,
            'length': length,
            'voice_mask': voice_mask,
            'voice_count': voice_count,
            'patch_offset': patch_offset,
            'perc_offset': perc_offset
        }

    def get_track_offsets(self, data: bytes, header: Dict) -> List[int]:
        """Extract track pointer offsets from voice mask."""
        voice_mask = header['voice_mask']
        offsets = []

        # Voice pointers start at 0x40
        ptr = 0x40

        for v in range(32):
            if voice_mask & (1 << v):
                if ptr + 2 <= len(data):
                    # Pointer value is relative to its own position
                    track_offset_rel = struct.unpack('<H', data[ptr:ptr+2])[0]
                    absolute_offset = ptr + track_offset_rel
                    offsets.append(absolute_offset)
                    ptr += 2

        return offsets

    def _parse_track_pass1(self, data: bytes, offset: int, track_num: int,
                          instrument_table: List[int], vaddroffset: int = 0,
                          track_boundaries: Optional[Dict[int, Tuple[int, int]]] = None,
                          percussion_table: Optional[List[Dict]] = None) -> Tuple[List, List[IREvent]]:
        """Pass 1: Linear parse to build disassembly and intermediate representation.

        Args:
            data: Song data buffer
            offset: Offset into data where this track starts
            track_num: Track/voice number
            instrument_table: List of instrument IDs for this song
            percussion_table: Unused for AKAO format (SNES-specific)
            vaddroffset: Address offset for target address calculations (unused for PSX)
            track_boundaries: Track boundaries for GOTO validation (unused for PSX)

        Returns:
            Tuple of (disasm_lines, ir_events)
        """
        disasm = []
        ir_events = []

        # Track state (use config defaults)
        octave = self.default_octave
        velocity = self.default_velocity
        tempo = self.default_tempo
        current_patch = 0  # Current GM patch
        inst_id = 0  # Raw instrument ID
        transpose_octaves = 0

        # Find end of track (next track start or end of data)
        track_end = len(data)

        p = offset
        disasm.append(f"Voice {track_num:02X}:")

        while p < track_end:
            if p >= len(data):
                disasm.append(f"  Warning: Reached end of data at {p:08X}")
                break

            cmd = data[p]
            line = f"  {p:08X}:  {cmd:02X} "
            p += 1

            # Note/tie/rest (0x00-0x9F, 0xF0-0xFD)
            if cmd < 0xA0 or (0xF0 <= cmd <= 0xFD):
                if cmd < 0xA0:
                    # Encoded note: notenum = cmd // 11, duration = durtbl[cmd % 11]
                    notenum = cmd // 0x0B
                    dur_idx = cmd % 0x0B
                    if dur_idx >= len(self.duration_table):
                        disasm.append(f"  Error: Invalid duration index {dur_idx}")
                        break
                    dur = self.duration_table[dur_idx]
                    line += "            "
                else:
                    # Extended note: F0-FD = notenum 0-13, next byte = duration
                    if p >= len(data):
                        disasm.append(f"  Error: Unexpected end of data reading duration")
                        break
                    notenum = cmd - 0xF0
                    dur = data[p]
                    line += f"{dur:02X}          "
                    p += 1

                if notenum < 12:
                    # Note
                    line += f"Note {NOTE_NAMES[notenum]} ({notenum:02d}) "

                    # Create IR note event (note_num is 0-11, Pass 2 will apply octave)
                    # Store native duration (no scaling, no gate)
                    # Scaling and gate will be applied in Pass 2
                    note_duration = dur
                    event = make_note(p - 1, notenum, note_duration)
                    event.metadata = {
                        'velocity': velocity,
                        'patch': current_patch,
                        'track_num': track_num,
                        'inst_id': inst_id,
                        'octave': octave  # Store current octave for debugging
                    }
                    ir_events.append(event)
                elif notenum == 12:
                    # Tie - create IR tie event (Pass 2 will extend previous note)
                    line += "Tie          "
                    # Store native duration (no scaling)
                    tie_duration = dur
                    event = make_tie(p - 1, tie_duration)
                    ir_events.append(event)
                else:
                    # Rest
                    line += "Rest         "
                    # Store native duration (no scaling)
                    rest_duration = dur
                    event = make_rest(p - 1, rest_duration)
                    ir_events.append(event)

                line += f"Dur {dur:02X}"
                # Note: total_time tracking removed - Pass 2 will calculate timing

            # FE-prefixed opcodes
            elif cmd == 0xFE:
                if p >= len(data):
                    disasm.append(f"  Error: Unexpected end of data reading FE opcode")
                    break

                op1 = data[p]
                line += f"{op1:02X} "
                p += 1

                oplen = self.fe_oplen[op1] if op1 < len(self.fe_oplen) else 0
                operands = []

                if oplen > 1:
                    if p + oplen - 1 > len(data):
                        disasm.append(f"  Error: Not enough data for FE {op1:02X} opcode")
                        break
                    operands = list(data[p:p+oplen-1])
                    p += oplen - 1

                line += ' '.join(f"{op:02X}" for op in operands)
                line += '   ' * (3 - len(operands))
                line += self.FE_OPCODE_NAMES.get(op1, f"FE_{op1:02X}")

                # Handle specific opcodes
                if op1 == 0x00 and len(operands) >= 2:
                    # Tempo
                    tempo = operands[0] | (operands[1] << 8)
                    # Calculate BPM: BPM = (60,000,000 * tempo_value) / tempo_factor
                    bpm = (60_000_000.0 * tempo) / self.tempo_factor
                    line += f" {bpm:.1f} bpm"
                    event = make_tempo(p - oplen, bpm, operands)
                    ir_events.append(event)
                elif op1 == 0x06 and len(operands) >= 2:
                    # Goto relative (signed 16-bit little-endian offset)
                    # Offset is relative to the third byte of FE command (first operand byte)
                    rel_offset = struct.unpack('<h', bytes(operands[0:2]))[0]
                    operand_start = p - oplen + 1  # Position of first operand byte (third byte of FE command)
                    target_offset = operand_start + rel_offset

                    line += f" -> 0x{target_offset:04X}"

                    # Create IR GOTO event
                    event = make_goto(p - oplen, target_offset, operands)
                    ir_events.append(event)

                    # For backwards GOTO (loop), end disassembly here
                    if target_offset < p:
                        disasm.append(line)
                        break
                elif op1 == 0x14 and len(operands) >= 1:
                    # Program Change (FE 14) - treat as raw instrument ID (like 0xA1)
                    inst_id = operands[0]

                    # Look up GM patch from patch_map if available
                    if hasattr(self, 'patch_map') and inst_id in self.patch_map:
                        patch_info = self.patch_map[inst_id]
                        gm_patch = patch_info['gm_patch']
                        transpose_octaves = patch_info.get('transpose', 0)

                        # Add annotation to disasm line
                        if gm_patch < 0:
                            line += f" -> PERC key={abs(gm_patch)}"
                        else:
                            line += f" -> GM patch {gm_patch}"
                    else:
                        # No patch mapping configured - default to Grand Piano
                        gm_patch = 0
                        transpose_octaves = 0

                    current_patch = gm_patch
                    event = make_patch_change(p - oplen, inst_id, gm_patch, transpose_octaves, operands)
                    ir_events.append(event)
                elif op1 == 0x15 and len(operands) >= 2:
                    # Time signature
                    denom_val = 0xC0 / operands[0] if operands[0] != 0 else 0
                    line += f" ({operands[1]}/{int(denom_val)})"

            # Regular opcodes (0xA0-0xEF, 0xFF)
            else:
                idx = cmd - 0xA0
                oplen = self.oplen[idx] if idx < len(self.oplen) else 0
                operands = []

                if oplen > 1:
                    if p + oplen - 1 > len(data):
                        disasm.append(f"  Error: Not enough data for {cmd:02X} opcode")
                        break
                    operands = list(data[p:p+oplen-1])
                    p += oplen - 1

                line += ' '.join(f"{op:02X}" for op in operands)
                line += '   ' * (4 - len(operands))
                line += self.OPCODE_NAMES.get(cmd, f"OP_{cmd:02X}")

                # Handle state-changing opcodes
                if cmd == 0xA1 and operands:
                    # Program Change (A1)
                    inst_id = operands[0]

                    # Look up GM patch from patch_map if available
                    if hasattr(self, 'patch_map') and inst_id in self.patch_map:
                        patch_info = self.patch_map[inst_id]
                        gm_patch = patch_info['gm_patch']
                        transpose_octaves = patch_info.get('transpose', 0)

                        # Add annotation to disasm line
                        if gm_patch < 0:
                            line += f" -> PERC key={abs(gm_patch)}"
                        else:
                            line += f" -> GM patch {gm_patch}"
                    else:
                        # No patch mapping configured - default to Grand Piano (GM patch 0)
                        gm_patch = 0
                        transpose_octaves = 0

                    current_patch = gm_patch
                    event = make_patch_change(p - oplen, inst_id, gm_patch, transpose_octaves, operands)
                    ir_events.append(event)
                elif cmd == 0xA5 and operands:
                    # Set octave
                    octave = operands[0]
                    event = make_octave_set(p - oplen, octave, operands)
                    ir_events.append(event)
                elif cmd == 0xA6:
                    # Inc octave
                    octave += 1
                    event = make_octave_inc(p - oplen)
                    ir_events.append(event)
                elif cmd == 0xA7:
                    # Dec octave
                    octave -= 1
                    event = make_octave_dec(p - oplen)
                    ir_events.append(event)
                elif cmd == 0xA8 and operands:
                    # Volume
                    velocity = operands[0]
                    event = make_volume(p - oplen, velocity, operands)
                    ir_events.append(event)
                elif cmd == 0xC8:
                    # Begin repeat - create IR event, DON'T execute
                    # Note: AKAO C8 may have no operand (infinite loop) or count operand
                    loop_count = operands[0] if operands else 255
                    event = make_loop_start(p - oplen, loop_count, operands)
                    ir_events.append(event)
                elif cmd == 0xC9:
                    # End repeat - create IR event, DON'T execute
                    event = make_loop_end(p - oplen)
                    ir_events.append(event)
                elif cmd == 0xA0 or cmd == 0xFF:
                    # Halt
                    event = make_halt(p - oplen, operands if operands else [])
                    ir_events.append(event)
                    disasm.append(line)
                    break

            # Add all lines to disasm (no loop filtering in Pass 1)
            disasm.append(line)

        return disasm, ir_events

    def _find_event_by_offset(self, ir_events: List[IREvent], target_offset: int) -> Optional[int]:
        """Find IR event index by byte offset.

        Args:
            ir_events: List of IR events to search
            target_offset: Byte offset to find

        Returns:
            Index of event at or after target_offset, or None if not found
            (GOTO targets may point to opcodes that don't generate IR events)
        """
        # Find the first event at or after the target offset
        for i, event in enumerate(ir_events):
            if event.offset >= target_offset:
                return i
        return None

    def _analyze_track_loops(self, ir_events: List[IREvent]) -> Dict:
        """Analyze IR events to find loop structure.

        For PSX AKAO format:
        - Loops can be marked with LOOP_START/LOOP_END (0xC8/0xC9)
        - Or backwards GOTO (0xFE 0x06 with negative offset)

        Returns:
            Dict with loop analysis: {
                'has_backwards_goto': bool,
                'goto_target_idx': int,  # Index in ir_events
                'intro_time': int,       # Ticks before loop
                'loop_time': int,        # Ticks per loop iteration
                'target_time': int       # intro + loop
            }
        """
        # Find the LAST backwards GOTO (for proper looping)
        # Forward GOTOs are sequence continuation, not loops
        last_goto_idx = None
        last_goto_target_idx = None

        for i, event in enumerate(ir_events):
            if event.type == IREventType.GOTO and event.target_offset is not None:
                # Find target event by offset
                target_idx = self._find_event_by_offset(ir_events, event.target_offset)

                if target_idx is not None and target_idx < i:
                    # Backwards GOTO found - track it
                    last_goto_idx = i
                    last_goto_target_idx = target_idx

        # Check if we found a backwards GOTO
        if last_goto_idx is not None and last_goto_target_idx is not None:
            # Calculate intro and loop times
            intro_time = sum(e.duration or 0 for e in ir_events[:last_goto_target_idx] if e.is_note_event())
            loop_time = sum(e.duration or 0 for e in ir_events[last_goto_target_idx:last_goto_idx] if e.is_note_event())

            return {
                'has_backwards_goto': True,
                'goto_target_idx': last_goto_target_idx,
                'intro_time': intro_time,
                'loop_time': loop_time,
                'target_time': intro_time + 2 * loop_time  # Play intro + 2 loops
            }

        # No backwards GOTO - check for LOOP_START/LOOP_END pairs
        # (less common in AKAO, but possible)
        # For now, return no loop detected

        return {
            'has_backwards_goto': False,
            'goto_target_idx': -1,
            'intro_time': 0,
            'loop_time': 0,
            'target_time': 0
        }

    def _parse_track_pass2(self, all_track_data: Dict, start_voice_num: int,
                          target_loop_time: int = 0) -> List[Dict]:
        """Pass 2: Expand IR events with loop execution to generate MIDI events.

        Args:
            all_track_data: Complete track data from parse_all_tracks() (includes loop_info per track)
            start_voice_num: Starting voice/track number to execute from
            target_loop_time: Target playthrough time in ticks (0 = no loop expansion)

        Returns:
            List of MIDI event dictionaries
        """
        # Initialize from starting track
        current_voice_num = start_voice_num
        ir_events = all_track_data['tracks'][current_voice_num]['ir_events']
        loop_info = all_track_data['tracks'][current_voice_num].get('loop_info', {})

        # Playback state
        octave = self.default_octave
        velocity = self.default_velocity
        tempo = self.default_tempo
        perc_key = 0
        transpose_octaves = 0
        current_channel = start_voice_num

        # Output MIDI events
        midi_events = []

        # Timing
        total_time = 0

        # Loop execution state
        loop_stack = []  # Stack of {start_idx, count, iteration, max_count}

        # Iteration limit (failsafe)
        max_iterations = max(len(ir_events) * 200, 10000)
        iteration_count = 0

        # Emergency brakes (match SNESFF2/FF3)
        import time
        start_time = time.time()
        max_time_seconds = 5.0  # 5 second time limit
        max_midi_events = 100000  # Max MIDI events per track

        # Gate timing (native ticks before full duration when note-off happens)
        gate_time = 2  # Default: 2 native ticks from end
                       # Can be modified by slur/roll opcodes
        slur_enabled = False  # Track slur state
        roll_enabled = False  # Track roll state
        staccato_percentage = 100  # Track staccato multiplier (100 = normal)
        utility_duration_override = None  # One-shot duration override for next note
        master_volume = 256  # Master volume multiplier (SoM) - 256 = normal (100%)
        volume_multiplier = 0  # Volume multiplier (CT/FF3) - 0 = normal

        # Tick scaling factor (native ticks -> MIDI ticks)
        tick_scale = 2  # 48 native ticks/quarter -> 96 MIDI ticks/quarter

        # Scale target_loop_time from native ticks to MIDI ticks
        # (loop analyzer works in native ticks, Pass 2 works in MIDI ticks)
        target_loop_time_midi = target_loop_time * tick_scale if target_loop_time > 0 else 0

        # Execute IR events
        i = 0
        while i < len(ir_events):
            iteration_count += 1
            if iteration_count > max_iterations:
                print(f"WARNING: Track {start_voice_num} hit max iteration limit", file=sys.stderr)
                break

            # Check if we've reached target playthrough time
            if target_loop_time_midi > 0 and total_time >= target_loop_time_midi:
                break

            # Emergency brakes (only warn, not error - looping is normal)
            if time.time() - start_time > max_time_seconds:
                # Silently stop - time limit reached (normal for looping songs)
                break
            if len(midi_events) > max_midi_events:
                # Silently stop - MIDI event limit reached (normal for looping songs)
                break

            event = ir_events[i]

            if event.type == IREventType.NOTE:
                # Generate MIDI note
                note_num = event.note_num
                dur = event.duration  # Native duration from Pass 1

                # Apply octave and transposition
                midi_note = (octave * 12) + note_num + (transpose_octaves * 12)
                midi_note = max(0, min(127, midi_note))

                # Get velocity from event metadata or current state
                vel = event.metadata.get('velocity', velocity)

                # Apply utility duration override (one-shot for this note only)
                native_duration = utility_duration_override if utility_duration_override is not None else dur
                utility_duration_override = None  # Clear after use

                # Apply staccato multiplier to duration
                if staccato_percentage < 100:
                    # Reduce duration by staccato percentage
                    native_duration = int(native_duration * staccato_percentage / 100)

                # Scale native duration to MIDI ticks
                midi_dur = native_duration * tick_scale

                # Apply gate timing (note-off before full duration)
                # If slur or roll is active, use 0 gate time (notes play full duration)
                effective_gate_time = 0 if (slur_enabled or roll_enabled) else gate_time
                gate_adjusted_dur = (native_duration - effective_gate_time) * tick_scale

                midi_events.append({
                    'type': 'note',
                    'time': total_time,
                    'duration': gate_adjusted_dur,  # Gate-adjusted MIDI duration
                    'note': midi_note,
                    'velocity': vel,
                    'channel': current_channel
                })

                total_time += midi_dur  # Advance by FULL MIDI duration
                i += 1

            elif event.type == IREventType.REST:
                # Scale native duration to MIDI ticks
                midi_dur = event.duration * tick_scale
                total_time += midi_dur
                i += 1

            elif event.type == IREventType.TIE:
                # Extend previous note
                # Scale native duration to MIDI ticks
                tie_dur = event.duration * tick_scale
                if midi_events and midi_events[-1]['type'] == 'note':
                    midi_events[-1]['duration'] += tie_dur
                total_time += tie_dur
                i += 1

            elif event.type == IREventType.PATCH_CHANGE:
                # Extract from IR event
                gm_patch = event.gm_patch
                transpose_octaves = event.transpose

                # Percussion mode check
                if gm_patch < 0:
                    perc_key = abs(gm_patch)
                else:
                    perc_key = 0
                    midi_events.append({
                        'type': 'program_change',
                        'time': total_time,
                        'patch': gm_patch
                    })

                i += 1

            elif event.type == IREventType.TEMPO:
                # Tempo change - add to midi_events (will be placed on track 0)
                assert event.value is not None, "TEMPO event must have value"
                midi_events.append({
                    'type': 'tempo',
                    'time': total_time,
                    'tempo': event.value
                })
                tempo = event.value
                i += 1

            elif event.type == IREventType.TEMPO_FADE:
                # Tempo fade - generate tempo events at 2-tick intervals
                assert event.duration is not None and event.value is not None, "TEMPO_FADE must have duration and value"
                fade_duration = event.duration * tick_scale  # Convert to MIDI ticks
                target_tempo = event.value
                start_tempo = tempo

                # Generate tempo events at 2-tick intervals
                num_steps = max(1, fade_duration // 2)
                for step in range(num_steps + 1):
                    step_time = total_time + (step * 2)
                    # Linear interpolation
                    if num_steps > 0:
                        step_tempo = start_tempo + (target_tempo - start_tempo) * step / num_steps
                    else:
                        step_tempo = target_tempo

                    midi_events.append({
                        'type': 'tempo',
                        'time': step_time,
                        'tempo': step_tempo
                    })

                # Update current tempo to target
                tempo = target_tempo
                i += 1

            elif event.type == IREventType.OCTAVE_SET:
                octave = event.value
                i += 1

            elif event.type == IREventType.OCTAVE_INC:
                octave += 1
                i += 1

            elif event.type == IREventType.OCTAVE_DEC:
                octave -= 1
                i += 1

            elif event.type == IREventType.VOLUME:
                velocity = event.value
                i += 1

            elif event.type == IREventType.VOLUME_FADE:
                # Volume fade - generate CC 7 events at 2-tick intervals
                assert event.duration is not None and event.value is not None, "VOLUME_FADE must have duration and value"
                fade_duration = event.duration * tick_scale  # Convert to MIDI ticks
                target_volume = event.value
                start_volume = velocity

                # Generate volume events at 2-tick intervals
                num_steps = max(1, fade_duration // 2)
                for step in range(num_steps + 1):
                    step_time = total_time + (step * 2)
                    # Linear interpolation
                    if num_steps > 0:
                        step_volume = int(start_volume + (target_volume - start_volume) * step / num_steps)
                    else:
                        step_volume = target_volume

                    midi_events.append({
                        'type': 'controller',
                        'time': step_time,
                        'controller': 7,  # Volume CC
                        'value': step_volume
                    })

                # Update current velocity to target
                velocity = target_volume
                i += 1

            elif event.type == IREventType.PAN_FADE:
                # Pan fade - generate CC 10 events at 2-tick intervals
                assert event.duration is not None and event.value is not None, "PAN_FADE must have duration and value"
                fade_duration = event.duration * tick_scale  # Convert to MIDI ticks
                target_pan = event.value
                # We need to track current pan value - for now use 64 (center) as default
                start_pan = 64  # TODO: track pan state properly

                # Generate pan events at 2-tick intervals
                num_steps = max(1, fade_duration // 2)
                for step in range(num_steps + 1):
                    step_time = total_time + (step * 2)
                    # Linear interpolation
                    if num_steps > 0:
                        step_pan = int(start_pan + (target_pan - start_pan) * step / num_steps)
                    else:
                        step_pan = target_pan

                    midi_events.append({
                        'type': 'controller',
                        'time': step_time,
                        'controller': 10,  # Pan CC
                        'value': step_pan
                    })

                i += 1

            elif event.type == IREventType.SLUR_ON:
                slur_enabled = True
                # Emit MIDI CC 68 (legato pedal) = 127
                midi_events.append({
                    'type': 'controller',
                    'time': total_time,
                    'controller': 68,  # Legato pedal CC
                    'value': 127
                })
                i += 1

            elif event.type == IREventType.SLUR_OFF:
                slur_enabled = False
                # Emit MIDI CC 68 (legato pedal) = 0
                midi_events.append({
                    'type': 'controller',
                    'time': total_time,
                    'controller': 68,  # Legato pedal CC
                    'value': 0
                })
                i += 1

            elif event.type == IREventType.ROLL_ON:
                roll_enabled = True
                i += 1

            elif event.type == IREventType.ROLL_OFF:
                roll_enabled = False
                i += 1

            elif event.type == IREventType.STACCATO:
                # Set staccato percentage for all subsequent notes
                assert event.value is not None, "STACCATO event must have value"
                staccato_percentage = int(event.value)
                i += 1

            elif event.type == IREventType.UTILITY_DURATION:
                # Override duration for next note only
                assert event.value is not None, "UTILITY_DURATION event must have value"
                utility_duration_override = int(event.value)
                i += 1

            elif event.type == IREventType.MASTER_VOLUME:
                # Set master volume (SoM 0xF8) - global volume multiplier
                assert event.value is not None, "MASTER_VOLUME event must have value"
                master_volume = int(event.value)
                i += 1

            elif event.type == IREventType.VOLUME_MULTIPLIER:
                # Set volume multiplier (CT/FF3 0xF4) - per-track multiplier
                assert event.value is not None, "VOLUME_MULTIPLIER event must have value"
                volume_multiplier = int(event.value)
                i += 1

            elif event.type == IREventType.PERCUSSION_MODE_ON:
                # Percussion mode handling is done in Pass 1
                i += 1

            elif event.type == IREventType.PERCUSSION_MODE_OFF:
                # Percussion mode handling is done in Pass 1
                i += 1

            elif event.type == IREventType.LOOP_START:
                # Push loop onto stack
                loop_stack.append({
                    'start_idx': i + 1,  # Next event after LOOP_START
                    'count': 0,
                    'max_count': event.loop_count
                })
                i += 1

            elif event.type == IREventType.LOOP_END:
                # Pop and potentially repeat
                if loop_stack:
                    loop = loop_stack[-1]
                    loop['count'] += 1

                    if loop['count'] < loop['max_count']:
                        # Repeat - jump back to start
                        i = loop['start_idx']
                    else:
                        # Done - pop and continue
                        loop_stack.pop()
                        i += 1
                else:
                    # Unmatched LOOP_END - skip
                    i += 1

            elif event.type == IREventType.GOTO:
                # Find target event
                if event.target_offset is None:
                    # Invalid GOTO - halt
                    break

                target_idx = self._find_event_by_offset(ir_events, event.target_offset)

                if target_idx is None:
                    # Invalid target - halt
                    break

                # Check if backwards (loop)
                if target_idx < i:
                    # Backwards GOTO - loop condition
                    if target_loop_time > 0 and total_time >= target_loop_time:
                        # Reached target duration - halt
                        break
                    else:
                        # Continue looping
                        i = target_idx
                else:
                    # Forward GOTO - could be cross-track
                    # For now, just jump forward
                    i = target_idx

            elif event.type == IREventType.HALT:
                # End of track
                break

            else:
                # Unknown event type - skip
                i += 1

        return midi_events
