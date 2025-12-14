#!/usr/bin/env python3
"""
Intermediate Representation (IR) for game music sequences.
Represents parsed music data before loop expansion and MIDI generation.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class IREventType(Enum):
    """Types of IR events."""
    NOTE = "note"
    REST = "rest"
    TIE = "tie"
    TEMPO = "tempo"
    TEMPO_FADE = "tempo_fade"
    PATCH_CHANGE = "patch_change"
    OCTAVE_SET = "octave_set"
    OCTAVE_INC = "octave_inc"
    OCTAVE_DEC = "octave_dec"
    VOLUME = "volume"
    VOLUME_FADE = "volume_fade"
    PAN = "pan"
    PAN_FADE = "pan_fade"
    VIBRATO_ON = "vibrato_on"
    VIBRATO_OFF = "vibrato_off"
    TREMOLO_ON = "tremolo_on"
    TREMOLO_OFF = "tremolo_off"
    PORTAMENTO_ON = "portamento_on"
    PORTAMENTO_OFF = "portamento_off"
    SLUR_ON = "slur_on"
    SLUR_OFF = "slur_off"
    ROLL_ON = "roll_on"
    ROLL_OFF = "roll_off"
    PERCUSSION_MODE_ON = "percussion_mode_on"
    PERCUSSION_MODE_OFF = "percussion_mode_off"
    LOOP_START = "loop_start"
    LOOP_END = "loop_end"
    LOOP_BREAK = "loop_break"  # Selective repeat / conditional jump
    LOOP_MARK = "loop_mark"  # Mark loop point for goto (SD3-style)
    GOTO = "goto"
    HALT = "halt"
    ENVELOPE = "envelope"
    GAIN = "gain"
    STACCATO = "staccato"
    UTILITY_DURATION = "utility_duration"
    MASTER_VOLUME = "master_volume"
    VOLUME_MULTIPLIER = "volume_multiplier"
    ECHO_VOLUME = "echo_volume"
    ECHO_SETTINGS = "echo_settings"
    PAN_SWEEP = "pan_sweep"
    NOISE_CLOCK = "noise_clock"
    NOP = "nop"
    UNKNOWN = "unknown"


@dataclass
class IREvent:
    """Base class for all IR events.

    Each event represents a parsed opcode or note event from the original sequence.
    Events are stored in linear order as they appear in the source data, without
    loop expansion. The second pass will expand loops and generate MIDI events.
    """
    type: IREventType
    offset: int  # Byte offset in original data

    # Optional fields used by various event types
    # Note/Rest/Tie events
    note_num: Optional[int] = None  # 0-11 for C to B
    duration: Optional[int] = None  # In MIDI ticks

    # State change events
    value: Optional[int|float] = None  # Generic value field
    operands: List[int] = field(default_factory=list)  # Raw operand bytes

    # For loop events
    loop_count: Optional[int] = None
    target_offset: Optional[int] = None  # Jump target for goto/loop_break
    condition: Optional[int] = None  # For conditional jumps (selective repeat)
    restore_octave: Optional[bool] = None  # For loop_end, restore octave to value at loop_start

    # For patch changes - track instrument context
    inst_id: Optional[int] = None  # Game-specific instrument ID
    gm_patch: Optional[int] = None  # General MIDI patch (negative = percussion)
    transpose: int = 0  # Octave transpose for this instrument

    # Additional metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_note_event(self) -> bool:
        """Check if this is a note, rest, or tie event."""
        return self.type in (IREventType.NOTE, IREventType.REST, IREventType.TIE)

    def is_loop_event(self) -> bool:
        """Check if this is a loop control event."""
        return self.type in (IREventType.LOOP_START, IREventType.LOOP_END,
                           IREventType.LOOP_BREAK, IREventType.LOOP_MARK, IREventType.GOTO)


@dataclass
class IRVoice:
    """Represents a single voice/track's parsed event stream.

    Contains the linear event sequence and disassembly, ready for expansion
    and MIDI generation in pass 2.
    """
    voice_num: int
    events: List[IREvent] = field(default_factory=list)
    disasm: List[str] = field(default_factory=list)

    # Initial state for this voice
    initial_octave: int = 4
    initial_velocity: int = 100
    initial_tempo: int = 120

    def __len__(self) -> int:
        """Return number of events in this voice."""
        return len(self.events)

    def get_note_events(self) -> List[IREvent]:
        """Get only note/rest/tie events."""
        return [e for e in self.events if e.is_note_event()]

    def get_loop_events(self) -> List[IREvent]:
        """Get only loop control events."""
        return [e for e in self.events if e.is_loop_event()]


@dataclass
class IRSequence:
    """Represents a complete multi-voice sequence.

    Contains all voices for a song, ready for MIDI generation.
    """
    song_id: int
    title: str
    voices: List[IRVoice] = field(default_factory=list)

    # Global sequence properties
    base_tempo: int = 120
    time_signature: tuple = (4, 4)

    def __len__(self) -> int:
        """Return number of voices in this sequence."""
        return len(self.voices)

    def add_voice(self, voice: IRVoice):
        """Add a voice to this sequence."""
        self.voices.append(voice)


# Helper functions for creating common event types

def make_note(offset: int, note_num: int, duration: int) -> IREvent:
    """Create a note event."""
    return IREvent(
        type=IREventType.NOTE,
        offset=offset,
        note_num=note_num,
        duration=duration
    )


def make_rest(offset: int, duration: int) -> IREvent:
    """Create a rest event."""
    return IREvent(
        type=IREventType.REST,
        offset=offset,
        duration=duration
    )


def make_tie(offset: int, duration: int) -> IREvent:
    """Create a tie event."""
    return IREvent(
        type=IREventType.TIE,
        offset=offset,
        duration=duration
    )


def make_tempo(offset: int, bpm: float, operands: Optional[List[int]] = None) -> IREvent:
    """Create a tempo change event.

    Args:
        offset: Byte offset in original data
        bpm: Tempo in beats per minute (floating point)
        operands: Raw operand bytes from the original format
    """
    return IREvent(
        type=IREventType.TEMPO,
        offset=offset,
        value=bpm,  # Store BPM directly (format-independent)
        operands=operands or []
    )


def make_tempo_fade(offset: int, duration: int, target_bpm: float,
                    operands: Optional[List[int]] = None) -> IREvent:
    """Create a tempo fade event.

    Args:
        offset: Byte offset in original data
        duration: Fade duration in native ticks
        target_bpm: Target tempo in beats per minute (floating point)
        operands: Raw operand bytes from the original format
    """
    return IREvent(
        type=IREventType.TEMPO_FADE,
        offset=offset,
        duration=duration,
        value=target_bpm,
        operands=operands or []
    )


def make_patch_change(offset: int, inst_id: int, gm_patch: int,
                     transpose: int = 0, operands: Optional[List[int]] = None) -> IREvent:
    """Create a patch change event."""
    return IREvent(
        type=IREventType.PATCH_CHANGE,
        offset=offset,
        inst_id=inst_id,
        gm_patch=gm_patch,
        transpose=transpose,
        operands=operands or []
    )


def make_octave_set(offset: int, octave: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create an octave set event."""
    return IREvent(
        type=IREventType.OCTAVE_SET,
        offset=offset,
        value=octave,
        operands=operands or []
    )


def make_octave_inc(offset: int) -> IREvent:
    """Create an octave increment event."""
    return IREvent(type=IREventType.OCTAVE_INC, offset=offset)


def make_octave_dec(offset: int) -> IREvent:
    """Create an octave decrement event."""
    return IREvent(type=IREventType.OCTAVE_DEC, offset=offset)


def make_volume(offset: int, volume: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a volume change event."""
    return IREvent(
        type=IREventType.VOLUME,
        offset=offset,
        value=volume,
        operands=operands or []
    )


def make_volume_fade(offset: int, duration: int, target_volume: int,
                     operands: Optional[List[int]] = None) -> IREvent:
    """Create a volume fade event.

    Args:
        offset: Byte offset in original data
        duration: Fade duration in native ticks
        target_volume: Target volume value (0-127 MIDI scale)
        operands: Raw operand bytes from the original format
    """
    return IREvent(
        type=IREventType.VOLUME_FADE,
        offset=offset,
        duration=duration,
        value=target_volume,
        operands=operands or []
    )


def make_pan(offset: int, pan: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a pan change event."""
    return IREvent(
        type=IREventType.PAN,
        offset=offset,
        value=pan,
        operands=operands or []
    )


def make_pan_fade(offset: int, duration: int, target_pan: int,
                  operands: Optional[List[int]] = None) -> IREvent:
    """Create a pan fade event.

    Args:
        offset: Byte offset in original data
        duration: Fade duration in native ticks
        target_pan: Target pan value (0-127 MIDI scale)
        operands: Raw operand bytes from the original format
    """
    return IREvent(
        type=IREventType.PAN_FADE,
        offset=offset,
        duration=duration,
        value=target_pan,
        operands=operands or []
    )


def make_loop_start(offset: int, count: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a loop start event."""
    return IREvent(
        type=IREventType.LOOP_START,
        offset=offset,
        loop_count=count,
        operands=operands or []
    )


def make_loop_end(offset: int, restore_octave: bool = False) -> IREvent:
    """Create a loop end event."""
    return IREvent(
        type=IREventType.LOOP_END, 
        offset=offset, 
        restore_octave=restore_octave
    )


def make_loop_mark(offset: int) -> IREvent:
    """Create a loop mark event (marks loop point for goto)."""
    return IREvent(type=IREventType.LOOP_MARK, offset=offset)


def make_loop_break(offset: int, condition: int, target_offset: int,
                   operands: Optional[List[int]] = None) -> IREvent:
    """Create a loop break (selective repeat) event."""
    return IREvent(
        type=IREventType.LOOP_BREAK,
        offset=offset,
        condition=condition,
        target_offset=target_offset,
        operands=operands or []
    )


def make_goto(offset: int, target_offset: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a goto event."""
    return IREvent(
        type=IREventType.GOTO,
        offset=offset,
        target_offset=target_offset,
        operands=operands or []
    )


def make_halt(offset: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a halt event."""
    return IREvent(
        type=IREventType.HALT,
        offset=offset,
        operands=operands or []
    )


def make_vibrato_on(offset: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a vibrato on event."""
    return IREvent(
        type=IREventType.VIBRATO_ON,
        offset=offset,
        operands=operands or [],
        metadata={'params': operands} if operands else {}
    )


def make_vibrato_off(offset: int) -> IREvent:
    """Create a vibrato off event."""
    return IREvent(type=IREventType.VIBRATO_OFF, offset=offset)


def make_tremolo_on(offset: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a tremolo on event."""
    return IREvent(
        type=IREventType.TREMOLO_ON,
        offset=offset,
        operands=operands or [],
        metadata={'params': operands} if operands else {}
    )


def make_tremolo_off(offset: int) -> IREvent:
    """Create a tremolo off event."""
    return IREvent(type=IREventType.TREMOLO_OFF, offset=offset)


def make_portamento_on(offset: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a portamento on event."""
    return IREvent(
        type=IREventType.PORTAMENTO_ON,
        offset=offset,
        operands=operands or [],
        metadata={'params': operands} if operands else {}
    )


def make_portamento_off(offset: int) -> IREvent:
    """Create a portamento off event."""
    return IREvent(type=IREventType.PORTAMENTO_OFF, offset=offset)


def make_slur_on(offset: int) -> IREvent:
    """Create a slur on event."""
    return IREvent(type=IREventType.SLUR_ON, offset=offset)


def make_slur_off(offset: int) -> IREvent:
    """Create a slur off event."""
    return IREvent(type=IREventType.SLUR_OFF, offset=offset)


def make_roll_on(offset: int) -> IREvent:
    """Create a roll on event."""
    return IREvent(type=IREventType.ROLL_ON, offset=offset)


def make_roll_off(offset: int) -> IREvent:
    """Create a roll off event."""
    return IREvent(type=IREventType.ROLL_OFF, offset=offset)


def make_percussion_mode_on(offset: int) -> IREvent:
    """Create a percussion mode on event."""
    return IREvent(type=IREventType.PERCUSSION_MODE_ON, offset=offset)


def make_percussion_mode_off(offset: int) -> IREvent:
    """Create a percussion mode off event."""
    return IREvent(type=IREventType.PERCUSSION_MODE_OFF, offset=offset)


def make_staccato(offset: int, percentage: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a staccato event.

    Args:
        offset: Byte offset in original data
        percentage: Note duration multiplier (0-100+)
        operands: Raw operand bytes from the original format
    """
    return IREvent(
        type=IREventType.STACCATO,
        offset=offset,
        value=percentage,
        operands=operands or []
    )


def make_utility_duration(offset: int, duration: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a utility duration event.

    This overrides the duration table for the next note only.

    Args:
        offset: Byte offset in original data
        duration: Duration value to use for next note (in native ticks)
        operands: Raw operand bytes from the original format
    """
    return IREvent(
        type=IREventType.UTILITY_DURATION,
        offset=offset,
        value=duration,
        operands=operands or []
    )


def make_master_volume(offset: int, volume: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a master volume event.

    Sets a global volume multiplier that affects all subsequent volume changes.
    Used in SoM (0xF8).

    Args:
        offset: Byte offset in original data
        volume: Master volume value (0-255 native scale)
        operands: Raw operand bytes from the original format
    """
    return IREvent(
        type=IREventType.MASTER_VOLUME,
        offset=offset,
        value=volume,
        operands=operands or []
    )


def make_volume_multiplier(offset: int, multiplier: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a volume multiplier event.

    Sets a per-track volume multiplier that affects note velocities.
    Used in CT/FF3 (0xF4).

    Args:
        offset: Byte offset in original data
        multiplier: Volume multiplier value (0-255 native scale)
        operands: Raw operand bytes from the original format
    """
    return IREvent(
        type=IREventType.VOLUME_MULTIPLIER,
        offset=offset,
        value=multiplier,
        operands=operands or []
    )


def make_echo_on(offset: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create an echo enable event.

    Enables SPC700 echo/reverb effect for this voice.
    Hardware effect - tracked for completeness but not rendered in MIDI.

    Args:
        offset: Byte offset in original data
        operands: Raw operand bytes from the original format
    """
    return IREvent(
        type=IREventType.ECHO_SETTINGS,
        offset=offset,
        value=1,  # 1 = on
        operands=operands or []
    )


def make_echo_off(offset: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create an echo disable event.

    Disables SPC700 echo/reverb effect for this voice.
    Hardware effect - tracked for completeness but not rendered in MIDI.

    Args:
        offset: Byte offset in original data
        operands: Raw operand bytes from the original format
    """
    return IREvent(
        type=IREventType.ECHO_SETTINGS,
        offset=offset,
        value=0,  # 0 = off
        operands=operands or []
    )


def make_adsr(offset: int, adsr_param: str, value: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create an ADSR envelope event.

    Sets SPC700 ADSR (Attack/Decay/Sustain/Release) envelope parameters.
    Hardware effect - tracked for completeness but not rendered in MIDI.

    Args:
        offset: Byte offset in original data
        adsr_param: Which ADSR parameter ("attack", "decay", "sustain", "release", "default")
        value: Parameter value
        operands: Raw operand bytes from the original format
    """
    return IREvent(
        type=IREventType.ENVELOPE,
        offset=offset,
        value=value,
        metadata={'adsr_param': adsr_param},
        operands=operands or []
    )
