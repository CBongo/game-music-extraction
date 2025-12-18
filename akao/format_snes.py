"""
SNES music format handler for AKAO-based games.
Supports FF2, FF3, FF5, CT, SD3, SoM via config-driven architecture.
"""

import struct
import sys
from typing import List, Tuple, Dict, Optional

# Import base classes
from format_base import SequenceFormat, NOTE_NAMES

# Import IR event classes
from ir_events import *


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
            # Check if opcode_table values include the opcode byte itself (1-based)
            # or just count operands (0-based). Default is 0-based (operands only).
            self.opcode_table_includes_opcode = table_cfg.get('includes_opcode', False)
        else:
            # Default opcode lengths (46 opcodes starting from first_opcode)
            self.opcode_table = [1] * 46
            self.opcode_table_includes_opcode = False

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
                'handler': op_config.get('handler', None),
                'restore_octave': op_config.get('restore_octave', False)
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

    def _calculate_fade_delta(self, start_value, target_value, duration_ticks):
        """Calculate per-tick delta for parameter fade (SPC-style).

        This implements the SPC fade algorithm:
        delta = (target - start) / duration

        Used for VOLUME_FADE, PAN_FADE, TEMPO_FADE, and other gradual parameter changes.

        Args:
            start_value: Current parameter value (any range)
            target_value: Target parameter value (same range as start)
            duration_ticks: Fade duration in native ticks

        Returns:
            float: Delta to add/subtract per tick
        """
        if duration_ticks <= 0:
            return 0.0
        return float(target_value - start_value) / float(duration_ticks)

    def _scale_volume_to_midi(self, ir_volume, volume_multiplier, master_volume, velocity_scale):
        """Scale IR volume value (0-255) to MIDI range (0-127) with multipliers applied.

        This scaling is applied consistently to:
        - VOLUME opcode values
        - VOLUME_FADE start and target values
        - NOTE event velocities (velocity strategy only)

        Args:
            ir_volume: Volume value from IR (0-255 range, can be float from fade)
            volume_multiplier: Current volume multiplier (0.0-1.0, or 1.0 for none)
            master_volume: Current master volume (0.0-1.0, or 1.0 for none)
            velocity_scale: Global velocity scaling factor (default 1.0)

        Returns:
            int: MIDI velocity/controller value (0-127 range)
        """
        # Scale IR range (0-255) to MIDI range (0-127)
        # Convert to int first since ir_volume can be float from fade advance logic
        midi_vol = int(ir_volume) >> 1

        # Apply volume multipliers
        adjusted = float(midi_vol)
        if volume_multiplier != 1.0:
            adjusted = adjusted * (0.5 + volume_multiplier)
        if master_volume != 1.0:
            adjusted = adjusted * master_volume

        # Apply velocity_scale
        adjusted = adjusted * velocity_scale

        # Clamp to MIDI range
        return int(min(127, max(0, adjusted)))

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

        # Read MIDI rendering configuration
        midi_config = self.config.get('midi_render', {})
        midi_strategy = midi_config.get('strategy', 'velocity')
        constant_velocity = midi_config.get('constant_velocity', 100)
        apply_multiplier = midi_config.get('apply_multiplier', True)
        apply_master_volume_config = midi_config.get('apply_master_volume', True)
        velocity_scale = midi_config.get('velocity_scale', 1.0)  # Global velocity scaling

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
        master_volume = 1.0  # Master volume multiplier (SoM) - 1.0 = normal (100%), range 0.0-1.0
        volume_multiplier = 1.0  # Volume multiplier (CT/FF3) - 1.0 = normal (100%), range 0.0-1.0

        # Fade state (SPC-style) for velocity strategy
        volume_fade_active = False
        volume_fade_target = 0.0
        volume_fade_delta = 0.0
        volume_fade_ticks_remaining = 0

        # Loop execution state
        loop_stack = []  # Stack of {start_idx, count, iteration, octave}

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
                # NOTE: octave and velocity are tracked as state variables, not in metadata
                # This allows loops to modify them during playback
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

                # Scale ORIGINAL native duration to MIDI ticks
                # This is ALWAYS used for time advancement (next event timing)
                midi_dur = native_duration * tick_scale

                # Calculate note-off duration (gate timing OR staccato - mutually exclusive)
                # Order of priority: slur/roll > staccato > gate
                if slur_enabled or roll_enabled:
                    # Slur/roll: play full duration (no gap before next note)
                    gate_adjusted_dur = midi_dur
                elif staccato_percentage < 100:
                    # Staccato: apply percentage reduction (replaces gate timing)
                    # Apply to ORIGINAL duration, not already-reduced duration
                    gate_adjusted_dur = int(native_duration * staccato_percentage / 100) * tick_scale
                else:
                    # Normal articulation: apply standard gate timing (2 native ticks before end)
                    gate_adjusted_dur = (native_duration - gate_time) * tick_scale

                # Calculate MIDI velocity using helper method
                adjusted_velocity = self._scale_volume_to_midi(velocity, volume_multiplier, master_volume, velocity_scale)

                # Strategy: velocity, expression, or cc7
                if midi_strategy in ['expression', 'cc7']:
                    # Controller-based strategies: use constant velocity for notes
                    # (Volume dynamics handled by CC11 or CC7)
                    note_velocity = constant_velocity

                    # For expression, also generate CC11 before note
                    # (VOLUME event already generated CC11, but regenerate for safety)
                    if midi_strategy == 'expression':
                        midi_events.append({
                            'type': 'controller',
                            'time': total_time,
                            'channel': midi_channel,
                            'controller': 11,  # Expression
                            'value': adjusted_velocity
                        })
                else:
                    # Velocity strategy: use calculated velocity
                    note_velocity = adjusted_velocity

                # Add MIDI note event
                midi_events.append({
                    'type': 'note',
                    'time': total_time,
                    'duration': gate_adjusted_dur,  # Adjusted for articulation
                    'note': midi_note,
                    'velocity': note_velocity,
                    'channel': midi_channel
                })

                # ALWAYS advance time by FULL MIDI duration (unmodified by staccato/gate)
                total_time += midi_dur

                # Advance fade states by this event's duration (native ticks)
                if volume_fade_active and volume_fade_ticks_remaining > 0:
                    ticks_to_advance = min(native_duration, volume_fade_ticks_remaining)
                    velocity += volume_fade_delta * ticks_to_advance
                    volume_fade_ticks_remaining -= ticks_to_advance
                    if volume_fade_ticks_remaining <= 0:
                        velocity = volume_fade_target  # Snap to target
                        volume_fade_active = False

                i += 1

            elif event.type == IREventType.REST:
                # Rest just advances time
                # Scale native duration to MIDI ticks
                assert event.duration is not None, "REST event must have duration"
                native_duration = event.duration
                midi_dur = native_duration * tick_scale
                total_time += midi_dur

                # Advance fade states by this event's duration (native ticks)
                if volume_fade_active and volume_fade_ticks_remaining > 0:
                    ticks_to_advance = min(native_duration, volume_fade_ticks_remaining)
                    velocity += volume_fade_delta * ticks_to_advance
                    volume_fade_ticks_remaining -= ticks_to_advance
                    if volume_fade_ticks_remaining <= 0:
                        velocity = volume_fade_target  # Snap to target
                        volume_fade_active = False

                i += 1

            elif event.type == IREventType.TIE:
                # Tie extends the last note
                # Scale native duration to MIDI ticks
                assert event.duration is not None, "TIE event must have duration"
                native_duration = event.duration
                tie_dur = native_duration * tick_scale
                for j in range(len(midi_events) - 1, -1, -1):
                    if midi_events[j]['type'] == 'note':
                        midi_events[j]['duration'] += tie_dur
                        break
                total_time += tie_dur

                # Advance fade states by this event's duration (native ticks)
                if volume_fade_active and volume_fade_ticks_remaining > 0:
                    ticks_to_advance = min(native_duration, volume_fade_ticks_remaining)
                    velocity += volume_fade_delta * ticks_to_advance
                    volume_fade_ticks_remaining -= ticks_to_advance
                    if volume_fade_ticks_remaining <= 0:
                        velocity = volume_fade_target  # Snap to target
                        volume_fade_active = False

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
                # IR stores normalized value (0-255)
                velocity = int(event.value)

                # Cancel any active volume fade (immediate volume change overrides fade)
                volume_fade_active = False

                # For controller-based strategies, generate controller event immediately
                if midi_strategy == 'expression':
                    # Expression strategy: Use CC11
                    expr_value = self._scale_volume_to_midi(velocity, volume_multiplier, master_volume, velocity_scale)
                    midi_events.append({
                        'type': 'controller',
                        'time': total_time,
                        'channel': current_channel,
                        'controller': 11,  # Expression
                        'value': expr_value
                    })
                elif midi_strategy == 'cc7':
                    # CC7 strategy: Use CC7 (Main Volume)
                    cc7_value = self._scale_volume_to_midi(velocity, volume_multiplier, master_volume, velocity_scale)
                    midi_events.append({
                        'type': 'controller',
                        'time': total_time,
                        'channel': current_channel,
                        'controller': 7,  # Main Volume
                        'value': cc7_value
                    })
                # else: velocity strategy stores in state variable for notes

                i += 1

            elif event.type == IREventType.VOLUME_FADE:
                # Volume fade - behavior depends on strategy
                assert event.duration is not None and event.value is not None, "VOLUME_FADE must have duration and value"

                target_volume_ir = event.value  # IR value (0-255)
                fade_duration_native = event.duration  # Native ticks

                if midi_strategy == 'velocity':
                    # Velocity strategy: Activate fade state (SPC-style)
                    # Fade updates velocity state variable gradually over time
                    volume_fade_active = True
                    volume_fade_target = target_volume_ir
                    volume_fade_delta = self._calculate_fade_delta(velocity, target_volume_ir, fade_duration_native)
                    volume_fade_ticks_remaining = fade_duration_native

                    # No controller events generated - notes will use fading velocity

                else:
                    # Controller strategies (expression/cc7): Generate controller events
                    fade_duration_midi = fade_duration_native * tick_scale  # Convert to MIDI ticks
                    start_volume_ir = velocity  # Current velocity state (IR value 0-255)

                    # Scale BOTH start and target to MIDI range with multipliers
                    start_volume_midi = self._scale_volume_to_midi(start_volume_ir, volume_multiplier, master_volume, velocity_scale)
                    target_volume_midi = self._scale_volume_to_midi(target_volume_ir, volume_multiplier, master_volume, velocity_scale)

                    # Determine which controller to use
                    controller_num = 11 if midi_strategy == 'expression' else 7

                    # Generate controller events at 2-tick intervals using SCALED values
                    num_steps = max(1, fade_duration_midi // 2)
                    for step in range(num_steps + 1):
                        step_time = total_time + (step * 2)
                        # Linear interpolation between SCALED values
                        if num_steps > 0:
                            step_volume = int(start_volume_midi + (target_volume_midi - start_volume_midi) * step / num_steps)
                        else:
                            step_volume = target_volume_midi

                        midi_events.append({
                            'type': 'controller',
                            'time': step_time,
                            'channel': current_channel,
                            'controller': controller_num,
                            'value': step_volume
                        })

                    # Update velocity state immediately to target (IR value)
                    velocity = target_volume_ir

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
                # Value is normalized float 0.0-1.0 from Pass 1
                assert event.value is not None, "MASTER_VOLUME event must have value"
                if apply_master_volume_config:
                    master_volume = float(event.value)
                # If disabled, keep master_volume at 1.0 (no effect)
                i += 1

            elif event.type == IREventType.VOLUME_MULTIPLIER:
                # Set volume multiplier (CT/FF3 0xF4/0xFD) - per-track multiplier
                # Value is normalized float 0.0-1.0 from Pass 1
                assert event.value is not None, "VOLUME_MULTIPLIER event must have value"
                if apply_multiplier:
                    volume_multiplier = float(event.value)
                # If disabled, keep volume_multiplier at 1.0 (no effect)
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
                    'octave': octave,
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
                        # Restore octave if needed
                        if event.restore_octave:
                            octave = loop['octave']
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

            # Compute track offsets from voice pointers
            # A pointer < 0x100 indicates unused voice (matches Perl script threshold)
            track_offsets = []
            for ptr in voice_pointers:
                if ptr >= 0x100:  # Valid voice pointer
                    # Convert SPC RAM address to offset in our data buffer
                    buffer_offset = ptr - self.spc_load_address
                    track_offsets.append(buffer_offset)

            return {
                'voice_pointers': voice_pointers,
                'track_offsets': track_offsets,
                'num_voices': len(track_offsets)
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

                # Handle utility duration (SD3-style ONLY: dur_idx == 13 means read next byte)
                # Only SD3 uses this feature (identified by 'inverted' note encoding)
                note_encoding = self.config.get('note_encoding', 'normal')
                utility_dur_idx = self.note_divisor - 1  # For divisor 14, utility is index 13
                if note_encoding == 'inverted' and dur_idx == utility_dur_idx and p + 1 < len(data):
                    # SD3 only: Read next byte as raw duration value
                    p += 1
                    cmd2 = data[p]
                    dur = cmd2  # Raw duration from next byte
                    line = f"      {spc_addr:04X}: {cmd:02X} {cmd2:02X}"
                else:
                    # Normal: use duration table
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
                        # NOTE: octave and velocity are NOT stored here - tracked as state in Pass 2
                        # because loops can modify them during execution
                        event.metadata = {
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
                    num_params = 0
                else:
                    # Check if opcode_table includes opcode byte or just operands
                    if self.opcode_table_includes_opcode:
                        # Table value = opcode + operands (e.g., 4 means opcode + 3 params)
                        oplen = raw_oplen
                        num_params = oplen - 1 if oplen > 0 else 0
                    else:
                        # Table value = operands only (e.g., 3 means 3 params)
                        num_params = raw_oplen
                        oplen = num_params + 1  # Add 1 for the opcode itself
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
                        # Calculate BPM based on game-specific formula
                        tempo_formula = self.config.get('tempo_formula', 'normal')
                        if tempo_formula == 'inverted':
                            # SD3-style: BPM = 60,000,000 / (tempo * timer_period_us * timer_count)
                            # MIDI tempo (μs/qn) = tempo * timer_period_us * timer_count
                            timer_period_us = self.config.get('timer_period_us', 125)
                            timer_count = self.config.get('timer_count', 48)
                            bpm = 60_000_000.0 / (tempo * timer_period_us * timer_count)
                        else:
                            # Normal: BPM = (60,000,000 * tempo_value) / tempo_factor
                            bpm = (60_000_000.0 * tempo) / self.tempo_factor
                        event = make_tempo(p, bpm, operands)
                        ir_events.append(event)

                elif semantic == "tempo_fade" and len(operands) >= 2:
                    # Tempo Fade - duration and target tempo
                    # Operands: [duration, target_tempo]
                    duration = operands[0]
                    target_tempo = operands[1]
                    # Calculate target BPM using same formula as tempo opcode
                    tempo_formula = self.config.get('tempo_formula', 'normal')
                    if tempo_formula == 'inverted':
                        # SD3-style: BPM = 60,000,000 / (tempo * timer_period_us * timer_count)
                        timer_period_us = self.config.get('timer_period_us', 125)
                        timer_count = self.config.get('timer_count', 48)
                        target_bpm = 60_000_000.0 / (target_tempo * timer_period_us * timer_count)
                    else:
                        # Normal: BPM = (60,000,000 * tempo_value) / tempo_factor
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
                        # Normalize volume to 0-255 range for IR
                        # Games use different ranges: FF2=255, CT=127, etc.
                        raw_volume = operands[value_idx]
                        volume_range = self.config.get('volume_range', 255)
                        if volume_range < 255:
                            # Scale up to 255 (e.g., CT 0-127 → 0-255)
                            normalized_volume = int((raw_volume / volume_range) * 255)
                        else:
                            # Already at 255 range (e.g., FF2)
                            normalized_volume = raw_volume

                        # Update state variable for Pass 1 note processing
                        velocity = normalized_volume

                        # Store normalized value in IR
                        event = make_volume(p, normalized_volume, operands)
                        ir_events.append(event)

                elif semantic == "volume_fade" and len(operands) >= 2:
                    # Volume Fade - duration and target volume
                    # Operands: [duration, target_volume]
                    # Scale target from 0-255 to MIDI 0-127
                    duration = operands[0]
                    target_volume = operands[1]
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
                    # set flag if this game restores octave after repeat
                    restore_octave = op_info.get('restore_octave', False) if op_info else False
                    event = make_loop_end(p, restore_octave)
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
                    # Normalize to 0.0-1.0 range (operand is $00-$FF)
                    master_vol = operands[0] / 256.0
                    event = make_master_volume(p, master_vol, operands)
                    ir_events.append(event)

                elif semantic == "volume_multiplier" and len(operands) >= 1:
                    # Volume multiplier (CT/FF3 0xF4/0xFD) - per-track volume multiplier
                    # Normalize to 0.0-1.0 range (operand is $00-$FF)
                    vol_mult = operands[0] / 256.0
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

                elif semantic == "echo_on":
                    # Echo on - SPC700 hardware effect
                    event = make_echo_on(p, operands)
                    ir_events.append(event)

                elif semantic == "echo_off":
                    # Echo off - SPC700 hardware effect
                    event = make_echo_off(p, operands)
                    ir_events.append(event)

                elif semantic == "adsr_default":
                    # ADSR default envelope - SPC700 hardware effect
                    event = make_adsr(p, "default", 0, operands)
                    ir_events.append(event)

                elif semantic == "adsr_attack" and len(operands) >= 1:
                    # ADSR attack - SPC700 hardware effect
                    event = make_adsr(p, "attack", operands[0], operands)
                    ir_events.append(event)

                elif semantic == "adsr_decay" and len(operands) >= 1:
                    # ADSR decay - SPC700 hardware effect
                    event = make_adsr(p, "decay", operands[0], operands)
                    ir_events.append(event)

                elif semantic == "adsr_sustain" and len(operands) >= 1:
                    # ADSR sustain - SPC700 hardware effect
                    event = make_adsr(p, "sustain", operands[0], operands)
                    ir_events.append(event)

                elif semantic == "adsr_release" and len(operands) >= 1:
                    # ADSR release - SPC700 hardware effect
                    event = make_adsr(p, "release", operands[0], operands)
                    ir_events.append(event)

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

