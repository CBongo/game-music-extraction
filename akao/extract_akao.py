#!/usr/bin/env python3
"""
VGM Sequence Extractor
Extracts music sequences from game ROMs/ISOs to text disassembly and MIDI files.
Chris Bongaarts - 2025-11-02 with help from Claude
"""

import struct
import yaml
from pathlib import Path
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod
from midiutil import MIDIFile
import re
import pycdlib.pycdlib as pycdlib_module
import io
import xml.etree.ElementTree as ET
from xml.dom import minidom


# Global constants
NOTE_NAMES = ["C ", "C#", "D ", "D#", "E ", "F ", "F#",
              "G ", "G#", "A ", "A#", "B "]

# General MIDI instrument names for reference
GM_INSTRUMENTS = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavi",
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar", "Guitar harmonics",
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "SynthStrings 1", "SynthStrings 2",
    "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "SynthBrass 1", "SynthBrass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute",
    "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)",
    "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bag pipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot"
]


@dataclass
class PatchInfo:
    """Information about an instrument patch."""
    gm_patch: int  # General MIDI patch number (0-127, or negative for percussion)
    transpose: int = 0  # Semitones to transpose
    name: Optional[str] = None  # Human-readable instrument name

    def is_percussion(self) -> bool:
        """Check if this patch represents percussion."""
        return self.gm_patch < 0


class PatchMapper:
    """Maps game-specific patch numbers to General MIDI patches."""

    def __init__(self, patch_map: Optional[Dict] = None):
        """Initialize with optional patch mapping configuration."""
        self.patch_map: Dict[int, PatchInfo] = {}

        if patch_map:
            for patch_num, info in patch_map.items():
                if isinstance(info, dict):
                    self.patch_map[patch_num] = PatchInfo(
                        gm_patch=info.get('gm_patch', 0),
                        transpose=info.get('transpose', 0),
                        name=info.get('name')
                    )
                elif isinstance(info, int):
                    # Simple mapping: just GM patch number
                    self.patch_map[patch_num] = PatchInfo(gm_patch=info)

    def get_patch_info(self, patch_num: int) -> PatchInfo:
        """Get patch info for a given patch number, with default fallback."""
        if patch_num in self.patch_map:
            return self.patch_map[patch_num]
        # Default: use piano (patch 0) with no transposition
        return PatchInfo(gm_patch=0, name="Acoustic Grand Piano")

    def get_instrument_name(self, patch_num: int) -> str:
        """Get human-readable instrument name for a patch."""
        info = self.get_patch_info(patch_num)
        if info.name:
            return info.name
        if info.is_percussion():
            return f"Percussion (GM {-info.gm_patch})"
        if 0 <= info.gm_patch < len(GM_INSTRUMENTS):
            return GM_INSTRUMENTS[info.gm_patch]
        return f"Unknown Instrument ({info.gm_patch})"


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


@dataclass
class ROMTable:
    """Configuration for a table to read from ROM."""
    address: int
    size: int  # number of items
    data_type: str = 'B'  # struct format code (B=byte, H=ushort, etc.)


@dataclass
class SongMetadata:
    """Metadata for a single song."""
    id: int
    title: Optional[str] = None
    sector: Optional[int] = None
    length: int = 0
    offset: Optional[int] = None
    file_path: Optional[str] = None  # ISO path or default_source_file path


class SequenceFormat(ABC):
    """Abstract base class for sequence format handlers."""
    
    @abstractmethod
    def parse_header(self, data: bytes) -> Dict:
        """Parse the sequence header and return metadata."""
        pass
    
    @abstractmethod
    def parse_track(self, data: bytes, offset: int, track_num: int) -> Tuple[List, List]:
        """
        Parse a single track's data.
        Returns (disasm_lines, midi_events) tuple.
        """
        pass
    
    @abstractmethod
    def get_track_offsets(self, data: bytes, header: Dict) -> List[int]:
        """Get list of track data offsets from header info."""
        pass


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
        self.tempo_factor = config.get('tempo_factor', 13107200000)
    
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
    
    def parse_header(self, data: bytes) -> Dict:
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
    
    def parse_track(self, data: bytes, offset: int, track_num: int) -> Tuple[List, List]:
        """Parse a single track's event data."""
        disasm = []
        midi_events = []

        # Track state (use config defaults)
        octave = self.default_octave
        velocity = self.default_velocity
        total_time = 0
        tempo = self.default_tempo
        current_patch = 0  # Current instrument patch
        
        # Repeat loop state (separate for disasm and MIDI)
        repeat_stack = []  # Stack for MIDI processing
        in_repeat_for_disasm = False  # Flag to avoid duplicating disasm output
        
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
                    midi_note = 12 * octave + notenum
                    
                    # Debug: check for invalid values
                    if midi_note < 0 or midi_note > 127:
                        disasm.append(f"  Warning: Invalid MIDI note {midi_note} (octave={octave}, notenum={notenum}) at {p-1:08X}, clamping")
                    
                    # Clamp MIDI note to valid range (0-127)
                    midi_note = max(0, min(127, midi_note))
                    
                    # Add note even if duration is non-positive (may be extended by ties)
                    note_duration = dur - 2
                    midi_events.append({
                        'type': 'note',
                        'time': total_time,
                        'duration': note_duration,
                        'note': midi_note,
                        'velocity': velocity,
                        'patch': current_patch  # Track which patch plays this note
                    })
                elif notenum == 12:
                    # Tie - extend the last note's duration
                    line += "Tie          "
                    # Find the last note event and extend its duration
                    for i in range(len(midi_events) - 1, -1, -1):
                        if midi_events[i]['type'] == 'note':
                            midi_events[i]['duration'] += dur
                            break
                else:
                    # Rest
                    line += "Rest         "
                
                line += f"Dur {dur:02X}"
                total_time += dur
            
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
                    line += f" ~{tempo // 218} bpm"
                    midi_events.append({
                        'type': 'tempo',
                        'time': total_time,
                        'tempo': tempo
                    })
                elif op1 == 0x06:
                    # Goto - end of track
                    break
                elif op1 == 0x14 and len(operands) >= 1:
                    # Program Change (FE 14)
                    current_patch = operands[0]
                    midi_events.append({
                        'type': 'program_change',
                        'time': total_time,
                        'patch': current_patch
                    })
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
                    current_patch = operands[0]
                    midi_events.append({
                        'type': 'program_change',
                        'time': total_time,
                        'patch': current_patch
                    })
                elif cmd == 0xA5 and operands:
                    octave = operands[0]
                elif cmd == 0xA6:
                    octave += 1
                elif cmd == 0xA7:
                    octave -= 1
                elif cmd == 0xA8 and operands:
                    velocity = operands[0]
                elif cmd == 0xC8:
                    # Begin repeat - push loop start position
                    repeat_stack.append({'start': p, 'count': 0, 'first_pass': True})
                elif cmd == 0xC9 and operands:
                    # End repeat
                    if repeat_stack:
                        loop = repeat_stack[-1]
                        loop['count'] += 1
                        if loop['count'] < operands[0]:
                            # Repeat again - jump back but mark we're in a repeat
                            loop['first_pass'] = False
                            p = loop['start']
                            continue
                        else:
                            # Done repeating
                            repeat_stack.pop()
                elif cmd == 0xA0 or cmd == 0xFF:
                    # Halt
                    disasm.append(line)
                    break
            
            # Only add to disasm on first pass through loops
            if not repeat_stack or repeat_stack[-1].get('first_pass', True):
                disasm.append(line)
        
        return disasm, midi_events


class SequenceExtractor:
    """Main extractor class."""

    def __init__(self, config_path: str, source_file: str, patch_based_tracks: bool = False):
        """Initialize with YAML config file and source ISO/ROM file."""
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        self.source_file = source_file
        self.console_type = self.config.get('console_type', 'psx')
        self.output_dir = Path(self.config.get('output_dir', 'output'))
        self.patch_based_tracks = patch_based_tracks  # Organize by patch instead of sequence track

        # Initialize patch mapper
        patch_map_config = self.config.get('patch_map', {})
        self.patch_mapper = PatchMapper(patch_map_config)
        
        # Detect sector size if not specified
        if 'sector_size' in self.config:
            self.sector_size = self.config['sector_size']
        else:
            self.sector_size = self._detect_sector_size()
            print(f"Detected sector size: {self.sector_size} bytes")
        
        # Keep track of raw sector size for sequence extraction
        self.raw_sector_size = self.sector_size
        
        # Open the ISO with pycdlib
        self.iso = pycdlib_module.PyCdlib()
        
        if self.sector_size == 2352:
            # Use our wrapper for on-the-fly conversion
            print("Opening raw CD-ROM image with on-the-fly conversion...")
            self._raw_wrapper = Raw2352FileWrapper(source_file)
            self.iso.open_fp(self._raw_wrapper)
        else:
            # Standard 2048-byte ISO
            self.iso.open(source_file)
        
        # Initialize file handle cache first
        self._exe_file_handle = None  # Will hold open file handle for executable
        self._img_file_cache = {}  # Cache of open file handles for large IMG files

        # Find and load the executable ROM
        # This will set _exe_file_handle as a side effect
        self.rom_data: bytes = self._load_executable()

        # Create format handler with ROM data
        format_name = self.config.get('format', 'akao_newstyle')
        if format_name == 'akao_newstyle':
            self.format_handler = AKAONewStyle(self.config, self.rom_data, exe_iso_reader=self)
        else:
            raise ValueError(f"Unknown format: {format_name}")
    
    def _load_executable(self) -> bytes:
        """Load the console executable from the disc image."""
        if self.console_type == 'psx':
            return self._load_psx_executable()
        else:
            raise ValueError(f"Unsupported console type: {self.console_type}")
    
    def _detect_sector_size(self) -> int:
        """Heuristically detect CD-ROM sector size (2048 or 2352 bytes)."""
        import os
        file_size = os.path.getsize(self.source_file)
        
        # Check if file size is evenly divisible by common sector sizes
        if file_size % 2352 == 0:
            return 2352  # Raw CD-ROM sectors (Mode 2 Form 1 with headers)
        elif file_size % 2048 == 0:
            return 2048  # Standard ISO 9660 sectors (data only)
        else:
            # Default to 2352 if neither divides evenly
            print(f"Warning: File size {file_size} not evenly divisible by 2048 or 2352, defaulting to 2352")
            return 2352
    
    def _load_psx_executable(self) -> bytes:
        """Load PSX executable by reading SYSTEM.CNF from the CD-ROM."""
        # Read SYSTEM.CNF from the root directory
        try:
            system_cnf_data = self._read_file_from_iso('/SYSTEM.CNF;1')
            system_cnf = system_cnf_data.decode('ascii', errors='ignore')
        except Exception as e:
            raise ValueError(f"Could not read SYSTEM.CNF: {e}")
        
        # Parse BOOT line to get executable name
        # Format: BOOT = cdrom:\PATH\EXECUTABLE.EXE;1
        boot_match = re.search(r'BOOT\s*=\s*cdrom:\\([^;]+)', system_cnf, re.IGNORECASE)
        if not boot_match:
            raise ValueError("Could not find BOOT line in SYSTEM.CNF")
        
        exe_path = boot_match.group(1).replace('\\', '/')
        print(f"Found PSX executable: {exe_path}")
        
        # Read the executable (add leading / and ensure ;1 version)
        exe_iso_path = '/' + exe_path
        if ';' not in exe_iso_path:
            exe_iso_path += ';1'
        
        # Just read the header for now - we'll open the file for table reads later
        from io import BytesIO
        output = BytesIO()

        # Read from ISO
        iso_path_upper = exe_iso_path.upper()
        if ';' not in iso_path_upper:
            iso_path_upper += ';1'

        self.iso.get_file_from_iso_fp(output, iso_path=iso_path_upper)
        exe_data = output.getvalue()
        print(f"Found PSX executable: {len(exe_data)} bytes")

        # Keep the full executable in a BytesIO for efficient seeking
        self._exe_file_handle = BytesIO(exe_data)

        # Only return the header for ROM address conversion
        return exe_data[:0x800]
    
    def _read_file_from_iso(self, iso_path: str) -> bytes:
        """Read a file from the ISO image using pycdlib."""
        # pycdlib expects paths in uppercase with version numbers
        iso_path_upper = iso_path.upper()

        # Ensure version number
        if ';' not in iso_path_upper:
            iso_path_upper += ';1'

        # Read file into memory
        from io import BytesIO
        output = BytesIO()
        try:
            self.iso.get_file_from_iso_fp(output, iso_path=iso_path_upper)
            return output.getvalue()
        except Exception as e:
            raise ValueError(f"Could not find path {iso_path_upper} in ISO: {e}")

    def _read_executable_bytes(self, offset: int, length: int) -> bytes:
        """Read bytes from the executable file at the given offset."""
        if not self._exe_file_handle:
            raise ValueError("Executable file handle not available")

        # Seek to the offset and read the requested bytes
        self._exe_file_handle.seek(offset)
        return self._exe_file_handle.read(length)

    def _read_file_chunk_from_iso(self, iso_path: str, offset: int, length: int) -> bytes:
        """Read a chunk from a file in the ISO without loading the whole file."""
        # Normalize the ISO path
        if not iso_path.startswith('/'):
            iso_path = '/' + iso_path
        iso_path_upper = iso_path.upper()
        if ';' not in iso_path_upper:
            iso_path_upper += ';1'

        # Check if we have this file cached
        if iso_path_upper not in self._img_file_cache:
            # Read the file once and cache it as a BytesIO
            from io import BytesIO
            output = BytesIO()
            self.iso.get_file_from_iso_fp(output, iso_path=iso_path_upper)
            self._img_file_cache[iso_path_upper] = output

        # Use the cached file handle to seek and read
        file_handle = self._img_file_cache[iso_path_upper]
        file_handle.seek(offset)
        return file_handle.read(length)
    
    def extract_sequence_data(self, song: SongMetadata) -> bytes:
        """Extract raw sequence data from source file."""
        # Check if song specifies a file path with offset
        if song.file_path:
            # Read from a specific file in the ISO
            if song.offset is not None:
                # Direct file offset (like FF9.IMG)
                # Use seek/read to avoid loading the entire large file
                return self._read_file_chunk_from_iso(song.file_path, song.offset, song.length)
            else:
                # Just read the entire file from ISO (for small files)
                return self._read_file_from_iso(song.file_path)
        
        # Otherwise read from raw sectors (for embedded sequences like Chrono Cross)
        # Use the original raw image, not the converted one
        if song.sector is None:
            raise ValueError(f"Song {song.id:02X} has no sector or file_path specified")
        
        with open(self.source_file, 'rb') as f:
            # The sector numbers in metadata are LOGICAL 2048-byte sectors
            # For raw 2352-byte images, we need to:
            # 1. Calculate the raw sector number (same as logical)
            # 2. Seek to raw_sector * 2352 + 24 (skip sync/header)
            # 3. Read from the data portion
            
            if self.raw_sector_size == 2352:
                # Skip to the sector and past the 24-byte header
                raw_offset = song.sector * 2352 + 24
                f.seek(raw_offset)
                
                # Read data, accounting for headers in each sector
                data = bytearray()
                bytes_remaining = song.length
                current_sector = song.sector
                offset_in_sector = 0
                
                while bytes_remaining > 0:
                    # How much can we read from this sector?
                    bytes_in_sector = min(2048 - offset_in_sector, bytes_remaining)
                    
                    # Seek to the right position
                    sector_offset = current_sector * 2352 + 24 + offset_in_sector
                    f.seek(sector_offset)
                    
                    # Read the data
                    chunk = f.read(bytes_in_sector)
                    data.extend(chunk)
                    
                    bytes_remaining -= len(chunk)
                    current_sector += 1
                    offset_in_sector = 0
                
                return bytes(data)
            else:
                # Standard 2048-byte sectors
                offset = song.sector * self.raw_sector_size if song.offset is None else song.offset
                f.seek(offset)
                return f.read(song.length)
    
    def disassemble_to_text(self, song: SongMetadata, data: bytes) -> str:
        """Generate text disassembly of sequence."""
        output = []
        
        # Parse header
        header = self.format_handler.parse_header(data)
        
        output.append(f"Magic:   {header['magic']}")
        output.append(f"ID:      {header['id']:02X}")
        output.append(f"Length:  {header['length']:04X}")
        output.append(f"Voice mask:  {header['voice_mask']:08X}")
        output.append(f"Voice count: {header['voice_count']}")
        output.append("")
        
        # Get track offsets
        track_offsets = self.format_handler.get_track_offsets(data, header)
        
        for i, offset in enumerate(track_offsets):
            output.append(f"Voice {i:02X} @ {offset:04X}")
        output.append("")
        
        # Parse each track
        for i, offset in enumerate(track_offsets):
            disasm, _ = self.format_handler.parse_track(data, offset, i)
            output.extend(disasm)
            output.append("")
        
        return '\n'.join(output)
    
    def generate_midi(self, song: SongMetadata, data: bytes, output_path: Path):
        """Generate MIDI file from sequence with patch mapping support."""
        try:
            header = self.format_handler.parse_header(data)
            track_offsets = self.format_handler.get_track_offsets(data, header)

            # Parse all tracks
            parsed_tracks = []
            for voice_num, offset in enumerate(track_offsets):
                try:
                    _, midi_events = self.format_handler.parse_track(data, offset, voice_num)
                    has_notes = any(e['type'] == 'note' for e in midi_events)
                    parsed_tracks.append({
                        'voice_num': voice_num,
                        'events': midi_events,
                        'has_notes': has_notes
                    })
                except Exception as e:
                    raise Exception(f"Failed parsing track {voice_num} at offset {offset:04X}: {e}") from e

            if self.patch_based_tracks:
                # Organize by patch
                tracks_to_write, conductor_events = self._organize_by_patch(parsed_tracks)
            else:
                # Organize by sequence voice
                tracks_to_write = [t for t in parsed_tracks if t['has_notes']]
                # Extract conductor events from voice-based tracks
                conductor_events = []
                for track in parsed_tracks:
                    conductor_events.extend([e for e in track['events'] if e['type'] == 'tempo'])

            # Create MIDI file
            num_tracks = len(tracks_to_write) + 1  # +1 for conductor track
            midi = MIDIFile(num_tracks, file_format=1)

            # Conductor track
            midi.addTrackName(0, 0, "Conductor")
            midi.addTempo(0, 0, 120)

            # Add conductor events (tempo changes)
            for event in conductor_events:
                time_beats = event['time'] / 48.0
                bpm = event['tempo'] / 218
                midi.addTempo(0, time_beats, bpm)

            # Add tracks
            for track_idx, track_info in enumerate(tracks_to_write):
                self._write_midi_track(midi, track_idx + 1, track_info)

            # Write file
            with open(output_path, 'wb') as f:
                midi.writeFile(f)
        except Exception as e:
            import traceback
            raise Exception(f"MIDI generation failed: {e}\n{traceback.format_exc()}") from e

    def _organize_by_patch(self, parsed_tracks: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """Reorganize events by patch instead of voice.

        Returns:
            Tuple of (patch_tracks, conductor_events) where conductor_events are tempo changes
        """
        # Collect all events across all voices
        patch_events: Dict[int, List] = {}
        conductor_events = []

        for track in parsed_tracks:
            for event in track['events']:
                if event['type'] == 'note':
                    patch = event.get('patch', 0)
                    if patch not in patch_events:
                        patch_events[patch] = []
                    patch_events[patch].append(event)
                elif event['type'] == 'tempo':
                    # Collect tempo events separately - they go on conductor track
                    conductor_events.append(event)

        # Create track info for each patch
        result = []
        for patch_num in sorted(patch_events.keys()):
            result.append({
                'patch': patch_num,
                'events': patch_events[patch_num],
                'has_notes': True,
                'is_patch_based': True
            })

        return result, conductor_events

    def _write_midi_track(self, midi: MIDIFile, track_num: int, track_info: Dict):
        """Write a single track to MIDI file."""
        is_patch_based = track_info.get('is_patch_based', False)

        if is_patch_based:
            # Patch-based track
            patch_num = track_info['patch']
            patch_info = self.patch_mapper.get_patch_info(patch_num)
            instrument_name = self.patch_mapper.get_instrument_name(patch_num)

            midi.addTrackName(track_num, 0, f"{patch_num:02X} {instrument_name}")

            # Determine channel based on track number, not patch number
            if patch_info.is_percussion():
                channel = 9  # GM percussion channel
            else:
                # Use channel based on track position (skip channel 9 for percussion)
                # track_num is 1-based (0 is conductor), so subtract 1 for 0-based channel
                channel = track_num - 1
                if channel >= 9:
                    channel += 1  # Skip channel 9 by mapping tracks 10+ to channels 10+

            # Set program
            if not patch_info.is_percussion():
                midi.addProgramChange(track_num, channel, 0, patch_info.gm_patch)

            # Add notes - need to handle overlapping notes on same pitch
            # Sort by time and collect only note events
            note_events = [e for e in track_info['events'] if e['type'] == 'note']
            note_events.sort(key=lambda e: (e['time'], e['note']))

            # Track active notes to prevent overlaps
            # Key: (note_number), Value: end_time
            active_notes = {}

            for event in note_events:
                time_beats = event['time'] / 48.0
                duration_beats = event['duration'] / 48.0

                if duration_beats <= 0:
                    continue

                # Apply transposition
                note = event['note'] + patch_info.transpose

                # For percussion, use GM percussion note if specified
                if patch_info.is_percussion():
                    note = -patch_info.gm_patch  # Use the percussion note number

                note = max(0, min(127, note))  # Clamp to valid range

                # Check if there's already an active note at this pitch
                current_time = event['time']
                note_end = current_time + event['duration']

                if note in active_notes:
                    # There's already a note playing - we need to end it before starting new one
                    active_end = active_notes[note]
                    if current_time < active_end:
                        # Notes would overlap - shorten the current note to end before the overlap
                        # Leave at least a small gap
                        max_duration = max(1, active_end - current_time - 1)  # At least 1 tick gap
                        note_end = current_time + max_duration
                        duration_beats = max_duration / 48.0

                # Add the note
                if duration_beats > 0:
                    midi.addNote(track_num, channel, note,
                               time_beats, duration_beats, event['velocity'])
                    # Track this note as active
                    active_notes[note] = note_end
        else:
            # Voice-based track
            voice_num = track_info['voice_num']
            midi.addTrackName(track_num, 0, f"Voice {voice_num:02X}")

            # Map voice to channel (skip channel 9 for percussion)
            channel = voice_num
            if channel >= 9:
                channel += 1
            channel = channel % 16

            # Track current patch for this voice
            current_patch = 0

            for event in track_info['events']:
                time_beats = event['time'] / 48.0

                if event['type'] == 'program_change':
                    # Handle program change
                    current_patch = event['patch']
                    patch_info = self.patch_mapper.get_patch_info(current_patch)

                    if not patch_info.is_percussion():
                        midi.addProgramChange(track_num, channel, time_beats, patch_info.gm_patch)

                elif event['type'] == 'note':
                    duration_beats = event['duration'] / 48.0
                    if duration_beats > 0:
                        # Get patch info for this note
                        note_patch = event.get('patch', current_patch)
                        patch_info = self.patch_mapper.get_patch_info(note_patch)

                        # Apply transposition
                        note = event['note'] + patch_info.transpose
                        note = max(0, min(127, note))

                        midi.addNote(track_num, channel, note,
                                   time_beats, duration_beats, event['velocity'])

                # Tempo events are now handled on conductor track, not here

    def generate_musicxml(self, song: SongMetadata, data: bytes, output_path: Path):
        """Generate MusicXML from sequence data."""
        # Parse tracks using same logic as MIDI generation
        header = self.format_handler.parse_header(data)
        track_offsets = self.format_handler.get_track_offsets(data, header)

        # Parse all tracks
        parsed_tracks = []
        for voice_num, offset in enumerate(track_offsets):
            _, midi_events = self.format_handler.parse_track(data, offset, voice_num)
            has_notes = any(e['type'] == 'note' for e in midi_events)
            parsed_tracks.append({
                'voice_num': voice_num,
                'events': midi_events,
                'has_notes': has_notes
            })

        # Organize tracks
        if self.patch_based_tracks:
            tracks_to_write, _ = self._organize_by_patch(parsed_tracks)
        else:
            tracks_to_write = [t for t in parsed_tracks if t['has_notes']]

        # Create MusicXML document
        root = ET.Element('score-partwise', version='3.1')

        # Add work title
        work = ET.SubElement(root, 'work')
        work_title = ET.SubElement(work, 'work-title')
        work_title.text = song.title if hasattr(song, 'title') and song.title else f"AKAO_{song.id:02X}"

        # Create part list
        part_list = ET.SubElement(root, 'part-list')

        for track_idx, track_info in enumerate(tracks_to_write):
            part_id = f"P{track_idx + 1}"
            score_part = ET.SubElement(part_list, 'score-part', id=part_id)
            part_name = ET.SubElement(score_part, 'part-name')

            if track_info.get('is_patch_based'):
                patch_num = track_info['patch']
                instrument_name = self.patch_mapper.get_instrument_name(patch_num)
                part_name.text = f"{patch_num:02X} {instrument_name}"
            else:
                part_name.text = f"Voice {track_info['voice_num'] + 1}"

        # Create parts
        for track_idx, track_info in enumerate(tracks_to_write):
            part_id = f"P{track_idx + 1}"
            part = ET.SubElement(root, 'part', id=part_id)

            # Get sorted events
            events = sorted([e for e in track_info['events'] if e['type'] == 'note'],
                          key=lambda e: e.get('time', 0))

            if not events:
                # Empty part - add one empty measure
                measure = ET.SubElement(part, 'measure', number='1')
                attributes = ET.SubElement(measure, 'attributes')
                divisions = ET.SubElement(attributes, 'divisions')
                divisions.text = '48'
                continue

            # Calculate measure breaks (4/4 time, 48 ticks per quarter = 192 ticks per measure)
            divisions_per_quarter = 48
            divisions_per_measure = divisions_per_quarter * 4

            # Find max time
            max_time = max(e['time'] + e['duration'] for e in events)
            num_measures = (max_time + divisions_per_measure - 1) // divisions_per_measure

            # Create measures
            for measure_num in range(1, int(num_measures) + 1):
                measure = ET.SubElement(part, 'measure', number=str(measure_num))

                # Add attributes to first measure
                if measure_num == 1:
                    attributes = ET.SubElement(measure, 'attributes')
                    divisions = ET.SubElement(attributes, 'divisions')
                    divisions.text = str(divisions_per_quarter)

                    time_elem = ET.SubElement(attributes, 'time')
                    beats = ET.SubElement(time_elem, 'beats')
                    beats.text = '4'
                    beat_type = ET.SubElement(time_elem, 'beat-type')
                    beat_type.text = '4'

                    clef = ET.SubElement(attributes, 'clef')
                    sign = ET.SubElement(clef, 'sign')
                    sign.text = 'G'
                    line = ET.SubElement(clef, 'line')
                    line.text = '2'

                # Find events in this measure
                measure_start = (measure_num - 1) * divisions_per_measure
                measure_end = measure_num * divisions_per_measure
                measure_events = [e for e in events
                                if measure_start <= e['time'] < measure_end]

                current_position = measure_start

                for event in measure_events:
                    # Add forward if needed to advance time
                    if event['time'] > current_position:
                        gap = event['time'] - current_position
                        forward = ET.SubElement(measure, 'forward')
                        duration_elem = ET.SubElement(forward, 'duration')
                        duration_elem.text = str(gap)
                        current_position = event['time']

                    # Calculate note duration, clamping to measure boundary
                    note_duration = event['duration']
                    note_end_time = event['time'] + note_duration

                    # If note extends past measure end, clamp it
                    if note_end_time > measure_end:
                        note_duration = measure_end - event['time']

                    if note_duration <= 0:
                        continue

                    # Add note
                    note_elem = ET.SubElement(measure, 'note')

                    # Pitch
                    pitch = ET.SubElement(note_elem, 'pitch')
                    midi_note = event['note']

                    # Apply transposition if patch-based
                    if track_info.get('is_patch_based'):
                        patch_num = track_info['patch']
                        patch_info = self.patch_mapper.get_patch_info(patch_num)
                        midi_note += patch_info.transpose

                    # Convert MIDI note to pitch notation
                    note_names = ['C', 'C', 'D', 'D', 'E', 'F', 'F', 'G', 'G', 'A', 'A', 'B']
                    alterations = [0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0]
                    octave = (midi_note // 12) - 1
                    note_class = midi_note % 12

                    step = ET.SubElement(pitch, 'step')
                    step.text = note_names[note_class]

                    if alterations[note_class] != 0:
                        alter = ET.SubElement(pitch, 'alter')
                        alter.text = str(alterations[note_class])

                    octave_elem = ET.SubElement(pitch, 'octave')
                    octave_elem.text = str(octave)

                    # Duration (clamped to measure boundary)
                    duration_elem = ET.SubElement(note_elem, 'duration')
                    duration_elem.text = str(note_duration)

                    # Type (approximate based on duration)
                    if note_duration >= divisions_per_quarter * 4:
                        note_type_text = 'whole'
                    elif note_duration >= divisions_per_quarter * 2:
                        note_type_text = 'half'
                    elif note_duration >= divisions_per_quarter:
                        note_type_text = 'quarter'
                    elif note_duration >= divisions_per_quarter // 2:
                        note_type_text = 'eighth'
                    elif note_duration >= divisions_per_quarter // 4:
                        note_type_text = '16th'
                    else:
                        note_type_text = '32nd'

                    note_type = ET.SubElement(note_elem, 'type')
                    note_type.text = note_type_text

                    current_position += note_duration

                # Fill remainder of measure with a forward/rest if needed
                if current_position < measure_end:
                    remaining = measure_end - current_position
                    forward = ET.SubElement(measure, 'forward')
                    duration_elem = ET.SubElement(forward, 'duration')
                    duration_elem.text = str(remaining)

        # Format and write XML
        xml_str = ET.tostring(root, encoding='unicode')
        dom = minidom.parseString(xml_str)
        pretty_xml = dom.toprettyxml(indent='  ')

        # Remove extra blank lines
        lines = [line for line in pretty_xml.split('\n') if line.strip()]
        pretty_xml = '\n'.join(lines)

        output_path.write_text(pretty_xml, encoding='utf-8')

    def extract_all(self):
        """Extract all songs defined in config."""
        songs = [SongMetadata(**s) for s in self.config.get('songs', [])]

        # Create output directories
        text_dir = Path('txt')
        midi_dir = Path('mid')
        xml_dir = Path('xml')
        text_dir.mkdir(exist_ok=True)
        midi_dir.mkdir(exist_ok=True)
        xml_dir.mkdir(exist_ok=True)
        
        for song in songs:
            # Use title if provided, otherwise fall back to AKAO ID
            display_name = song.title if hasattr(song, 'title') and song.title else f"AKAO_{song.id:02X}"
            print(f"Processing: {display_name}")
            
            try:
                # Extract data
                data = self.extract_sequence_data(song)
                
                # Generate text disassembly
                text_output = self.disassemble_to_text(song, data)
                text_file = text_dir / f"{song.id:02X} {display_name}.txt"
                text_file.write_text(text_output)

                # Generate MIDI
                midi_file = midi_dir / f"{song.id:02X} {display_name}.mid"
                self.generate_midi(song, data, midi_file)

                # Generate MusicXML
                xml_file = xml_dir / f"{song.id:02X} {display_name}.musicxml"
                self.generate_musicxml(song, data, xml_file)

                print(f"  ✓ Generated {text_file.name}, {midi_file.name}, and {xml_file.name}")
            
            except Exception as e:
                print(f"  ✗ Error: {e}")
        
        # Close the ISO and wrapper when done
        self.iso.close()
        if hasattr(self, '_raw_wrapper'):
            self._raw_wrapper.close()
        if self._exe_file_handle:
            self._exe_file_handle.close()
        # Close all cached IMG file handles
        for handle in self._img_file_cache.values():
            handle.close()


def main():
    """Main entry point."""
    import sys
    import traceback

    # Parse command-line arguments
    patch_based_tracks = False
    args = []

    for arg in sys.argv[1:]:
        if arg == '--patch-based-tracks':
            patch_based_tracks = True
        else:
            args.append(arg)

    if len(args) < 2:
        print("Usage: python extract_akao.py <config.yaml> <source_file> [--patch-based-tracks]")
        print()
        print("Arguments:")
        print("  config.yaml             - Game metadata configuration file")
        print("  source_file             - ISO/ROM file containing the game data")
        print("  --patch-based-tracks    - Organize MIDI tracks by instrument/patch instead of sequence")
        print()
        print("Examples:")
        print("  python extract_akao.py ff9.yaml ff9.iso")
        print("  python extract_akao.py ff8.yaml ff8.iso --patch-based-tracks")
        sys.exit(1)

    config_file = args[0]
    source_file = args[1]

    try:
        extractor = SequenceExtractor(config_file, source_file, patch_based_tracks=patch_based_tracks)
        extractor.extract_all()
    except Exception as e:
        print(f"\nError: {e}")
        print("\nFull traceback:")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()