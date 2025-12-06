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
    PATCH_CHANGE = "patch_change"
    OCTAVE_SET = "octave_set"
    OCTAVE_INC = "octave_inc"
    OCTAVE_DEC = "octave_dec"
    VOLUME = "volume"
    PAN = "pan"
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
    GOTO = "goto"
    HALT = "halt"
    ENVELOPE = "envelope"
    GAIN = "gain"
    STACCATO = "staccato"
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
                           IREventType.LOOP_BREAK, IREventType.GOTO)


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


def make_loop_start(offset: int, count: int, operands: Optional[List[int]] = None) -> IREvent:
    """Create a loop start event."""
    return IREvent(
        type=IREventType.LOOP_START,
        offset=offset,
        loop_count=count,
        operands=operands or []
    )


def make_loop_end(offset: int) -> IREvent:
    """Create a loop end event."""
    return IREvent(type=IREventType.LOOP_END, offset=offset)


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
