#!/usr/bin/env python
"""
Test PoE-CAM images with horizontal flip + tagStandard52h13 detection.
Author: modeltapriltagcat gen-16
"""

import cv2
import numpy as np
from pupil_apriltags import Detector

# Test images — original and the cropped/enhanced variants
IMAGES = [
    r"C:\tmp\poecam.jpg",
    r"C:\tmp\poecam2.jpg",
    r"C:\tmp\poecam_crop.jpg",
    r"C:\tmp\poecam_crop_enhanced.jpg",
    r"C:\tmp\poecam_enhanced_2x.jpg",
    r"C:\tmp\poe_hand_tag_padded.png",
]

det = Detector(
    families='tagStandard52h13',
    nthreads=4,
    quad_decimate=1.0,
    quad_sigma=0.0,
    refine_edges=1,
    decode_sharpening=0.25,
    debug=0
)

# Also try 48h12 since that was the previous suspect
det_48 = Detector(
    families='tagCustom48h12',
    nthreads=4,
    quad_decimate=1.0,
    quad_sigma=0.0,
    refine_edges=1,
    decode_sharpening=0.25,
    debug=0
)

print("="*70)
print("PoE-CAM Mirror Detection Test")
print("  Families: tagStandard52h13, tagCustom48h12")
print("  Orientations: original, H-flip, V-flip, 180°")
print("="*70)

FLIPS = {
    "original":  None,
    "H-flip":    1,    # cv2.flip code: 1 = horizontal
    "V-flip":    0,    # 0 = vertical
    "180":       -1,   # -1 = both axes = 180° rotation
}

for img_path in IMAGES:
    img = cv2.imread(img_path)
    if img is None:
        print(f"\n  SKIP (can't read): {img_path}")
        continue

    h, w = img.shape[:2]
    print(f"\n--- {img_path} ({w}x{h}) ---")

    for flip_name, flip_code in FLIPS.items():
        if flip_code is not None:
            flipped = cv2.flip(img, flip_code)
        else:
            flipped = img

        gray = cv2.cvtColor(flipped, cv2.COLOR_BGR2GRAY)

        # Try at multiple scales
        for scale_label, target_w in [("native", None), ("2x", w*2), ("3x", w*3)]:
            if target_w and target_w > 8000:
                continue  # skip absurdly large
            if target_w:
                scale = target_w / w
                test_gray = cv2.resize(gray, (int(w*scale), int(h*scale)), interpolation=cv2.INTER_CUBIC)
            else:
                test_gray = gray

            th, tw = test_gray.shape[:2]

            # 52h13
            dets_52 = det.detect(test_gray, estimate_tag_pose=False)
            # 48h12
            dets_48 = det_48.detect(test_gray, estimate_tag_pose=False)

            all_dets = [(d, "52h13") for d in dets_52] + [(d, "48h12") for d in dets_48]

            if all_dets:
                for d, fam in all_dets:
                    print(f"  *** HIT *** {flip_name} {scale_label} ({tw}x{th}): "
                          f"{fam} id={d.tag_id} margin={d.decision_margin:.1f} "
                          f"center=({d.center[0]:.0f},{d.center[1]:.0f})")

print("\n" + "="*70)
print("Done.")
