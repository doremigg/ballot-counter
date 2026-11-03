# -*- coding: utf-8 -*-
"""选票自动计票 — 后端（批处理引擎 + 分页 + 导出 + 断点续扫）"""
import os
import sys
import csv
import io
import json
import threading

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import batch as batch_mod

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "output")
SCAN_DIR = os.path.join(BASE, "real_scan")
STATIC = os.path.join(BASE, "static")
os.makedirs(OUT, exist_ok=True)
os.makedirs(SCAN_DIR, exist_ok=True)

app = FastAPI()
app.mount("/files", StaticFiles(directory=OUT), name="files")
WRITEIN_DIR = os.path.join(OUT, "writeins")
os.makedirs(WRITEIN_DIR, exist_ok=True)
app.mount("/writeins_img", StaticFiles(directory=WRITEIN_DIR), name="writeins_img")

B = batch_mod.Batch(OUT, SCAN_DIR)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/state")
def state():
    with B.lock:
        pend = [r for r in B.results if B._effective(r)[0] == "待人工"]
        return {
            "running": B.running,
            "total": len(B.results),
            "tally": B.tally,
            "pending_count": len(pend),
            "last_img": B.last_img,
            "logs": B.logs[-30:],
            "recent": B.results[-20:],
        }


@app.get("/api/pending")
def pending(limit: int = 200):
    return {"rows": B.pending(limit)}


@app.post("/api/override")
def override(no: int, verdict: str):
    return B.override(no, verdict)


@app.post("/api/review")
def review(payload: dict):
    no = int(payload["no"])
    marks = payload["marks"]
    return B.review(no, marks)


@app.post("/api/unreview")
def unreview(no: int):
    return B.unreview(no)


@app.get("/writeins")
def writeins_page():
    return FileResponse(os.path.join(STATIC, "writeins.html"))


@app.get("/api/writeins")
def list_writeins():
    with B.lock:
        rows = [r for r in B.results if any(r.get("writein") or [])]
    return {"rows": rows, "total": len(rows)}


@app.post("/api/writein")
def set_writein(payload: dict):
    no = int(payload["no"])
    row = int(payload["row"])
    name = str(payload.get("name", "")).strip()
    with B.lock:
        rec = next((r for r in B.results if r["no"] == no), None)
        if rec is None:
            return {"ok": False, "msg": "票号不存在"}
        names = list(rec.get("writein_names") or ["", "", "", ""])
        while len(names) < 4:
            names.append("")
        names[row] = name
        rec["writein_names"] = names
        tmp = os.path.join(OUT, "results.jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in B.results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, os.path.join(OUT, "results.jsonl"))
    return {"ok": True, "no": no, "row": row, "name": name}


@app.get("/api/results")
def results(page: int = 1, size: int = 20):
    with B.lock:
        total = len(B.results)
        rev = list(reversed(B.results))   # 倒序：票号最大的在最前
        start = max(0, (page - 1) * size)
        rows = []
        for r in rev[start:start + size]:
            rr = dict(r)
            v, st = B._effective(r)        # 套用人工改判后的最终判定
            rr["verdict"] = v
            rr["states"] = st
            rows.append(rr)
    return {"page": page, "size": size, "total": total, "rows": rows}


@app.post("/api/scan")
def scan():
    if B.running:
        return {"ok": False, "msg": "正在运行"}
    threading.Thread(target=B.run_scan, daemon=True).start()
    return {"ok": True, "msg": "开始扫描并计票"}


@app.post("/api/process")
def process():
    if B.running:
        return {"ok": False, "msg": "正在运行"}
    threading.Thread(target=B.run_folder, args=(SCAN_DIR,), daemon=True).start()
    return {"ok": True, "msg": "开始处理已扫描图片"}


@app.post("/api/stop")
def stop():
    B.stop()
    return {"ok": True, "msg": "已请求停止（当前票处理完后停止）"}


@app.post("/api/reset")
def reset():
    if B.running:
        return {"ok": False, "msg": "请先停止"}
    with B.lock:
        B.results = []
        B.hashes = set()
        B.tally = None
        B.logs = []
        B.last_img = None
        open(B.jsonl, "w").close()   # 清空 JSONL 断点
    return {"ok": True, "msg": "已清空（归档图片与 JSON 保留）"}


@app.get("/api/export.csv")
def export_csv():
    with B.lock:
        rows = list(B.results)
        tally = B.tally
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["票号", "判定", "原因", "刘正先", "李校博", "杨宁", "范秋筠", "原始标记", "另选标记", "SHA256"])
    for r in rows:
        marks = r.get("pattern", [])
        alt = r.get("alternate", [])
        w.writerow([r["no"], r["verdict"], r["reason"],
                    *[marks[i] if i < len(marks) else "" for i in range(4)],
                    " ".join(marks), " ".join(alt), r.get("sha256", "")])
    if tally:
        w.writerow([])
        w.writerow(["候选人", "赞成", "反对", "弃权"])
        for name, v in tally["candidates"].items():
            w.writerow([name, v["赞成"], v["反对"], v["弃权"]])
        w.writerow([])
        w.writerow(["有效", tally["counts"].get("有效", 0), "无效", tally["counts"].get("无效", 0),
                    "弃权票", tally["counts"].get("弃权", 0), "待人工", tally["counts"].get("待人工", 0)])
    data = "\ufeff" + buf.getvalue()  # BOM for Excel
    return StreamingResponse(io.BytesIO(data.encode("utf-8")),
                             media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=ballot_tally.csv"})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8791, log_level="warning")
