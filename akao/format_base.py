"""
Base classes and shared utilities for sequence format handlers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional

# Import IR event classes
from ir_events import IREvent


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
