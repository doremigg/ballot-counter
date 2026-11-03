# -*- coding: utf-8 -*-
import sys, time
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import cv2
import recognizer

# 预热
recognizer.analyze("real_scan/scan_01.jpg")

t = time.time()
N = 3
for _ in range(N):
    for i in range(1, 6):
        recognizer.analyze(f"real_scan/scan_{i:02d}.jpg")
dt = time.time() - t
print(f"识别 {N*5} 张用时 {dt:.2f}s  → 单张 {dt/(N*5)*1000:.1f} ms  → 3000 张约 {dt/(N*5)*3000/60:.1f} 分钟")

# 各阶段耗时
img = cv2.imread("real_scan/scan_01.jpg", cv2.IMREAD_GRAYSCALE)
t = time.time()
for _ in range(5):
    up = recognizer.upright("real_scan/scan_01.jpg")
print(f"upright: {(time.time()-t)/5*1000:.1f} ms")
t = time.time()
for _ in range(5):
    q = recognizer.find_quad(up)
print(f"find_quad: {(time.time()-t)/5*1000:.1f} ms")
t = time.time()
for _ in range(5):
    w = recognizer.warp_table(up, q)
print(f"warp: {(time.time()-t)/5*1000:.1f} ms")
