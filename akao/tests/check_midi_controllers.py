#!/usr/bin/env python3
"""Quick script to check for controller events in a MIDI file."""

import sys
import struct

if len(sys.argv) < 2:
    print("Usage: python3 check_midi_controllers.py <midi_file>")
    sys.exit(1)

midi_file = sys.argv[1]

# Read MIDI file and extract controller change events
controllers = {}  # Track controller number -> count

with open(midi_file, 'rb') as f:
    data = f.read()

    # Simple scan for controller change events (status byte 0xB0-0xBF)
    i = 0
    while i < len(data) - 2:
        byte = data[i]
        # Check if this is a controller change status byte (0xB0-0xBF)
        if 0xB0 <= byte <= 0xBF:
            # Next two bytes should be controller number and value
            controller = data[i+1]
            value = data[i+2]
            if controller not in controllers:
                controllers[controller] = 0
            controllers[controller] += 1
            i += 3
        else:
            i += 1

if not controllers:
    print("No controller events found")
    sys.exit(0)

# Show controller statistics
print(f"Found {sum(controllers.values())} controller events:")
for controller_num in sorted(controllers.keys()):
    count = controllers[controller_num]
    # Common controller names
    names = {
        7: "Volume",
        10: "Pan",
        11: "Expression",
        64: "Sustain Pedal",
        68: "Legato Pedal"
    }
    name = names.get(controller_num, f"Unknown CC{controller_num}")
    print(f"  CC{controller_num:3d} ({name:20s}): {count:5d} events")
