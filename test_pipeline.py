# -*- coding: utf-8 -*-
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import recognizer
import rules

ballots = []
for i in range(1, 6):
    per = recognizer.analyze(f"real_scan/scan_{i:02d}.jpg")
    res = rules.judge(per)
    ballots.append((per, res))
    print(f"scan_{i:02d} 符号={[c['symbol'] for c in per]} 另选={[c['alternate'] for c in per]}")
    print(f"        -> {res['verdict']}  状态={res['states']}  {res['reason']}")

t = rules.tally(ballots)
print("\n=== 汇总 ===")
print("有效/无效/弃权:", t["counts"])
for name, v in t["candidates"].items():
    print(f"  {name}: 赞成 {v['赞成']} 反对 {v['反对']} 弃权 {v['弃权']}")
