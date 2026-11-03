# -*- coding: utf-8 -*-
import sys, re, httpx
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

s = httpx.get("http://127.0.0.1:8791/api/state", timeout=10).json()
print("server: running", s["running"], "total", s["total"], "pending", s.get("pending_count"))
print("--- last logs ---")
for x in s["logs"][-6:]:
    print("  ", x)

xml = httpx.get("http://192.168.31.227:80/eSCL/ScannerStatus", timeout=10).text
print("printer state:", re.search(r"<pwg:State>([^<]+)<", xml).group(1))
print("adf:", re.search(r"<scan:AdfState>([^<]+)<", xml).group(1))
print("--- jobs ---")
for m in re.finditer(r"<scan:JobInfo>(.*?)</scan:JobInfo>", xml, re.S):
    b = m.group(1)
    u = re.search(r"<pwg:JobUri>([^<]+)<", b)
    st = re.search(r"<pwg:JobState>([^<]+)<", b)
    r = re.search(r"<pwg:JobStateReason>([^<]+)<", b)
    print("  ", u.group(1) if u else None, st.group(1) if st else None, r.group(1) if r else "")
