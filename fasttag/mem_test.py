#!/usr/bin/env python3
"""Memory diagnostic: test cv2 vs detector separately."""
import sys
import time
import cv2
import os

# Just import — don't create yet
print(f"After imports: PID {os.getpid()}")
print("Check memory now, then press Enter...")
input()

# Test 1: VideoCapture only
url = sys.argv[1] if len(sys.argv) > 1 else "rtsp://admin:@192.168.0.105:554/Streaming/Channels/101"
print(f"Opening {url}...")
cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    print("Failed to open")
    sys.exit(1)

print("Grabbing 30 frames (no decode)...")
for _ in range(30):
    cap.grab()
print("After 30 grabs: check memory, press Enter...")
input()

print("Retrieving 10 frames...")
for _ in range(10):
    ret, frame = cap.retrieve()
    if ret:
        print(f"  Frame shape: {frame.shape}, {frame.nbytes/1e6:.1f}MB")
print("After retrieves: check memory, press Enter...")
input()

# Test 2: Add detector
print("Creating Detector...")
from pupil_apriltags import Detector
detector = Detector(families="tagCustom48h12", nthreads=4, quad_decimate=2.0)
print("Detector created: check memory, press Enter...")
input()

print("Running 10 detections...")
for i in range(10):
    cap.grab()
    ret, frame = cap.retrieve()
    if ret:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        dets = detector.detect(gray)
        print(f"  {i}: {len(dets)} tags")
print("After detections: check memory, press Enter...")
input()

cap.release()
print("Done")
