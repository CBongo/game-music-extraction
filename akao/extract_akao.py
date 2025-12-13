#!/usr/bin/env python3
"""
VGM Sequence Extractor
Extracts music sequences from game ROMs/ISOs to text disassembly and MIDI files.
Chris Bongaarts - 2025-11-02 with help from Claude
"""

import sys
import struct
import traceback
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
    make_note, make_rest, make_tie, make_tempo, make_tempo_fade,
    make_patch_change,
    make_octave_set, make_octave_inc, make_octave_dec,
    make_volume, make_volume_fade, make_pan, make_pan_fade,
    make_loop_start, make_loop_end, make_loop_mark, make_loop_break, make_goto, make_halt,
    make_vibrato_on, make_vibrato_off, make_tremolo_on, make_tremolo_off,
    make_portamento_on, make_portamento_off,
    make_slur_on, make_slur_off, make_roll_on, make_roll_off,
    make_percussion_mode_on, make_percussion_mode_off,
    make_staccato, make_utility_duration,
    make_master_volume, make_volume_multiplier
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
    
    # Note: parse_track() is DEPRECATED - removed from abstract interface
    # All format handlers now use two-pass architecture:
    #   Pass 1: _parse_track_pass1() returns (disasm, ir_events)
    #   Pass 2: _parse_track_pass2() returns midi_events from IR

    @abstractmethod
    def get_track_offsets(self, data: bytes, header: Dict) -> List[int]:
        """Get list of track data offsets from header info."""
        pass

    @abstractmethod
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


class SNESUnified(SequenceFormat):
    """Unified SNES music format handler - config-driven for all SNES AKAO games."""

    # OPCODE_NAMES will be built from config in __init__

    def __init__(self, config: Dict, rom_data: bytes):
        """Initialize with game-specific config and ROM data."""
        self.config = config
        self.rom_data: bytes = rom_data
        self.has_smc_header = len(rom_data) % 1024 == 512  # SMC header is 512 bytes
        self.smc_header_size = 512 if self.has_smc_header else 0

        # PHASE 1: Config-driven parameters (new!)
        self.spc_load_address = config.get('spc_load_address', 0x2000)
        self.note_divisor = config.get('note_divisor', 15)
        self.first_opcode = config.get('first_opcode', 0xD2)
        self.tie_note_value = config.get('tie_note_value', 12)
        self.rest_note_value = config.get('rest_note_value', 13)  

        # Auto-detect ROM mapping mode (LoROM vs HiROM)
        self.rom_mapping = self._detect_rom_mapping()

        # Read duration table from ROM
        if 'duration_table' in config:
            table_cfg = config['duration_table']
            self.duration_table = self._read_rom_table(
                table_cfg.get('address'),
                table_cfg.get('size', self.note_divisor),
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
            # Default opcode lengths (46 opcodes starting from first_opcode)
            self.opcode_table = [1] * 46

        # Get state defaults from config
        self.default_tempo = config.get('default_tempo', 255)
        self.default_octave = config.get('default_octave', 4)
        self.default_velocity = config.get('default_velocity', 64)

        # Calculate tempo_factor from component values
        # tempo_factor = timer_period_us * timer_count * tempo_resolution
        timer_period_us = config.get('timer_period_us', 4500)
        timer_count = config.get('timer_count', 48)
        tempo_resolution = config.get('tempo_resolution', 256)
        self.tempo_factor = timer_period_us * timer_count * tempo_resolution

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

        # Build patch_map_low for FF3 dual_map handler (optional)
        self.patch_map_low = {}
        if 'patch_map_low' in config:
            for inst_id_str, patch_info in config['patch_map_low'].items():
                if isinstance(inst_id_str, str):
                    inst_id = int(inst_id_str, 0)
                else:
                    inst_id = inst_id_str
                self.patch_map_low[inst_id] = patch_info

        # Get base address for song pointers
        self.base_address = config.get('base_address', 0x04C000)

        # PHASE 2: Build opcode dispatch table from config
        self.opcode_dispatch = self._build_opcode_dispatch()

        # Calculate base_offset once for reuse
        base_offset = self._snes_addr_to_offset(self.base_address)

        # Set instrument table offset
        # FF3/SoM-style: offset from base_address (configured in YAML)
        # FF2-style: read from master pointer table (handled in _read_song_pointer_table)
        inst_table_offset_config = config.get('instrument_table_offset')
        if inst_table_offset_config is not None:
            # FF3/SoM style: base + offset
            self.instrument_table_offset = base_offset + inst_table_offset_config

        # Percussion table configuration (CT/FF3-specific)
        # Apply same base_offset adjustment as instrument_table_offset
        perc_table_offset_config = config.get('percussion_table_offset')
        if perc_table_offset_config is not None:
            self.percussion_table_offset = base_offset + perc_table_offset_config
            self.percussion_table_stride = config.get('percussion_table_stride', 0x24)
        else:
            self.percussion_table_offset = None
            self.percussion_table_stride = 0x24

        # Instrument mapping configuration (game-wide)
        self.instrument_mapping = config.get('instrument_mapping')
        if self.instrument_mapping is None:
            self.instrument_mapping = {'type': 'direct', 'param': 0}

        # Read song pointer table (may set instrument_table_offset for FF2 style)
        self.song_pointers = self._read_song_pointer_table()

        # Build opcode names for disassembly from config
        self.OPCODE_NAMES = self._build_opcode_names()

    def _build_opcode_names(self) -> Dict[int, str]:
        """Build opcode name dictionary from YAML config for disassembly.

        Returns:
            Dict mapping opcode byte -> human-readable name
        """
        # Semantic to display name mapping
        semantic_names = {
            'tempo': 'Tempo',
            'tempo_fade': 'Tempo Fade',
            'volume': 'Voice Volume',
            'volume_fade': 'Volume Fade',
            'pan': 'Balance/Pan',
            'pan_fade': 'Pan Fade',
            'octave_set': 'Set Octave',
            'octave_inc': 'Inc Octave',
            'octave_dec': 'Dec Octave',
            'transpose_set': 'Set Transpose',
            'transpose_change': 'Change Transpose',
            'detune': 'Detune',
            'patch_change': 'Patch Change',
            'adsr_attack': 'ADSR Attack',
            'adsr_decay': 'ADSR Decay',
            'adsr_sustain': 'ADSR Sustain',
            'adsr_release': 'ADSR Release',
            'adsr_default': 'Default ADSR',
            'loop_start': 'Begin Repeat',
            'loop_end': 'End Repeat',
            'loop_break': 'Selective Repeat',
            'goto': 'Goto',
            'halt': 'Halt',
            'vibrato_on': 'Vibrato',
            'vibrato_off': 'Vibrato Off',
            'tremolo_on': 'Tremolo',
            'tremolo_off': 'Tremolo Off',
            'portamento_on': 'Portamento',
            'portamento_off': 'Portamento Off',
            'pan_sweep': 'Pan Sweep',
            'echo_on': 'Enable Echo',
            'echo_off': 'Disable Echo',
            'echo_volume': 'Echo Volume',
            'echo_volume_fade': 'Echo Volume Fade',
            'echo_feedback': 'Echo Feedback',
            'echo_feedback_fade': 'Echo Feedback Fade',
            'filter_fade': 'Filter Fade',
            'volume_multiplier': 'Volume Multiplier',
            'ignore_volume_mult': 'Ignore Vol Mult',
            'master_volume': 'Master Volume',
            'master_volume_ignore': 'Ignore Master Volume',
            'reset_crpt': 'Reset Loop Counter',
            'noise_on': 'Enable Noise',
            'noise_off': 'Disable Noise',
            'pitchmod_on': 'Enable Pitchmod',
            'pitchmod_off': 'Disable Pitchmod',
            'advance_cue': 'Advance Cue',
            'zero_cue': 'Zero Cue',
            'branch_if_dd': 'Branch If $DD',
            'noise_clock': 'Set Noise Clock',
            'slur_on': 'Begin Slur',
            'slur_off': 'End Slur',
            'roll_on': 'Begin Roll',
            'roll_off': 'End Roll',
            'utility_rest': 'Utility Rest',
            'play_sfx': 'Play SFX',
            'nop': 'NOP',
            'echo_settings': 'Echo Settings',
            'pan_sweep_on': 'Pan Sweep On',
            'pan_sweep_off': 'Pan Sweep Off',
            'adsr_set': 'Set Envelope',
            'gain_set': 'Set Gain',
            'staccato_set': 'Set Staccato',
            'goto_indexed': 'Goto Indexed',
        }

        names = {}
        opcodes_config = self.config.get('opcodes', {})

        for opcode_key, op_config in opcodes_config.items():
            # Convert opcode key to int
            if isinstance(opcode_key, str):
                opcode = int(opcode_key, 0)
            else:
                opcode = opcode_key

            semantic = op_config.get('semantic', 'unknown')
            # Use semantic mapping, or capitalize semantic as fallback
            names[opcode] = semantic_names.get(semantic, semantic.replace('_', ' ').title())

        return names

    def _build_opcode_dispatch(self) -> Dict[int, Dict]:
        """Build opcode dispatch table from YAML config.

        Returns:
            Dict mapping opcode byte -> dispatch config with keys:
                'semantic': str - what this opcode does (e.g., 'tempo', 'octave_set')
                'params': int - number of operand bytes
                'value_param': int - which operand contains the value (for simple ops)
                'handler': str - special handler name if needed
        """
        dispatch = {}

        opcodes_config = self.config.get('opcodes', {})

        for opcode_key, op_config in opcodes_config.items():
            # Convert opcode key to int (handles 0xD2, "0xD2", 210, etc.)
            if isinstance(opcode_key, str):
                opcode = int(opcode_key, 0)  # base 0 auto-detects hex
            else:
                opcode = opcode_key

            dispatch[opcode] = {
                'semantic': op_config.get('semantic', 'unknown'),
                'params': op_config.get('params', 0),
                'value_param': op_config.get('value_param', 0),
                'handler': op_config.get('handler', None)
            }

        return dispatch

    # Copy all methods from SNESFF2 with modifications for config-driven parameters
    # These are inherited from the base SequenceFormat or copied identically:
    # Methods copied from SNESFF2:

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


    def _read_song_instrument_table(self, song_id: int) -> List[int]:
        """Read the instrument table for a specific song.

        Returns a list of instrument IDs (indexes into the patch_map).
        In Perl: my (@inst) = map { $_ == 0 ? () : ($_) }
                   &read2ptrs($ptr{songinst} + $i * 0x20, 0x10);

        Note: For SD3-style games with song-embedded instrument tables,
        this returns an empty list since the table is parsed during song loading.
        """
        # SD3-style: Instrument table is embedded in song data, not in a separate location
        if not hasattr(self, 'instrument_table_offset') or self.instrument_table_offset is None:
            return []

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


    def _read_song_percussion_table(self, song_id: int) -> List[Dict]:
        """Read the percussion table for a specific song (CT/FF3-specific).

        Returns a list of 12 percussion entries, each with:
            - instrument_id: Instrument ID (applies patch mapping)
            - note: Note value (may override mapped note)
            - volume: Volume for this percussion note

        In Perl (ctmus.pl lines 194-211):
            my (@perc) = map { [$_ >> 16, ($_ >> 8) & 0xff, $_ & 0xff] }
                        &readptrs($ptr{percmap} + $i * 0x24, 0xc);
        """
        if self.percussion_table_offset is None:
            return []

        # Each song has 12 (0xC) 3-byte percussion entries at percussion_table_offset + song_id * stride
        offset = self.percussion_table_offset + song_id * self.percussion_table_stride
        percussion_entries = []

        for i in range(0xC):  # 12 percussion notes
            entry_offset = offset + i * 3
            entry_bytes = self.rom_data[entry_offset:entry_offset + 3]
            if len(entry_bytes) < 3:
                break

            # Perl code: map { [$_ >> 16, ($_ >> 8) & 0xff, $_ & 0xff] } &readptrs(...)
            # readptrs reads 3-byte little-endian values
            # Bytes in ROM: [instrument_id, note, volume]
            # When read as 24-bit LE and shifted: [volume, note, instrument_id]
            # But the result array is [instrument_id, note, volume] so the Perl is returning
            # the values in the order they appear in ROM
            instrument_id = entry_bytes[0]  # Byte 0 = instrument_id
            note = entry_bytes[1]           # Byte 1 = note
            volume = entry_bytes[2]         # Byte 2 = volume

            percussion_entries.append({
                'instrument_id': instrument_id,
                'note': note,
                'volume': volume
            })

        return percussion_entries


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
            if event.type == IREventType.GOTO and event.target_offset is not None:
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
            'goto_target_idx': target_idx,
            'target_time': intro_time + 2 * loop_time  # Play intro + 2 loops
        }

    def _parse_track_pass2(self, all_track_data: Dict, start_voice_num: int,
                          target_loop_time: int = 0) -> List[Dict]:
        """Pass 2: Expand IR events with loop execution to generate MIDI events.

        This pass takes all track data and executes from a starting voice, expanding loops,
        following GOTOs (including cross-track), and generating timed MIDI events.

        Args:
            all_track_data: Complete track data from parse_all_tracks()
            start_voice_num: Starting track/voice number (0-7)
            target_loop_time: Target absolute time for all tracks (0 = no looping)

        Returns:
            List of MIDI event dictionaries
        """
        import time
        start_time = time.time()
        max_time_seconds = 2.0  # Emergency brake: 2 second time limit
        max_midi_events = 50000  # Emergency brake: max MIDI events per track

        # Gate timing (native ticks before full duration when note-off happens)
        gate_time = 2  # Default: 2 native ticks from end
                       # Can be modified by slur/legato opcodes

        # Tick scaling factor (native ticks -> MIDI ticks)
        tick_scale = 2  # 48 native ticks/quarter -> 96 MIDI ticks/quarter


        # Scale target_loop_time from native ticks to MIDI ticks
        # (loop analyzer works in native ticks, Pass 2 works in MIDI ticks)
        target_loop_time_midi = target_loop_time * tick_scale if target_loop_time > 0 else 0

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
        slur_enabled = False  # Track slur state
        roll_enabled = False  # Track roll state
        staccato_percentage = 100  # Track staccato multiplier (100 = normal)
        utility_duration_override = None  # One-shot duration override for next note
        master_volume = 256  # Master volume multiplier (SoM) - 256 = normal (100%)
        volume_multiplier = 0  # Volume multiplier (CT/FF3) - 0 = normal

        # Loop execution state
        loop_stack = []  # Stack of {start_idx, count, iteration}

        # Use global target time directly (no per-track calculation needed)

        # Event pointer for execution
        i = 0
        # Most game music loops infinitely. Allow enough iterations for ~2 full playthroughs
        # Typical: 100-200 IR events, with loops expanding to 10,000-20,000 iterations
        # However, deeply nested loops (e.g., FF3 song 3E with 4 nested count=6 loops) can
        # expand to 40,000+ iterations even for non-looping (no backwards GOTO) tracks
        # - Multiplier of 1000 per event handles deeply nested loops
        # - Minimum of 50000 ensures adequate headroom
        max_iterations = max(len(ir_events) * 1000, 50000)
        iteration_count = 0

        while i < len(ir_events) and iteration_count < max_iterations:
            iteration_count += 1

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

                # Apply utility duration override (one-shot for this note only)
                native_duration = utility_duration_override if utility_duration_override is not None else event.duration
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

                # Add MIDI note event
                midi_events.append({
                    'type': 'note',
                    'time': total_time,
                    'duration': gate_adjusted_dur,  # Gate-adjusted MIDI duration
                    'note': midi_note,
                    'velocity': velocity,
                    'channel': midi_channel
                })

                total_time += midi_dur  # Advance by FULL MIDI duration
                i += 1

            elif event.type == IREventType.REST:
                # Rest just advances time
                # Scale native duration to MIDI ticks
                assert event.duration is not None, "REST event must have duration"
                midi_dur = event.duration * tick_scale
                total_time += midi_dur
                i += 1

            elif event.type == IREventType.TIE:
                # Tie extends the last note
                # Scale native duration to MIDI ticks
                assert event.duration is not None, "TIE event must have duration"
                tie_dur = event.duration * tick_scale
                for j in range(len(midi_events) - 1, -1, -1):
                    if midi_events[j]['type'] == 'note':
                        midi_events[j]['duration'] += tie_dur
                        break
                total_time += tie_dur
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

                    # target_loop_time is global - no need to recalculate

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

    def _read_song_pointer_table(self) -> Dict[int, Tuple[int, int]]:
        """Read song pointer table - supports both FF2 and FF3 styles via config."""
        ptr_table_config = self.config.get('song_pointer_table')

        if ptr_table_config:
            # FF3/SoM style: Direct song pointers at base + offset
            base_offset = self._snes_addr_to_offset(self.base_address)
            table_offset = ptr_table_config.get('offset', 0x3E96)
            song_count = ptr_table_config.get('count', 85)
            song_pointers = {}
            raw_song_pointers = self._read_3byte_pointers(base_offset + table_offset, song_count)

            for i in range(song_count):
                if ptr_table_config.get('style') == 'direct':
                    rom_offset = self._snes_addr_to_offset(raw_song_pointers[i])

                elif ptr_table_config.get('style') == 'offsets':
                    # FF2 style: song offsets from base table method
                    rom_offset = base_offset + raw_song_pointers[i]
                
                else:
                    raise ValueError(f"Unknown song pointer table style: {ptr_table_config.get('style')}")

                # Read actual length from 2-byte size field at start of song data
                if rom_offset + 2 <= len(self.rom_data):
                    length = struct.unpack('<H', self.rom_data[rom_offset:rom_offset + 2])[0]
                    song_pointers[i] = (rom_offset, length)

            return song_pointers
 
        else:
            raise ValueError("Song pointer table configuration missing in YAML")

    def _snes_addr_to_offset(self, snes_addr: int) -> int:
        """Convert SNES address (bank/addr format) to ROM file offset.

        Handles both LoROM and HiROM mapping modes.
        """
        bank = (snes_addr >> 16) & 0xFF
        addr = snes_addr & 0xFFFF

        if self.rom_mapping == "hirom":
            rom_offset = ((bank - 0xC0) * 0x10000) + addr
        else:
            # LoROM
            rom_offset = (bank * 0x8000) + (addr - 0x8000)

        return rom_offset + self.smc_header_size

    def _read_rom_table(self, address: int, size: int, data_type: str) -> List[int]:
        """Read a table from the ROM using instance method for address conversion."""
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
        """Parse song header - supports both FF2 and FF3/SoM styles via config.

        FF2 style: Simple voice pointers at fixed offsets
        FF3/SoM style: vaddroffset calculation + dual voice pointer sets
        """
        vaddroffset_config = self.config.get('vaddroffset_formula')

        if vaddroffset_config:
            # FF3/SoM style: Calculate vaddroffset
            if len(data) < 36:
                return {'track_offsets': [], 'instrument_table': [], 'vaddroffset': 0}

            # Read vaddroffset_raw and emptyvoice
            vaddroffset_raw = struct.unpack('<H', data[0:2])[0]
            emptyvoice_offset = vaddroffset_config.get('emptyvoice_offset', 2)
            emptyvoice = struct.unpack('<H', data[emptyvoice_offset:emptyvoice_offset+2])[0]

            # Apply vaddroffset formula from config
            formula_type = vaddroffset_config.get('type')
            if formula_type == 'subtract_constant':
                constant = vaddroffset_config.get('constant', 0x11C24)
                vaddroffset = (constant - vaddroffset_raw) & 0xFFFF
            else:
                vaddroffset = 0

            # Read both standard and alternate voice pointers
            vptr_offset = self.config.get('vptr_offset', 4)
            active_vstart = struct.unpack('<8H', data[vptr_offset:vptr_offset+0x10])

            if self.config.get('has_alternate_voice_pointers', False) and use_alternate_pointers:
                active_vstart = struct.unpack('<8H', data[vptr_offset+0x10:vptr_offset+0x20])

            # Process active voice pointers
            track_offsets = []
            adjusted_voice_pointers = []
            for ptr in active_vstart:
                if ptr != emptyvoice:
                    adjusted_ptr = (ptr + vaddroffset) & 0xFFFF
                    adjusted_voice_pointers.append(adjusted_ptr)
                    # Convert SPC address to buffer offset
                    buffer_offset = adjusted_ptr - self.spc_load_address
                    if buffer_offset >= 0 and buffer_offset < len(data):
                        track_offsets.append(buffer_offset)
                else:
                    adjusted_voice_pointers.append(0)

            # Read instrument table
            instrument_table = self._read_song_instrument_table(song_id)

            # Read percussion table (CT/FF3-specific)
            percussion_table = self._read_song_percussion_table(song_id)

            return {
                'track_offsets': track_offsets,
                'voice_pointers': adjusted_voice_pointers,
                'instrument_table': instrument_table,
                'percussion_table': percussion_table,
                'vaddroffset': vaddroffset
            }
        else:
            # FF2 style: Simple header parsing
            if len(data) < 16:
                raise ValueError(f"Song data too short: {len(data)} bytes (need at least 16 for voice pointers)")

            # Read voice pointers (now at offset 0 since size bytes were removed)
            voice_pointers = struct.unpack('<8H', data[0:16])

            # A pointer < 0x100 indicates unused voice (matches Perl script threshold)
            return {
                'voice_pointers': voice_pointers,
                'num_voices': sum(1 for ptr in voice_pointers if ptr >= 0x100)
            }

    def buffer_offset_to_spc_addr(self, offset: int) -> int:
        """Convert buffer offset to SPC RAM address.

        The song data buffer contains only music data (size bytes already removed).
        SPC loads data at configurable address (default 0x2000).

        Args:
            offset: Offset within the song data buffer

        Returns:
            SPC RAM address
        """
        return offset + self.spc_load_address

    def get_track_offsets(self, data: bytes, header: Dict) -> List[int]:
        """Get list of voice offsets within song data.

        Voice pointers are SPC-700 RAM addresses where the song data is loaded.
        Pointers < 0x100 indicate unused voices (matches Perl script threshold).
        The data buffer no longer includes size bytes.
        """
        offsets = []
        for ptr in header['voice_pointers']:
            if ptr >= 0x100:  # Valid voice pointer (matches Perl: next if $vstart[$v] < 0x100)
                # Convert SPC RAM address to offset in our data buffer
                buffer_offset = ptr - self.spc_load_address
                offsets.append(buffer_offset)
        return offsets

    def _resolve_patch(self, inst_id: int, handler: Optional[Dict] = None,
                      instrument_table: Optional[List[int]] = None) -> tuple[int, int, int]:
        """Resolve an instrument ID to GM patch, transpose, and percussion key.

        This centralizes the patch mapping logic used by both patch_change and percussion mode.

        Args:
            inst_id: Raw instrument ID (from operand or percussion table)
            handler: Optional handler config (for patch_change opcodes with inst_table lookup)
            instrument_table: Instrument table for inst_table lookups

        Returns:
            Tuple of (gm_patch, transpose_octaves, perc_key)
            - gm_patch: General MIDI patch number (negative = percussion GM key)
            - transpose_octaves: Octave transpose for this instrument
            - perc_key: Percussion key (0 if not percussion, abs(gm_patch) if percussion)
        """
        actual_inst_id = inst_id
        patch_map_to_use = self.patch_map

        # If handler is provided, resolve inst_id through instrument_table
        if handler and instrument_table:
            handler_type = handler.get('type', 'inst_table')
            handler_param = handler.get('param', 0)

            if handler_type == "inst_table":
                # FF2/SoM style: inst_id is index into inst_table
                patch_index = inst_id - handler_param
                if 0 <= patch_index < len(instrument_table):
                    actual_inst_id = instrument_table[patch_index]
                else:
                    actual_inst_id = 0
            elif handler_type == "dual_map":
                # FF3/CT style: inst_id >= 0x20 uses high map, < 0x20 uses low map
                if inst_id >= handler_param:
                    patch_index = inst_id - handler_param
                    if 0 <= patch_index < len(instrument_table):
                        actual_inst_id = instrument_table[patch_index]
                    else:
                        actual_inst_id = 0
                else:
                    # Use low patch map directly (inst_id stays the same)
                    actual_inst_id = inst_id
                    if self.patch_map_low:
                        patch_map_to_use = self.patch_map_low

        # Note: When called without handler (e.g., percussion mode), always use patch_map
        # patch_map_low is only for opcodes with dual_map handler

        # Look up in patch map
        if actual_inst_id in patch_map_to_use:
            patch_info = patch_map_to_use[actual_inst_id]
            gm_patch = patch_info['gm_patch']
            transpose_octaves = patch_info.get('transpose', 0)

            # Check if this is percussion (negative GM patch number)
            if gm_patch < 0:
                perc_key = abs(gm_patch)
            else:
                perc_key = 0

            return (gm_patch, transpose_octaves, perc_key)
        else:
            # Instrument not found in patch map
            return (0, 0, 0)

    def _parse_track_pass1(self, data: bytes, offset: int, track_num: int,
                          instrument_table: List[int], vaddroffset: int = 0,
                          track_boundaries: Optional[Dict[int, Tuple[int, int]]] = None,
                          percussion_table: Optional[List[Dict]] = None) -> Tuple[List, List[IREvent]]:
        """Pass 1: Linear parse to build disassembly and intermediate representation.

        This pass does NOT execute loops or generate MIDI events. It simply parses
        the opcode stream linearly, building IR events that represent the sequence
        structure. Disassembly is generated with loop awareness to avoid duplicates.

        Args:
            percussion_table: List of percussion entries for CT/FF3 (12 entries with instrument_id, note, volume)

        Returns:
            Tuple of (disassembly_lines, ir_events)
        """
        disasm = []
        ir_events = []

        # Track state for disassembly and IR building
        octave = self.default_octave
        velocity = self.default_velocity
        tempo = self.default_tempo
        perc_key = 0
        percussion_mode = False  # Track percussion mode state
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

            # Convert buffer offset to SPC RAM address for display (config-driven)
            spc_addr = p + self.spc_load_address
            line = f"      {spc_addr:04X}: {cmd:02X} "

            # Check if it's a note (< first_opcode, config-driven)
            if cmd < self.first_opcode:
                # Note encoding: check if inverted (SD3-style) or normal
                note_encoding = self.config.get('note_encoding', 'normal')
                if note_encoding == 'inverted':
                    # SD3-style: duration = cmd / divisor, note = cmd % divisor
                    dur_idx = cmd // self.note_divisor
                    note_num = cmd % self.note_divisor
                else:
                    # Normal: note = cmd / divisor, duration = cmd % divisor
                    note_num = cmd // self.note_divisor
                    dur_idx = cmd % self.note_divisor

                # Handle utility duration (SD3-style: dur_idx == 13 means read next byte)
                # Check if this is configured (SD3 uses dur_idx == 13 for utility duration)
                utility_dur_idx = self.note_divisor - 1  # For divisor 14, utility is index 13
                if dur_idx == utility_dur_idx and p + 1 < len(data):
                    # Read next byte as raw duration value
                    p += 1
                    cmd2 = data[p]
                    dur = cmd2  # Raw duration from next byte
                    line = f"      {spc_addr:04X}: {cmd:02X} {cmd2:02X}"
                else:
                    # Store native duration (no scaling)
                    # Scaling will be applied in Pass 2
                    dur = self.duration_table[dur_idx]

                # Apply game-specific duration formula if configured
                duration_formula = self.config.get('duration_formula')
                if duration_formula == 'sd3':
                    # SD3 formula: dur = dur + 1 (shift is tick scaling, happens in Pass 2)
                    dur = dur + 1

                if note_num < 12:
                    # Check if percussion mode is active
                    if percussion_mode and percussion_table and note_num < len(percussion_table):
                        # Percussion mode: lookup in percussion table
                        perc_entry = percussion_table[note_num]
                        perc_inst_id = perc_entry['instrument_id']
                        perc_note = perc_entry['note']
                        perc_vol = perc_entry['volume']

                        # Resolve patch mapping for percussion instrument
                        # Use game-wide instrument mapping
                        gm_patch, perc_transpose, resolved_perc_key = self._resolve_patch(
                            perc_inst_id, self.instrument_mapping, instrument_table
                        )

                        # Override note if percussion table specifies it (non-zero)
                        if perc_note != 0:
                            # Percussion table provides explicit note value
                            actual_note_num = perc_note % 12  # Extract note within octave
                            actual_octave_offset = perc_note // 12  # Extract octave offset
                        else:
                            # Use original note value
                            actual_note_num = note_num
                            actual_octave_offset = 0

                        # Create IR event for percussion note
                        event = make_note(p, actual_note_num, dur)
                        event.metadata = {
                            'velocity': perc_vol,  # Use volume from percussion table
                            'perc_key': resolved_perc_key,  # GM percussion key
                            'transpose': perc_transpose + actual_octave_offset,  # Apply transpose + octave offset
                            'track_num': 9,  # MIDI percussion channel
                            'inst_id': perc_inst_id,
                            'percussion_mode': True
                        }
                        ir_events.append(event)
                        has_timing_events = True

                        # Build disassembly line - show original note value from the score
                        note_name = NOTE_NAMES[note_num]
                        line += f"           Note {note_name:<2} ({note_num:02}) Dur {dur:<3} [PERC inst={perc_inst_id:02X} vol={perc_vol} key={resolved_perc_key}]"
                    else:
                        # Normal note - create IR event
                        event = make_note(p, note_num, dur)
                        # Store current state in event for pass 2
                        # NOTE: octave is NOT stored here - it's tracked as state in Pass 2
                        # because loops can modify octave during execution
                        event.metadata = {
                            'velocity': velocity,
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
                            line += f"           Note {note_name:<2} ({note_num:02}) Dur {dur:<3} [PERC key={perc_key}]"
                        else:
                            line += f"           Note {note_name:<2} ({note_num:02}) Dur {dur}"

                elif note_num == self.rest_note_value:
                    # Rest - create IR event
                    event = make_rest(p, dur)
                    ir_events.append(event)
                    line += f"           Rest         Dur {dur}"

                elif note_num == self.tie_note_value:
                    # Tie - create IR event
                    event = make_tie(p, dur)
                    ir_events.append(event)
                    line += f"           Tie          Dur {dur}"

                else:
                    # Unknown note value
                    line += f"           OP_NOTE_UNKNOWN ({note_num:02}) Dur {dur}"
                
                p += 1
                disasm.append(line)

            else:
                # Command opcode (config-driven first_opcode)
                raw_oplen = self.opcode_table[cmd - self.first_opcode]
                # SD3 uses 0xFF as a magic value meaning "no parameters" (treat as oplen=1)
                # Also treat 0 as 1 to prevent infinite loops
                if raw_oplen == 0xFF or raw_oplen == 0:
                    oplen = 1
                else:
                    oplen = raw_oplen
                # oplen includes the opcode byte, so parameter count is oplen-1
                num_params = oplen - 1 if oplen > 0 else 0
                operands = list(data[p+1:p+1+num_params])

                # Format operands with proper spacing
                operand_str = ' '.join(f"{op:02X}" for op in operands)
                # Pad operands to align text descriptions (assume max 3 operands = 8 chars)
                line += f"{operand_str:<8}   {self.OPCODE_NAMES.get(cmd, f'OP_{cmd:02X}')}"

                # Create IR events for opcodes that affect playback
                # Use opcode_dispatch table for config-driven handling
                op_info = self.opcode_dispatch.get(cmd)
                semantic = op_info['semantic'] if op_info else None
                handler = op_info.get('handler') if op_info else None

                if semantic == "tempo" and len(operands) >= 1:
                    # Tempo change - operand index varies by game
                    # Use value_param if specified, otherwise default to operand 0
                    value_idx = op_info.get('value_param', 0) if op_info else 0
                    if len(operands) > value_idx:
                        tempo = operands[value_idx]
                        # Calculate BPM: BPM = (60,000,000 * tempo_value) / tempo_factor
                        bpm = (60_000_000.0 * tempo) / self.tempo_factor
                        event = make_tempo(p, bpm, operands)
                        ir_events.append(event)

                elif semantic == "tempo_fade" and len(operands) >= 2:
                    # Tempo Fade - duration and target tempo
                    # Operands: [duration, target_tempo]
                    duration = operands[0]
                    target_tempo = operands[1]
                    # Calculate target BPM using same formula
                    target_bpm = (60_000_000.0 * target_tempo) / self.tempo_factor
                    event = make_tempo_fade(p, duration, target_bpm, operands)
                    ir_events.append(event)

                elif semantic == "patch_change" and len(operands) >= 1:
                    # Use game-wide mapping, fall back to opcode-specific
                    if handler is None:
                        handler = self.instrument_mapping

                    # Use helper function to resolve patch mapping
                    inst_id = operands[0]
                    gm_patch, transpose_octaves, perc_key = self._resolve_patch(
                        inst_id, handler, instrument_table
                    )

                    # Add disassembly annotation
                    if gm_patch < 0:
                        # Percussion mode
                        line += f" -> PERC key={perc_key}"
                    elif gm_patch > 0:
                        # Regular instrument
                        line += f" -> GM patch {gm_patch}"

                    # Create IR event for patch change
                    event = make_patch_change(p, inst_id, gm_patch, transpose_octaves, operands)
                    ir_events.append(event)

                elif semantic == "octave_set" and len(operands) >= 1:
                    # Set octave
                    octave = operands[0]
                    event = make_octave_set(p, octave, operands)
                    ir_events.append(event)

                elif semantic == "octave_inc":
                    # Increment octave
                    octave += 1
                    event = make_octave_inc(p)
                    ir_events.append(event)

                elif semantic == "octave_dec":
                    # Decrement octave
                    octave -= 1
                    event = make_octave_dec(p)
                    ir_events.append(event)

                elif semantic == "volume" and len(operands) >= 1:
                    # Voice Volume - operand index varies by game
                    # Use value_param if specified, otherwise use last operand
                    value_idx = op_info.get('value_param', len(operands) - 1) if op_info else len(operands) - 1
                    if len(operands) > value_idx:
                        # Scale from 0-255 to MIDI velocity 0-127
                        velocity = operands[value_idx] >> 1
                        event = make_volume(p, velocity, operands)
                        ir_events.append(event)

                elif semantic == "volume_fade" and len(operands) >= 2:
                    # Volume Fade - duration and target volume
                    # Operands: [duration, target_volume]
                    # Scale target from 0-255 to MIDI 0-127
                    duration = operands[0]
                    target_volume = operands[1] >> 1
                    event = make_volume_fade(p, duration, target_volume, operands)
                    ir_events.append(event)

                elif semantic == "pan" and len(operands) >= 1:
                    # Voice Pan/Balance
                    # Use value_param if specified, otherwise use last operand
                    value_idx = op_info.get('value_param', len(operands) - 1) if op_info else len(operands) - 1
                    if len(operands) > value_idx:
                        # Pan: 0=left, 64=center, 127=right (MIDI convention)
                        # SNES uses 0-127 scale, so map directly or scale if needed
                        pan = operands[value_idx]
                        # Some games may use 0-255 scale, check if > 127
                        if pan > 127:
                            pan = pan >> 1  # Scale from 0-255 to 0-127
                        event = make_pan(p, pan, operands)
                        ir_events.append(event)

                elif semantic == "pan_fade" and len(operands) >= 2:
                    # Pan Fade - duration and target pan
                    # Operands: [duration, target_pan]
                    duration = operands[0]
                    target_pan = operands[1]
                    # Scale if needed (some games may use 0-255)
                    if target_pan > 127:
                        target_pan = target_pan >> 1
                    event = make_pan_fade(p, duration, target_pan, operands)
                    ir_events.append(event)

                elif semantic == "loop_start" and len(operands) >= 1:
                    # Begin repeat - just record it, don't execute
                    loop_depth += 1
                    event = make_loop_start(p, operands[0], operands)
                    ir_events.append(event)

                elif semantic == "loop_end":
                    # End repeat - just record it, don't execute
                    loop_depth = max(0, loop_depth - 1)
                    event = make_loop_end(p)
                    ir_events.append(event)

                elif semantic == "loop_mark":
                    # Mark loop point (SD3-style) - used with goto for infinite loops
                    event = make_loop_mark(p)
                    ir_events.append(event)

                elif semantic == "loop_break" and len(operands) >= 3:
                    # Selective repeat - conditional jump - just record
                    # Handler determines address calculation
                    if handler == "vaddroffset":
                        # SoM/FF3 style: apply vaddroffset formula
                        # Formula: target_offset = ((op1 + op2*256) + vaddroffset - spc_load_address) & 0xFFFF
                        raw_addr = operands[1] + operands[2] * 256
                        target_offset = ((raw_addr + vaddroffset - self.spc_load_address) & 0xFFFF)
                        target_spc_addr = target_offset + self.spc_load_address
                    else:
                        # FF2 style: operands directly encode SPC address
                        # Formula: target_offset = (op1 + op2*256) - spc_load_address
                        target_spc_addr = operands[1] + operands[2] * 256
                        target_offset = target_spc_addr - self.spc_load_address
                    # Add calculated target address to disassembly line
                    line += f" ${target_spc_addr:04X}"
                    event = make_loop_break(p, operands[0], target_offset, operands)
                    ir_events.append(event)

                elif semantic == "vibrato_on" and len(operands) >= 3:
                    # Vibrato settings
                    event = make_vibrato_on(p, operands)
                    ir_events.append(event)

                elif semantic == "vibrato_off":
                    # Vibrato off
                    event = make_vibrato_off(p)
                    ir_events.append(event)

                elif semantic == "tremolo_on" and len(operands) >= 3:
                    # Tremolo settings
                    event = make_tremolo_on(p, operands)
                    ir_events.append(event)

                elif semantic == "tremolo_off":
                    # Tremolo off
                    event = make_tremolo_off(p)
                    ir_events.append(event)

                elif semantic == "portamento_on" and len(operands) >= 3:
                    # Portamento settings
                    event = make_portamento_on(p, operands)
                    ir_events.append(event)

                elif semantic == "portamento_off":
                    # Portamento off
                    event = make_portamento_off(p)
                    ir_events.append(event)

                elif semantic == "slur_on":
                    # Slur on - legato articulation
                    event = make_slur_on(p)
                    ir_events.append(event)

                elif semantic == "slur_off":
                    # Slur off - restore normal articulation
                    event = make_slur_off(p)
                    ir_events.append(event)

                elif semantic == "roll_on":
                    # Roll on - similar to slur, notes play full duration
                    event = make_roll_on(p)
                    ir_events.append(event)

                elif semantic == "roll_off":
                    # Roll off - restore normal articulation
                    event = make_roll_off(p)
                    ir_events.append(event)

                elif semantic == "staccato_set" and len(operands) >= 1:
                    # Staccato - set note duration multiplier (percentage)
                    percentage = operands[0]
                    event = make_staccato(p, percentage, operands)
                    ir_events.append(event)

                elif semantic == "utility_duration" and len(operands) >= 1:
                    # Utility duration - override duration table for next note
                    duration_value = operands[0]
                    event = make_utility_duration(p, duration_value, operands)
                    ir_events.append(event)

                elif semantic == "master_volume" and len(operands) >= 1:
                    # Master volume (SoM 0xF8) - global volume multiplier
                    master_vol = operands[0]
                    event = make_master_volume(p, master_vol, operands)
                    ir_events.append(event)

                elif semantic == "volume_multiplier" and len(operands) >= 1:
                    # Volume multiplier (CT/FF3 0xF4) - per-track volume multiplier
                    vol_mult = operands[0]
                    event = make_volume_multiplier(p, vol_mult, operands)
                    ir_events.append(event)

                elif semantic == "percussion_mode_on":
                    # Percussion mode on - notes will index percussion table
                    percussion_mode = True
                    event = make_percussion_mode_on(p)
                    ir_events.append(event)

                elif semantic == "percussion_mode_off":
                    # Percussion mode off - restore normal note handling
                    percussion_mode = False
                    event = make_percussion_mode_off(p)
                    ir_events.append(event)

                elif semantic == "goto" and len(operands) >= 2:
                    # Goto with address - handler determines address calculation
                    if handler == "vaddroffset":
                        # SoM/FF3 style: apply vaddroffset formula
                        # Formula: target_offset = ((op1 + op2*256) + vaddroffset - spc_load_address) & 0xFFFF
                        raw_addr = operands[0] + operands[1] * 256
                        target_offset = ((raw_addr + vaddroffset - self.spc_load_address) & 0xFFFF)
                        target_spc_addr = target_offset + self.spc_load_address
                    else:
                        # FF2 style: operands directly encode SPC address
                        # Formula: target_offset = (op1 + op2*256) - spc_load_address
                        target_spc_addr = operands[0] + operands[1] * 256
                        target_offset = target_spc_addr - self.spc_load_address

                    # Determine if this is a backwards GOTO
                    is_backwards = target_offset < p

                    # Add calculated target address to disassembly line
                    line += f" ${target_spc_addr:04X}"
                    event = make_goto(p, target_offset, operands)
                    ir_events.append(event)
                    disasm.append(line)

                    # GOTO is always terminal - halt disassembly
                    # (Both backwards GOTOs for loops and forward GOTOs end the track)
                    break

                elif semantic == "halt_or_loop":
                    # SD3-specific: D0 opcode can be either HALT or LOOP depending on context
                    # Check if a LOOP_MARK event precedes this opcode anywhere in the track
                    # If so, convert to a GOTO that loops back to the mark
                    loop_mark_event = None
                    for prev_event in reversed(ir_events):
                        if prev_event.type == IREventType.LOOP_MARK:
                            loop_mark_event = prev_event
                            break

                    if loop_mark_event is not None:
                        # SD3-style loop: D0 after LOOP_MARK becomes GOTO to marked position
                        target_offset = loop_mark_event.offset
                        target_spc_addr = target_offset + self.spc_load_address
                        line = f"      {spc_addr:04X}: {cmd:02X}         Loop -> ${target_spc_addr:04X}"
                        event = make_goto(p, target_offset, operands)
                        ir_events.append(event)
                        disasm.append(line)
                    else:
                        # Normal halt - no loop mark
                        line = f"      {spc_addr:04X}: {cmd:02X}         Halt"
                        event = make_halt(p, operands)
                        ir_events.append(event)
                        disasm.append(line)
                    break

                elif semantic == "halt":
                    # Halt - end of track
                    event = make_halt(p, operands)
                    ir_events.append(event)
                    disasm.append(line)
                    break

                else:
                    # Unknown/unimplemented opcode - generate NO-OP IR event
                    # This ensures all opcodes can be targets of GOTOs
                    if semantic:
                        # Known semantic but no handler - create placeholder NO-OP
                        event = IREvent(IREventType.NOP, p, operands=operands)
                        ir_events.append(event)
                    # If no semantic at all, don't create IR event (truly unknown)

                p += oplen  # oplen includes the opcode byte itself
                disasm.append(line)

        return disasm, ir_events


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
        elif format_name == 'snes_unified':
            self.format_handler = SNESUnified(self.config, self.rom_data)
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

                    # Check if game has song-embedded instrument table (SD3-style)
                    inst_table_config = self.config.get('instrument_table', {})
                    if inst_table_config.get('location') == 'song_prefix':
                        # SD3-style: Instrument table at start of song data
                        # Format: pairs of (instrument_id, volume) terminated by 0xFF, then 2-byte length, then song data
                        # Parse instrument table to find where song data starts
                        p = 0
                        while p < len(self.rom_data) - offset - 3:
                            inst_byte = self.rom_data[offset + p]
                            if inst_byte == 0xFF:
                                # Found terminator
                                # Read 2-byte length after 0xFF
                                song_length = struct.unpack('<H', self.rom_data[offset + p + 1:offset + p + 3])[0]
                                # Song data starts after: instrument_pairs + 0xFF + length_field
                                song_data_offset = p + 3
                                return self.rom_data[offset + song_data_offset:offset + song_data_offset + song_length]
                            p += 2  # Skip instrument pair
                        raise ValueError(f"Song {song.id:02X}: Could not find instrument table terminator (0xFF)")
                    else:
                        # Standard format: Skip the 2-byte size field at the start
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

        # Read instrument table for SNES formats
        instrument_table = None
        if isinstance(self.format_handler, SNESUnified):
            # SNESUnified: check if instrument_table is in header (FF3/SoM style) or needs to be read (FF2 style)
            instrument_table = header.get('instrument_table')
            if instrument_table is None:
                # FF2 style: read from instrument_table_offset
                instrument_table = self.format_handler._read_song_instrument_table(song.id)

        # Read percussion table for SNES formats (CT/FF3-specific)
        percussion_table = None
        if isinstance(self.format_handler, SNESUnified):
            # Check if percussion_table is in header (FF3/CT style) or needs to be read
            percussion_table = header.get('percussion_table')
            if percussion_table is None and self.format_handler.percussion_table_offset is not None:
                # Read from percussion_table_offset
                percussion_table = self.format_handler._read_song_percussion_table(song.id)

        # Get vaddroffset for formats that use it (FF3, SoM)
        vaddroffset = header.get('vaddroffset', 0)

        # Parse all tracks
        tracks = {}
        for voice_num, offset in enumerate(track_offsets):
            # Call _parse_track_pass1 to get disassembly and IR events
            # Returns: (disasm_lines, ir_events)
            disasm_lines, ir_events = self.format_handler._parse_track_pass1(
                data, offset, voice_num, instrument_table or [], vaddroffset,
                track_boundaries=header.get('track_boundaries'),
                percussion_table=percussion_table
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
                'song_length': int,  # Maximum target_time across all tracks (in native ticks)
                'longest_intro_time': int,  # Intro time of longest track (in native ticks)
                'longest_loop_time': int,   # Loop time of longest track (in native ticks)
            }
        """
        analysis = {
            'tracks': {},
            'song_length': 0,
            'longest_intro_time': 0,
            'longest_loop_time': 0,
        }

        max_target_time = 0
        longest_intro_time = 0
        longest_loop_time = 0

        for voice_num, track in track_data['tracks'].items():
            # Analyze loop structure for this track
            loop_info = self.format_handler._analyze_track_loops(track['ir_events'])

            analysis['tracks'][voice_num] = {
                'loop_info': loop_info,
            }

            # Track maximum target time for song length
            intro_time = loop_info.get('intro_time', 0)
            loop_time = loop_info.get('loop_time', 0)
            target_time = intro_time + 2 * loop_time  # intro + 2 loops

            if target_time > max_target_time:
                max_target_time = target_time
                longest_intro_time = intro_time
                longest_loop_time = loop_time

        analysis['song_length'] = max_target_time
        analysis['longest_intro_time'] = longest_intro_time
        analysis['longest_loop_time'] = longest_loop_time

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
            if isinstance(self.format_handler, SNESUnified) and hasattr(self.format_handler, 'song_pointers'):
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

            # Read and display percussion table (CT/FF3-specific)
            percussion_table = header.get('percussion_table', [])
            if percussion_table:
                output.append("  Percussion:")
                for i, entry in enumerate(percussion_table):
                    if entry['instrument_id'] != 0 or entry['note'] != 0 or entry['volume'] != 0:
                        output.append(f"    {i:02X}: instr {entry['instrument_id']:02X}, note {entry['note']:02X}, vol {entry['volume']:02X}")

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
            # Calculate target playthrough time: intro + 2 * loop (in native ticks)
            # Loop analyzer has already found the longest track
            intro_time = loop_analysis.get('longest_intro_time', 0)
            loop_time = loop_analysis.get('longest_loop_time', 0)
            target_loop_time = intro_time + 2 * loop_time
            print(f"DEBUG generate_midi {song.id:02X} {song.title}: intro_time={intro_time}, loop_time={loop_time}, target_loop_time={target_loop_time}", file=sys.stderr)

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
            num_tracks = len(tracks_to_write)
            midi = MIDIFile(num_tracks, file_format=1, ticks_per_quarternote=96)

            # Add tempo events to first track (tempo events apply globally in MIDI format 1)
            for event in conductor_events:
                time_beats = event['time'] / 96.0
                # Use BPM directly from IR event (already calculated in Pass 1)
                bpm = event['tempo']
                midi.addTempo(0, time_beats, bpm)

            # Add tracks (now starting from 0 instead of 1)
            for track_idx, track_info in enumerate(tracks_to_write):
                self._write_midi_track(midi, track_idx, track_info, conductor_events if track_idx == 0 else [])

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
            Tuple of (patch_tracks, tempo_events) where tempo_events are placed on track 0
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
                    # Collect tempo events separately - they go on track 0
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
            'tempo_events': conductor_events,  # Tempo events now on track 0
            'tracks': []
        }

        for track_idx, track_info in enumerate(tracks_to_write):
            track_data = {
                'track_num': track_idx,  # Now 0-based
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
                    channel = track_idx  # Now 0-based
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

    def _write_midi_track(self, midi: MIDIFile, track_num: int, track_info: Dict, tempo_events: Optional[List[Dict]] = None):
        """Write a single track to MIDI file.

        Args:
            midi: MIDIFile object
            track_num: Track number (0-based)
            track_info: Track information dict
            tempo_events: Optional list of tempo events (only for track 0)
        """
        if tempo_events is None:
            tempo_events = []

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
                # track_num is now 0-based
                channel = track_num
                if channel >= 9:
                    channel += 1  # Skip channel 9 by mapping tracks 9+ to channels 10+

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
                    # All format handlers store GM patch in IR events (Pass 2 puts this in event['patch'])
                    gm_patch = event['patch']
                    midi.addProgramChange(track_num, default_channel, time_beats, gm_patch)
                    current_patch = gm_patch

                elif event['type'] == 'note':
                    duration_beats = event['duration'] / 96.0
                    if duration_beats > 0:
                        # Use channel from event if present (for percussion mode), otherwise use default
                        channel = event.get('channel', default_channel)

                        # Transposition already applied in Pass 2 for all formats
                        note = event['note']
                        note = max(0, min(127, note))

                        midi.addNote(track_num, channel, note,
                                   time_beats, duration_beats, event['velocity'])

                # Tempo events are now handled on track 0, not per-track

    def generate_musicxml(self, song: SongMetadata, track_data: Dict, loop_analysis: Dict, output_path: Path):
        """Generate MusicXML from sequence data.

        Args:
            song: Song metadata
            track_data: Output from parse_all_tracks()
            loop_analysis: Output from analyze_song_structure()
            output_path: Path to write MusicXML file
        """
        # Calculate target total time (same as MIDI generation)
        longest_loop_time = 0
        max_total_time = 0
        for voice_num in track_data['tracks'].keys():
            loop_info = loop_analysis['tracks'][voice_num]['loop_info']
            if loop_info.get('has_backwards_goto', False):
                intro_time = loop_info.get('intro_time', 0)
                loop_time = loop_info.get('loop_time', 0)
                total_time = intro_time + loop_time

                if loop_time > longest_loop_time:
                    longest_loop_time = loop_time
                if total_time > max_total_time:
                    max_total_time = total_time

        target_total_time = max_total_time + longest_loop_time

        # Embed loop_info in track_data for Pass 2
        for voice_num in track_data['tracks'].keys():
            track_data['tracks'][voice_num]['loop_info'] = \
                loop_analysis['tracks'][voice_num]['loop_info']

        # Parse all tracks (Pass 2)
        parsed_tracks = []
        for voice_num in sorted(track_data['tracks'].keys()):
            # Pass 2 - expand IR events into MIDI events
            midi_events = self.format_handler._parse_track_pass2(  # type: ignore[attr-defined]
                track_data, voice_num, target_total_time
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
                divisions.text = '96'
                continue

            # Calculate measure breaks (4/4 time, 96 ticks per quarter = 384 ticks per measure)
            divisions_per_quarter = 96
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
                print(f"  ERROR: {e} {traceback.format_exc()}")
        
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