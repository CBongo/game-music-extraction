"""
Output generation for AKAO music sequences.

Generates MIDI files, MusicXML files, and text disassembly from parsed track data.
"""

import sys
import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from midiutil import MIDIFile

from ir_events import IREventType


# Note names for text output
NOTE_NAMES = ["C ", "C#", "D ", "D#", "E ", "F ", "F#",
              "G ", "G#", "A ", "A#", "B "]


def disassemble_to_text(song, track_data: Dict, console_type: str,
                       format_handler, patch_mapper=None) -> str:
    """Generate text disassembly of sequence from pre-parsed track data.

    Args:
        song: Song metadata (must have .id and .title attributes)
        track_data: Output from parse_all_tracks()
        console_type: 'snes' or 'psx'
        format_handler: Format handler instance (for SNES-specific methods)
        patch_mapper: Optional PatchMapper instance (unused currently)

    Returns:
        Formatted disassembly text
    """
    output = []
    header = track_data['header']

    # Different header formats for different console types
    if console_type == 'snes':
        # SNES format - add song header with title and location
        output.append(f"Song {song.id:02X} - {song.title}:")

        # Add song location info if available (from song_pointers)
        # Check if format handler has song_pointers attribute (SNES-specific)
        if hasattr(format_handler, 'song_pointers') and hasattr(format_handler, 'rom_offset_to_display_addr'):
            if song.id in format_handler.song_pointers:
                song_offset, song_length = format_handler.song_pointers[song.id]
                # Convert ROM offset to display format
                addr_str = format_handler.rom_offset_to_display_addr(song_offset)
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
        if console_type == 'snes':
            output.append(f"    Voice {voice_num} data:")

        # Use pre-parsed disassembly lines
        disasm_lines = track_data['tracks'][voice_num]['disasm_lines']
        output.extend(disasm_lines)
        output.append("")

    return '\n'.join(output)


def dump_ir_to_text(song, track_data: Dict, loop_analysis: Dict) -> str:
    """Generate IR (Intermediate Representation) dump from pre-parsed track data.

    Args:
        song: Song metadata (must have .id and .title attributes)
        track_data: Output from parse_all_tracks()
        loop_analysis: Output from analyze_song_structure()

    Returns:
        Formatted IR dump text
    """
    output = []
    output.append(f"Song: {song.id:02X} {song.title}")
    output.append(f"Song length: {loop_analysis['song_length']} ticks")
    output.append("")

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

            elif event.type in (IREventType.VOLUME_FADE, IREventType.PAN_FADE):
                parts.append(f"dur={event.duration} target={event.value}")

            elif event.type == IREventType.LOOP_START:
                parts.append(f"count={event.loop_count}")

            elif event.type == IREventType.LOOP_END:
                parts.append(f"restore_octave={event.restore_octave}")

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


class MidiGenerator:
    """Generates MIDI files from parsed track data."""

    def __init__(self, format_handler, patch_mapper, patch_based_tracks: bool):
        """Initialize MIDI generator.

        Args:
            format_handler: Format handler instance (for Pass 2 parsing)
            patch_mapper: PatchMapper instance for instrument info
            patch_based_tracks: If True, organize tracks by patch; if False, by voice
        """
        self.format_handler = format_handler
        self.patch_mapper = patch_mapper
        self.patch_based_tracks = patch_based_tracks

    def generate(self, song, track_data: Dict, loop_analysis: Dict, output_path: Path):
        """Generate MIDI file from sequence with patch mapping support.

        Args:
            song: Song metadata (must have .id and .title attributes)
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
                    midi_events = self.format_handler._parse_track_pass2(
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

                elif event['type'] == 'controller':
                    # Controller change (CC) events
                    # Used for: CC7 (volume fade), CC10 (pan fade), CC11 (expression), CC68 (legato)
                    channel = event.get('channel', default_channel)
                    midi.addControllerEvent(track_num, channel, time_beats,
                                          event['controller'], event['value'])

                # Tempo events are now handled on track 0, not per-track


class MusicXmlGenerator:
    """Generates MusicXML files from parsed track data."""

    def __init__(self, format_handler, patch_mapper, patch_based_tracks: bool):
        """Initialize MusicXML generator.

        Args:
            format_handler: Format handler instance (for Pass 2 parsing)
            patch_mapper: PatchMapper instance for instrument info
            patch_based_tracks: If True, organize tracks by patch; if False, by voice
        """
        self.format_handler = format_handler
        self.patch_mapper = patch_mapper
        self.patch_based_tracks = patch_based_tracks

    def generate(self, song, track_data: Dict, loop_analysis: Dict, output_path: Path):
        """Generate MusicXML from sequence data.

        Args:
            song: Song metadata (must have .id and .title attributes)
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
            midi_events = self.format_handler._parse_track_pass2(
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
            # Import the organize function from MidiGenerator
            midi_gen = MidiGenerator(self.format_handler, self.patch_mapper, self.patch_based_tracks)
            tracks_to_write, _ = midi_gen._organize_by_patch(parsed_tracks)
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
