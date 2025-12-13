#!/usr/bin/env python3
"""
VGM Sequence Extractor
Extracts music sequences from game ROMs/ISOs to text disassembly and MIDI files.
Chris Bongaarts - 2025-11-02 with help from Claude
"""

import sys
import traceback

# Import the extractor
from extractor import SequenceExtractor


def main():
    """Main entry point."""
    # Parse command-line arguments
    patch_based_tracks = False
    song_id_filter = None
    args = []

    i = 0
    while i < len(sys.argv[1:]):
        arg = sys.argv[1 + i]
        if arg == '--patch-based-tracks':
            patch_based_tracks = True
        elif arg == '--song' and i + 1 < len(sys.argv[1:]):
            # Next arg is the song ID
            song_id_filter = int(sys.argv[1 + i + 1], 0)  # Support hex with 0x prefix
            i += 1  # Skip next arg
        else:
            args.append(arg)
        i += 1

    if len(args) < 2:
        print("Usage: python extract_akao.py <config.yaml> <source_file> [options]")
        print()
        print("Arguments:")
        print("  config.yaml             - Game metadata configuration file")
        print("  source_file             - ISO/ROM file containing the game data")
        print()
        print("Options:")
        print("  --patch-based-tracks    - Organize MIDI tracks by instrument/patch instead of sequence")
        print("  --song <id>             - Extract only the specified song ID (decimal or hex with 0x)")
        print()
        print("Examples:")
        print("  python extract_akao.py ff9.yaml ff9.iso")
        print("  python extract_akao.py ff8.yaml ff8.iso --patch-based-tracks")
        print("  python extract_akao.py ff3.yaml ff3.smc --song 0x0D")
        sys.exit(1)

    config_file = args[0]
    source_file = args[1]

    try:
        extractor = SequenceExtractor(config_file, source_file, patch_based_tracks=patch_based_tracks)
        extractor.extract_all(song_id_filter=song_id_filter)
    except Exception as e:
        print(f"\nError: {e}")
        print("\nFull traceback:")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
