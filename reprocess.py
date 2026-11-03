# -*- coding: utf-8 -*-
"""按冻结上限重算：只重算票号 > frozen.max 的记录，已冻结的票一律不动。

用法：python reprocess.py [--all]
  --all  忽略冻结（谨慎，会改动已完成的票）
"""
import os
import sys
import json
import glob
import hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "output")
SCAN_DIR = os.path.join(BASE, "real_scan")

sys.path.insert(0, BASE)
import cv2
import recognizer
import rules


def frozen_max():
    try:
        return int(json.load(open(os.path.join(OUT, "frozen.json"), encoding="utf-8"))["max"])
    except Exception:
        return 0


def main():
    force = "--all" in sys.argv
    limit = 0 if force else frozen_max()
    rows = [json.loads(l) for l in open(os.path.join(OUT, "results.jsonl"), encoding="utf-8") if l.strip()]
    sha_map = {}
    for f in glob.glob(os.path.join(SCAN_DIR, "scan_*.jpg")):
        try:
            sha_map[hashlib.sha256(open(f, "rb").read()).hexdigest()] = f
        except Exception:
            pass

    changed = 0
    skipped = 0
    for r in rows:
        if r["no"] <= limit:
            skipped += 1
            continue
        f = sha_map.get(r["sha256"])
        if not f or cv2.imread(f, 0) is None:
            continue
        try:
            per, ok = recognizer.analyze_checked(f)
        except Exception:
            continue
        if not ok:
            res = {"verdict": "待人工", "reason": "表格四角检测失败", "states": ["待人工"] * 4}
        else:
            res = rules.judge(per)
        r["verdict"] = res["verdict"]
        r["reason"] = res["reason"]
        r["states"] = res["states"]
        r["pattern"] = [c["symbol"] for c in per]
        r["alternate"] = [c["alternate"] for c in per]
        changed += 1

    tmp = os.path.join(OUT, "results.jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, os.path.join(OUT, "results.jsonl"))
    print("frozen<=", limit, "skipped", skipped, "reprocessed", changed)


if __name__ == "__main__":
    main()
