# 选票自动计票（Ballot Auto-Counter）

面向 Windows + 网络扫描仪的**本地**选票识别/计票工具：通过 eSCL（AirScan/Mopria）驱动扫描仪连续扫描，自动定位选票表格、透视矫正、识别符号栏与"另选他人"栏，按确定性规则计票，并保留完整审计材料（原图 + 预览图 + 结果 JSON + SHA-256）。

- 全程本地运行，不依赖云服务
- 扫描一张、处理一张，支持断点续扫
- 识别人工可复核：待人工票、人工改判、另选姓名补录
- 附带 Web 仪表盘与 CSV 导出

## 功能

| 能力 | 说明 |
|------|------|
| 连续扫描 | ADF（`Feeder`/`AdfSimplex`）自动出纸，支持任意叠数 |
| 表格矫正 | 四角检测 + 透视变换到固定 900×500，消除纸张倾斜/偏移 |
| 符号识别 | `○` 赞成、`√` 赞成、`×` 反对、空 弃权、涂实（删除涂抹）→ 弃权 |
| 另选他人 | 检测"另选他人姓名"栏手写姓名（写名），支持单独裁图 + 人工录入 |
| 规则计票 | 4 选 3：赞成 ≤ 3 有效；多投/异常 → 待人工 |
| 审计 | 原图、矫正预览、结果 JSON、SHA-256 去重 |
| 断点续扫 | 记录追加式保存，重启不丢、不重扫 |
| Web 界面 | 实时统计、逐票明细、票面弹窗、待人工审核、CSV 导出 |

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 修改扫描仪地址（见下方"配置"）
#    scanner.py 中 ScannerClient 的 base_url

# 3. 启动服务
python server.py
```

打开 http://127.0.0.1:8791/ ，放纸后点「扫描并计票」。

## 配置

### 扫描仪地址

`scanner.py`：

```python
def __init__(self, base_url: str = "http://192.168.31.15:80/eSCL", timeout: float = 30.0):
```

用 `GET http://<ip>/eSCL/ScannerCapabilities` 确认设备支持 eSCL。

### 选票版式（重要）

`recognizer.py` 顶部常量按实际选票标定：

```python
ROW_CENTERS = [120, 202, 285, 369]   # 4 位候选人在矫正图(900×500)中的行中心 y
SYM_X = (8, 228)                      # 符号栏 x 范围
ALT_X = (578, 892)                    # 另选他人栏 x 范围
ROW_H = 34                            # 行半高（切格）
INK_TH = 130                          # 墨迹阈值
```

换选票版式时，用一张标准票跑 `test_pipeline.py` 或看预览图重新标定这四个值。

## 识别流程

```
扫描(JPG) → 裁剪/旋转 → 四角检测 → 透视矫正(900×500)
          → 按固定行列切格 → 单格分类(空/○/√/×/涂实)
          → 另选栏"写名"检测 → 规则引擎判定 → 归档 + 计票
```

单格分类要点：
- 先按连通域取最大紧凑块（剔除细长表格线：长宽比 > 5 且很长）；
- 有孔洞 → `○`（含小圈/粗圈，避免把"小圈"误判成"涂实"）；
- 墨迹重心实心 → `×`（交叉点在重心附近）；
- 实心大块且无孔 → `涂实`（删除涂抹 → 弃权）；
- 其余 → `√`。

## 计票规则

- 赞成（○/√）≤ 3 → **有效**
- 赞成 > 3（多投） → **待人工**
- 赞成 = 0 → **弃权**（计入有效票）
- 涂实（删除涂抹）→ 该候选人**弃权**
- 符号栏空白、另选栏为 ○/×/√ → 以**另选栏符号**为准（看错表头）
- 符号 + 另选姓名 → 正常按符号判定，姓名单独记录
- **有效票 + 无效票 = 总票数**（弃权计入有效）

## 目录

```
.
├── scanner.py        # eSCL 客户端
├── recognizer.py     # 图像矫正 + 符号/写名识别
├── rules.py          # 确定性规则引擎
├── batch.py          # 批处理、归档、断点续扫、汇总
├── server.py         # FastAPI + 静态页
├── reprocess.py      # 按冻结上限重算（只动新票）
├── static/
│   ├── index.html    # 仪表盘
│   └── writeins.html # 另选他人姓名录入
├── test_pipeline.py  # 5 张标准票回归测试
├── bench.py          # 单张耗时基准
├── stress.py         # 3000 张压力测试
├── diag.py           # 设备/服务诊断
├── requirements.txt
└── HANDOFF.md        # 交接说明（部署与数据状态）
```

运行时数据（不入库）：

```
output/       results.jsonl / overrides.json / <id>.json / <id>_orig.jpg / <id>_preview.jpg / writeins/
real_scan/    扫描原图 scan_*.jpg
```

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/state` | 运行状态、汇总、日志 |
| GET | `/api/results?page&size` | 逐票明细（含最终判定） |
| GET | `/api/pending` | 待人工列表 |
| POST | `/api/scan` / `/api/process` / `/api/stop` | 扫描 / 处理已扫图 / 停止 |
| POST | `/api/review` | 人工复核（4 格符号） |
| POST | `/api/override?no&verdict` | 直接改判 |
| POST | `/api/unreview` | 撤销改判 |
| POST | `/api/writein` | 保存另选姓名 |
| GET | `/writeins` | 另选姓名录入页 |
| GET | `/api/export.csv` | 导出 CSV（UTF-8 BOM） |

## 已知限制

- 扫描仪 eSCL 实现差异较大（HP 常见：源名 `Feeder` 或 `AdfSimplex`、格式必须 `pwg:DocumentFormat`）；
- 手写姓名**不做 OCR**（离线 OCR 对手写中文不可靠），只检测"有写名"并裁图，姓名由人工录入；
- 识别对纸张摆放敏感：严重歪斜/折叠会进入"待人工"，不会静默算错；
- 表头/版式改变需要重新标定 `ROW_CENTERS` 等常量。

## 许可

MIT，见 [LICENSE](LICENSE)。
