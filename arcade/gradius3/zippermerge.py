#!/usr/bin/env python

import sys

def interleave_files(file1_path, file2_path, output_path):
    try:
        with open(file1_path, 'rb') as file1, \
             open(file2_path, 'rb') as file2, \
             open(output_path, 'wb') as file_out:
            while True:
                byte1 = file1.read(1)
                byte2 = file2.read(1)

                # If both files are out of bytes, break the loop
                if not byte1 and not byte2:
                    break
                
                # Write bytes if they exist
                if byte1:
                    file_out.write(byte1)
                if byte2:
                    file_out.write(byte2)

    except IOError as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python script.py <file1_path> <file2_path> <output_path>")
        sys.exit(1)
    
    file1_path = sys.argv[1]
    file2_path = sys.argv[2]
    output_path = sys.argv[3]
    
    interleave_files(file1_path, file2_path, output_path)
    print(f"Files interleaved successfully into {output_path}")
