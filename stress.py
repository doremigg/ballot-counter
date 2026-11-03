# -*- coding: utf-8 -*-
"""压力测试：生成 N 张变体票 + 坏图，跑批处理，测吞吐与稳定性。"""
import os
import sys
import time
import shutil

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import cv2
import numpy as np
import batch

N = 200
SRC = [f"real_scan/scan_{i:02d}.jpg" for i in range(1, 6)]
INDIR = "stress_in"
OUTDIR = "stress_out"

shutil.rmtree(INDIR, ignore_errors=True)
shutil.rmtree(OUTDIR, ignore_errors=True)
os.makedirs(INDIR, exist_ok=True)
os.makedirs(OUTDIR, exist_ok=True)

# 生成变体（微调一个像素改变哈希）
for i in range(N):
    img = cv2.imread(SRC[i % 5], cv2.IMREAD_GRAYSCALE)
    img[0, i % img.shape[1]] = 255 - (i % 120)
    cv2.imwrite(os.path.join(INDIR, f"s_{i:04d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])

# 坏图：随机噪声、全白
rng = np.random.default_rng(0)
for k, name in enumerate(["bad_noise.jpg", "bad_white.jpg"]):
    if name == "bad_noise.jpg":
        arr = rng.integers(0, 255, (3508, 2480), dtype=np.uint8)
    else:
        arr = np.full((3508, 2480), 255, np.uint8)
    cv2.imwrite(os.path.join(INDIR, name), arr, [cv2.IMWRITE_JPEG_QUALITY, 85])

b = batch.Batch(OUTDIR, os.path.join(OUTDIR, "scan"))
t = time.time()
b.run_folder(INDIR)
dt = time.time() - t

print("=" * 50)
print(f"处理 {N + 2} 张用时 {dt:.1f}s  平均 {dt / (N + 2) * 1000:.1f} ms/张")
print(f"→ 3000 张约 {dt / (N + 2) * 3000 / 60:.1f} 分钟（仅识别+存档，不含扫描）")
print("total:", len(b.results))
print("counts:", b.tally["counts"])
bad = [r for r in b.results if r["verdict"] == "待人工"]
print("待人工:", [(r["no"], r["reason"][:24]) for r in bad])
