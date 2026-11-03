# -*- coding: utf-8 -*-
"""
识别器（v7）：透视矫正归一化到 900×500（表格方正），再按固定行/列切格分类。
"""
import math
import cv2
import numpy as np

CANDIDATES = ["刘正先", "李校博", "杨宁", "范秋筠"]
INK_TH = 130

# 矫正图(900×500)标定：4 行候选人中心 y、符号列 x、另选列 x、半行高
ROW_CENTERS = [120, 202, 285, 369]
SYM_X = (8, 228)
ALT_X = (578, 892)
ROW_H = 34


def upright(scan_path, width=1000):
    img = cv2.imread(scan_path, cv2.IMREAD_GRAYSCALE)
    crop = img[180:1600, 900:1800]
    rot = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
    scale = width / rot.shape[1]
    return cv2.resize(rot, (width, int(rot.shape[0] * scale)), interpolation=cv2.INTER_AREA)


def _order_quad(pts):
    pts = pts.reshape(4, 2).astype(np.float32)
    s = pts.sum(axis=1)
    d = pts[:, 0] - pts[:, 1]
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmax(d)]
    bl = pts[np.argmin(d)]
    return np.array([tl, tr, br, bl], np.float32)


def find_quad(up):
    h_img, w_img = up.shape[:2]
    for th in (220, 190, 170, 150):
        ink = (up < th).astype(np.uint8) * 255
        ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        cnts, _ = cv2.findContours(ink, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        for c in cnts:
            area = cv2.contourArea(c)
            if area < 120000:
                continue
            approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
            if len(approx) == 4 and (best is None or area > best[0]):
                best = (area, approx.reshape(4, 2))
        if best is None:
            continue
        quad = _order_quad(best[1])
        # 只拒绝"四角都贴图像边缘"的整图伪框（背景太暗时会出现）
        near_border = (quad[:, 0].min() <= 3 and quad[:, 0].max() >= w_img - 4 and
                       quad[:, 1].min() <= 3 and quad[:, 1].max() >= h_img - 4)
        if not near_border:
            return quad
    return None


def warp_table(up, quad, size=(900, 500)):
    dst = np.array([[0, 0], [size[0] - 1, 0], [size[0] - 1, size[1] - 1], [0, size[1] - 1]], np.float32)
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(up, M, size, borderValue=255)


def detect_rows(w):
    """自适应检测候选行中心（用水平分隔线）。"""
    ink = (w < 225).astype(np.uint8)
    rows = ink.sum(axis=1)
    ys = np.where(rows > w.shape[1] * 0.55)[0]
    if len(ys) == 0:
        return ROW_CENTERS
    # 分组
    groups = [[ys[0]]]
    for y in ys[1:]:
        if y - groups[-1][-1] <= 8:
            groups[-1].append(y)
        else:
            groups.append([y])
    lines = [int(np.mean(g)) for g in groups]
    # 用相邻分隔线中点作为行中心；去掉最上和最下的边
    centers = [int((lines[i] + lines[i + 1]) / 2) for i in range(len(lines) - 1)]
    # 取 5 个候选带（header + 4 candidates）中后 4 个，若数量不够则回退
    cand = [c for c in centers if c > lines[0] + 10]
    if len(centers) >= 5:
        return centers[1:5]
    return ROW_CENTERS


def _line_intersection(x1, y1, x2, y2, x3, y3, x4, y4):
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-6:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / den
    return px, py


def _thin(img):
    """Zhang-Suen 细化，返回 1px 骨架。"""
    img = (np.asarray(img) > 0).astype(np.uint8)
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            P = np.pad(img, 1)
            p2 = P[0:-2, 1:-1]; p3 = P[0:-2, 2:]; p4 = P[1:-1, 2:]; p5 = P[2:, 2:]
            p6 = P[2:, 1:-1]; p7 = P[2:, 0:-2]; p8 = P[1:-1, 0:-2]; p9 = P[0:-2, 0:-2]
            B = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            seq = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            A = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8) for i in range(8))
            if step == 0:
                cond = (img == 1) & (B >= 2) & (B <= 6) & (A == 1) & ((p2 * p4 * p6) == 0) & ((p4 * p6 * p8) == 0)
            else:
                cond = (img == 1) & (B >= 2) & (B <= 6) & (A == 1) & ((p2 * p4 * p8) == 0) & ((p2 * p6 * p8) == 0)
            if cond.any():
                img = img.copy()
                img[cond] = 0
                changed = True
    return img


def _skeleton_ends(mask):
    sk = _thin(mask)
    if sk.sum() == 0:
        return 0
    P = np.pad(sk, 1)
    neigh = np.zeros(sk.shape, dtype=int)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            neigh += P[1 + dy:1 + dy + sk.shape[0], 1 + dx:1 + dx + sk.shape[1]]
    return int(((sk == 1) & (neigh == 1)).sum())


def _is_x(mask):
    """× 的交叉点就在墨迹重心处（实心）；○ 是空心环、√ 的重心落在 V 内部（空）。"""
    ys, xs = np.where(mask)
    if len(xs) < 20:
        return False
    cy, cx = int(ys.mean()), int(xs.mean())
    y0, y1 = max(0, cy - 2), min(mask.shape[0], cy + 3)
    x0, x1 = max(0, cx - 2), min(mask.shape[1], cx + 3)
    return float(mask[y0:y1, x0:x1].mean()) > 0.3


def classify_cell(cell):
    ink = (cell < INK_TH).astype(np.uint8)
    h, w = ink.shape
    n, lab, st, ce = cv2.connectedComponentsWithStats(ink, 8)
    best = None
    for i in range(1, n):
        x, y, cw, ch, area = st[i]
        if area < 40:
            continue
        # 剔除细长表格线：长宽比大且很长（粗线也能滤掉）
        if max(cw, ch) / max(1, min(cw, ch)) > 5 and max(cw, ch) > 0.5 * max(w, h):
            continue
        if best is None or area > best[0]:
            best = (area, x, y, cw, ch, i)
    if best is None:
        return {"label": "空", "fill": 0.0}
    area, x, y, cw, ch, idx = best
    mask = (lab == idx).astype(np.uint8)
    fill = float(mask[y:y + ch, x:x + cw].mean())

    # 先统计孔洞：小圈/粗圈也有孔 → 应算 ○（规则3：圈小误当涂实 → 赞同）
    cnts, hier = cv2.findContours(mask * 255, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    holes = sum(1 for hh in hier[0] if hh[3] != -1) if hier is not None else 0

    # 实心大墨块且无孔 → 涂实（删除涂抹 → 弃权）
    if fill > 0.6 and holes == 0:
        return {"label": "涂实", "fill": round(fill, 3)}
    # × 先于圆检测
    if _is_x(mask):
        return {"label": "×", "fill": round(fill, 3)}
    # 有孔 → ○（含小圈、粗圈）
    if holes >= 1:
        return {"label": "○", "fill": round(fill, 3)}

    roi = cell[y:y + ch, x:x + cw]
    minr = max(5, min(cw, ch) // 5)
    maxr = max(minr + 2, min(cw, ch) // 2)
    circles = cv2.HoughCircles(roi, cv2.HOUGH_GRADIENT, 1.2, max(8, min(cw, ch) // 2),
                               param1=100, param2=20, minRadius=minr, maxRadius=maxr)
    if circles is not None and len(circles[0]) >= 1:
        return {"label": "○", "fill": round(fill, 3)}

    ys, xs = np.where(mask)
    if len(xs) > 20:
        cy, cx = ys.mean(), xs.mean()
        d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
        ring = float(d.std() / (d.mean() + 1e-6))
        if ring < 0.45:
            return {"label": "○", "fill": round(fill, 3), "ring": round(ring, 3)}
    return {"label": "√", "fill": round(fill, 3)}


def _marks(w):
    out = []
    for i, yc in enumerate(ROW_CENTERS):
        sym = classify_cell(w[yc - ROW_H:yc + ROW_H, SYM_X[0]:SYM_X[1]])
        alt_cell = w[yc - ROW_H:yc + ROW_H, ALT_X[0]:ALT_X[1]]
        alt = classify_cell(alt_cell)
        # 另选他人栏：横跨大部分格宽、面积较大的墨迹判为"手写姓名"（而非符号）
        # 另选他人栏：剔除表格线后，横跨大部分格宽的墨迹判为"手写姓名"
        ink = (alt_cell < INK_TH).astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(ink, 8)
        sel = np.zeros_like(ink)
        for j in range(1, n):
            x, y, cw, ch, a = st[j]
            if a < 20:
                continue
            # 行分隔线：贴在单元格上下边缘的细长条
            if ch < 6 and (y <= 3 or y + ch >= alt_cell.shape[0] - 3):
                continue
            if max(cw, ch) / max(1, min(cw, ch)) > 8 and max(cw, ch) > 0.5 * max(alt_cell.shape):
                continue
            sel[lab == j] = 1
        ys, xs = np.where(sel)
        writein = False
        if len(xs) > 0:
            bw = int(xs.max() - xs.min() + 1)
            if bw > 0.35 * alt_cell.shape[1] and int(sel.sum()) > 150:
                writein = True
        out.append({"candidate": CANDIDATES[i] if i < 4 else f"候选{i+1}",
                    "symbol": sym["label"], "symbol_fill": sym["fill"],
                    "alternate": alt["label"], "alternate_fill": alt["fill"],
                    "writein": writein})
    return out


def analyze_checked(scan_path):
    """返回 (marks, quad_ok)。quad_ok=False 表示四角检测失败，建议人工复核。"""
    up = upright(scan_path)
    quad = find_quad(up)
    ok = quad is not None
    w = warp_table(up, quad) if ok else cv2.resize(up, (900, 500))
    return _marks(w), ok


def analyze(scan_path):
    return analyze_checked(scan_path)[0]
