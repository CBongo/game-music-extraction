#!/usr/bin/env python3
"""Test that emergency brakes prevent infinite loops."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import time
from ir_events import IREvent, IREventType

# Create a simple infinite loop scenario
ir_events = [
    IREvent(type=IREventType.NOTE, offset=0, note_num=0, duration=480),
    IREvent(type=IREventType.GOTO, offset=10, target_offset=0),  # Backwards GOTO to offset 0
]

print("Testing emergency brake with infinite loop (backwards GOTO)...")
print(f"IR events: {len(ir_events)}")
print(f"  Event 0: {ir_events[0].type.value} at offset {ir_events[0].offset}")
print(f"  Event 1: {ir_events[1].type.value} at offset {ir_events[1].offset}, target={ir_events[1].target_offset}")

# Simulate the loop detection and execution that would happen in pass 2
max_iterations = max(len(ir_events) * 200, 10000)
iteration_count = 0
start_time = time.time()
max_time_seconds = 2.0
max_midi_events = 50000
midi_events = []
total_time = 0
target_loop_time = 0  # No target, so should loop forever without emergency brakes

i = 0
brake_reason = None
while i < len(ir_events):
    iteration_count += 1
    
    # Check iteration limit
    if iteration_count > max_iterations:
        brake_reason = f"iteration limit ({max_iterations})"
        break
    
    # Check target loop time
    if target_loop_time > 0 and total_time >= target_loop_time:
        brake_reason = f"target loop time ({target_loop_time})"
        break
    
    # Emergency brakes
    if time.time() - start_time > max_time_seconds:
        brake_reason = f"time limit ({max_time_seconds}s)"
        break
    
    if len(midi_events) > max_midi_events:
        brake_reason = f"MIDI event limit ({max_midi_events})"
        break
    
    # Process event
    event = ir_events[i]
    if event.type == IREventType.NOTE:
        midi_events.append({'type': 'note', 'time': total_time})
        total_time += event.duration
        i += 1
    elif event.type == IREventType.GOTO and event.target_offset is not None:
        # Find target
        target_idx = None
        for j, e in enumerate(ir_events):
            if e.offset == event.target_offset:
                target_idx = j
                break
        if target_idx is not None:
            i = target_idx  # Jump to target (infinite loop!)
        else:
            i += 1
    else:
        i += 1

elapsed = time.time() - start_time
print(f"\nEmergency brake triggered: {brake_reason}")
print(f"Iterations: {iteration_count}")
print(f"MIDI events generated: {len(midi_events)}")
print(f"Time elapsed: {elapsed:.2f}s")
print(f"Total music time: {total_time} ticks")

if brake_reason and "time limit" in brake_reason:
    print("\n✓ SUCCESS: Time-based emergency brake prevented infinite loop")
elif brake_reason and "MIDI event" in brake_reason:
    print("\n✓ SUCCESS: MIDI event limit emergency brake prevented infinite loop")
elif brake_reason and "iteration" in brake_reason:
    print("\n✓ SUCCESS: Iteration limit emergency brake prevented infinite loop")
else:
    print("\n✗ FAIL: No emergency brake triggered!")
