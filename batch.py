# -*- coding: utf-8 -*-
"""
批处理引擎（v2，面向 3000+ 张）：
- 追加式 JSONL 持久化：每票一行，避免全量重写，崩溃后按行恢复
- 哈希去重、四角检测失败→待人工、磁盘空间检查、单票异常不中断
- 扫描循环：ADF 空自动等待补纸、自动开新批次
"""
import os
import json
import time
import shutil
import hashlib
import threading
from datetime import datetime

import cv2
import recognizer
import rules
import scanner

CAND_LABELS = rules.CANDIDATES
MIN_FREE_BYTES = 500 * 1024 * 1024   # 低于 500MB 告警


class Batch:
    def __init__(self, out_dir, scan_dir):
        self.out = out_dir
        self.scan_dir = scan_dir
        os.makedirs(self.out, exist_ok=True)
        os.makedirs(self.scan_dir, exist_ok=True)
        self.jsonl = os.path.join(self.out, "results.jsonl")
        self.overrides_path = os.path.join(self.out, "overrides.json")
        self.lock = threading.Lock()
        self.results = []
        self.hashes = set()
        self.tally = None
        self.running = False
        self.stop_flag = False
        self.logs = []
        self.last_img = None
        self.overrides = {}
        self._load()

    # ---------- 持久化 ----------
    def _load(self):
        if not os.path.exists(self.jsonl):
            return
        with open(self.jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                self.results.append(m)
                if m.get("sha256"):
                    self.hashes.add(m["sha256"])
        if os.path.exists(self.overrides_path):
            try:
                self.overrides = json.load(open(self.overrides_path, encoding="utf-8"))
            except Exception:
                self.overrides = {}
        self._recompute()
        if self.results:
            self.last_img = self.results[-1].get("preview") or None

    def _append(self, meta):
        with open(self.jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.logs.append(f"[{ts}] {msg}")
            self.logs = self.logs[-300:]
        print(f"[{ts}] {msg}", flush=True)

    def _disk_ok(self):
        try:
            return shutil.disk_usage(self.out).free > MIN_FREE_BYTES
        except Exception:
            return True

    # ---------- 单票处理 ----------
    def process_one(self, path):
        if not self._disk_ok():
            self.log("磁盘剩余空间不足 500MB，暂停处理")
            raise RuntimeError("磁盘空间不足")

        try:
            sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
        except Exception:
            return {"skipped": True, "reason": "读取失败"}

        with self.lock:
            if sha in self.hashes:
                return {"skipped": True, "reason": "重复票"}
            no = max((m.get("no", 0) for m in self.results), default=0) + 1

        try:
            per, quad_ok = recognizer.analyze_checked(path)
            if not quad_ok:
                res = {"verdict": "待人工", "reason": "表格四角检测失败（倾斜过大/遮挡/残缺），需人工复核",
                       "states": ["待人工"] * 4}
            else:
                res = rules.judge(per)
        except Exception as e:
            per = [{"candidate": c, "symbol": "空", "alternate": "空"} for c in CAND_LABELS]
            res = {"verdict": "待人工", "reason": f"识别异常: {type(e).__name__}: {e}",
                   "states": ["待人工"] * 4}

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        oid = f"{ts}_{no:05d}"
        orig_name = f"{oid}_orig.jpg"
        try:
            shutil.copyfile(path, os.path.join(self.out, orig_name))
            orig_ok = True
        except Exception:
            orig_ok = False

        preview_name = f"{oid}_preview.jpg"
        preview_ok = True
        try:
            self._make_preview(path, os.path.join(self.out, preview_name))
        except Exception:
            preview_ok = False

        meta = {"no": no, "id": oid, "verdict": res["verdict"], "reason": res["reason"],
                "states": res["states"], "pattern": [c["symbol"] for c in per],
                "alternate": [c["alternate"] for c in per],
                "writein": [bool(c.get("writein")) for c in per], "sha256": sha,
                "orig": f"/files/{orig_name}" if orig_ok else "",
                "preview": f"/files/{preview_name}" if preview_ok else "",
                "time": datetime.now().isoformat()}
        with open(os.path.join(self.out, f"{oid}.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        with self.lock:
            self.results.append(meta)
            self.hashes.add(sha)
            self._append(meta)
            self._recompute()
            self.last_img = meta["preview"] if preview_ok else None
        return meta

    def _make_preview(self, path, out_path):
        up = recognizer.upright(path)
        quad = recognizer.find_quad(up)
        w = recognizer.warp_table(up, quad) if quad is not None else cv2.resize(up, (900, 500))
        bgr = cv2.cvtColor(w, cv2.COLOR_GRAY2BGR)
        for yc in recognizer.ROW_CENTERS:
            cv2.rectangle(bgr, (recognizer.SYM_X[0], yc - recognizer.ROW_H),
                          (recognizer.SYM_X[1], yc + recognizer.ROW_H), (0, 160, 0), 2)
            cv2.rectangle(bgr, (recognizer.ALT_X[0], yc - recognizer.ROW_H),
                          (recognizer.ALT_X[1], yc + recognizer.ROW_H), (0, 0, 200), 1)
        cv2.imwrite(out_path, bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])

    def _recompute(self):
        cand = {c: {"赞成": 0, "反对": 0, "弃权": 0} for c in CAND_LABELS}
        counts = {"有效": 0, "无效": 0, "弃权": 0, "待人工": 0}
        writein_ballots = 0
        for r in self.results:
            v, states = self._effective(r)
            counts[v] = counts.get(v, 0) + 1
            if v == "弃权":
                counts["有效"] += 1   # 弃权票计入有效票
            if v in ("有效", "弃权"):
                for i, st in enumerate(states):
                    if i < len(CAND_LABELS) and st in ("赞成", "反对", "弃权"):
                        cand[CAND_LABELS[i]][st] += 1
                if any(r.get("writein") or []):
                    writein_ballots += 1
        self.tally = {"candidates": cand, "counts": counts, "writein_ballots": writein_ballots}

    def _effective(self, r):
        """返回 (最终判定, 四格状态)，应用人工改判。"""
        ov = self.overrides.get(str(r["no"]))
        if isinstance(ov, dict):
            return ov.get("verdict", r["verdict"]), ov.get("states", r["states"])
        if isinstance(ov, str):
            return ov, r["states"]
        return r["verdict"], r["states"]

    def _save_overrides(self):
        with open(self.overrides_path, "w", encoding="utf-8") as f:
            json.dump(self.overrides, f, ensure_ascii=False)

    def override(self, no, verdict):
        """直接强制改判。"""
        with self.lock:
            self.overrides[str(no)] = {"verdict": verdict}
            self._save_overrides()
            self._recompute()
        return {"ok": True, "no": no, "verdict": verdict}

    def review(self, no, marks):
        """人工复核：按修正后的 4 格标记重新判定（human 模式 → 涂实/多投直接判无效）。"""
        per = [{"candidate": CAND_LABELS[i], "symbol": marks[i], "alternate": "空"} for i in range(4)]
        res = rules.judge(per, human=True)
        with self.lock:
            self.overrides[str(no)] = {"verdict": res["verdict"], "states": res["states"], "marks": marks}
            self._save_overrides()
            self._recompute()
        return {"ok": True, "no": no, "verdict": res["verdict"], "states": res["states"]}

    def pending(self, limit=200):
        """返回待人工审核的票。"""
        with self.lock:
            return [r for r in self.results if self._effective(r)[0] == "待人工"][:limit]

    def unreview(self, no):
        """撤销人工改判，恢复自动判定。"""
        with self.lock:
            self.overrides.pop(str(no), None)
            self._save_overrides()
            self._recompute()
        return {"ok": True, "no": no}

    # ---------- 批量入口 ----------
    def run_folder(self, folder, limit=None):
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith((".jpg", ".png", ".jpeg")))
        if limit:
            files = files[:limit]
        self.running = True
        self.stop_flag = False
        done = 0
        try:
            for f in files:
                if self.stop_flag:
                    break
                r = self.process_one(os.path.join(folder, f))
                if not r.get("skipped"):
                    done += 1
                    if done % 20 == 0:
                        self.log(f"已处理 {len(self.results)} 票")
        finally:
            self.running = False
            self.log(f"批量处理结束（本次新增 {done} 票）")

    def run_scan(self):
        """持续扫描：ADF 有多少扫多少（每批数量随机），扫完自动等补纸再开新批。"""
        self.running = True
        self.stop_flag = False
        client = scanner.ScannerClient()
        # 续编文件号，避免重启后覆盖已有扫描图
        existing = [f for f in os.listdir(self.scan_dir) if f.startswith("scan_") and f.endswith(".jpg")]
        page_total = len(existing)
        batch_no = 0
        try:
            while not self.stop_flag:
                st = client.scanner_status()
                if st.get("state") == "Stopped":
                    self.log("⚠ 设备报告 Stopped（卡纸/盖板/面板错误），请检查打印机；恢复后会自动继续")
                    time.sleep(5)
                    continue
                adf = st.get("adf_state")
                if adf != "ScannerAdfLoaded":
                    if adf == "ScannerAdfProcessing":
                        time.sleep(0.5)
                    else:
                        self.log("ADF 无纸，等待放入选票…（放入后自动开始）")
                        time.sleep(2)
                    continue

                # 让操作员把纸放稳，再开扫
                time.sleep(1.0)
                uri = None
                for src in ("AdfSimplex", "Feeder"):
                    try:
                        uri = client.create_job(source=src)
                        self.log(f"输入源确认：{src}")
                        break
                    except Exception:
                        uri = None
                if uri is None:
                    self.log("创建作业失败（尝试 AdfSimplex / Feeder 均失败）")
                    time.sleep(1.5)
                    continue

                batch_no += 1
                self.log(f"第 {batch_no} 批开始扫描…")
                n = 0
                while not self.stop_flag:
                    try:
                        data = client.next_document(uri)
                    except Exception as e:
                        if "503" in str(e) or "忙" in str(e):
                            time.sleep(0.4)
                            continue
                        self.log(f"取页结束: {e}")
                        break
                    if data is None:
                        break
                    n += 1
                    page_total += 1
                    p = os.path.join(self.scan_dir, f"scan_{page_total:05d}.jpg")
                    with open(p, "wb") as f:
                        f.write(data)
                    try:
                        meta = self.process_one(p)
                    except RuntimeError as e:
                        self.log(f"暂停: {e}")
                        time.sleep(5)
                        continue
                    if meta.get("verdict"):
                        self.log(f"[第{batch_no}批 #{meta['no']}] {meta['verdict']} | {meta['states']}")
                # 批次结束/被停止时主动取消作业，释放扫描仪（避免设备端残留 Processing 卡住后续作业）
                try:
                    client.cancel_job(uri)
                except Exception:
                    pass
                self.log(f"第 {batch_no} 批完成：本批 {n} 张，累计 {page_total} 张，请补充选票")
        finally:
            try:
                client.close()
            except Exception:
                pass
            self.running = False
            self.log("扫描会话结束")

    def stop(self):
        self.stop_flag = True
