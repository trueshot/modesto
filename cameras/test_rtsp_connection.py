#!/usr/bin/env python
"""
Quick test to debug RTSP connection issues
"""
import cv2
import sys

rtsp_url = "rtsp://admin:@192.168.0.165:554/ch07/0"

print(f"Testing RTSP connection...")
print(f"URL: {rtsp_url}")
print()

# Try to connect
print("Opening stream with cv2.CAP_FFMPEG...")
cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)

if not cap.isOpened():
    print("ERROR: Could not open camera stream")
    sys.exit(1)

print("Stream opened successfully!")

# Try to read a frame
print("Reading first frame...")
ret, frame = cap.read()

if not ret or frame is None:
    print("ERROR: Could not read frame from stream")
    cap.release()
    sys.exit(1)

h, w = frame.shape[:2]
print(f"SUCCESS! Got frame: {w}x{h}")
print()

# Show the frame
print("Displaying frame in window...")
print("Press any key to close")
cv2.imshow("RTSP Test - Press any key to close", frame)
cv2.waitKey(0)
cv2.destroyAllWindows()
cap.release()

print("Test complete!")
