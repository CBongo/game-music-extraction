"""
Base classes and shared utilities for sequence format handlers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional
import math

# Import IR event classes
from ir_events import IREvent, IREventType


def linear_to_midi(linear_value: float) -> int:
    """Convert linear amplitude (0.0-1.0) to MIDI value (0-127).

    Uses square-root curve to match GM logarithmic interpretation.
    GM synths apply: dB = 40 × log₁₀(cc/127)
    We want linear 0.5 → -6dB (half amplitude)
    Solution: cc = 127 × sqrt(linear)

    Args:
        linear_value: Linear amplitude from 0.0 (silent) to 1.0 (full volume)

    Returns:
        MIDI value from 0 to 127
    """
    if linear_value <= 0:
        return 0
    return int(min(127, max(0, math.sqrt(linear_value) * 127)))


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

    # Attributes that subclasses must define (type hints for Pylance)
    config: Dict
    rom_data: bytes

    # _read_rom_table() is implemented by subclasses but not abstract
    # (type hint for Pylance to recognize the method exists)
    def _read_rom_table(self, address: int, size: int, data_type: str) -> List[int]:
        """Read a table from ROM data.

        Subclasses must implement this method.

        Args:
            address: ROM/file address to read from
            size: Number of items to read
            data_type: Data type code (e.g., 'B' for byte, 'H' for ushort)

        Returns:
            List of integers read from ROM
        """
        raise NotImplementedError("Subclasses must implement _read_rom_table()")

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
        """Analyze track for backwards GOTO loops and calculate timing.

        This is a shared implementation that detects loop patterns and measures
        timing by executing LOOP_START/LOOP_END/LOOP_BREAK constructs.

        Args:
            ir_events: List of IR events from pass 1

        Returns:
            Dict with keys:
                'has_backwards_goto': bool - True if track ends with backwards GOTO
                'intro_time': int - Time units before loop starts (0 if no loop)
                'loop_time': int - Time units for one loop iteration (0 if no loop)
                'goto_target_idx': int - Index of GOTO target event (None if no loop)
                'target_time': int - intro_time + 2 * loop_time
        """
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
                'goto_target_idx': None,
                'target_time': 0
            }

        # Find target event index
        target_idx = None
        for j, e in enumerate(ir_events):
            if e.offset == last_goto_event.target_offset:
                target_idx = j
                break

        # Check if backwards (target comes before GOTO)
        assert last_goto_idx is not None, "last_goto_idx should not be None here"
        if target_idx is None or target_idx >= last_goto_idx:
            # Forward GOTO or target not found - not a loop
            return {
                'has_backwards_goto': False,
                'intro_time': 0,
                'loop_time': 0,
                'goto_target_idx': None,
                'target_time': 0
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

        return {
            'has_backwards_goto': True,
            'intro_time': intro_time,
            'loop_time': loop_time,
            'goto_target_idx': target_idx,
            'target_time': intro_time + 2 * loop_time  # Play intro + 2 loops
        }

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

    @staticmethod
    def _parse_int_key(key) -> int:
        """Convert YAML config key to int (handles '0xAA', '170', etc).

        Args:
            key: Config key (str or int)

        Returns:
            Integer value (auto-detects hex with 0x prefix)
        """
        return int(key, 0) if isinstance(key, str) else key

    @staticmethod
    def _make_controller_event(time: int, channel: int,
                               controller: int, value: int) -> Dict:
        """Create a MIDI controller change event.

        Args:
            time: Time in MIDI ticks
            channel: MIDI channel (0-15)
            controller: Controller number (0-127)
            value: Controller value (0-127)

        Returns:
            MIDI controller event dictionary
        """
        return {
            'type': 'controller',
            'time': time,
            'channel': channel,
            'controller': controller,
            'value': value
        }

    def _generate_fade_events(self, event_type: str, start_value: float,
                             target_value: float, fade_duration_midi: int,
                             start_time: int, channel: int = 0,
                             controller: Optional[int] = None) -> List[Dict]:
        """Generate interpolated MIDI events for a fade.

        This method creates a series of MIDI events spaced 2 ticks apart
        that smoothly transition from start_value to target_value over
        the specified duration. Used for TEMPO_FADE, VOLUME_FADE, PAN_FADE.

        Args:
            event_type: Type of event ('tempo' or 'controller')
            start_value: Starting value
            target_value: Target value
            fade_duration_midi: Duration in MIDI ticks
            start_time: Starting time in MIDI ticks
            channel: MIDI channel (for controller events)
            controller: Controller number (for controller events, e.g., 10=pan, 11=expression)

        Returns:
            List of MIDI event dictionaries
        """
        events = []
        num_steps = max(1, fade_duration_midi // 2)

        for step in range(num_steps + 1):
            step_time = start_time + (step * 2)

            # Linear interpolation
            if num_steps > 0:
                step_value = start_value + (target_value - start_value) * step / num_steps
            else:
                step_value = target_value

            # Create appropriate event type
            if event_type == 'tempo':
                events.append({
                    'type': 'tempo',
                    'time': step_time,
                    'tempo': step_value
                })
            elif event_type == 'controller':
                events.append({
                    'type': 'controller',
                    'time': step_time,
                    'channel': channel,
                    'controller': controller,
                    'value': int(step_value)
                })

        return events

    def _load_config_table(self, config_key: str, default_values: List[int],
                          default_size: Optional[int] = None,
                          default_type: str = 'B') -> List[int]:
        """Load optional ROM table from config with fallback to defaults.

        Args:
            config_key: Key name in config dict (e.g., 'duration_table')
            default_values: Fallback values if config key not present
            default_size: Default table size if not in config (None = len(default_values))
            default_type: Default data type code (e.g., 'B' for byte)

        Returns:
            List of integers from either ROM or default values
        """
        if config_key in self.config:
            table_cfg = self.config[config_key]
            if default_size is None:
                default_size = len(default_values)
            return self._read_rom_table(
                table_cfg.get('address'),
                table_cfg.get('size', default_size),
                table_cfg.get('type', default_type)
            )
        else:
            return default_values


# ==============================================================================
# MIDI Render Strategy Classes
# ==============================================================================

class MidiRenderStrategy(ABC):
    """Abstract base for MIDI rendering strategies.

    Strategies control how volume dynamics are represented in MIDI output:
    - VelocityStrategy: Uses note-on velocity (SPC-accurate)
    - ExpressionStrategy: Uses CC11 (Expression controller) with constant velocity
    - CC7Strategy: Uses CC7 (Main Volume) with constant velocity
    """

    def __init__(self, constant_velocity: int = 100):
        """Initialize strategy.

        Args:
            constant_velocity: Fixed velocity for controller strategies (ignored by velocity strategy)
        """
        self.constant_velocity = constant_velocity

    @abstractmethod
    def get_note_velocity(self, state: 'Pass2State', adjusted_velocity: int) -> int:
        """Return velocity value for note-on event.

        Args:
            state: Current Pass2 state
            adjusted_velocity: Calculated velocity after applying volume/multiplier

        Returns:
            MIDI velocity (0-127) to use for note-on
        """
        pass

    @abstractmethod
    def get_note_preamble_events(self, state: 'Pass2State', adjusted_velocity: int,
                                  midi_channel: int) -> List[Dict]:
        """Return MIDI events to emit before a note (e.g., CC11).

        Args:
            state: Current Pass2 state
            adjusted_velocity: Calculated velocity after applying volume/multiplier
            midi_channel: MIDI channel number

        Returns:
            List of MIDI event dicts to prepend before note-on
        """
        pass

    @abstractmethod
    def handle_volume_event(self, state: 'Pass2State', value: int,
                           scaled_value: int, midi_channel: int) -> List[Dict]:
        """Handle VOLUME IR event, return MIDI events to emit.

        Args:
            state: Current Pass2 state (may be modified)
            value: Raw volume value from IR event
            scaled_value: Scaled MIDI value (0-127)
            midi_channel: MIDI channel number

        Returns:
            List of MIDI event dicts to emit
        """
        pass

    @abstractmethod
    def handle_volume_fade(self, state: 'Pass2State',
                          start_volume: int, target_volume: int,
                          start_scaled: int, target_scaled: int,
                          fade_duration_midi: int, fade_duration_native: int,
                          midi_channel: int,
                          generate_fade_events_fn,
                          calculate_fade_delta_fn) -> List[Dict]:
        """Handle VOLUME_FADE IR event, return MIDI events to emit.

        For velocity strategy: Activates internal fade state on state directly
        For controller strategies: Returns interpolated CC events

        Args:
            state: Current Pass2 state (may be modified by velocity strategy)
            start_volume: Starting volume value (IR range)
            target_volume: Target volume value (IR range)
            start_scaled: Starting volume (MIDI 0-127)
            target_scaled: Target volume (MIDI 0-127)
            fade_duration_midi: Fade duration in MIDI ticks
            fade_duration_native: Fade duration in native ticks
            midi_channel: MIDI channel number
            generate_fade_events_fn: Function to generate interpolated events
            calculate_fade_delta_fn: Function to calculate fade delta per tick

        Returns:
            List of MIDI event dicts to emit (empty for velocity strategy)
        """
        pass


class VelocityStrategy(MidiRenderStrategy):
    """Use note velocity for dynamics (SPC-accurate).

    This strategy varies note-on velocity to represent volume changes,
    matching the behavior of the original SPC700 sound chip.
    """

    def get_note_velocity(self, state: 'Pass2State', adjusted_velocity: int) -> int:
        return adjusted_velocity  # Use calculated velocity

    def get_note_preamble_events(self, state: 'Pass2State', adjusted_velocity: int,
                                  midi_channel: int) -> List[Dict]:
        return []  # No preamble events

    def handle_volume_event(self, state: 'Pass2State', value: int,
                           scaled_value: int, midi_channel: int) -> List[Dict]:
        # Just update state, no controller events
        return []

    def handle_volume_fade(self, state: 'Pass2State',
                          start_volume: int, target_volume: int,
                          start_scaled: int, target_scaled: int,
                          fade_duration_midi: int, fade_duration_native: int,
                          midi_channel: int,
                          generate_fade_events_fn,
                          calculate_fade_delta_fn) -> List[Dict]:
        # Activate internal fade state directly
        state.volume_fade_active = True
        state.volume_fade_target = float(target_volume)
        state.volume_fade_delta = calculate_fade_delta_fn(
            start_volume, target_volume, fade_duration_native)
        state.volume_fade_ticks_remaining = fade_duration_native
        return []  # No controller events


class ExpressionStrategy(MidiRenderStrategy):
    """Use CC11 (Expression) for dynamics.

    This strategy uses MIDI Expression controller (CC11) to represent volume
    changes, with notes played at constant velocity. Better suited for some
    MIDI players that don't handle velocity variation well.
    """

    CONTROLLER_NUM = 11  # Expression

    def get_note_velocity(self, state: 'Pass2State', adjusted_velocity: int) -> int:
        return self.constant_velocity  # Use constant velocity

    def get_note_preamble_events(self, state: 'Pass2State', adjusted_velocity: int,
                                  midi_channel: int) -> List[Dict]:
        # Emit CC11 before each note
        return [{
            'type': 'controller',
            'time': state.total_time,
            'channel': midi_channel,
            'controller': self.CONTROLLER_NUM,
            'value': adjusted_velocity
        }]

    def handle_volume_event(self, state: 'Pass2State', value: int,
                           scaled_value: int, midi_channel: int) -> List[Dict]:
        # Emit CC11 immediately
        return [{
            'type': 'controller',
            'time': state.total_time,
            'channel': midi_channel,
            'controller': self.CONTROLLER_NUM,
            'value': scaled_value
        }]

    def handle_volume_fade(self, state: 'Pass2State',
                          start_volume: int, target_volume: int,
                          start_scaled: int, target_scaled: int,
                          fade_duration_midi: int, fade_duration_native: int,
                          midi_channel: int,
                          generate_fade_events_fn,
                          calculate_fade_delta_fn) -> List[Dict]:
        # Generate CC11 fade events (ignore native duration and delta fn)
        return generate_fade_events_fn(
            'controller', start_scaled, target_scaled,
            fade_duration_midi, state.total_time,
            midi_channel, self.CONTROLLER_NUM
        )


class CC7Strategy(MidiRenderStrategy):
    """Use CC7 (Main Volume) for dynamics.

    This strategy uses MIDI Main Volume controller (CC7) to represent volume
    changes, with notes played at constant velocity. Some MIDI players prefer
    this over Expression (CC11).
    """

    CONTROLLER_NUM = 7  # Main Volume

    def get_note_velocity(self, state: 'Pass2State', adjusted_velocity: int) -> int:
        return self.constant_velocity  # Use constant velocity

    def get_note_preamble_events(self, state: 'Pass2State', adjusted_velocity: int,
                                  midi_channel: int) -> List[Dict]:
        # Emit CC7 before each note
        return [{
            'type': 'controller',
            'time': state.total_time,
            'channel': midi_channel,
            'controller': self.CONTROLLER_NUM,
            'value': adjusted_velocity
        }]

    def handle_volume_event(self, state: 'Pass2State', value: int,
                           scaled_value: int, midi_channel: int) -> List[Dict]:
        # Emit CC7 immediately
        return [{
            'type': 'controller',
            'time': state.total_time,
            'channel': midi_channel,
            'controller': self.CONTROLLER_NUM,
            'value': scaled_value
        }]

    def handle_volume_fade(self, state: 'Pass2State',
                          start_volume: int, target_volume: int,
                          start_scaled: int, target_scaled: int,
                          fade_duration_midi: int, fade_duration_native: int,
                          midi_channel: int,
                          generate_fade_events_fn,
                          calculate_fade_delta_fn) -> List[Dict]:
        # Generate CC7 fade events (ignore native duration and delta fn)
        return generate_fade_events_fn(
            'controller', start_scaled, target_scaled,
            fade_duration_midi, state.total_time,
            midi_channel, self.CONTROLLER_NUM
        )


def create_render_strategy(strategy_name: str, constant_velocity: int = 100) -> MidiRenderStrategy:
    """Create a render strategy instance from config name.

    Args:
        strategy_name: Strategy name ('velocity', 'expression', 'cc7')
        constant_velocity: Fixed velocity for controller strategies

    Returns:
        MidiRenderStrategy instance
    """
    strategies = {
        'velocity': VelocityStrategy,
        'expression': ExpressionStrategy,
        'cc7': CC7Strategy,
    }
    strategy_class = strategies.get(strategy_name, VelocityStrategy)
    return strategy_class(constant_velocity)


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


@dataclass
class Pass2State:
    """Mutable state for Pass 2 MIDI event generation.

    This dataclass consolidates all state variables used during Pass 2 processing,
    eliminating ~70 lines of duplicated initialization code between PSX and SNES formats.

    Volume representation is normalized to float (0.0-1.0 range, 1.0 = normal) across
    both PSX and SNES formats for consistency and cleaner math.
    """

    # Configuration (immutable after init)
    midi_strategy: str = 'velocity'
    constant_velocity: int = 100
    render_strategy: Optional[MidiRenderStrategy] = None  # Strategy instance (replaces midi_strategy branching)
    apply_multiplier: bool = True
    apply_master_volume_config: bool = True
    velocity_scale: float = 1.0
    tick_scale: int = 2

    # Playback state (mutable)
    total_time: int = 0
    octave: int = 4
    velocity: int = 100
    tempo: float = 120.0  # BPM
    current_pan: int = 64
    perc_key: int = 0
    transpose_octaves: int = 0
    current_channel: int = 0

    # Volume state (mutable) - NORMALIZED TO FLOAT (1.0 = normal)
    master_volume: float = 1.0
    volume_multiplier: float = 1.0

    # Volume fade state (mutable) - BOTH PSX AND SNES
    volume_fade_active: bool = False
    volume_fade_target: float = 0.0
    volume_fade_delta: float = 0.0
    volume_fade_ticks_remaining: int = 0

    # Articulation state (mutable)
    slur_enabled: bool = False
    roll_enabled: bool = False
    staccato_percentage: int = 100
    utility_duration_override: Optional[int] = None
    gate_time: int = 2  # Native ticks before full duration when note-off happens

    # Execution state (mutable)
    current_voice_num: int = 0
    i: int = 0  # Event stream pointer
    iteration_count: int = 0
    midi_events: List[Dict] = field(default_factory=list)
    loop_stack: List[Dict] = field(default_factory=list)

    # References (immutable) - set during initialization
    ir_events: List[IREvent] = field(default_factory=list)
    loop_info: Dict = field(default_factory=dict)
