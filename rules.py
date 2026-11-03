# -*- coding: utf-8 -*-
"""确定性规则引擎：把 4 位候选人的符号/另选标记 → 赞成/反对/弃权 + 有效性。"""
CANDIDATES = ["刘正先", "李校博", "杨宁", "范秋筠"]
APPROVE = {"○", "√"}
OPPOSE = {"×"}


def judge(per_candidate, human=False):
    """
    per_candidate: [{candidate, symbol, alternate, writein}, ...] 长度 4。
    返回 {verdict, reason, states(长度4: 赞成/反对/弃权), approve_idx}

    规则：
      1. 符号栏空白、另选栏为符号（○/×/√）→ 以另选栏符号为准（看错表头）。
      2. 符号 + 另选姓名 → 正常按符号判定，姓名单独记录。
      3. 涂实（删除涂抹）→ 该候选人弃权；小圈（有孔）在识别层已还原为 ○。
    """
    states = []
    for c in per_candidate:
        lbl = c["symbol"]
        if lbl == "空" and not c.get("writein"):
            alt = c.get("alternate", "空")
            if alt in APPROVE or alt in OPPOSE:
                lbl = alt
        if lbl in APPROVE:
            states.append("赞成")
        elif lbl in OPPOSE:
            states.append("反对")
        elif lbl in ("空", "涂实"):
            states.append("弃权")
        else:
            states.append("无效")

    if "无效" in states:
        if human:
            return {"verdict": "无效", "reason": "人工确认：存在无法识别的标记", "states": states}
        return {"verdict": "待人工", "reason": "存在无法识别的标记，需人工审核", "states": states}
    n_app = states.count("赞成")
    if n_app > 3:
        if human:
            return {"verdict": "无效", "reason": f"人工确认：多投（赞成 {n_app}）", "states": states}
        return {"verdict": "待人工", "reason": f"赞成数 {n_app} > 应选名额 3（多投），需人工审核", "states": states}
    if n_app == 0:
        return {"verdict": "弃权", "reason": "无任何赞成标记", "states": states}
    return {"verdict": "有效", "reason": f"赞成 {n_app} 人（≤3）", "states": states}


def tally(annotated_ballots):
    cand = {c: {"赞成": 0, "反对": 0, "弃权": 0} for c in CANDIDATES}
    counts = {"有效": 0, "无效": 0, "弃权": 0}
    details = []
    for i, (per, res) in enumerate(annotated_ballots, 1):
        counts[res["verdict"]] = counts.get(res["verdict"], 0) + 1
        if res["verdict"] == "有效":
            for c, st in zip(per, res["states"]):
                cand[c["candidate"]][st] += 1
        details.append({"no": i, "verdict": res["verdict"], "reason": res["reason"],
                        "states": res["states"],
                        "pattern": [c["symbol"] for c in per]})
    return {"candidates": cand, "counts": counts, "details": details}
