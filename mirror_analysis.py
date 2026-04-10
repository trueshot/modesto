#!/usr/bin/env python
"""
Mirror analysis of tagStandard52h13 codebook.
Checks whether any valid codeword's horizontal or vertical mirror
matches another valid codeword (in any of 4 rotations).

Author: modeltapriltagcat gen-16
"""

import ctypes
import sys
from pupil_apriltags import Detector

def extract_codebook():
    """Extract all codewords and bit layout from the DLL."""
    det = Detector(families='tagStandard52h13', nthreads=1)
    fam_ptr = det.tag_families['tagStandard52h13']
    addr = ctypes.cast(fam_ptr, ctypes.c_void_p).value

    # Read struct fields
    ncodes = ctypes.c_uint32.from_address(addr).value
    codes_ptr = ctypes.c_uint64.from_address(addr + 8).value
    nbits = ctypes.c_uint32.from_address(addr + 28).value
    bit_x_ptr = ctypes.c_uint64.from_address(addr + 32).value
    bit_y_ptr = ctypes.c_uint64.from_address(addr + 40).value
    h = ctypes.c_uint32.from_address(addr + 48).value

    print(f"Family: tagStandard52h13")
    print(f"  ncodes={ncodes}, nbits={nbits}, h={h}")

    # Extract bit position arrays
    bit_x = []
    bit_y = []
    for i in range(nbits):
        bx = ctypes.c_uint32.from_address(bit_x_ptr + i * 4).value
        by = ctypes.c_uint32.from_address(bit_y_ptr + i * 4).value
        # These are signed — convert from uint32
        if bx > 0x7FFFFFFF:
            bx -= 0x100000000
        if by > 0x7FFFFFFF:
            by -= 0x100000000
        bit_x.append(bx)
        bit_y.append(by)

    # Extract all codewords
    codes = []
    for i in range(ncodes):
        code = ctypes.c_uint64.from_address(codes_ptr + i * 8).value
        codes.append(code)

    return codes, bit_x, bit_y, nbits, ncodes


def codeword_to_grid(code, bit_x, bit_y, nbits):
    """Convert a codeword to a 10x10 grid. grid[row][col] = 0 or 1."""
    grid = [[0]*10 for _ in range(10)]
    for i in range(nbits):
        col = bit_x[i] + 2  # offset to 0-based grid
        row = bit_y[i] + 2
        if 0 <= row < 10 and 0 <= col < 10:
            bit_val = (code >> (nbits - 1 - i)) & 1
            grid[row][col] = bit_val
    # Fill the 8x8 border (rows 1-8, cols 1-8 edges) as black
    for r in range(1, 9):
        for c in range(1, 9):
            if r == 1 or r == 8 or c == 1 or c == 8:
                grid[r][c] = 1
    # Fill the 6x6 white quiet zone (rows 2-7, cols 2-7 edges)
    for r in range(2, 8):
        for c in range(2, 8):
            if r == 2 or r == 7 or c == 2 or c == 7:
                grid[r][c] = 0
    return grid


def grid_to_codeword(grid, bit_x, bit_y, nbits):
    """Convert a 10x10 grid back to a codeword (reading data bit positions)."""
    code = 0
    for i in range(nbits):
        col = bit_x[i] + 2
        row = bit_y[i] + 2
        if 0 <= row < 10 and 0 <= col < 10:
            if grid[row][col]:
                code |= (1 << (nbits - 1 - i))
    return code


def mirror_h(grid):
    """Mirror grid horizontally (flip left-right)."""
    return [row[::-1] for row in grid]


def mirror_v(grid):
    """Mirror grid vertically (flip top-bottom)."""
    return grid[::-1]


def rotate_90(grid):
    """Rotate grid 90 degrees clockwise."""
    n = len(grid)
    return [[grid[n-1-j][i] for j in range(n)] for i in range(n)]


def all_rotations_code(grid, bit_x, bit_y, nbits):
    """Return set of codewords for all 4 rotations of a grid."""
    codes = set()
    g = grid
    for _ in range(4):
        codes.add(grid_to_codeword(g, bit_x, bit_y, nbits))
        g = rotate_90(g)
    return codes


def hamming_distance(a, b, nbits):
    return bin(a ^ b).count('1')


def main():
    print("Extracting codebook...")
    codes, bit_x, bit_y, nbits, ncodes = extract_codebook()

    # Build lookup: codeword -> tag_id (canonical, no rotations yet)
    # But we need to check all rotations. Build set of all valid codewords
    # across all rotations.
    print("Building rotation-expanded codebook...")
    code_to_id = {}
    for tag_id, code in enumerate(codes):
        grid = codeword_to_grid(code, bit_x, bit_y, nbits)
        for rot_code in all_rotations_code(grid, bit_x, bit_y, nbits):
            if rot_code not in code_to_id:
                code_to_id[rot_code] = tag_id

    print(f"  Canonical codes: {ncodes}")
    print(f"  With rotations: {len(code_to_id)} unique codewords")

    # Now check mirrors
    print("\nChecking horizontal mirrors...")
    h_mirror_hits = []
    print("\nChecking vertical mirrors...")
    v_mirror_hits = []

    for tag_id, code in enumerate(codes):
        grid = codeword_to_grid(code, bit_x, bit_y, nbits)

        # Horizontal mirror
        hm = mirror_h(grid)
        hm_codes = all_rotations_code(hm, bit_x, bit_y, nbits)
        for hmc in hm_codes:
            if hmc in code_to_id:
                match_id = code_to_id[hmc]
                if match_id != tag_id:
                    h_mirror_hits.append((tag_id, match_id))
                    break  # one match is enough

        # Vertical mirror
        vm = mirror_v(grid)
        vm_codes = all_rotations_code(vm, bit_x, bit_y, nbits)
        for vmc in vm_codes:
            if vmc in code_to_id:
                match_id = code_to_id[vmc]
                if match_id != tag_id:
                    v_mirror_hits.append((tag_id, match_id))
                    break

        if tag_id % 5000 == 0:
            print(f"  Checked {tag_id}/{ncodes}...")

    # Also check: does any tag mirror to ITSELF (same ID)?
    self_mirror_h = []
    self_mirror_v = []
    for tag_id, code in enumerate(codes):
        grid = codeword_to_grid(code, bit_x, bit_y, nbits)
        hm_codes = all_rotations_code(mirror_h(grid), bit_x, bit_y, nbits)
        vm_codes = all_rotations_code(mirror_v(grid), bit_x, bit_y, nbits)
        if code in hm_codes:
            self_mirror_h.append(tag_id)
        if code in vm_codes:
            self_mirror_v.append(tag_id)

    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)

    print(f"\nHorizontal mirror -> different valid ID: {len(h_mirror_hits)} pairs")
    if h_mirror_hits:
        for a, b in h_mirror_hits[:20]:
            print(f"  tag {a} <-> tag {b}")
        if len(h_mirror_hits) > 20:
            print(f"  ... and {len(h_mirror_hits) - 20} more")

    print(f"\nVertical mirror -> different valid ID: {len(v_mirror_hits)} pairs")
    if v_mirror_hits:
        for a, b in v_mirror_hits[:20]:
            print(f"  tag {a} <-> tag {b}")
        if len(v_mirror_hits) > 20:
            print(f"  ... and {len(v_mirror_hits) - 20} more")

    print(f"\nSelf-symmetric (H-mirror = self, any rotation): {len(self_mirror_h)} tags")
    if self_mirror_h:
        print(f"  First 20: {self_mirror_h[:20]}")

    print(f"Self-symmetric (V-mirror = self, any rotation): {len(self_mirror_v)} tags")
    if self_mirror_v:
        print(f"  First 20: {self_mirror_v[:20]}")

    # Check: mirror -> no valid match (closest Hamming distance)
    if not h_mirror_hits and not v_mirror_hits:
        print("\nNo mirror collisions found! Checking minimum Hamming distance from mirrors to codebook...")
        min_hd = nbits
        worst_tag = -1
        # Sample 500 tags for speed
        import random
        sample = random.sample(range(ncodes), min(500, ncodes))
        for tag_id in sample:
            grid = codeword_to_grid(codes[tag_id], bit_x, bit_y, nbits)
            hm_code = grid_to_codeword(mirror_h(grid), bit_x, bit_y, nbits)
            for other_code in codes:
                hd = hamming_distance(hm_code, other_code, nbits)
                if hd < min_hd:
                    min_hd = hd
                    worst_tag = tag_id
        print(f"  Min Hamming distance (mirror to nearest valid, sample of {len(sample)}): {min_hd}")
        print(f"  Worst case tag: {worst_tag}")

    print("\nDone.")


if __name__ == '__main__':
    main()
