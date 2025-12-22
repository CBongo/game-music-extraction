"""
Sequence extraction orchestrator.
Handles ROM/ISO loading, format detection, song extraction, and batch processing.
"""

import sys
import struct
import traceback
import yaml
import re
from pathlib import Path
from typing import List, Tuple, Dict, Optional
from io import BytesIO
import pycdlib.pycdlib as pycdlib_module

# Import base classes
from format_base import PatchMapper, SequenceFormat, SongMetadata

# Import format handlers
from format_snes import SNESUnified
from format_psx import AKAONewStyle, AKAOFF7, Raw2352FileWrapper

# Import output generators
from output_generators import (
    disassemble_to_text as disasm_to_text_func,
    dump_ir_to_text as dump_ir_func,
    MidiGenerator,
    MusicXmlGenerator
)


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

        # Check for directory-based loading (FF7 pre-extracted files)
        if 'akao_directory' in self.config:
            # Directory-based: No ISO/ROM loading needed
            self.iso = None
            self.sector_size = None
            self.raw_sector_size = None
            self.rom_data = b''  # Empty, won't be used
            print(f"Using pre-extracted AKAO files from: {self.config['akao_directory']}")
        elif self.console_type == 'snes':
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
        elif format_name == 'akao_ff7':
            self.format_handler = AKAOFF7(self.config, self.rom_data, exe_iso_reader=self)
        elif format_name == 'snes_unified':
            self.format_handler = SNESUnified(self.config, self.rom_data)
        else:
            raise ValueError(f"Unknown format: {format_name}")

        # Initialize output generators
        self.midi_generator = MidiGenerator(self.format_handler, self.patch_mapper, self.patch_based_tracks)
        self.musicxml_generator = MusicXmlGenerator(self.format_handler, self.patch_mapper, self.patch_based_tracks)

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
            output = BytesIO()
            self.iso.get_file_from_iso_fp(output, iso_path=iso_path_upper)
            self._img_file_cache[iso_path_upper] = output

        # Use the cached file handle to seek and read
        file_handle = self._img_file_cache[iso_path_upper]
        file_handle.seek(offset)
        return file_handle.read(length)

    def extract_sequence_data(self, song: SongMetadata) -> bytes:
        """Extract raw sequence data from source file."""
        # Check for directory-based loading (FF7 pre-extracted .bin files)
        if 'akao_directory' in self.config:
            akao_dir = Path(self.config['akao_directory'])
            bin_file = akao_dir / f"{song.id:02x}.bin"

            if not bin_file.exists():
                raise FileNotFoundError(f"AKAO file not found: {bin_file}")

            with open(bin_file, 'rb') as f:
                return f.read()

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

    def parse_all_tracks(self, song: SongMetadata, data: bytes, use_alternate_pointers: bool = False) -> Optional[Dict]:
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
        # parse_header() now returns track_offsets for all formats
        header = self.format_handler.parse_header(data, song.id, use_alternate_pointers)
        track_offsets = header.get('track_offsets', [])

        # Check if song has any valid tracks
        if not track_offsets:
            # Empty song - no valid voice pointers
            # Return None to signal that this song should be skipped
            return None

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
        """Generate text disassembly of sequence from pre-parsed track data."""
        return disasm_to_text_func(song, track_data, self.console_type, self.format_handler)

    def dump_ir_to_text(self, song: SongMetadata, track_data: Dict, loop_analysis: Dict) -> str:
        """Generate IR (Intermediate Representation) dump from pre-parsed track data."""
        return dump_ir_func(song, track_data, loop_analysis)

    def generate_midi(self, song: SongMetadata, track_data: Dict, loop_analysis: Dict, output_path: Path):
        """Generate MIDI file from sequence with patch mapping support."""
        self.midi_generator.generate(song, track_data, loop_analysis, output_path)

    def generate_musicxml(self, song: SongMetadata, track_data: Dict, loop_analysis: Dict, output_path: Path):
        """Generate MusicXML from sequence data."""
        self.musicxml_generator.generate(song, track_data, loop_analysis, output_path)

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

                # Check if song is empty (no valid voice pointers)
                if track_data is None:
                    # Generate minimal stub file
                    stub_output = f"Song {song.id:02X}: {song.title}\n\n  [Empty song - no valid voice data]\n"
                    text_file = text_dir / f"{filename}.txt"
                    text_file.write_text(stub_output)
                    print(f"  SKIP: {song.title} (no valid voice data)")
                    continue

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

                    # Skip if alternate version is also empty
                    if alt_track_data is None:
                        print(f"  SKIP: {alt_filename} (no valid voice data)")
                    else:
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
