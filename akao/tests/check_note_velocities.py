#!/usr/bin/env python3
"""Quick script to check note velocity distribution in a MIDI file."""

import sys
import struct

if len(sys.argv) < 2:
    print("Usage: python3 check_note_velocities.py <midi_file>")
    sys.exit(1)

midi_file = sys.argv[1]

# Read MIDI file and extract note-on velocities
velocities = []

with open(midi_file, 'rb') as f:
    data = f.read()

    # Simple scan for note-on events (status byte 0x90-0x9F)
    i = 0
    while i < len(data) - 2:
        byte = data[i]
        # Check if this is a note-on status byte (0x90-0x9F)
        if 0x90 <= byte <= 0x9F:
            # Next two bytes should be note and velocity
            note = data[i+1]
            velocity = data[i+2]
            # Only count actual note-ons (velocity > 0)
            if velocity > 0:
                velocities.append(velocity)
            i += 3
        else:
            i += 1

if not velocities:
    print("No note events found")
    sys.exit(0)

# Calculate statistics
velocities.sort()
total = len(velocities)
unique_velocities = set(velocities)
max_vel = max(velocities)
min_vel = min(velocities)
avg_vel = sum(velocities) / total

print(f"Total notes: {total}")
print(f"Unique velocities: {len(unique_velocities)}")
print(f"Min velocity: {min_vel}")
print(f"Max velocity: {max_vel}")
print(f"Avg velocity: {avg_vel:.1f}")

# If few unique velocities, show them
if len(unique_velocities) <= 10:
    print(f"\nVelocity values used: {sorted(unique_velocities)}")
else:
    # Show distribution in ranges
    ranges = [(0, 31), (32, 63), (64, 95), (96, 126), (127, 127)]
    print("\nVelocity distribution:")
    for low, high in ranges:
        count = sum(1 for v in velocities if low <= v <= high)
        pct = (count / total) * 100
        if low == high:
            print(f"  {low:3d}      : {count:5d} ({pct:5.1f}%)")
        else:
            print(f"  {low:3d}-{high:3d} : {count:5d} ({pct:5.1f}%)")
