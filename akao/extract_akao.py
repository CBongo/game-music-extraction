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

# Import IR event classes
from ir_events import (
    IREvent, IRVoice, IRSequence, IREventType,
    make_note, make_rest, make_tie, make_tempo, make_patch_change,
    make_octave_set, make_octave_inc, make_octave_dec, make_volume,
    make_loop_start, make_loop_end, make_loop_break, make_goto, make_halt,
    make_vibrato_on, make_vibrato_off, make_tremolo_on, make_tremolo_off,
    make_portamento_on, make_portamento_off
)


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
    def parse_header(self, data: bytes, song_id: int = 0, use_alternate_pointers: bool = False) -> Dict:
        """Parse the sequence header and return metadata.

        Args:
            data: Song data buffer
            song_id: Song ID (optional, for format-specific lookups like FF3 instrument table)
            use_alternate_pointers: If True, use alternate voice pointers (format-specific)
        """
        pass
    
    @abstractmethod
    def parse_track(self, data: bytes, offset: int, track_num: int,
                    song_id: int = 0, instrument_table: Optional[List[int]] = None) -> Tuple[List, List]:
        """
        Parse a single track's data.
        Returns (disasm_lines, midi_events) tuple.

        Args:
            data: Song data buffer
            offset: Offset into data where this track starts
            track_num: Track/voice number
            song_id: Song ID (for format-specific lookups)
            instrument_table: List of instrument IDs for this song
        """
        pass
    
    @abstractmethod
    def get_track_offsets(self, data: bytes, header: Dict) -> List[int]:
        """Get list of track data offsets from header info."""
        pass

    @abstractmethod
    def _parse_track_pass1(self, data: bytes, offset: int, track_num: int,
                          instrument_table: List[int], vaddroffset: int = 0,
                          track_boundaries: Optional[Dict[int, Tuple[int, int]]] = None) -> Tuple[List, List[IREvent]]:
        """Pass 1: Linear parse to build disassembly and intermediate representation.

        Args:
            data: Song data buffer
            offset: Offset into data where this track starts
            track_num: Track/voice number
            instrument_table: List of instrument IDs for this song
            vaddroffset: Address offset for target address calculations (format-specific)

        Returns:
            Tuple of (disasm_lines, ir_events)
        """
        pass

    @abstractmethod
    def _parse_track_pass2(self, all_track_data: Dict, start_voice_num: int,
                          target_loop_time: int = 0) -> List[Dict]:
        """Pass 2: Expand IR events with loop execution to generate MIDI events.

        This pass takes all track data and executes from a starting voice, expanding loops,
        following GOTOs (including cross-track), and generating timed MIDI events.
        Loop information is read from all_track_data['tracks'][voice_num]['loop_info'].

        Args:
            all_track_data: Complete track data from parse_all_tracks() (includes loop_info per track)
            start_voice_num: Starting voice/track number to execute from
            target_loop_time: Target playthrough time in ticks (0 = no loop expansion)

        Returns:
            List of MIDI event dictionaries
        """
        pass

    @abstractmethod
    def _analyze_track_loops(self, ir_events: List[IREvent]) -> Dict:
        """Analyze IR events to find loop structure.

        Args:
            ir_events: IR events from pass 1

        Returns:
            Dict with loop analysis: {
                'has_backwards_goto': bool,
                'goto_target_idx': int,
                'intro_time': int,
                'loop_time': int,
                'target_time': int
            }
        """
        pass

    def _validate_track_address(self, track_num: int, target_spc_addr: int,
                                source_offset: int, event_type: str, has_timing_events: bool = False) -> bool:
        """Validate that a target address is within the valid range for this track.

        This is a hook for format-specific address validation. Subclasses can override
        to implement their own validation logic appropriate to their addressing scheme.

        Args:
            track_num: Track/voice number
            target_spc_addr: Target SPC RAM address to validate
            source_offset: Byte offset of the instruction containing this target
            event_type: Type of event (e.g., "GOTO", "LOOP_BREAK")
            has_timing_events: True if any notes/rests have been processed yet

        Returns:
            True if valid, False if invalid (warning already printed)
        """
        return True  # Default: no validation


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
    
    def parse_track(self, data: bytes, offset: int, track_num: int,
                    song_id: int = 0, instrument_table: Optional[List[int]] = None) -> Tuple[List, List]:
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


def snes_addr_to_offset(snes_addr: int, has_header: bool = True) -> int:
    """Convert SNES address to ROM file offset.

    SNES uses bank/offset addressing where addresses are in format 0xBBOOOO
    Bank BB and offset OOOO map to file as: bank * 0x8000 + (offset - 0x8000)
    If ROM has SMC header, add 0x200 bytes
    """
    bank = (snes_addr >> 16) & 0xFF
    offset = snes_addr & 0xFFFF
    file_offset = bank * 0x8000 + (offset - 0x8000)
    if has_header:
        file_offset += 0x200
    return file_offset


class SNESFF2(SequenceFormat):
    """SNES Final Fantasy II music format handler."""

    OPCODE_NAMES = {
        0xd2: 'Tempo', 0xd3: 'nop', 0xd4: 'Echo Volume',
        0xd5: 'Echo Settings', 0xd6: 'Portamento Settings',
        0xd7: 'Tremolo Settings', 0xd8: 'Vibrato Settings',
        0xd9: 'Pan Sweep Settings', 0xda: 'Set Octave',
        0xdb: 'Set Patch', 0xdc: 'Set Envelope',
        0xdd: 'Set Gain (Exp Dec Time)',
        0xde: 'Set Staccato (note dur ratio)',
        0xdf: 'Set Noise Clock', 0xe0: 'Start Repeat',
        0xe1: 'Inc Octave', 0xe2: 'Dec Octave', 0xe3: 'nop',
        0xe4: 'nop', 0xe5: 'nop', 0xe6: 'Portamento Off',
        0xe7: 'Tremolo Off', 0xe8: 'Vibrato Off',
        0xe9: 'Pan Sweep Off', 0xea: 'Enable Echo',
        0xeb: 'Disable Echo', 0xec: 'Enable Noise',
        0xed: 'Disable Noise', 0xee: 'Enable Pitchmod',
        0xef: 'Disable Pitchmod', 0xf0: 'End Repeat',
        0xf1: 'Halt', 0xf2: 'Voice Volume', 0xf3: 'Voice Balance',
        0xf4: 'Goto', 0xf5: 'Selective Repeat',
        0xf6: 'Goto 0760+X', 0xf7: 'Halt', 0xf8: 'Halt',
        0xf9: 'Halt', 0xfa: 'Halt', 0xfb: 'Halt',
        0xfc: 'Halt', 0xfd: 'Halt', 0xfe: 'Halt', 0xff: 'Halt'
    }

    def __init__(self, config: Dict, rom_data: bytes):
        """Initialize with game-specific config and ROM data."""
        self.config = config
        self.rom_data: bytes = rom_data
        self.has_smc_header = len(rom_data) % 1024 == 512  # SMC header is 512 bytes
        self.smc_header_size = 512 if self.has_smc_header else 0

        # Auto-detect ROM mapping mode (LoROM vs HiROM)
        self.rom_mapping = self._detect_rom_mapping()

        # Read duration table from ROM
        if 'duration_table' in config:
            table_cfg = config['duration_table']
            self.duration_table = self._read_rom_table(
                table_cfg.get('address'),
                table_cfg.get('size', 15),
                table_cfg.get('type', 'B')
            )
        else:
            # Default duration table
            self.duration_table = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]

        # Read opcode length table from ROM
        if 'opcode_table' in config:
            table_cfg = config['opcode_table']
            self.opcode_table = self._read_rom_table(
                table_cfg.get('address'),
                table_cfg.get('size', 46),
                table_cfg.get('type', 'B')
            )
        else:
            # Default opcode lengths (46 opcodes starting from 0xD2)
            self.opcode_table = [1] * 46

        # Get state defaults from config
        self.default_tempo = config.get('default_tempo', 255)
        self.default_octave = config.get('default_octave', 4)
        self.default_velocity = config.get('default_velocity', 64)
        self.tempo_factor = config.get('tempo_factor', 55296000)

        # Build patch map from config (instrument_id -> PatchInfo)
        self.patch_map = {}
        if 'patch_map' in config:
            for inst_id_str, patch_info in config['patch_map'].items():
                # Convert hex string to int (handles both "0x00" and "00" formats)
                if isinstance(inst_id_str, str):
                    inst_id = int(inst_id_str, 0)  # base 0 auto-detects hex with 0x prefix
                else:
                    inst_id = inst_id_str
                self.patch_map[inst_id] = patch_info

        # Get base address for song pointers
        self.base_address = config.get('base_address', 0x04C000)

        # Read song pointer table
        self.song_pointers = self._read_song_pointer_table()

    def _detect_rom_mapping(self) -> str:
        """Auto-detect ROM mapping mode (LoROM vs HiROM).

        Based on snesutil.pl:openrom logic - checks ROM header at two possible locations
        and validates with complement checksum.

        Returns:
            "lorom" or "hirom"
        """
        offset = self.smc_header_size

        # Read potential headers at LoROM and HiROM locations
        lorom_header_offset = offset + 0x7FC0
        hirom_header_offset = offset + 0xFFC0

        # Read 32-byte headers
        if lorom_header_offset + 32 <= len(self.rom_data):
            lorom_header = self.rom_data[lorom_header_offset:lorom_header_offset + 32]
        else:
            lorom_header = None

        if hirom_header_offset + 32 <= len(self.rom_data):
            hirom_header = self.rom_data[hirom_header_offset:hirom_header_offset + 32]
        else:
            hirom_header = None

        # Check complement checksums (bytes 28-29 = checksum, 30-31 = inverse checksum)
        # Format: A21C7v2 = 21 byte title, 7 single bytes, 2 words (little-endian)
        lorom_valid = False
        hirom_valid = False

        if lorom_header:
            # Checksum at offset 28-29, inverse at 30-31
            checksum = struct.unpack('<H', lorom_header[28:30])[0]
            inverse = struct.unpack('<H', lorom_header[30:32])[0]
            if (checksum ^ 0xFFFF) == inverse:
                lorom_valid = True

        if hirom_header:
            checksum = struct.unpack('<H', hirom_header[28:30])[0]
            inverse = struct.unpack('<H', hirom_header[30:32])[0]
            if (checksum ^ 0xFFFF) == inverse:
                hirom_valid = True

        # If checksum validation worked, use that
        if lorom_valid and not hirom_valid:
            return "lorom"
        elif hirom_valid and not lorom_valid:
            return "hirom"

        # Fallback: check mapping mode byte (offset 21 in header)
        # Bit 0-3 = mode: 0 = LoROM, 1 = HiROM
        if lorom_header and (lorom_header[21] & 0xF) == 0:
            return "lorom"
        if hirom_header and (hirom_header[21] & 0xF) == 1:
            return "hirom"

        # Default to LoROM (most common)
        return "lorom"

    def rom_offset_to_display_addr(self, offset: int) -> str:
        """Convert ROM file offset to SNES bank/address display format.

        Args:
            offset: ROM file offset (includes SMC header if present)

        Returns:
            Formatted string like "06/F727" (bank/address)
        """
        # Remove SMC header from offset
        rom_offset = offset - self.smc_header_size

        if self.rom_mapping == "hirom":
            # HiROM: bank = (offset / 0x10000) | 0xC0, addr = offset & 0xFFFF
            bank = (rom_offset // 0x10000) | 0xC0
            addr = rom_offset & 0xFFFF
        else:
            # LoROM: bank = offset / 0x8000, addr = (offset % 0x8000) + 0x8000
            bank = rom_offset // 0x8000
            addr = (rom_offset % 0x8000) + 0x8000

        return f"{bank:02X}/{addr:04X}"

    def buffer_offset_to_spc_addr(self, offset: int) -> int:
        """Convert buffer offset to SPC RAM address.

        The song data buffer contains only music data (size bytes already removed).
        SPC loads data at 0x2000.

        Args:
            offset: Offset within the song data buffer

        Returns:
            SPC RAM address
        """
        return offset + 0x2000

    def _find_event_by_offset(self, all_track_data: Dict, target_offset: int) -> Optional[Tuple[int, int]]:
        """Find which track/event corresponds to a data buffer offset.

        Works for SNES formats where tracks are parsed from a shared buffer.
        NOT applicable to PSX which uses different memory layout.

        Args:
            all_track_data: Complete track data from parse_all_tracks()
            target_offset: Target byte offset in data buffer

        Returns:
            (track_num, event_idx) if found, None otherwise
        """
        # For each track, check if this offset falls within its range
        for track_num, track_info in all_track_data['tracks'].items():
            ir_events = track_info['ir_events']
            if not ir_events:
                continue

            # Check if target_offset is within this track's event range
            first_offset = ir_events[0].offset
            last_offset = ir_events[-1].offset

            if first_offset <= target_offset <= last_offset:
                # Find exact event at this offset
                for idx, event in enumerate(ir_events):
                    if event.offset == target_offset:
                        return (track_num, idx)

        return None

    def _find_track_containing_offset(self, all_track_data: Dict, target_offset: int) -> Optional[int]:
        """Find which track owns a given offset based on track boundaries.

        Works for SNES formats where tracks are parsed from a shared buffer.
        NOT applicable to PSX which uses different memory layout.

        Args:
            all_track_data: Complete track data from parse_all_tracks()
            target_offset: Target byte offset in data buffer

        Returns:
            track_num if offset falls within that track's boundary, None otherwise
        """
        for track_num, track_info in all_track_data['tracks'].items():
            boundaries = all_track_data['header']['track_boundaries'].get(track_num)
            if boundaries is None:
                continue

            start_offset, end_offset = boundaries
            if start_offset <= target_offset < end_offset:
                return track_num

        return None

    def _read_3byte_pointers(self, offset: int, count: int) -> List[int]:
        """Read a table of 3-byte SNES pointers from ROM."""
        pointers = []
        for i in range(count):
            ptr_offset = offset + i * 3
            ptr_bytes = self.rom_data[ptr_offset:ptr_offset + 3]
            # 3-byte little-endian pointer
            ptr = struct.unpack('<I', ptr_bytes + b'\x00')[0]  # Pad to 4 bytes
            pointers.append(ptr)
        return pointers

    def _read_song_pointer_table(self) -> Dict[int, Tuple[int, int]]:
        """Read the song pointer table and return a dict of song_id -> (offset, length)."""
        # Convert base SNES address to ROM file offset
        # In Perl: $base = s2o('04/C000') converts SNES address to ROM offset
        base_offset = snes_addr_to_offset(self.base_address, self.has_smc_header)

        # Read the master pointer table (5 pointers at base address)
        # These 3-byte pointers are ROM file offsets relative to base_offset
        # In Perl: map {$base + $_} &read3ptrs($base, 5)
        # Pointers: [0]=songtbl, [1]=samptbl, [2]=songinst, [3]=srcdir, [4]=ff40
        master_ptrs_rel = self._read_3byte_pointers(base_offset, 5)
        # Add base_offset to get absolute ROM file offsets
        master_ptrs_abs = [base_offset + ptr for ptr in master_ptrs_rel]

        # Store important table offsets
        song_table_offset = master_ptrs_abs[0]
        self.instrument_table_offset = master_ptrs_abs[2]  # Song instrument table

        # Read song pointers from the song table
        # Find the maximum song ID in the config to know how many pointers to read
        songs = self.config.get('songs', [])
        if not songs:
            return {}

        #max_song_id = max(s['id'] for s in songs)
        song_ptrs_rel = self._read_3byte_pointers(song_table_offset, len(songs))
        # Add base_offset to get absolute ROM file offsets
        song_ptrs_abs = [base_offset + ptr for ptr in song_ptrs_rel]

        # Calculate song data offsets and lengths (all in ROM file offset space)
        # Song ID matches the table index (song ID 0x00 → index 0, song ID 0x01 → index 1, etc.)
        # On SNES, each song starts with a 2-byte length field, so we read that directly
        song_data = {}

        for i in range(len(song_ptrs_abs)):
            song_offset = song_ptrs_abs[i]

            # Read the 2-byte length from the beginning of each song
            if song_offset + 2 <= len(self.rom_data):
                song_length_bytes = self.rom_data[song_offset:song_offset + 2]
                song_length = struct.unpack('<H', song_length_bytes)[0]

                # Store length as-is (not including the 2-byte size field itself)
                # This matches the Perl convention where the length is the data sent to SPC
                # The size bytes will be skipped when extracting the actual data
                song_data[i] = (song_offset, song_length)  # Song ID equals table index

        return song_data

    def _read_rom_table(self, address: int, size: int, data_type: str) -> List[int]:
        """Read a table from the ROM."""
        file_offset = snes_addr_to_offset(address, self.has_smc_header)

        type_map = {
            'byte': 'B', 'B': 'B',
            'ubyte': 'B', 'unsigned char': 'B',
            'short': 'H', 'H': 'H', 'ushort': 'H',
            'int': 'I', 'I': 'I', 'uint': 'I'
        }
        format_char = type_map.get(data_type, data_type)

        item_size = struct.calcsize(format_char)
        bytes_to_read = size * item_size

        data = self.rom_data[file_offset:file_offset + bytes_to_read]
        return list(struct.unpack(f'<{size}{format_char}', data))

    def _read_song_instrument_table(self, song_id: int) -> List[int]:
        """Read the instrument table for a specific song.

        Returns a list of instrument IDs (indexes into the patch_map).
        In Perl: my (@inst) = map { $_ == 0 ? () : ($_) }
                   &read2ptrs($ptr{songinst} + $i * 0x20, 0x10);
        """
        # Each song has 16 (0x10) 2-byte instrument pointers at songinst + song_id * 0x20
        offset = self.instrument_table_offset + song_id * 0x20
        instruments = []

        for i in range(0x10):
            ptr_offset = offset + i * 2
            inst_bytes = self.rom_data[ptr_offset:ptr_offset + 2]
            inst_id = struct.unpack('<H', inst_bytes)[0]
            # Filter out 0 entries (unused instrument slots)
            if inst_id != 0:
                instruments.append(inst_id)

        return instruments

    def parse_header(self, data: bytes, song_id: int = 0, use_alternate_pointers: bool = False) -> Dict:
        """Parse SNES FF2 song header.

        Format:
        - Bytes 0-15: 8 voice pointers (2 bytes each, little-endian)

        Note: The 2-byte size field has already been removed from the data buffer.
        """
        if len(data) < 16:
            raise ValueError(f"Song data too short: {len(data)} bytes (need at least 16 for voice pointers)")

        # Read voice pointers (now at offset 0 since size bytes were removed)
        voice_pointers = struct.unpack('<8H', data[0:16])

        # A pointer < 0x100 indicates unused voice (matches Perl script threshold)
        return {
            'voice_pointers': voice_pointers,
            'num_voices': sum(1 for ptr in voice_pointers if ptr >= 0x100)
        }

    def get_track_offsets(self, data: bytes, header: Dict) -> List[int]:
        """Get list of voice offsets within song data.

        Voice pointers are SPC-700 RAM addresses where the song data is loaded at 0x2000.
        Pointers < 0x100 indicate unused voices (matches Perl script threshold).
        The data buffer no longer includes size bytes, so we just subtract 0x2000.
        """
        SPC_LOAD_ADDR = 0x2000
        offsets = []
        for ptr in header['voice_pointers']:
            if ptr >= 0x100:  # Valid voice pointer (matches Perl: next if $vstart[$v] < 0x100)
                # Convert SPC RAM address to offset in our data buffer
                # ptr is SPC address, data at SPC starts at 0x2000
                buffer_offset = ptr - SPC_LOAD_ADDR  # Our buffer now has no size prefix
                offsets.append(buffer_offset)
        return offsets

    def parse_track(self, data: bytes, offset: int, track_num: int, song_id: int = 0,
                    instrument_table: Optional[List[int]] = None,
                    target_loop_time: int = 0, loop_info: Optional[Dict] = None) -> Tuple[List, List]:
        """Parse a single SNES voice's event data using a two-pass approach.

        Pass 1: Parse opcodes linearly, generate disassembly, build intermediate representation (IR)
        Pass 2: Expand IR with loops to generate MIDI events

        Args:
            data: Song data buffer
            offset: Offset into data where this voice starts
            track_num: Track/voice number (0-7)
            song_id: Song ID for looking up instruments
            instrument_table: List of instrument IDs for this song
            target_loop_time: Target time for 2x loop playthrough (0 = no looping)
            loop_info: Loop analysis info from _analyze_track_loops()

        Returns:
            Tuple of (disassembly_lines, midi_events)
        """
        if instrument_table is None:
            instrument_table = []

        # Pass 1: Parse and build IR
        disasm, ir_events = self._parse_track_pass1(data, offset, track_num, instrument_table)

        # Pass 2: Expand IR and generate MIDI
        midi_events = self._parse_track_pass2(ir_events, track_num, target_loop_time, loop_info)

        return disasm, midi_events

    def _parse_track_pass1(self, data: bytes, offset: int, track_num: int,
                          instrument_table: List[int], vaddroffset: int = 0,
                          track_boundaries: Optional[Dict[int, Tuple[int, int]]] = None) -> Tuple[List, List[IREvent]]:
        """Pass 1: Linear parse to build disassembly and intermediate representation.

        This pass does NOT execute loops or generate MIDI events. It simply parses
        the opcode stream linearly, building IR events that represent the sequence
        structure. Disassembly is generated with loop awareness to avoid duplicates.

        Returns:
            Tuple of (disassembly_lines, ir_events)
        """
        disasm = []
        ir_events = []

        # Track state for disassembly and IR building
        octave = self.default_octave
        velocity = self.default_velocity
        tempo = self.default_tempo
        current_patch = 0
        perc_key = 0
        transpose_octaves = 0
        inst_id = 0  # Current instrument ID from table

        # Track loop depth for disassembly indentation (but don't execute loops)
        loop_depth = 0

        # Track whether any timing events (notes/rests) have been processed
        # This allows cross-track GOTOs at the very start (for chorus effects)
        has_timing_events = False

        # Start from voice offset
        p = offset

        while p < len(data):
            cmd = data[p]

            # Convert buffer offset to SPC RAM address for display
            # SPC data loaded at 0x2000
            spc_addr = p + 0x2000
            line = f"      {spc_addr:04X}: {cmd:02X} "

            # Check if it's a note (< 0xD2)
            if cmd < 0xD2:
                # Note encoding: note = cmd / 15, duration = cmd % 15
                note_num = cmd // 15
                dur_idx = cmd % 15
                # Duration table values are in native units (48 ticks/quarter)
                # Double them to convert to MIDI resolution (96 ticks/quarter)
                raw_dur = self.duration_table[dur_idx]
                dur = raw_dur * 2

                if note_num < 12:
                    # Regular note - create IR event
                    event = make_note(p, note_num, dur)
                    # Store current state in event for pass 2
                    # NOTE: octave is NOT stored here - it's tracked as state in Pass 2
                    # because loops can modify octave during execution
                    event.metadata = {
                        'velocity': velocity,
                        'patch': current_patch,
                        'perc_key': perc_key,
                        'transpose': transpose_octaves,
                        'track_num': track_num,
                        'inst_id': inst_id
                    }
                    ir_events.append(event)
                    has_timing_events = True

                    # Build disassembly line
                    # Format: "4E            Note F  (05) Dur 48"
                    note_name = NOTE_NAMES[note_num]
                    if perc_key:
                        line += f"           Note {note_name:<2} ({note_num:02}) Dur {raw_dur:<3} [PERC key={perc_key}]"
                    else:
                        line += f"           Note {note_name:<2} ({note_num:02}) Dur {raw_dur}"

                elif note_num == 12:
                    # Rest - create IR event
                    event = make_rest(p, dur)
                    ir_events.append(event)
                    line += f"           Rest         Dur {raw_dur}"

                else:
                    # Tie - create IR event
                    event = make_tie(p, dur)
                    ir_events.append(event)
                    line += f"           Tie          Dur {raw_dur}"

                p += 1
                disasm.append(line)

            else:
                # Command opcode
                oplen = self.opcode_table[cmd - 0xD2]
                operands = list(data[p+1:p+1+oplen])

                # Format operands with proper spacing
                operand_str = ' '.join(f"{op:02X}" for op in operands)
                # Pad operands to align text descriptions (assume max 3 operands = 8 chars)
                line += f"{operand_str:<8}   {self.OPCODE_NAMES.get(cmd, f'OP_{cmd:02X}')}"

                # Create IR events for opcodes that affect playback
                if cmd == 0xD2 and len(operands) >= 3:
                    # Tempo change
                    tempo = operands[2]
                    event = make_tempo(p, tempo, operands)
                    ir_events.append(event)

                elif cmd == 0xDB and len(operands) >= 1:
                    # Patch change
                    patch_index = operands[0] - 0x40
                    if 0 <= patch_index < len(instrument_table):
                        inst_id = instrument_table[patch_index]
                        current_patch = operands[0]

                        # Look up instrument in patch map
                        if inst_id in self.patch_map:
                            patch_info = self.patch_map[inst_id]
                            gm_patch = patch_info['gm_patch']
                            transpose_octaves = patch_info.get('transpose', 0)

                            # Check if this is percussion (negative GM patch number)
                            if gm_patch < 0:
                                # Percussion mode
                                perc_key = abs(gm_patch)
                                line += f" -> PERC key={perc_key}"
                            else:
                                # Regular instrument
                                perc_key = 0
                                line += f" -> GM patch {gm_patch}"

                            # Create IR event for patch change
                            event = make_patch_change(p, inst_id, gm_patch, transpose_octaves, operands)
                            ir_events.append(event)
                        else:
                            # Instrument not in patch map, use default
                            current_patch = operands[0]
                            perc_key = 0
                            transpose_octaves = 0
                    else:
                        # Invalid patch index
                        current_patch = operands[0]
                        perc_key = 0
                        transpose_octaves = 0

                elif cmd == 0xDA and len(operands) >= 1:
                    # Set octave
                    octave = operands[0]
                    event = make_octave_set(p, octave, operands)
                    ir_events.append(event)

                elif cmd == 0xE1:
                    # Inc octave
                    octave += 1
                    event = make_octave_inc(p)
                    ir_events.append(event)

                elif cmd == 0xE2:
                    # Dec octave
                    octave -= 1
                    event = make_octave_dec(p)
                    ir_events.append(event)

                elif cmd == 0xF2 and len(operands) >= 3:
                    # Voice Volume (F2): 3 operands
                    # Bytes 0-1: fade steps (if both zero, immediate change)
                    # Byte 2: target volume
                    # For MIDI, we use the target volume and ignore fades
                    # Scale from 0-255 to MIDI velocity 0-127
                    velocity = operands[2] >> 1
                    event = make_volume(p, velocity, operands)
                    ir_events.append(event)

                elif cmd == 0xE0 and len(operands) >= 1:
                    # Begin repeat (0xE0) - just record it, don't execute
                    loop_depth += 1
                    event = make_loop_start(p, operands[0], operands)
                    ir_events.append(event)

                elif cmd == 0xF0:
                    # End repeat (0xF0) - just record it, don't execute
                    loop_depth = max(0, loop_depth - 1)
                    event = make_loop_end(p)
                    ir_events.append(event)

                elif cmd == 0xF5 and len(operands) >= 3:
                    # Selective repeat (0xF5) - conditional jump - just record
                    target_spc_addr = operands[1] + operands[2] * 256
                    target_offset = target_spc_addr - 0x2000  # Convert SPC address to buffer offset
                    # Add target address to disassembly line
                    line += f" ${target_spc_addr:04X}"
                    event = make_loop_break(p, operands[0], target_offset, operands)
                    ir_events.append(event)

                elif cmd == 0xD8 and len(operands) >= 3:
                    # Vibrato settings
                    event = make_vibrato_on(p, operands)
                    ir_events.append(event)

                elif cmd == 0xE8:
                    # Vibrato off
                    event = make_vibrato_off(p)
                    ir_events.append(event)

                elif cmd == 0xD7 and len(operands) >= 3:
                    # Tremolo settings
                    event = make_tremolo_on(p, operands)
                    ir_events.append(event)

                elif cmd == 0xE7:
                    # Tremolo off
                    event = make_tremolo_off(p)
                    ir_events.append(event)

                elif cmd == 0xD6 and len(operands) >= 3:
                    # Portamento settings
                    event = make_portamento_on(p, operands)
                    ir_events.append(event)

                elif cmd == 0xE6:
                    # Portamento off
                    event = make_portamento_off(p)
                    ir_events.append(event)

                elif cmd in (0xF1, 0xF4) or cmd >= 0xF7:
                    # Halt or goto
                    if cmd == 0xF4 and len(operands) >= 2:
                        # Goto with address
                        target_spc_addr = operands[0] + operands[1] * 256
                        target_offset = target_spc_addr - 0x2000  # Convert SPC address to buffer offset

                        # Determine if this is a backwards GOTO
                        is_backwards = target_offset < p

                        # Add target address to disassembly line
                        line += f" ${target_spc_addr:04X}"
                        event = make_goto(p, target_offset, operands)
                        ir_events.append(event)
                        disasm.append(line)

                        # Backwards GOTO - loop, halt disassembly
                        if is_backwards:
                            break
                        else:
                            # Forward GOTO - check if target is in another track's assigned region
                            if track_boundaries:
                                target_track = None
                                for t_num, (t_start, t_end) in track_boundaries.items():
                                    if t_num != track_num and t_start <= target_offset < t_end:
                                        target_track = t_num
                                        break

                                if target_track is not None:
                                    # GOTO into another track's data (songs 49, 4A) - halt
                                    break

                            # Either no track_boundaries, or target is unassigned - continue (songs 53, 54)
                            p += oplen + 1
                    else:
                        # Halt - end of track
                        event = make_halt(p, operands)
                        ir_events.append(event)
                        disasm.append(line)
                        break

                p += oplen + 1
                disasm.append(line)

        return disasm, ir_events

    def _analyze_track_loops(self, ir_events: List[IREvent]) -> Dict:
        """Analyze track for backwards GOTO loops and calculate timing.

        This is a simplified analysis pass that detects loop patterns and measures
        timing without generating MIDI events.

        Args:
            ir_events: List of IR events from pass 1

        Returns:
            Dict with keys:
                'has_backwards_goto': bool - True if track ends with backwards GOTO
                'intro_time': int - Time units before loop starts (0 if no loop)
                'loop_time': int - Time units for one loop iteration (0 if no loop)
                'goto_target_idx': int - Index of GOTO target event (None if no loop)
        """
        total_time = 0
        loop_stack = []

        # Find the last BACKWARDS GOTO event (for looping)
        # Forward GOTOs are sequence continuation, not loops
        last_goto_event = None
        last_goto_idx = None
        for i, event in enumerate(ir_events):
            if event.type == IREventType.GOTO:
                # Check if this GOTO is backwards (target_offset < current offset)
                if event.target_offset < event.offset:
                    last_goto_event = event
                    last_goto_idx = i

        # Check if it's a backwards GOTO
        if last_goto_event is None:
            return {
                'has_backwards_goto': False,
                'intro_time': 0,
                'loop_time': 0,
                'goto_target_idx': None
            }

        # Find target event index
        target_idx = None
        for j, e in enumerate(ir_events):
            if e.offset == last_goto_event.target_offset:
                target_idx = j
                break

        # DEBUG
        # if target_idx is not None and last_goto_idx is not None:
        #     print(f"    Found backwards GOTO: last_goto_idx={last_goto_idx}, target_idx={target_idx}")

        # Check if backwards (target comes before GOTO)
        assert last_goto_idx is not None, "last_goto_idx should not be None here"
        if target_idx is None or target_idx >= last_goto_idx:
            # Forward GOTO or target not found - not a loop
            return {
                'has_backwards_goto': False,
                'intro_time': 0,
                'loop_time': 0,
                'goto_target_idx': None
            }

        # It's a backwards GOTO - calculate intro and loop times
        # Intro time = time from start (index 0) to GOTO target (target_idx)
        # Loop time = time from GOTO target to GOTO itself

        # Measure intro time: execute from 0 to target_idx
        intro_time = 0
        loop_stack_intro = []
        i = 0
        while i < target_idx:
            event = ir_events[i]

            if event.type == IREventType.NOTE or event.type == IREventType.REST or event.type == IREventType.TIE:
                assert event.duration is not None
                intro_time += event.duration
                i += 1
            elif event.type == IREventType.LOOP_START:
                loop_stack_intro.append({'start_idx': i + 1, 'count': event.loop_count, 'iteration': 0})
                i += 1
            elif event.type == IREventType.LOOP_END:
                if loop_stack_intro:
                    loop = loop_stack_intro[-1]
                    loop['count'] -= 1
                    if loop['count'] >= 0:
                        i = loop['start_idx']
                    else:
                        loop_stack_intro.pop()
                        i += 1
                else:
                    i += 1
            elif event.type == IREventType.LOOP_BREAK:
                if loop_stack_intro:
                    loop = loop_stack_intro[-1]
                    loop['iteration'] += 1
                    if loop['iteration'] == event.condition:
                        for j, e in enumerate(ir_events):
                            if e.offset == event.target_offset:
                                i = j
                                break
                        loop_stack_intro.pop()
                    else:
                        i += 1
                else:
                    i += 1
            else:
                i += 1

        # Measure loop time: execute from target_idx to last_goto_idx
        loop_time = 0
        assert target_idx is not None, "target_idx should not be None for backwards GOTO"
        assert last_goto_idx is not None, "last_goto_idx should not be None for backwards GOTO"
        loop_stack_loop = []
        j = target_idx

        while j < last_goto_idx:
            e = ir_events[j]

            if e.type == IREventType.NOTE or e.type == IREventType.REST or e.type == IREventType.TIE:
                assert e.duration is not None
                loop_time += e.duration
                j += 1
            elif e.type == IREventType.LOOP_START:
                loop_stack_loop.append({'start_idx': j + 1, 'count': e.loop_count, 'iteration': 0})
                j += 1
            elif e.type == IREventType.LOOP_END:
                if loop_stack_loop:
                    lp = loop_stack_loop[-1]
                    lp['count'] -= 1
                    if lp['count'] >= 0:
                        j = lp['start_idx']
                    else:
                        loop_stack_loop.pop()
                        j += 1
                else:
                    j += 1
            elif e.type == IREventType.LOOP_BREAK:
                if loop_stack_loop:
                    lp = loop_stack_loop[-1]
                    lp['iteration'] += 1
                    if lp['iteration'] == e.condition:
                        for k, ev in enumerate(ir_events):
                            if ev.offset == e.target_offset:
                                j = k
                                break
                        loop_stack_loop.pop()
                    else:
                        j += 1
                else:
                    j += 1
            else:
                j += 1

        # DEBUG
        # print(f"      Analysis: intro_time={intro_time}, loop_time={loop_time}, target_idx={target_idx}, last_goto_idx={last_goto_idx}")

        return {
            'has_backwards_goto': True,
            'intro_time': intro_time,
            'loop_time': loop_time,
            'goto_target_idx': target_idx
        }

    def _parse_track_pass2(self, all_track_data: Dict, start_voice_num: int,
                          target_loop_time: int = 0) -> List[Dict]:
        """Pass 2: Expand IR events with loop execution to generate MIDI events.

        This pass takes all track data and executes from a starting voice, expanding loops,
        following GOTOs (including cross-track), and generating timed MIDI events.

        Args:
            all_track_data: Complete track data from parse_all_tracks()
            start_voice_num: Starting track/voice number (0-7)
            target_loop_time: Target time for 2x loop playthrough (0 = no looping)
            loop_info: Loop analysis info from _analyze_track_loops()

        Returns:
            List of MIDI event dictionaries
        """
        import time
        start_time = time.time()
        max_time_seconds = 2.0  # Emergency brake: 2 second time limit
        max_midi_events = 50000  # Emergency brake: max MIDI events per track

        midi_events = []
        total_time = 0

        # Start with the specified voice
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

        # Loop execution state
        loop_stack = []  # Stack of {start_idx, count, iteration}

        # Calculate target time for loop termination
        target_total_time = 0
        if loop_info and loop_info['has_backwards_goto'] and target_loop_time > 0:
            intro_time = loop_info['intro_time']
            target_total_time = intro_time + target_loop_time

        # Event pointer for execution
        i = 0
        # Most game music loops infinitely. Allow enough iterations for ~2 full playthroughs
        # Typical: 100-200 IR events, with loops expanding to 10,000-20,000 iterations
        # Ensure minimum iterations for short loops while still acting as failsafe
        # - Multiplier of 200 per event handles normal cases
        # - Minimum of 10000 ensures short loops (1-2 events) can reach target duration
        max_iterations = max(len(ir_events) * 200, 10000)
        iteration_count = 0

        while i < len(ir_events) and iteration_count < max_iterations:
            iteration_count += 1

            # Check if we've reached target playthrough time
            if target_total_time > 0 and total_time >= target_total_time:
                break

            # Emergency brakes (only warn, not error - looping is normal)
            if time.time() - start_time > max_time_seconds:
                # Silently stop - time limit reached (normal for looping songs)
                break
            if len(midi_events) > max_midi_events:
                # Silently stop - MIDI event limit reached (normal for looping songs)
                break

            if i < 0 or i >= len(ir_events):
                print(f"WARNING: Track {start_voice_num} invalid event index {i}, stopping")
                break
            event = ir_events[i]

            if event.type == IREventType.NOTE:
                # Extract state from metadata (stored in pass 1)
                # NOTE: octave is tracked as state variable, not in metadata
                velocity = event.metadata['velocity']
                perc_key = event.metadata['perc_key']
                transpose_octaves = event.metadata['transpose']

                # Calculate MIDI note
                assert event.note_num is not None, "NOTE event must have note_num"
                assert event.duration is not None, "NOTE event must have duration"
                midi_note = 12 * (octave + transpose_octaves) + event.note_num

                # Check if we're in percussion mode
                if perc_key:
                    midi_channel = 9  # Percussion channel
                    midi_note = perc_key
                else:
                    midi_channel = current_channel

                # Add MIDI note event
                midi_events.append({
                    'type': 'note',
                    'time': total_time,
                    'duration': event.duration - 1,  # Matches Perl: $dur - 1
                    'note': midi_note,
                    'velocity': velocity,
                    'channel': midi_channel
                })

                total_time += event.duration
                i += 1

            elif event.type == IREventType.REST:
                # Rest just advances time
                assert event.duration is not None, "REST event must have duration"
                total_time += event.duration
                i += 1

            elif event.type == IREventType.TIE:
                # Tie extends the last note
                assert event.duration is not None, "TIE event must have duration"
                for j in range(len(midi_events) - 1, -1, -1):
                    if midi_events[j]['type'] == 'note':
                        midi_events[j]['duration'] += event.duration
                        break
                total_time += event.duration
                i += 1

            elif event.type == IREventType.TEMPO:
                # Tempo change
                assert event.value is not None, "TEMPO event must have value"
                midi_events.append({
                    'type': 'tempo',
                    'time': total_time,
                    'tempo': event.value
                })
                tempo = event.value
                i += 1

            elif event.type == IREventType.PATCH_CHANGE:
                # Patch change
                assert event.gm_patch is not None, "PATCH_CHANGE event must have gm_patch"
                if event.gm_patch < 0:
                    # Percussion mode
                    perc_key = abs(event.gm_patch)
                    transpose_octaves = event.transpose
                else:
                    # Regular instrument
                    perc_key = 0
                    transpose_octaves = event.transpose
                    midi_events.append({
                        'type': 'program_change',
                        'time': total_time,
                        'patch': event.gm_patch
                    })
                i += 1

            elif event.type == IREventType.OCTAVE_SET:
                assert event.value is not None, "OCTAVE_SET event must have value"
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

            elif event.type == IREventType.LOOP_START:
                # Push loop onto stack
                loop_stack.append({
                    'start_idx': i + 1,  # Start after LOOP_START
                    'count': event.loop_count,
                    'iteration': 0,
                    'end_idx': None  # Will be set when we find LOOP_END
                })
                i += 1

            elif event.type == IREventType.LOOP_END:
                # End of loop body - decide if we repeat
                if loop_stack:
                    loop = loop_stack[-1]
                    loop['count'] -= 1

                    if loop['count'] >= 0:
                        # Repeat: jump back to loop start
                        i = loop['start_idx']
                    else:
                        # Done looping: pop and continue
                        loop_stack.pop()
                        i += 1
                else:
                    # No matching loop start? Just continue
                    i += 1

            elif event.type == IREventType.LOOP_BREAK:
                # Selective repeat (F5): conditional jump on specific iteration
                # This increments the current loop iteration and jumps if it matches the condition
                if loop_stack:
                    loop = loop_stack[-1]
                    loop['iteration'] += 1

                    if loop['iteration'] == event.condition:
                        # Condition met: jump to target and exit loop
                        # Find event at target offset
                        target_idx = None
                        for j, e in enumerate(ir_events):
                            if e.offset == event.target_offset:
                                target_idx = j
                                break

                        if target_idx is not None:
                            i = target_idx
                            loop_stack.pop()  # Exit this loop level
                        else:
                            # Target not found, just continue
                            i += 1
                    else:
                        # Condition not met: continue to next event
                        i += 1
                else:
                    # No loop context, just skip
                    i += 1

            elif event.type == IREventType.GOTO:
                # Determine GOTO type and handle accordingly
                if event.target_offset is None:
                    break  # Invalid GOTO

                # Find target track and event
                result = self._find_event_by_offset(all_track_data, event.target_offset)

                if result is None:
                    # Target not found - halt
                    break

                target_track, target_idx = result

                # Classify GOTO type
                is_backwards = event.target_offset < event.offset
                is_cross_track = target_track != current_voice_num

                if is_backwards and not is_cross_track:
                    # Backwards loop within same track
                    if loop_info and loop_info.get('has_backwards_goto', False) and target_loop_time > 0:
                        # Follow loop until target time reached
                        i = target_idx
                    else:
                        # No loop playback requested - halt at loop point
                        break
                else:
                    # Forward GOTO or cross-track GOTO - follow as normal continuation
                    current_voice_num = target_track
                    ir_events = all_track_data['tracks'][current_voice_num]['ir_events']
                    i = target_idx

                    # Switch to target track's loop_info
                    loop_info = all_track_data['tracks'][current_voice_num].get('loop_info', {})

                    # Recalculate target_total_time for new track's loop
                    if loop_info.get('has_backwards_goto', False) and target_loop_time > 0:
                        intro_time = loop_info['intro_time']
                        target_total_time = intro_time + target_loop_time
                    else:
                        target_total_time = 0

            elif event.type == IREventType.HALT:
                # End of track
                break

            else:
                # Other event types (vibrato, tremolo, etc.) are not yet expanded
                # Just skip for now
                i += 1

        # Check if we hit the iteration limit
        if iteration_count >= max_iterations:
            print(f"WARNING: Track {start_voice_num} hit max iteration limit ({max_iterations}), possible infinite loop")

        return midi_events


class SNESFF3(SequenceFormat):
    """SNES Final Fantasy III (US) / VI (JP) music format handler."""

    OPCODE_NAMES = {
        0xc4: 'Set Volume', 0xc5: 'Volume Fade', 0xc6: 'Set Balance', 0xc7: 'Balance Fade',
        0xc8: 'Portamento', 0xc9: 'Vibrato', 0xca: 'Vibrato Off',
        0xcb: 'Tremolo', 0xcc: 'Tremolo Off', 0xcd: 'Pan Sweep', 0xce: 'Pan Sweep Off',
        0xcf: 'Set Noise Clock', 0xd0: 'Enable Noise', 0xd1: 'Disable Noise',
        0xd2: 'Enable Pitchmod', 0xd3: 'Disable Pitchmod',
        0xd4: 'Enable Echo', 0xd5: 'Disable Echo',
        0xd6: 'Set Octave', 0xd7: 'Inc Octave', 0xd8: 'Dec Octave',
        0xd9: 'Set Transpose', 0xda: 'Change Transpose', 0xdb: 'Detune',
        0xdc: 'Patch Change',
        0xdd: 'Set ADSR Attack', 0xde: 'Set ADSR Decay',
        0xdf: 'Set ADSR Sustain', 0xe0: 'Set ADSR Release', 0xe1: 'Set Default ADSR',
        0xe2: 'Begin Repeat', 0xe3: 'End Repeat',
        0xe4: 'Begin Slur', 0xe5: 'End Slur',
        0xe6: 'Begin Roll', 0xe7: 'End Roll',
        0xe8: 'Utility Rest', 0xe9: 'Play SFX (E9)', 0xea: 'Play SFX (EA)',
        0xeb: 'Halt', 0xec: 'Halt', 0xed: 'Halt', 0xee: 'Halt', 0xef: 'Halt',
        0xf0: 'Set Tempo', 0xf1: 'Tempo Fade',
        0xf2: 'Set Echo Volume', 0xf3: 'Fade Echo Volume',
        0xf4: 'Set Volume Multiplier', 0xf5: 'Conditional Loop',
        0xf6: 'Goto', 0xf7: 'Set/Fade Echo Feedback',
        0xf8: 'Set/Fade Filter', 0xf9: 'Advance Cue Point',
        0xfa: 'Zero Cue Point', 0xfb: 'Ignore Volume Multiplier',
        0xfc: 'Branch if $DD', 0xfd: 'Halt', 0xfe: 'Halt', 0xff: 'Halt'
    }

    def __init__(self, config: Dict, rom_data: bytes):
        """Initialize with game-specific config and ROM data."""
        self.config = config
        self.rom_data: bytes = rom_data
        self.has_smc_header = len(rom_data) % 1024 == 512  # SMC header is 512 bytes
        self.smc_header_size = 512 if self.has_smc_header else 0

        # Auto-detect ROM mapping mode (LoROM vs HiROM)
        self.rom_mapping = self._detect_rom_mapping()

        # Read duration table from ROM
        if 'duration_table' in config:
            table_cfg = config['duration_table']
            self.duration_table = self._read_rom_table(
                table_cfg.get('address'),
                table_cfg.get('size', 14),
                table_cfg.get('type', 'B')
            )
        else:
            # Default duration table (14 values for FF3)
            self.duration_table = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]

        # Read opcode length table from ROM
        if 'opcode_table' in config:
            table_cfg = config['opcode_table']
            self.opcode_table = self._read_rom_table(
                table_cfg.get('address'),
                table_cfg.get('size', 60),
                table_cfg.get('type', 'B')
            )
        else:
            # Default opcode lengths (60 opcodes starting from 0xC4)
            self.opcode_table = [1] * 60

        # Get state defaults from config
        self.default_tempo = config.get('default_tempo', 255)
        self.default_octave = config.get('default_octave', 4)
        self.default_velocity = config.get('default_velocity', 64)
        self.tempo_factor = config.get('tempo_factor', 55296000)

        # Build patch map from config (instrument_id -> PatchInfo)
        self.patch_map = {}
        if 'patch_map' in config:
            for inst_id_str, patch_info in config['patch_map'].items():
                # Convert hex string to int (handles both "0x00" and "00" formats)
                if isinstance(inst_id_str, str):
                    inst_id = int(inst_id_str, 0)  # base 0 auto-detects hex with 0x prefix
                else:
                    inst_id = inst_id_str
                self.patch_map[inst_id] = patch_info

        # Build patch_map_low from config
        self.patch_map_low = {}
        if 'patch_map_low' in config:
            for patch_id_str, patch_info in config['patch_map_low'].items():
                if isinstance(patch_id_str, str):
                    patch_id = int(patch_id_str, 0)
                else:
                    patch_id = patch_id_str
                self.patch_map_low[patch_id] = patch_info

        # Get note divisor (14 for FF3 vs 15 for FF2)
        self.note_divisor = config.get('note_divisor', 14)

        # Get first opcode value (0xC4 for FF3 vs 0xD2 for FF2)
        self.first_opcode = config.get('first_opcode', 0xC4)

        # Get base address for song pointers
        self.base_address = config.get('base_address', 0x04C000)

        # Read song pointer table
        self.song_pointers = self._read_song_pointer_table()

    def _detect_rom_mapping(self) -> str:
        """Auto-detect ROM mapping mode (LoROM vs HiROM).

        Based on snesutil.pl:openrom logic - checks ROM header at two possible locations
        and validates with complement checksum.

        Returns:
            "lorom" or "hirom"
        """
        offset = self.smc_header_size

        # Read potential headers at LoROM and HiROM locations
        lorom_header_offset = offset + 0x7FC0
        hirom_header_offset = offset + 0xFFC0

        # Read 32-byte headers
        if lorom_header_offset + 32 <= len(self.rom_data):
            lorom_header = self.rom_data[lorom_header_offset:lorom_header_offset + 32]
        else:
            lorom_header = None

        if hirom_header_offset + 32 <= len(self.rom_data):
            hirom_header = self.rom_data[hirom_header_offset:hirom_header_offset + 32]
        else:
            hirom_header = None

        # Check complement checksums (bytes 28-29 = checksum, 30-31 = inverse checksum)
        # Format: A21C7v2 = 21 byte title, 7 single bytes, 2 words (little-endian)
        lorom_valid = False
        hirom_valid = False

        if lorom_header:
            # Checksum at offset 28-29, inverse at 30-31
            checksum = struct.unpack('<H', lorom_header[28:30])[0]
            inverse = struct.unpack('<H', lorom_header[30:32])[0]
            if (checksum ^ 0xFFFF) == inverse:
                lorom_valid = True

        if hirom_header:
            checksum = struct.unpack('<H', hirom_header[28:30])[0]
            inverse = struct.unpack('<H', hirom_header[30:32])[0]
            if (checksum ^ 0xFFFF) == inverse:
                hirom_valid = True

        # If checksum validation worked, use that
        if lorom_valid and not hirom_valid:
            return "lorom"
        elif hirom_valid and not lorom_valid:
            return "hirom"

        # Fallback: check mapping mode byte (offset 21 in header)
        # Bit 0-3 = mode: 0 = LoROM, 1 = HiROM
        if lorom_header and (lorom_header[21] & 0xF) == 0:
            return "lorom"
        if hirom_header and (hirom_header[21] & 0xF) == 1:
            return "hirom"

        # Default to LoROM (most common)
        return "lorom"

    def rom_offset_to_display_addr(self, offset: int) -> str:
        """Convert ROM file offset to SNES bank/address display format.

        Args:
            offset: ROM file offset (includes SMC header if present)

        Returns:
            Formatted string like "06/F727" (bank/address)
        """
        # Remove SMC header from offset
        rom_offset = offset - self.smc_header_size

        if self.rom_mapping == "hirom":
            # HiROM: bank = (offset / 0x10000) | 0xC0, addr = offset & 0xFFFF
            bank = (rom_offset // 0x10000) | 0xC0
            addr = rom_offset & 0xFFFF
        else:
            # LoROM: bank = offset / 0x8000, addr = (offset % 0x8000) + 0x8000
            bank = rom_offset // 0x8000
            addr = (rom_offset % 0x8000) + 0x8000

        return f"{bank:02X}/{addr:04X}"

    def buffer_offset_to_spc_addr(self, offset: int) -> int:
        """Convert buffer offset to SPC RAM address.

        For FF3, SPC loads data at 0x1C00 (vs 0x2000 for FF2).
        """
        return offset + 0x1C00

    def _find_event_by_offset(self, all_track_data: Dict, target_offset: int) -> Optional[Tuple[int, int]]:
        """Find which track/event corresponds to a data buffer offset.

        Works for SNES formats where tracks are parsed from a shared buffer.
        NOT applicable to PSX which uses different memory layout.

        Args:
            all_track_data: Complete track data from parse_all_tracks()
            target_offset: Target byte offset in data buffer

        Returns:
            (track_num, event_idx) if found, None otherwise
        """
        # For each track, check if this offset falls within its range
        for track_num, track_info in all_track_data['tracks'].items():
            ir_events = track_info['ir_events']
            if not ir_events:
                continue

            # Check if target_offset is within this track's event range
            first_offset = ir_events[0].offset
            last_offset = ir_events[-1].offset

            if first_offset <= target_offset <= last_offset:
                # Find exact event at this offset
                for idx, event in enumerate(ir_events):
                    if event.offset == target_offset:
                        return (track_num, idx)

        return None

    def _find_track_containing_offset(self, all_track_data: Dict, target_offset: int) -> Optional[int]:
        """Find which track owns a given offset based on track boundaries.

        Works for SNES formats where tracks are parsed from a shared buffer.
        NOT applicable to PSX which uses different memory layout.

        Args:
            all_track_data: Complete track data from parse_all_tracks()
            target_offset: Target byte offset in data buffer

        Returns:
            track_num if offset falls within that track's boundary, None otherwise
        """
        for track_num, track_info in all_track_data['tracks'].items():
            boundaries = all_track_data['header']['track_boundaries'].get(track_num)
            if boundaries is None:
                continue

            start_offset, end_offset = boundaries
            if start_offset <= target_offset < end_offset:
                return track_num

        return None

    def _read_3byte_pointers(self, offset: int, count: int) -> List[int]:
        """Read a table of 3-byte SNES pointers from ROM."""
        pointers = []
        for i in range(count):
            ptr_offset = offset + i * 3
            ptr_bytes = self.rom_data[ptr_offset:ptr_offset + 3]
            # 3-byte little-endian pointer
            ptr = struct.unpack('<I', ptr_bytes + b'\x00')[0]  # Pad to 4 bytes
            pointers.append(ptr)
        return pointers

    def _snes_addr_to_offset(self, snes_addr: int) -> int:
        """Convert SNES address (bank/addr format) to ROM file offset."""
        # For LoROM: bank bytes 0x00-0x7F map to ROM offset = (bank * 0x8000) + (addr - 0x8000)
        # For HiROM: bank bytes 0xC0-0xFF map to ROM offset = ((bank - 0xC0) * 0x10000) + addr
        bank = (snes_addr >> 16) & 0xFF
        addr = snes_addr & 0xFFFF

        if self.rom_mapping == "hirom":
            rom_offset = ((bank - 0xC0) * 0x10000) + addr
        else:
            rom_offset = (bank * 0x8000) + (addr - 0x8000)

        return rom_offset + self.smc_header_size

    def _read_song_pointer_table(self) -> Dict[int, Tuple[int, int]]:
        """Read FF3 song pointer table.

        FF3 has direct pointers at base + 0x3e96 for 85 songs.
        Returns dict mapping song_id -> (offset, length).
        """
        base_offset = self._snes_addr_to_offset(self.base_address)

        # Read 85 song pointers (3 bytes each: 2-byte addr + 1-byte bank)
        song_count = 0x55  # 85 songs
        song_pointers = {}

        for i in range(song_count):
            ptr_offset = base_offset + 0x3e96 + (i * 3)
            if ptr_offset + 3 > len(self.rom_data):
                break

            addr_low = self.rom_data[ptr_offset]
            addr_high = self.rom_data[ptr_offset + 1]
            bank = self.rom_data[ptr_offset + 2]
            snes_addr = (bank << 16) | (addr_high << 8) | addr_low
            rom_offset = self._snes_addr_to_offset(snes_addr)

            # Read actual length from 2-byte size field at start of song data
            if rom_offset + 2 <= len(self.rom_data):
                length = struct.unpack('<H', self.rom_data[rom_offset:rom_offset + 2])[0]
            else:
                length = 0

            # Map song index to (offset, length)
            song_pointers[i] = (rom_offset, length)

        # DEBUG
        # print(f"DEBUG: Read {len(song_pointers)} FF3 song pointers")
        # if 0x00 in song_pointers:
        #     print(f"  Song 00: offset={song_pointers[0x00][0]:06X}, length={song_pointers[0x00][1]:04X}")
        # if 0x01 in song_pointers:
        #     print(f"  Song 01: offset={song_pointers[0x01][0]:06X}, length={song_pointers[0x01][1]:04X}")

        return song_pointers

    def _read_rom_table(self, address: int, size: int, data_type: str) -> List[int]:
        """Read a table from the ROM."""
        file_offset = self._snes_addr_to_offset(address)

        type_map = {
            'byte': 'B', 'B': 'B',
            'ubyte': 'B', 'unsigned char': 'B',
            'short': 'H', 'H': 'H', 'ushort': 'H',
            'int': 'I', 'I': 'I', 'uint': 'I'
        }
        format_char = type_map.get(data_type, data_type)

        item_size = struct.calcsize(format_char)
        bytes_to_read = size * item_size

        data = self.rom_data[file_offset:file_offset + bytes_to_read]
        return list(struct.unpack(f'<{size}{format_char}', data))

    def parse_header(self, data: bytes, song_id: int = 0, use_alternate_pointers: bool = False) -> Dict:
        """Parse FF3 song header with vaddroffset calculation.

        FF3 header structure:
        - 2 bytes: vaddroffset (raw)
        - 2 bytes: emptyvoice marker
        - 16 bytes: 8 voice pointers (vstart)
        - 16 bytes: 8 voice pointers (vstart2) - alternate starting points

        Args:
            data: Song data bytes
            song_id: Song ID for reading instrument table
            use_alternate_pointers: If True, use vstart2 instead of vstart

        Returns:
            Dict with header info including track offsets, pointers, boundaries
        """
        if len(data) < 36:
            return {'track_offsets': [], 'instrument_table': []}

        # Read vaddroffset and emptyvoice
        vaddroffset_raw, emptyvoice = struct.unpack('<HH', data[0:4])
        # FF3 specific: adjust vaddroffset
        vaddroffset = (0x11c24 - vaddroffset_raw) & 0xFFFF

        # Read both standard and alternate voice pointers
        vstart = struct.unpack('<8H', data[4:20])
        vstart2 = struct.unpack('<8H', data[20:36])

        # Choose which set to use for primary track data
        active_vstart = vstart2 if use_alternate_pointers else vstart

        # Process active voice pointers (standard or alternate depending on flag)
        track_offsets = []
        adjusted_voice_pointers = []
        for ptr in active_vstart:
            if ptr != emptyvoice:
                adjusted_ptr = (ptr + vaddroffset) & 0xFFFF
                adjusted_voice_pointers.append(adjusted_ptr)
                # Convert SPC address to buffer offset (0x1C00 base)
                buffer_offset = adjusted_ptr - 0x1C00
                if buffer_offset >= 0 and buffer_offset < len(data):
                    track_offsets.append(buffer_offset)
            else:
                adjusted_voice_pointers.append(0)  # Mark unused voices as 0

        # Read instrument table at base + 0x3f95 + song_id * 0x20
        base_offset = self._snes_addr_to_offset(self.base_address)
        inst_table_offset = base_offset + 0x3f95 + (song_id * 0x20)
        instrument_table = []

        if inst_table_offset + 32 <= len(self.rom_data):
            for j in range(16):
                inst_id = struct.unpack('<H', self.rom_data[inst_table_offset + j*2:inst_table_offset + j*2 + 2])[0]
                if inst_id != 0:
                    instrument_table.append(inst_id)

        # Calculate track boundaries for address validation
        # Each track occupies SPC RAM from its start address to the nearest other track's start (or end of data)
        # Note: Tracks are NOT necessarily in ascending address order!
        track_boundaries = {}
        for i, start_addr in enumerate(adjusted_voice_pointers):
            if start_addr != 0:  # Not empty voice
                # Find the smallest address > start_addr among ALL other tracks
                other_starts = [addr for j, addr in enumerate(adjusted_voice_pointers)
                               if j != i and addr != 0 and addr > start_addr]
                end_addr = min(other_starts) if other_starts else (0x1C00 + len(data))
                track_boundaries[i] = (start_addr, end_addr)

        # Store for validation in _parse_track_pass1
        self._track_boundaries = track_boundaries

        # When using standard pointers, also process alternate pointers to detect differences
        has_alternate_pointers = False
        alternate_track_offsets = []
        alternate_voice_pointers_list = []
        alternate_track_boundaries = {}

        if not use_alternate_pointers:
            # Process alternate voice pointers
            for ptr in vstart2:
                if ptr != emptyvoice:
                    adjusted_ptr = (ptr + vaddroffset) & 0xFFFF
                    alternate_voice_pointers_list.append(adjusted_ptr)
                    buffer_offset = adjusted_ptr - 0x1C00
                    if buffer_offset >= 0 and buffer_offset < len(data):
                        alternate_track_offsets.append(buffer_offset)
                else:
                    alternate_voice_pointers_list.append(0)

            # Calculate alternate track boundaries
            for i, start_addr in enumerate(alternate_voice_pointers_list):
                if start_addr != 0:
                    other_starts = [addr for j, addr in enumerate(alternate_voice_pointers_list)
                                   if j != i and addr != 0 and addr > start_addr]
                    end_addr = min(other_starts) if other_starts else (0x1C00 + len(data))
                    alternate_track_boundaries[i] = (start_addr, end_addr)

            # Check if any alternate pointer differs from standard (excluding empty voices)
            for i in range(len(adjusted_voice_pointers)):
                std_ptr = adjusted_voice_pointers[i]
                alt_ptr = alternate_voice_pointers_list[i]
                # Only compare non-empty voices
                if std_ptr != 0 or alt_ptr != 0:
                    if std_ptr != alt_ptr:
                        has_alternate_pointers = True
                        break

        result = {
            'track_offsets': track_offsets,
            'instrument_table': instrument_table,
            'vaddroffset': vaddroffset,
            'emptyvoice': emptyvoice,
            'voice_pointers': tuple(adjusted_voice_pointers),  # Adjusted SPC RAM addresses for disassembly
            'track_boundaries': track_boundaries  # For address validation: {track_num: (start_spc, end_spc)}
        }

        # Add alternate pointer information when processing standard pointers
        if not use_alternate_pointers:
            result['has_alternate_pointers'] = has_alternate_pointers
            result['alternate_track_offsets'] = alternate_track_offsets
            result['alternate_voice_pointers'] = tuple(alternate_voice_pointers_list)
            result['alternate_track_boundaries'] = alternate_track_boundaries

        # Store song data length for validation
        self._song_data_length = len(data)

        return result

    def get_track_offsets(self, data: bytes, header: Dict) -> List[int]:
        """Get list of voice offsets within song data.

        For FF3, track offsets are pre-calculated in parse_header.
        """
        return header.get('track_offsets', [])

    def _validate_track_address(self, track_num: int, target_spc_addr: int,
                                source_offset: int, event_type: str, has_timing_events: bool = False) -> bool:
        """Validate that a target address is within the valid range for this track.

        For FF3, each track should only reference addresses within the song data.
        GOTO/LOOP_BREAK targets must fall within the song's SPC RAM region, EXCEPT:
        - Cross-track GOTOs at the very start (chorus effects, e.g. song 3D)
        - "Fall through" execution into subsequent voice regions (e.g. song 1E)

        Args:
            track_num: Track/voice number
            target_spc_addr: Target SPC RAM address to validate
            source_offset: Byte offset of the instruction containing this target
            event_type: Type of event (e.g., "GOTO", "LOOP_BREAK")
            has_timing_events: True if any notes/rests have been processed yet

        Returns:
            True if valid, False if invalid (warning already printed)
        """
        # Allow GOTO to other tracks at the very start (for chorus effects)
        if event_type == "GOTO" and not has_timing_events:
            return True

        # Simplified validation - just check song boundaries
        # All track data (including fall through) lies within the song's SPC RAM region
        if not hasattr(self, '_song_data_length'):
            return True  # No song length available, can't validate

        song_start = 0x1C00
        song_end = 0x1C00 + self._song_data_length

        if target_spc_addr < song_start or target_spc_addr >= song_end:
            # Target is outside song data - warn
            import sys
            print(f"WARNING: Track {track_num} @ 0x{source_offset:04X}: {event_type} targets SPC addr 0x{target_spc_addr:04X}",
                  file=sys.stderr)
            print(f"         Song data range: [0x{song_start:04X}, 0x{song_end:04X})",
                  file=sys.stderr)
            print(f"         This indicates incorrect vaddroffset calculation",
                  file=sys.stderr)
            return False

        return True

    def parse_track(self, data: bytes, offset: int, track_num: int, song_id: int = 0,
                    instrument_table: Optional[List[int]] = None,
                    target_loop_time: int = 0, loop_info: Optional[Dict] = None) -> Tuple[List, List]:
        """Parse a single SNES voice's event data using a two-pass approach.

        Pass 1: Parse opcodes linearly, generate disassembly, build intermediate representation (IR)
        Pass 2: Expand IR with loops to generate MIDI events

        Args:
            data: Song data buffer
            offset: Offset into data where this voice starts
            track_num: Track/voice number (0-7)
            song_id: Song ID for looking up instruments
            instrument_table: List of instrument IDs for this song
            target_loop_time: Target time for 2x loop playthrough (0 = no looping)
            loop_info: Loop analysis info from _analyze_track_loops()

        Returns:
            Tuple of (disassembly_lines, midi_events)
        """
        if instrument_table is None:
            instrument_table = []

        # Pass 1: Parse and build IR
        disasm, ir_events = self._parse_track_pass1(data, offset, track_num, instrument_table)

        # Pass 2: Expand IR and generate MIDI
        midi_events = self._parse_track_pass2(ir_events, track_num, target_loop_time, loop_info)

        return disasm, midi_events

    def _parse_track_pass1(self, data: bytes, offset: int, track_num: int,
                          instrument_table: List[int], vaddroffset: int = 0,
                          track_boundaries: Optional[Dict[int, Tuple[int, int]]] = None) -> Tuple[List, List[IREvent]]:
        """Pass 1: Linear parse to build disassembly and intermediate representation.

        This pass does NOT execute loops or generate MIDI events. It simply parses
        the opcode stream linearly, building IR events that represent the sequence
        structure. Disassembly is generated with loop awareness to avoid duplicates.

        Returns:
            Tuple of (disassembly_lines, ir_events)
        """
        disasm = []
        ir_events = []

        # Track state for disassembly and IR building
        octave = self.default_octave
        velocity = self.default_velocity
        tempo = self.default_tempo
        current_patch = 0
        perc_key = 0
        transpose_octaves = 0
        inst_id = 0  # Current instrument ID from table

        # Track loop depth for disassembly indentation (but don't execute loops)
        loop_depth = 0

        # Track whether any timing events (notes/rests) have been processed
        # This allows cross-track GOTOs at the very start (for chorus effects)
        has_timing_events = False

        # Start from voice offset
        p = offset

        while p < len(data):
            cmd = data[p]

            # Convert buffer offset to SPC RAM address for display
            # SPC data loaded at 0x1C00 for FF3
            spc_addr = self.buffer_offset_to_spc_addr(p)
            line = f"      {spc_addr:04X}: {cmd:02X} "

            # Check if it's a note (< self.first_opcode)
            if cmd < self.first_opcode:
                # Note encoding: note = cmd / note_divisor, duration = cmd % note_divisor
                note_num = cmd // self.note_divisor
                dur_idx = cmd % self.note_divisor
                # Duration table values are in native units (48 ticks/quarter)
                # Double them to convert to MIDI resolution (96 ticks/quarter)
                raw_dur = self.duration_table[dur_idx]
                dur = raw_dur * 2

                if note_num < 12:
                    # Regular note - create IR event
                    event = make_note(p, note_num, dur)
                    # Store current state in event for pass 2
                    # NOTE: octave is NOT stored here - it's tracked as state in Pass 2
                    # because loops can modify octave during execution
                    event.metadata = {
                        'velocity': velocity,
                        'patch': current_patch,
                        'perc_key': perc_key,
                        'transpose': transpose_octaves,
                        'track_num': track_num,
                        'inst_id': inst_id
                    }
                    ir_events.append(event)
                    has_timing_events = True

                    # Build disassembly line
                    # Format: "4E            Note F  (05) Dur 48"
                    note_name = NOTE_NAMES[note_num]
                    if perc_key:
                        line += f"           Note {note_name:<2} ({note_num:02}) Dur {raw_dur:<3} [PERC key={perc_key}]"
                    else:
                        line += f"           Note {note_name:<2} ({note_num:02}) Dur {raw_dur}"

                elif note_num == 12:
                     # Tie - create IR event
                    event = make_tie(p, dur)
                    ir_events.append(event)
                    has_timing_events = True
                    line += f"           Tie          Dur {raw_dur}"

                else:
                    # Rest - create IR event
                    event = make_rest(p, dur)
                    ir_events.append(event)
                    has_timing_events = True
                    line += f"           Rest         Dur {raw_dur}"

                p += 1
                disasm.append(line)

            else:
                # Command opcode
                oplen = self.opcode_table[cmd - self.first_opcode]
                operands = list(data[p+1:p+1+oplen])

                # Format operands with proper spacing
                operand_str = ' '.join(f"{op:02X}" for op in operands)
                # Pad operands to align text descriptions (assume max 3 operands = 8 chars)
                line += f"{operand_str:<8}   {self.OPCODE_NAMES.get(cmd, f'OP_{cmd:02X}')}"

                # Create IR events for opcodes that affect playback
                if cmd == 0xF0 and len(operands) >= 1:
                    # Tempo change (FF3 uses 0xF0 vs FF2 uses 0xD2)
                    tempo = operands[0]
                    event = make_tempo(p, tempo, operands)
                    ir_events.append(event)

                elif cmd == 0xDC and len(operands) >= 1:  # Patch Change
                    patch_operand = operands[0]

                    # FF3 dual patch map: operand < 0x20 uses patch_map_low
                    if patch_operand < 0x20:
                        # Use low patch map
                        if patch_operand in self.patch_map_low:
                            patch_info = self.patch_map_low[patch_operand]
                            gm_patch = patch_info['gm_patch']
                            transpose_octaves = patch_info.get('transpose', 0)
                            inst_id = patch_operand  # For disasm
                        else:
                            gm_patch = 0
                            transpose_octaves = 0
                            inst_id = patch_operand
                    else:
                        # Use main patch map with instrument table
                        inst_idx = patch_operand - 0x20
                        if inst_idx < len(instrument_table):
                            inst_id = instrument_table[inst_idx]
                            if inst_id in self.patch_map:
                                patch_info = self.patch_map[inst_id]
                                gm_patch = patch_info['gm_patch']
                                transpose_octaves = patch_info.get('transpose', 0)
                            else:
                                gm_patch = 0
                                transpose_octaves = 0
                        else:
                            gm_patch = 0
                            transpose_octaves = 0
                            inst_id = patch_operand

                    # Check if this is percussion (negative GM patch number)
                    if gm_patch < 0:
                        # Percussion mode
                        perc_key = abs(gm_patch)
                        line += f" -> PERC key={perc_key}"
                    else:
                        # Regular instrument
                        perc_key = 0
                        line += f" -> GM patch {gm_patch}"

                    # Create patch change IR event
                    current_patch = gm_patch
                    ir_events.append(make_patch_change(p, inst_id, gm_patch, transpose_octaves, operands))

                elif cmd == 0xD6 and len(operands) >= 1:
                    # Set octave (FF3 uses 0xD6 vs FF2 uses 0xDA)
                    octave = operands[0]
                    event = make_octave_set(p, octave, operands)
                    ir_events.append(event)

                elif cmd == 0xD7:
                    # Inc octave (FF3 uses 0xD7 vs FF2 uses 0xE1)
                    octave += 1
                    event = make_octave_inc(p)
                    ir_events.append(event)

                elif cmd == 0xD8:
                    # Dec octave (FF3 uses 0xD8 vs FF2 uses 0xE2)
                    octave -= 1
                    event = make_octave_dec(p)
                    ir_events.append(event)

                elif cmd == 0xC4 and len(operands) >= 1:
                    # Voice Volume (FF3 uses 0xC4 vs FF2 uses 0xF2)
                    # For FF3, single operand for immediate volume
                    # Scale from 0-255 to MIDI velocity 0-127
                    velocity = operands[0] >> 1
                    event = make_volume(p, velocity, operands)
                    ir_events.append(event)

                elif cmd == 0xE2 and len(operands) >= 1:
                    # Begin repeat (FF3 uses 0xE2 vs FF2 uses 0xE0)
                    loop_depth += 1
                    event = make_loop_start(p, operands[0], operands)
                    ir_events.append(event)

                elif cmd == 0xE3:
                    # End repeat (FF3 uses 0xE3 vs FF2 uses 0xF0)
                    loop_depth = max(0, loop_depth - 1)
                    event = make_loop_end(p)
                    ir_events.append(event)

                elif cmd == 0xF5 and len(operands) >= 3:
                    # Selective repeat (0xF5) - conditional jump - just record
                    # Apply vaddroffset per Perl code: target = (($op2 + $op3 * 256) + $vaddroffset - 1) & 0xffff
                    target_spc_addr = ((operands[1] + operands[2] * 256) + vaddroffset - 1) & 0xFFFF
                    target_offset = target_spc_addr - 0x1C00  # Convert SPC address to buffer offset (FF3 base)

                    # Validate address is within track boundaries
                    self._validate_track_address(track_num, target_spc_addr, p, "LOOP_BREAK", has_timing_events)

                    # Add target address to disassembly line
                    line += f" ${target_spc_addr:04X}"

                    event = make_loop_break(p, operands[0], target_offset, operands)
                    ir_events.append(event)

                elif cmd == 0xC9 and len(operands) >= 3:
                    # Vibrato settings (FF3 uses 0xC9 vs FF2 uses 0xD8)
                    event = make_vibrato_on(p, operands)
                    ir_events.append(event)

                elif cmd == 0xCA:
                    # Vibrato off (FF3 uses 0xCA vs FF2 uses 0xE8)
                    event = make_vibrato_off(p)
                    ir_events.append(event)

                elif cmd == 0xCB and len(operands) >= 3:
                    # Tremolo settings (FF3 uses 0xCB vs FF2 uses 0xD7)
                    event = make_tremolo_on(p, operands)
                    ir_events.append(event)

                elif cmd == 0xCC:
                    # Tremolo off (FF3 uses 0xCC vs FF2 uses 0xE7)
                    event = make_tremolo_off(p)
                    ir_events.append(event)

                elif cmd == 0xC8 and len(operands) >= 3:
                    # Portamento settings (FF3 uses 0xC8 vs FF2 uses 0xD6)
                    event = make_portamento_on(p, operands)
                    ir_events.append(event)

                elif cmd == 0xCE:
                    # Pan Sweep off (replaces FF2's portamento off at 0xE6)
                    event = make_portamento_off(p)
                    ir_events.append(event)

                elif cmd == 0xF6 and len(operands) >= 2:
                    # Goto (FF3 uses 0xF6 vs FF2 uses 0xF4)
                    # Apply vaddroffset per Perl code: target = (($op1 + $op2 * 256) + $vaddroffset) & 0xffff
                    target_spc_addr = ((operands[0] + operands[1] * 256) + vaddroffset) & 0xFFFF
                    target_offset = target_spc_addr - 0x1C00  # Convert SPC address to buffer offset (FF3 base)

                    # Determine if this is a backwards GOTO
                    is_backwards = target_offset < p

                    # Validate address is within track boundaries
                    # Allow cross-track GOTOs at the start for chorus effects (e.g., song 3D)
                    self._validate_track_address(track_num, target_spc_addr, p, "GOTO", has_timing_events)

                    # Add target address to disassembly line
                    line += f" ${target_spc_addr:04X}"

                    event = make_goto(p, target_offset, operands)
                    ir_events.append(event)
                    disasm.append(line)

                    # Backwards GOTO - loop, halt disassembly
                    if is_backwards:
                        break
                    else:
                        # Forward GOTO - check if target is in another track's assigned region
                        if track_boundaries:
                            target_track = None
                            for t_num, (t_start, t_end) in track_boundaries.items():
                                if t_num != track_num and t_start <= target_offset < t_end:
                                    target_track = t_num
                                    break

                            if target_track is not None:
                                # GOTO into another track's data (songs 49, 4A) - halt
                                break

                        # Either no track_boundaries, or target is unassigned - continue (songs 53, 54)
                        p += oplen + 1

                elif cmd in (0xEB, 0xEC, 0xED, 0xEE, 0xEF, 0xFD, 0xFE, 0xFF):
                    # Halt - end of track
                    event = make_halt(p, operands)
                    ir_events.append(event)
                    disasm.append(line)
                    break

                p += oplen + 1
                disasm.append(line)

        return disasm, ir_events

    def _analyze_track_loops(self, ir_events: List[IREvent]) -> Dict:
        """Analyze track for backwards GOTO loops and calculate timing.

        This is a simplified analysis pass that detects loop patterns and measures
        timing without generating MIDI events.

        Args:
            ir_events: List of IR events from pass 1

        Returns:
            Dict with keys:
                'has_backwards_goto': bool - True if track ends with backwards GOTO
                'intro_time': int - Time units before loop starts (0 if no loop)
                'loop_time': int - Time units for one loop iteration (0 if no loop)
                'goto_target_idx': int - Index of GOTO target event (None if no loop)
        """
        total_time = 0
        loop_stack = []

        # Find the last BACKWARDS GOTO event (for looping)
        # Forward GOTOs are sequence continuation, not loops
        last_goto_event = None
        last_goto_idx = None
        for i, event in enumerate(ir_events):
            if event.type == IREventType.GOTO:
                # Check if this GOTO is backwards (target_offset < current offset)
                if event.target_offset < event.offset:
                    last_goto_event = event
                    last_goto_idx = i

        # Check if it's a backwards GOTO
        if last_goto_event is None:
            return {
                'has_backwards_goto': False,
                'intro_time': 0,
                'loop_time': 0,
                'goto_target_idx': None
            }

        # Find target event index
        target_idx = None
        for j, e in enumerate(ir_events):
            if e.offset == last_goto_event.target_offset:
                target_idx = j
                break

        # DEBUG
        # if target_idx is not None and last_goto_idx is not None:
        #     print(f"    Found backwards GOTO: last_goto_idx={last_goto_idx}, target_idx={target_idx}")

        # Check if backwards (target comes before GOTO)
        assert last_goto_idx is not None, "last_goto_idx should not be None here"
        if target_idx is None or target_idx >= last_goto_idx:
            # Forward GOTO or target not found - not a loop
            return {
                'has_backwards_goto': False,
                'intro_time': 0,
                'loop_time': 0,
                'goto_target_idx': None
            }

        # It's a backwards GOTO - calculate intro and loop times
        # Intro time = time from start (index 0) to GOTO target (target_idx)
        # Loop time = time from GOTO target to GOTO itself

        # Measure intro time: execute from 0 to target_idx
        intro_time = 0
        loop_stack_intro = []
        i = 0
        while i < target_idx:
            event = ir_events[i]

            if event.type == IREventType.NOTE or event.type == IREventType.REST or event.type == IREventType.TIE:
                assert event.duration is not None
                intro_time += event.duration
                i += 1
            elif event.type == IREventType.LOOP_START:
                loop_stack_intro.append({'start_idx': i + 1, 'count': event.loop_count, 'iteration': 0})
                i += 1
            elif event.type == IREventType.LOOP_END:
                if loop_stack_intro:
                    loop = loop_stack_intro[-1]
                    loop['count'] -= 1
                    if loop['count'] >= 0:
                        i = loop['start_idx']
                    else:
                        loop_stack_intro.pop()
                        i += 1
                else:
                    i += 1
            elif event.type == IREventType.LOOP_BREAK:
                if loop_stack_intro:
                    loop = loop_stack_intro[-1]
                    loop['iteration'] += 1
                    if loop['iteration'] == event.condition:
                        for j, e in enumerate(ir_events):
                            if e.offset == event.target_offset:
                                i = j
                                break
                        loop_stack_intro.pop()
                    else:
                        i += 1
                else:
                    i += 1
            else:
                i += 1

        # Measure loop time: execute from target_idx to last_goto_idx
        loop_time = 0
        assert target_idx is not None, "target_idx should not be None for backwards GOTO"
        assert last_goto_idx is not None, "last_goto_idx should not be None for backwards GOTO"
        loop_stack_loop = []
        j = target_idx

        while j < last_goto_idx:
            e = ir_events[j]

            if e.type == IREventType.NOTE or e.type == IREventType.REST or e.type == IREventType.TIE:
                assert e.duration is not None
                loop_time += e.duration
                j += 1
            elif e.type == IREventType.LOOP_START:
                loop_stack_loop.append({'start_idx': j + 1, 'count': e.loop_count, 'iteration': 0})
                j += 1
            elif e.type == IREventType.LOOP_END:
                if loop_stack_loop:
                    lp = loop_stack_loop[-1]
                    lp['count'] -= 1
                    if lp['count'] >= 0:
                        j = lp['start_idx']
                    else:
                        loop_stack_loop.pop()
                        j += 1
                else:
                    j += 1
            elif e.type == IREventType.LOOP_BREAK:
                if loop_stack_loop:
                    lp = loop_stack_loop[-1]
                    lp['iteration'] += 1
                    if lp['iteration'] == e.condition:
                        for k, ev in enumerate(ir_events):
                            if ev.offset == e.target_offset:
                                j = k
                                break
                        loop_stack_loop.pop()
                    else:
                        j += 1
                else:
                    j += 1
            else:
                j += 1

        # DEBUG
        # print(f"      Analysis: intro_time={intro_time}, loop_time={loop_time}, target_idx={target_idx}, last_goto_idx={last_goto_idx}")

        return {
            'has_backwards_goto': True,
            'intro_time': intro_time,
            'loop_time': loop_time,
            'goto_target_idx': target_idx
        }

    def _parse_track_pass2(self, all_track_data: Dict, start_voice_num: int,
                          target_loop_time: int = 0) -> List[Dict]:
        """Pass 2: Expand IR events with loop execution to generate MIDI events.

        This pass takes all track data and executes from a starting voice, expanding loops,
        following GOTOs (including cross-track), and generating timed MIDI events.

        Args:
            all_track_data: Complete track data from parse_all_tracks()
            start_voice_num: Starting track/voice number (0-7)
            target_loop_time: Target time for 2x loop playthrough (0 = no looping)
            loop_info: Loop analysis info from _analyze_track_loops()

        Returns:
            List of MIDI event dictionaries
        """
        import time
        start_time = time.time()
        max_time_seconds = 2.0  # Emergency brake: 2 second time limit
        max_midi_events = 50000  # Emergency brake: max MIDI events per track

        midi_events = []
        total_time = 0

        # Start with the specified voice
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

        # Loop execution state
        loop_stack = []  # Stack of {start_idx, count, iteration}

        # Calculate target time for loop termination
        target_total_time = 0
        if loop_info and loop_info['has_backwards_goto'] and target_loop_time > 0:
            intro_time = loop_info['intro_time']
            target_total_time = intro_time + target_loop_time

        # Event pointer for execution
        i = 0
        # Most game music loops infinitely. Allow enough iterations for ~2 full playthroughs
        # Typical: 100-200 IR events, with loops expanding to 10,000-20,000 iterations
        # Ensure minimum iterations for short loops while still acting as failsafe
        # - Multiplier of 200 per event handles normal cases
        # - Minimum of 10000 ensures short loops (1-2 events) can reach target duration
        max_iterations = max(len(ir_events) * 200, 10000)
        iteration_count = 0

        while i < len(ir_events) and iteration_count < max_iterations:
            iteration_count += 1

            # Check if we've reached target playthrough time
            if target_total_time > 0 and total_time >= target_total_time:
                break

            # Emergency brakes (only warn, not error - looping is normal)
            if time.time() - start_time > max_time_seconds:
                # Silently stop - time limit reached (normal for looping songs)
                break
            if len(midi_events) > max_midi_events:
                # Silently stop - MIDI event limit reached (normal for looping songs)
                break

            if i < 0 or i >= len(ir_events):
                print(f"WARNING: Track {start_voice_num} invalid event index {i}, stopping")
                break
            event = ir_events[i]

            if event.type == IREventType.NOTE:
                # Extract state from metadata (stored in pass 1)
                # NOTE: octave is tracked as state variable, not in metadata
                velocity = event.metadata['velocity']
                perc_key = event.metadata['perc_key']
                transpose_octaves = event.metadata['transpose']

                # Calculate MIDI note
                assert event.note_num is not None, "NOTE event must have note_num"
                assert event.duration is not None, "NOTE event must have duration"
                midi_note = 12 * (octave + transpose_octaves) + event.note_num

                # Check if we're in percussion mode
                if perc_key:
                    midi_channel = 9  # Percussion channel
                    midi_note = perc_key
                else:
                    midi_channel = current_channel

                # Add MIDI note event
                midi_events.append({
                    'type': 'note',
                    'time': total_time,
                    'duration': event.duration - 1,  # Matches Perl: $dur - 1
                    'note': midi_note,
                    'velocity': velocity,
                    'channel': midi_channel
                })

                total_time += event.duration
                i += 1

            elif event.type == IREventType.REST:
                # Rest just advances time
                assert event.duration is not None, "REST event must have duration"
                total_time += event.duration
                i += 1

            elif event.type == IREventType.TIE:
                # Tie extends the last note
                assert event.duration is not None, "TIE event must have duration"
                for j in range(len(midi_events) - 1, -1, -1):
                    if midi_events[j]['type'] == 'note':
                        midi_events[j]['duration'] += event.duration
                        break
                total_time += event.duration
                i += 1

            elif event.type == IREventType.TEMPO:
                # Tempo change
                assert event.value is not None, "TEMPO event must have value"
                midi_events.append({
                    'type': 'tempo',
                    'time': total_time,
                    'tempo': event.value
                })
                tempo = event.value
                i += 1

            elif event.type == IREventType.PATCH_CHANGE:
                # Patch change
                assert event.gm_patch is not None, "PATCH_CHANGE event must have gm_patch"
                if event.gm_patch < 0:
                    # Percussion mode
                    perc_key = abs(event.gm_patch)
                    transpose_octaves = event.transpose
                else:
                    # Regular instrument
                    perc_key = 0
                    transpose_octaves = event.transpose
                    midi_events.append({
                        'type': 'program_change',
                        'time': total_time,
                        'patch': event.gm_patch
                    })
                i += 1

            elif event.type == IREventType.OCTAVE_SET:
                assert event.value is not None, "OCTAVE_SET event must have value"
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

            elif event.type == IREventType.LOOP_START:
                # Push loop onto stack
                loop_stack.append({
                    'start_idx': i + 1,  # Start after LOOP_START
                    'count': event.loop_count,
                    'iteration': 0,
                    'end_idx': None  # Will be set when we find LOOP_END
                })
                i += 1

            elif event.type == IREventType.LOOP_END:
                # End of loop body - decide if we repeat
                if loop_stack:
                    loop = loop_stack[-1]
                    loop['count'] -= 1

                    if loop['count'] >= 0:
                        # Repeat: jump back to loop start
                        i = loop['start_idx']
                    else:
                        # Done looping: pop and continue
                        loop_stack.pop()
                        i += 1
                else:
                    # No matching loop start? Just continue
                    i += 1

            elif event.type == IREventType.LOOP_BREAK:
                # Selective repeat (F5): conditional jump on specific iteration
                # This increments the current loop iteration and jumps if it matches the condition
                if loop_stack:
                    loop = loop_stack[-1]
                    loop['iteration'] += 1

                    if loop['iteration'] == event.condition:
                        # Condition met: jump to target and exit loop
                        # Find event at target offset
                        target_idx = None
                        for j, e in enumerate(ir_events):
                            if e.offset == event.target_offset:
                                target_idx = j
                                break

                        if target_idx is not None:
                            i = target_idx
                            loop_stack.pop()  # Exit this loop level
                        else:
                            # Target not found, just continue
                            i += 1
                    else:
                        # Condition not met: continue to next event
                        i += 1
                else:
                    # No loop context, just skip
                    i += 1

            elif event.type == IREventType.GOTO:
                # Determine GOTO type and handle accordingly
                if event.target_offset is None:
                    break  # Invalid GOTO

                # Find target track and event
                result = self._find_event_by_offset(all_track_data, event.target_offset)

                if result is None:
                    # Target not found - halt
                    break

                target_track, target_idx = result

                # Classify GOTO type
                is_backwards = event.target_offset < event.offset
                is_cross_track = target_track != current_voice_num

                if is_backwards and not is_cross_track:
                    # Backwards loop within same track
                    if loop_info and loop_info.get('has_backwards_goto', False) and target_loop_time > 0:
                        # Follow loop until target time reached
                        i = target_idx
                    else:
                        # No loop playback requested - halt at loop point
                        break
                else:
                    # Forward GOTO or cross-track GOTO - follow as normal continuation
                    current_voice_num = target_track
                    ir_events = all_track_data['tracks'][current_voice_num]['ir_events']
                    i = target_idx

                    # Switch to target track's loop_info
                    loop_info = all_track_data['tracks'][current_voice_num].get('loop_info', {})

                    # Recalculate target_total_time for new track's loop
                    if loop_info.get('has_backwards_goto', False) and target_loop_time > 0:
                        intro_time = loop_info['intro_time']
                        target_total_time = intro_time + target_loop_time
                    else:
                        target_total_time = 0

            elif event.type == IREventType.HALT:
                # End of track
                break

            else:
                # Other event types (vibrato, tremolo, etc.) are not yet expanded
                # Just skip for now
                i += 1

        # Check if we hit the iteration limit
        if iteration_count >= max_iterations:
            print(f"WARNING: Track {start_voice_num} hit max iteration limit ({max_iterations}), possible infinite loop")

        return midi_events


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

        # Initialize file handle cache first
        self._exe_file_handle = None  # Will hold open file handle for executable
        self._img_file_cache = {}  # Cache of open file handles for large IMG files

        # Handle SNES ROMs differently from PSX ISOs
        self.iso: Optional[pycdlib_module.PyCdlib]
        self.sector_size: Optional[int]
        self.raw_sector_size: Optional[int]

        if self.console_type == 'snes':
            # SNES ROMs are simple binary files, no ISO needed
            self.iso = None
            self.sector_size = None
            self.raw_sector_size = None
            self.rom_data: bytes = self._load_snes_rom()
        else:
            # PSX: Detect sector size if not specified
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

            # Find and load the executable ROM
            # This will set _exe_file_handle as a side effect
            self.rom_data: bytes = self._load_executable()

        # Create format handler with ROM data
        format_name = self.config.get('format', 'akao_newstyle')
        self.format_handler: SequenceFormat
        if format_name == 'akao_newstyle':
            self.format_handler = AKAONewStyle(self.config, self.rom_data, exe_iso_reader=self)
        elif format_name == 'snes_ff2':
            self.format_handler = SNESFF2(self.config, self.rom_data)
        elif format_name == 'snes_ff3':
            self.format_handler = SNESFF3(self.config, self.rom_data)
        else:
            raise ValueError(f"Unknown format: {format_name}")
    
    def _load_executable(self) -> bytes:
        """Load the console executable from the disc image."""
        if self.console_type == 'psx':
            return self._load_psx_executable()
        else:
            raise ValueError(f"Unsupported console type: {self.console_type}")

    def _load_snes_rom(self) -> bytes:
        """Load SNES ROM file directly."""
        import os
        print(f"Loading SNES ROM: {self.source_file}")

        # Read entire ROM file
        with open(self.source_file, 'rb') as f:
            rom_data = f.read()

        file_size = len(rom_data)
        print(f"ROM size: {file_size} bytes ({file_size // 1024}K)")

        # Detect SMC header (512 bytes)
        # SMC header is present if file size % 1024 == 512
        if file_size % 1024 == 512:
            print("Detected SMC header (512 bytes)")
        else:
            print("No SMC header detected")

        return rom_data

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
        assert self.iso is not None, "ISO must be loaded for PSX executables"

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
        if self.console_type == 'snes':
            raise ValueError("SNES ROMs do not support ISO file access")

        assert self.iso is not None, "ISO must be loaded for PSX file access"

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
        if self.console_type == 'snes':
            raise ValueError("SNES ROMs do not support ISO file access")

        assert self.iso is not None, "ISO must be loaded for PSX file access"

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
        # SNES ROMs: read directly from ROM using song pointer table
        if self.console_type == 'snes':
            # Get offset and length from format handler's song pointer table
            if hasattr(self.format_handler, 'song_pointers'):
                if song.id in self.format_handler.song_pointers:  # type: ignore[attr-defined]
                    offset, length = self.format_handler.song_pointers[song.id]  # type: ignore[attr-defined]
                    # Skip the 2-byte size field at the start - we only want the actual music data
                    return self.rom_data[offset + 2:offset + 2 + length]
                else:
                    raise ValueError(f"Song {song.id:02X} not found in song pointer table")
            else:
                # Fallback to explicit offset/length if specified
                if song.offset is None or song.length is None:
                    raise ValueError(f"Song {song.id:02X} has no offset/length specified")
                return self.rom_data[song.offset:song.offset + song.length]

        # PSX: Check if song specifies a file path with offset
        if song.file_path:
            # Read from a specific file in the ISO
            if song.offset is not None and song.length is not None:
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
                assert song.length is not None, "Song must have length for sector-based extraction"
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
                assert song.length is not None, "Song must have length for sector-based extraction"
                assert self.raw_sector_size is not None, "raw_sector_size must be set for PSX"
                offset = song.sector * self.raw_sector_size if song.offset is None else song.offset
                f.seek(offset)
                return f.read(song.length)

    def parse_all_tracks(self, song: SongMetadata, data: bytes, use_alternate_pointers: bool = False) -> Dict:
        """Parse all tracks for a song (Pass 1) and return IR events + disassembly.

        This method performs the first pass parsing for all tracks in a song,
        generating both IR events and disassembly text. The results can be reused
        for generating multiple output formats without re-parsing.

        Args:
            song: Song metadata
            data: Raw song data
            use_alternate_pointers: If True, use alternate voice pointers (FF3 vstart2)

        Returns:
            Dict with structure:
            {
                'header': {...},  # Parsed header
                'tracks': {
                    voice_num: {
                        'offset': int,  # Byte offset within data
                        'ir_events': List[IREvent],  # IR events from pass 1
                        'disasm_lines': List[str],  # Disassembly text lines
                    },
                    ...
                }
            }
        """
        # Parse header (pass song_id for FF3, and use_alternate_pointers flag)
        header = self.format_handler.parse_header(data, song.id, use_alternate_pointers)
        track_offsets = self.format_handler.get_track_offsets(data, header)

        # Read instrument table for SNES FF2/FF3 format
        instrument_table = None
        if isinstance(self.format_handler, SNESFF2):
            instrument_table = self.format_handler._read_song_instrument_table(song.id)
        elif isinstance(self.format_handler, SNESFF3):
            # FF3: instrument table is in the header
            instrument_table = header.get('instrument_table', [])

        # Get vaddroffset for FF3 (for GOTO/LOOP_BREAK address calculations)
        vaddroffset = header.get('vaddroffset', 0) if isinstance(self.format_handler, SNESFF3) else 0

        # Parse all tracks
        tracks = {}
        for voice_num, offset in enumerate(track_offsets):
            # Call _parse_track_pass1 to get disassembly and IR events
            # Returns: (disasm_lines, ir_events)
            disasm_lines, ir_events = self.format_handler._parse_track_pass1(
                data, offset, voice_num, instrument_table or [], vaddroffset,
                track_boundaries=header.get('track_boundaries')
            )

            tracks[voice_num] = {
                'offset': offset,
                'ir_events': ir_events,
                'disasm_lines': disasm_lines,
            }

        return {
            'header': header,
            'tracks': tracks,
        }

    def analyze_song_structure(self, track_data: Dict) -> Dict:
        """Analyze loop structure for all tracks and determine song length.

        Args:
            track_data: Output from parse_all_tracks()

        Returns:
            Dict with structure:
            {
                'tracks': {
                    voice_num: {
                        'loop_info': {...},  # From _analyze_track_loops
                    },
                    ...
                },
                'song_length': int,  # Maximum target_time across all tracks (in ticks)
            }
        """
        analysis = {
            'tracks': {},
            'song_length': 0,
        }

        max_target_time = 0

        for voice_num, track in track_data['tracks'].items():
            # Analyze loop structure for this track
            loop_info = self.format_handler._analyze_track_loops(track['ir_events'])

            analysis['tracks'][voice_num] = {
                'loop_info': loop_info,
            }

            # Track maximum target time for song length
            target_time = loop_info.get('target_time', 0)
            if target_time > max_target_time:
                max_target_time = target_time

        analysis['song_length'] = max_target_time

        return analysis

    def disassemble_to_text(self, song: SongMetadata, track_data: Dict) -> str:
        """Generate text disassembly of sequence from pre-parsed track data.

        Args:
            song: Song metadata
            track_data: Output from parse_all_tracks()

        Returns:
            Formatted disassembly text
        """
        output = []
        header = track_data['header']

        # Different header formats for different console types
        if self.console_type == 'snes':
            # SNES format - add song header with title and location
            output.append(f"Song {song.id:02X} - {song.title}:")

            # Add song location info if available (from song_pointers)
            if isinstance(self.format_handler, (SNESFF2, SNESFF3)) and hasattr(self.format_handler, 'song_pointers'):
                if song.id in self.format_handler.song_pointers:  # type: ignore[attr-defined]
                    song_offset, song_length = self.format_handler.song_pointers[song.id]  # type: ignore[attr-defined]
                    # Convert ROM offset to display format
                    addr_str = self.format_handler.rom_offset_to_display_addr(song_offset)
                    output.append(f"  Start {addr_str}  Length {song_length:04X}")

            # Read and display instrument table
            instrument_table = header.get('instrument_table', [])
            if instrument_table:
                inst_str = ' '.join(f"{inst:02X}" for inst in instrument_table)
                output.append(f"  Instruments: {inst_str}")

            # Voice start addresses
            output.append("  Voice start addresses:")
            voice_addrs = []
            for i, ptr in enumerate(header['voice_pointers']):
                if ptr >= 0x100:
                    voice_addrs.append(f"{i}:{ptr:04X}")
            # Print 8 per line
            for i in range(0, len(voice_addrs), 8):
                chunk = voice_addrs[i:i+8]
                output.append(f"    {' '.join(chunk)}")
            output.append("")
        else:
            # PSX AKAO format
            output.append(f"Magic:   {header['magic']}")
            output.append(f"ID:      {header['id']:02X}")
            output.append(f"Length:  {header['length']:04X}")
            output.append(f"Voice mask:  {header['voice_mask']:08X}")
            output.append(f"Voice count: {header['voice_count']}")
            output.append("")

            # List track offsets
            for voice_num in sorted(track_data['tracks'].keys()):
                offset = track_data['tracks'][voice_num]['offset']
                output.append(f"Voice {voice_num:02X} @ {offset:04X}")
            output.append("")

        # Output disassembly for each track
        for voice_num in sorted(track_data['tracks'].keys()):
            # Add voice label for SNES format
            if self.console_type == 'snes':
                output.append(f"    Voice {voice_num} data:")

            # Use pre-parsed disassembly lines
            disasm_lines = track_data['tracks'][voice_num]['disasm_lines']
            output.extend(disasm_lines)
            output.append("")

        return '\n'.join(output)

    def dump_ir_to_text(self, song: SongMetadata, track_data: Dict, loop_analysis: Dict) -> str:
        """Generate IR (Intermediate Representation) dump from pre-parsed track data.

        Args:
            song: Song metadata
            track_data: Output from parse_all_tracks()
            loop_analysis: Output from analyze_song_structure()

        Returns:
            Formatted IR dump text
        """
        output = []
        output.append(f"Song: {song.id:02X} {song.title}")
        output.append(f"Song length: {loop_analysis['song_length']} ticks")
        output.append("")

        # Note names for display
        NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

        for voice_num in sorted(track_data['tracks'].keys()):
            track = track_data['tracks'][voice_num]
            ir_events = track['ir_events']
            loop_info = loop_analysis['tracks'][voice_num]['loop_info']

            output.append(f"=== Voice {voice_num} ===")
            output.append("")

            # Dump IR events
            for idx, event in enumerate(ir_events):
                parts = [f"[{idx:03d}] @0x{event.offset:04X} {event.type.name:15s}"]

                # Add event-specific fields
                if event.type == IREventType.NOTE:
                    note_name = NOTE_NAMES[event.note_num] if event.note_num is not None else '?'
                    parts.append(f"note={event.note_num}({note_name:2s}) dur={event.duration}")
                    # Add metadata
                    if event.metadata:
                        meta_str = ', '.join(f"{k}={v}" for k, v in event.metadata.items())
                        parts.append(f" metadata={{{meta_str}}}")

                elif event.type in (IREventType.REST, IREventType.TIE):
                    parts.append(f"dur={event.duration}")

                elif event.type == IREventType.PATCH_CHANGE:
                    parts.append(f"inst_id=0x{event.inst_id:02X} gm_patch={event.gm_patch} transpose={event.transpose}")

                elif event.type == IREventType.TEMPO:
                    parts.append(f"value={event.value}")

                elif event.type in (IREventType.OCTAVE_SET, IREventType.VOLUME, IREventType.PAN):
                    parts.append(f"value={event.value}")

                elif event.type == IREventType.LOOP_START:
                    parts.append(f"count={event.loop_count}")

                elif event.type in (IREventType.GOTO, IREventType.LOOP_BREAK):
                    parts.append(f"target=0x{event.target_offset:04X}")
                    if event.condition is not None:
                        parts.append(f" cond={event.condition}")

                # Add operands if present
                if event.operands:
                    op_str = ' '.join(f"{b:02X}" for b in event.operands)
                    parts.append(f" operands=[{op_str}]")

                output.append(''.join(parts))

            # Add loop analysis summary
            output.append("")
            output.append("Loop Analysis:")
            output.append(f"  has_backwards_goto: {loop_info.get('has_backwards_goto', False)}")
            if loop_info.get('has_backwards_goto'):
                output.append(f"  goto_target_index: {loop_info.get('goto_target_idx', 'N/A')}")
            output.append(f"  intro_time: {loop_info.get('intro_time', 0)} ticks")
            output.append(f"  loop_time: {loop_info.get('loop_time', 0)} ticks")
            output.append(f"  target_time: {loop_info.get('target_time', 0)} ticks")
            output.append("")

        return '\n'.join(output)

    def generate_midi(self, song: SongMetadata, track_data: Dict, loop_analysis: Dict, output_path: Path):
        """Generate MIDI file from sequence with patch mapping support.

        Args:
            song: Song metadata
            track_data: Output from parse_all_tracks()
            loop_analysis: Output from analyze_song_structure()
            output_path: Path to write MIDI file
        """
        try:
            # Find longest loop time among all tracks with backwards GOTOs
            longest_loop_time = 0
            for voice_num in track_data['tracks'].keys():
                loop_info = loop_analysis['tracks'][voice_num]['loop_info']
                if loop_info.get('has_backwards_goto', False):
                    loop_time = loop_info.get('loop_time', 0)
                    if loop_time > longest_loop_time:
                        longest_loop_time = loop_time

            # Calculate target playthrough time: intro + (2× longest loop)
            target_loop_time = 2 * longest_loop_time

            # Embed loop_info in track_data for Pass 2
            for voice_num in track_data['tracks'].keys():
                track_data['tracks'][voice_num]['loop_info'] = \
                    loop_analysis['tracks'][voice_num]['loop_info']

            # Parse all tracks with loop info (Pass 2)
            parsed_tracks = []
            for voice_num in sorted(track_data['tracks'].keys()):
                try:
                    # Pass 2 - expand IR events into MIDI events
                    midi_events = self.format_handler._parse_track_pass2(  # type: ignore[attr-defined]
                        track_data, voice_num, target_loop_time
                    )
                    has_notes = any(e['type'] == 'note' for e in midi_events)
                    parsed_tracks.append({
                        'voice_num': voice_num,
                        'events': midi_events,
                        'has_notes': has_notes
                    })
                except Exception as e:
                    raise Exception(f"Failed parsing track {voice_num}: {e}") from e

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
            # Use 96 ticks per quarter note to match Perl MIDI module default
            # SNES duration_table values are already in this resolution
            num_tracks = len(tracks_to_write) + 1  # +1 for conductor track
            midi = MIDIFile(num_tracks, file_format=1, ticks_per_quarternote=96)

            # Conductor track
            midi.addTrackName(0, 0, "Conductor")
            # Note: Don't add a default tempo - the sequence will have its own tempo events

            # Add conductor events (tempo changes)
            for event in conductor_events:
                time_beats = event['time'] / 96.0
                # Convert game tempo value to BPM
                # First calculate microseconds per quarter note: uspqn = tempo_factor / tempo_value
                # tempo_factor already includes ticks_per_quarter and resolution (256 for SNES, 65536 for PSX)
                # Then convert to BPM: BPM = 60,000,000 / uspqn = (60,000,000 * tempo_value) / tempo_factor
                uspqn = self.format_handler.tempo_factor / event['tempo']  # type: ignore[attr-defined]
                bpm = 60000000.0 / uspqn
                midi.addTempo(0, time_beats, bpm)

            # Add tracks
            for track_idx, track_info in enumerate(tracks_to_write):
                self._write_midi_track(midi, track_idx + 1, track_info)

            # Disable deinterleaving to avoid "pop from empty list" errors in MIDIUtil
            # See: https://github.com/DataGreed/polyendtracker-midi-export/pull/5
            for track in midi.tracks:
                track.deinterleave = False

            # Debug: Write raw events structure before writing MIDI file
            debug_path = output_path.with_suffix('.events')
            self._write_debug_events(debug_path, tracks_to_write, conductor_events)

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

    def _write_debug_events(self, output_path: Path, tracks_to_write: List[Dict], conductor_events: List[Dict]):
        """Write debug output of raw MIDI events structure to help diagnose issues."""
        import json

        debug_data = {
            'conductor_track': {
                'events': conductor_events
            },
            'tracks': []
        }

        for track_idx, track_info in enumerate(tracks_to_write):
            track_data = {
                'track_num': track_idx + 1,
                'is_patch_based': track_info.get('is_patch_based', False),
            }

            if track_info.get('is_patch_based'):
                patch_num = track_info['patch']
                patch_info = self.patch_mapper.get_patch_info(patch_num)
                instrument_name = self.patch_mapper.get_instrument_name(patch_num)

                track_data['patch'] = patch_num
                track_data['instrument_name'] = instrument_name
                track_data['gm_patch'] = patch_info.gm_patch
                track_data['is_percussion'] = patch_info.is_percussion()
                track_data['transpose'] = patch_info.transpose

                # Calculate channel assignment
                if patch_info.is_percussion():
                    channel = 9
                else:
                    channel = (track_idx + 1) - 1  # track_num - 1
                    if channel >= 9:
                        channel += 1
                track_data['channel'] = channel
            else:
                track_data['voice_num'] = track_info['voice_num']
                channel = track_info['voice_num']
                if channel >= 9:
                    channel += 1
                track_data['channel'] = channel % 16

            # Add events with converted time values
            track_data['events'] = []
            for event in track_info['events']:
                event_copy = dict(event)
                # Add time in beats for reference
                event_copy['time_beats'] = event['time'] / 96.0
                if event['type'] == 'note':
                    event_copy['duration_beats'] = event['duration'] / 96.0
                track_data['events'].append(event_copy)

            debug_data['tracks'].append(track_data)

        # Write to file
        with open(output_path, 'w') as f:
            json.dump(debug_data, f, indent=2)

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
                time_beats = event['time'] / 96.0
                duration_beats = event['duration'] / 96.0

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
                        duration_beats = max_duration / 96.0

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

            # Default channel mapping (may be overridden by individual notes for percussion)
            default_channel = voice_num
            if default_channel >= 9:
                default_channel += 1
            default_channel = default_channel % 16

            # Track current patch for this voice
            current_patch = 0

            for event in track_info['events']:
                time_beats = event['time'] / 96.0

                if event['type'] == 'program_change':
                    # Handle program change
                    # For SNES FF2, the patch is already the GM patch number
                    # For PSX/generic, use the patch mapper
                    if isinstance(self.format_handler, SNESFF2):
                        # SNES FF2: patch is already GM patch from parse_track
                        gm_patch = event['patch']
                        midi.addProgramChange(track_num, default_channel, time_beats, gm_patch)
                    else:
                        # PSX/generic: use patch mapper
                        current_patch = event['patch']
                        patch_info = self.patch_mapper.get_patch_info(current_patch)
                        if not patch_info.is_percussion():
                            midi.addProgramChange(track_num, default_channel, time_beats, patch_info.gm_patch)

                elif event['type'] == 'note':
                    duration_beats = event['duration'] / 96.0
                    if duration_beats > 0:
                        # Use channel from event if present (for percussion mode), otherwise use default
                        channel = event.get('channel', default_channel)

                        # For SNES FF2, transposition is already applied in parse_track
                        # For PSX/generic, apply transposition from patch mapper
                        if isinstance(self.format_handler, SNESFF2):
                            note = event['note']
                        else:
                            # Get patch info for this note
                            note_patch = event.get('patch', current_patch)
                            patch_info = self.patch_mapper.get_patch_info(note_patch)
                            note = event['note'] + patch_info.transpose

                        note = max(0, min(127, note))

                        midi.addNote(track_num, channel, note,
                                   time_beats, duration_beats, event['velocity'])

                # Tempo events are now handled on conductor track, not here

    def generate_musicxml(self, song: SongMetadata, track_data: Dict, loop_analysis: Dict, output_path: Path):
        """Generate MusicXML from sequence data.

        Args:
            song: Song metadata
            track_data: Output from parse_all_tracks()
            loop_analysis: Output from analyze_song_structure()
            output_path: Path to write MusicXML file
        """
        # Calculate target loop time (same as MIDI generation)
        longest_loop_time = 0
        for voice_num in track_data['tracks'].keys():
            loop_info = loop_analysis['tracks'][voice_num]['loop_info']
            if loop_info.get('has_backwards_goto', False):
                loop_time = loop_info.get('loop_time', 0)
                if loop_time > longest_loop_time:
                    longest_loop_time = loop_time

        target_loop_time = 2 * longest_loop_time

        # Embed loop_info in track_data for Pass 2
        for voice_num in track_data['tracks'].keys():
            track_data['tracks'][voice_num]['loop_info'] = \
                loop_analysis['tracks'][voice_num]['loop_info']

        # Parse all tracks (Pass 2)
        parsed_tracks = []
        for voice_num in sorted(track_data['tracks'].keys()):
            # Pass 2 - expand IR events into MIDI events
            midi_events = self.format_handler._parse_track_pass2(  # type: ignore[attr-defined]
                track_data, voice_num, target_loop_time
            )
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

    def extract_all(self, song_id_filter=None):
        """Extract all songs defined in config.

        Args:
            song_id_filter: If specified, only extract this song ID
        """
        songs = [SongMetadata(**s) for s in self.config.get('songs', [])]

        # Apply song ID filter if specified
        if song_id_filter is not None:
            songs = [s for s in songs if s.id == song_id_filter]
            if not songs:
                print(f"WARNING: Song ID {song_id_filter:02X} not found in config")
                return

        # Create output directories
        text_dir = Path('txt')
        midi_dir = Path('mid')
        xml_dir = Path('xml')
        text_dir.mkdir(exist_ok=True)
        midi_dir.mkdir(exist_ok=True)
        xml_dir.mkdir(exist_ok=True)
        
        for song in songs:
            # Use title if provided, otherwise fall back to AKAO ID
            if hasattr(song, 'title') and song.title:
                # Always prepend ID for consistency
                filename = f"{song.id:02X} {song.title}"
            else:
                # No title, use AKAO ID
                filename = f"AKAO_{song.id:02X}"

            # Sanitize filename for Windows/cross-platform compatibility
            # Replace characters that are invalid in Windows filenames
            invalid_chars = '<>:"/\\|?*'
            for char in invalid_chars:
                filename = filename.replace(char, '_')

            print(f"Processing: {filename}")

            try:
                # Extract data
                data = self.extract_sequence_data(song)

                # Parse all tracks once (Pass 1)
                track_data = self.parse_all_tracks(song, data)

                # Analyze loop structure
                loop_analysis = self.analyze_song_structure(track_data)

                # Generate text disassembly
                text_output = self.disassemble_to_text(song, track_data)
                text_file = text_dir / f"{filename}.txt"
                text_file.write_text(text_output)

                # Generate IR dump
                ir_output = self.dump_ir_to_text(song, track_data, loop_analysis)
                ir_file = text_dir / f"{filename}.ir"
                ir_file.write_text(ir_output)

                # Generate MIDI
                midi_file = midi_dir / f"{filename}.mid"
                self.generate_midi(song, track_data, loop_analysis, midi_file)

                # Generate MusicXML
                xml_file = xml_dir / f"{filename}.musicxml"
                self.generate_musicxml(song, track_data, loop_analysis, xml_file)

                print(f"  OK: Generated {text_file.name}, {ir_file.name}, {midi_file.name}, and {xml_file.name}")

                # Check if song has alternate voice pointers (FF3 feature)
                if track_data['header'].get('has_alternate_pointers', False):
                    # Generate alternate filename (insert "alt" after song ID)
                    if hasattr(song, 'title') and song.title:
                        alt_filename = f"{song.id:02X}alt {song.title}"
                    else:
                        alt_filename = f"AKAO_{song.id:02X}alt"

                    # Sanitize filename (same as standard version)
                    for char in invalid_chars:
                        alt_filename = alt_filename.replace(char, '_')

                    print(f"  Processing alternate version: {alt_filename}")

                    # Re-parse with alternate pointers
                    alt_track_data = self.parse_all_tracks(song, data, use_alternate_pointers=True)
                    alt_loop_analysis = self.analyze_song_structure(alt_track_data)

                    # Generate all outputs with "alt" filename
                    alt_text = self.disassemble_to_text(song, alt_track_data)
                    (text_dir / f"{alt_filename}.txt").write_text(alt_text)

                    alt_ir = self.dump_ir_to_text(song, alt_track_data, alt_loop_analysis)
                    (text_dir / f"{alt_filename}.ir").write_text(alt_ir)

                    self.generate_midi(song, alt_track_data, alt_loop_analysis, midi_dir / f"{alt_filename}.mid")
                    self.generate_musicxml(song, alt_track_data, alt_loop_analysis, xml_dir / f"{alt_filename}.musicxml")

                    print(f"  OK: Generated alternate files for {alt_filename}")

            except Exception as e:
                print(f"  ERROR: {e}")
        
        # Close the ISO and wrapper when done (SNES ROMs don't use ISO)
        if self.iso:
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
    song_id_filter = None
    args = []

    i = 0
    while i < len(sys.argv[1:]):
        arg = sys.argv[1 + i]
        if arg == '--patch-based-tracks':
            patch_based_tracks = True
        elif arg == '--song' and i + 1 < len(sys.argv[1:]):
            # Next arg is the song ID
            song_id_filter = int(sys.argv[1 + i + 1], 0)  # Support hex with 0x prefix
            i += 1  # Skip next arg
        else:
            args.append(arg)
        i += 1

    if len(args) < 2:
        print("Usage: python extract_akao.py <config.yaml> <source_file> [options]")
        print()
        print("Arguments:")
        print("  config.yaml             - Game metadata configuration file")
        print("  source_file             - ISO/ROM file containing the game data")
        print()
        print("Options:")
        print("  --patch-based-tracks    - Organize MIDI tracks by instrument/patch instead of sequence")
        print("  --song <id>             - Extract only the specified song ID (decimal or hex with 0x)")
        print()
        print("Examples:")
        print("  python extract_akao.py ff9.yaml ff9.iso")
        print("  python extract_akao.py ff8.yaml ff8.iso --patch-based-tracks")
        sys.exit(1)

    config_file = args[0]
    source_file = args[1]

    try:
        extractor = SequenceExtractor(config_file, source_file, patch_based_tracks=patch_based_tracks)
        extractor.extract_all(song_id_filter=song_id_filter)
    except Exception as e:
        print(f"\nError: {e}")
        print("\nFull traceback:")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()