# 阶段 C 证据归档：真实成片、审计、截图与费用说明

日期：2026-09-20
对应交接文档：`codex-handoff-delivery-2026-09.md` 第 9 节「阶段 C」第 5 条。

## 0. 这份文档做什么

把一次**真实 2 镜成片**的全部可复核证据集中到一处。原则是：**每一条结论都指向磁盘上一个文件，并给出字节数与 SHA-256**，让任何人能独立复算，而不是相信这里的叙述。无法确认的事情不写进结论，单独放在第 5 节。

所有路径相对 `VisionCraft/`。

---

## 1. 主证据：真实 2 镜成片

项目 `project_ee43a20710`，标题 `LIVE2SHOT-042935 春秋蝉鸣少年归`，源文本 `春秋蝉鸣少年归。`，模式 `live_strict`，真实联网（`real_network: true`）。

### 1.1 成片与下载件

| 文件 | 字节 | SHA-256（前 16 位） |
|---|---|---|
| `output/playwright/live-2shot/final-cut.mp4` | 1,589,233 | `26d061a09e1bfd31` |
| `output/playwright/live-2shot/downloaded-final.mp4` | 1,589,233 | `26d061a09e1bfd31` |

完整哈希：`26d061a09e1bfd31bf924be87ae891c14bfb4113d6bdb34ebea0f05c82ee9906`

**下载件与本地合成件是同一个文件**：字节数相同，SHA-256 逐位相同，差异 0 字节。这排除了「预览看到的是本地文件、下载下来的其实是另一样东西」这类问题。

### 1.2 ffprobe 实测

我用本机 `ffprobe`（`../.tools/ffmpeg/bin/ffprobe.EXE`）对成片独立复测，结果与应用自身在运行时的探测记录一致：

| 项 | 值 |
|---|---|
| 容器 | `mov,mp4,m4a,3gp,3g2,mj2` |
| 视频编码 | h264 |
| 分辨率 | 1280 × 720 |
| 帧率 | 24/1（恒定） |
| 帧数 | 215 |
| 时长 | 8.958333 s |
| 像素格式 | yuv420p |
| 码率 | 1,419,222 bit/s |
| 音轨 | **无** |
| 编码器 | Lavc63.8.101 libx264 |

原始记录见 `output/playwright/live-2shot/live_run_ffprobe.json` 与 `result.json` 的 `ffprobe` 字段。

**关于「无音轨」——这是默认配置下的既定行为，不是缺陷。** 依据是代码而非推测：`backend/services/video_service.py` 的 `_assembly_note()` 明确写着「未启用背景音频、原声和字幕时，行为与 P6-C 一致：只拼接视频流并使用 `-an`」，而设置项 `keep_source_audio` 的默认值是 `False`（`DEFAULT_ASSEMBLY_SETTINGS`）。本次运行未开启任何音频选项，所以按设计输出纯视频流。同一文件里另有 `_has_audio_stream()` 逐镜探测源音轨、并统计 `source_audio_shot_count` 的能力，说明「保留原声」是可选项而非缺失能力。

### 1.3 审计与血缘

12 个阶段全部 `PASS`（`output/playwright/live-2shot/result.json` 与 `live_run_audit.json`）：

`create_project` → `adaptation` → `bible` → `storyboard` → `confirm_storyboard` → `upload_first_frames` → `vision_review` → `video_shot_1` → `video_shot_2` → `assembly` → `preview` → `download`

血缘与计数（`live_run_lineage.json`）：

| 项 | 值 |
|---|---|
| 镜头 | 2（`shot_70cd409906` / `shot_50bb28248f`，均 `video_ready`） |
| Provider / 模型 / 模式 | minimax / MiniMax-H3 / i2v，每镜 4 s |
| 远程任务 | 2 个，均 `completed` / `succeeded` |
| 唯一远程任务 | 2 |
| `duplicate_remote_groups` / `duplicate_assets` / `duplicate_submits` | 0 / 0 / 0 |
| `live_text_call_count` / `live_vision_call_count` / `live_video_call_count` | 3 / 1 / 2 |
| `secret_leak` | false |
| `protected_untouched` | true（跑之前就存在的 11 个项目未被改动） |
| `cleanup_verified` | true |

文本/视觉走 `deepseek / deepseek-v4-flash`（视觉为 `deepseek-v4-flash-vision-exp`），视频走 `minimax / MiniMax-H3 I2V 768P 4s`。

**注意**：项目在取证完成后已被清理退役（这正是 `cleanup_verified: true` 的含义），所以**无法再回到网页界面里打开这个项目**。可复核的东西是本节列出的文件，不是界面状态。

### 1.4 截图

`output/playwright/live-2shot/` 下 11 张 1440 宽流程截图，另有 1 张侧栏局部图，逐张 SHA-256 记录在 `browser_screenshot_hashes.json`：

| 文件 | 覆盖环节 |
|---|---|
| `00-create-form-1440.png` | 新建表单 |
| `01-created-1440.png` | 项目创建完成 |
| `02-adaptation-1440.png` | 改编方案 |
| `03-bible-1440.png` | 故事圣经 |
| `04-storyboard-1440.png` | 分镜表 |
| `05-first-frames-1440.png` | 首帧上传/登记 |
| `06-vision-1440.png` | 视觉检查 |
| `07-video-shot1-1440.png` | 镜头 1 视频就绪 |
| `08-video-shot2-1440.png` | 镜头 2 视频就绪 |
| `09-assembly-1440.png` | 合成完成 |
| `10-export-1440.png` | 导出/下载 |

### 1.5 费用说明

| 项 | 值 | 来源 |
|---|---|---|
| 本次授权额度 `budget_cny` | 8.0 | `result.json` |
| 本地估算 `local_estimate_cny` | **5.4395** | `result.json` / `live_run_audit.json` |
| 其中文本 3 次 | 0.2097 | `local_estimate_breakdown` |
| 其中视觉 1 次 | 0.0297 | 同上 |
| 其中视频 2 次提交（原始） | 4.0 | 同上 |
| 其中视频按 30% 缓冲后 | 5.2 | 同上 |
| **平台实际扣费** | **无法确认** | `cost_visibility: "无法确认"` |

三点必须说清楚：

1. **5.4395 元是本地估算，不是账单。** 平台实际扣费本地拿不到（`platform_cost: "无法确认"`），所以任何地方都不应把 5.4395 说成「实际花了多少」。
2. **估算含 30% 缓冲**，所以它高于裸算（0.2097 + 0.0297 + 4.0 = 4.2394）。这也是「缓冲后的 5.4395 超过默认额度 5.0」却没被拦下的原因：本次运行显式把额度放宽到了 8 元，而不是护栏失守。判定之前我核对过原始记录，`budget_cny: 8`。
3. 交接文档排期表里「阶段 B：本地估算 5.4395 元」与此处一致。

---

## 2. 补充真实运行（不作为本次主证据）

### 2.1 5 镜多镜头（`output/playwright/live-multishot/`）

`final-cut.mp4`：4,850,116 字节，SHA-256 `1b6253ace9fb32f1…`，h264 1280×720 24 fps、536 帧、22.333333 s、无音轨。

血缘：5 个唯一远程任务（续跑时新提交 4 次 + 复用 1 次），`duplicate_submits` 与 `duplicate_assets` 均为 0。

**两个必须注明的限定**：该目录的 `live_run_audit.json` 标着 `reconstructed_after_cleanup: true`、`real_network_this_phase: false`、`cost_cny_this_phase: 0`。也就是说这份审计是**清理之后重建**的，`0` 指的是「重建这个动作本身没联网、没花钱」，**不代表当时生成成片没花钱**。同时 `db_available: false`，数据库已不在。

### 2.2 断点闭环（`output/playwright/live-closed-loop/`）

`result.json` 记录：1 次视频提交，浏览器闭环 `PASS`，但 `ffmpeg_assembly` 与 `assembly_preview_download` 都是 **SKIP**，理由是「镜头 02 尚未生成视频，请先完成该镜头的视频生成」。目录里保留了 `99-failure.png`。

这条 SKIP 是**有价值的正面证据**：它证明「没凑齐镜头就不合成」的守卫确实生效，而不是把半成品当成功。它的 provider 回传素材 `media/asset_3fc79c1aaa.mp4`（973,553 字节，SHA-256 `3e0ea7e268236c1f…`）是 1024×768、4.458 s，**带 AAC 双声道音轨**——说明链路并非一律剥离音轨，音轨的有无取决于该次生成与合成设置。

该次运行的界面提示记录为 `首帧已选 · 无尾帧 · 无参考图`，可作为「尾帧槽位在界面上存在但未走通真实验收」的直接落地形态。

---

## 3. 数据库备份与操作审计

`output/playwright/live-2shot/` 下 10 份 SQLite 快照，每份均为 757,760 字节：

`db-backup-20260920-121231` / `db-backup-20260920-122539` / `db-degraded-attempt-20260920-1217` / `db-pre-cleanup-20260920-121750` / `db-pre-cleanup-20260920-122855` / `db-pre-cleanup-20260920-122856` / `db-pre-restore-20260920-121610` / `db-pre-restore-20260920-121639` / `db-pre-restore-20260920-121654` / `db-pre-restore-20260920-121715`

另有一份 `output/playwright/stageC/db-before-stageC-20260920-124737.db`（同尺寸）。

各阶段操作报告（同目录）：`resume_report.json`、`cleanup_report.json`、`restore_report.json`、`retire_report.json`、`create_guard_report.json`、`db_snapshot_after_fail.json`、`browser_evidence.json`、`browser_dom_snapshots.json`、`browser_screenshot_hashes.json`。

另有三个失败/中止运行的归档目录，保留作为负面对照：`archive-2026-09-20-failed-2shot/`、`archive-2026-09-20-stageB-aborted/`、`archive-2026-09-20-stageB-guard-abort/`。

---

## 4. 如何自己复核

```powershell
# 1) 成片参数（本机 ffmpeg 在上一级 .tools 目录）
..\.tools\ffmpeg\bin\ffprobe.exe -v error -print_format json -show_format -show_streams output\playwright\live-2shot\final-cut.mp4

# 2) 下载件与本地件是否同一份（Windows）
certutil -hashfile output\playwright\live-2shot\final-cut.mp4 SHA256
certutil -hashfile output\playwright\live-2shot\downloaded-final.mp4 SHA256

# 3) 看这件事本身有无费用
.venv\Scripts\python.exe tools\run_no_cost_regression.py
```

---

## 5. 已知空白与未归因项

以下四条是**没有解决**或**无法从现有证据解决**的，列出来而不是掩盖：

1. **平台实际扣费未知。** 只有本地估算 5.4395 元，`cost_visibility` 与 `platform_cost` 均为「无法确认」。任何以「实际花费」口径引用该数字的说法都是错的。
2. **2 镜成片无音轨，归因只做到一半。** 「合成默认用 `-an`」有代码依据；但**源片段本身是否带音轨，已经无法回查**——那两个源 MP4 随项目清理一并删除了。因此不能断言「是合成丢掉了音轨」。
3. **首尾帧模式仍未真实验收。** 它是工作区根目录 `../task_plan.md` 的 Phase 3 唯一未勾选项，已单独排期（见交接文档第 9 节），属付费项，需逐次授权。
4. **一次性诊断探针已清掉。** `output/playwright/stageC/_probe_run.py` 与本次归档用的临时探测脚本（`tmp/_ffprobe_probe.py`、`tmp/_ffprobe_probe.json`）在写成本文后已删除，不属于交付物。
5. **`live-2shot/create_guard_report.json` 曾被无费用回归覆盖过（已修）。** `tools/test_live_2shot_create_guard.cjs` 原先**无条件**写 `output/playwright/live-2shot/`，因此 2026-09-20 14:23 那次无费用回归把 `create_guard_report.json`、`create_guard_backend.log` 等三个文件覆盖成了"离线回归"的内容，而且从**文件名上完全看不出被覆盖**。现改为：有 `VISIONCRAFT_DATA_DIR`（即回归运行）时写进运行目录，只有人工单独执行才写归档。**影响面已核实是可控的**：这三个文件不在 `browser_screenshot_hashes.json` 的哈希清单内（清单只覆盖 11 张 `NN-*.png`），因此没有连带破坏截图证据的一致性；但本归档中的真实 `create_guard` 证据文本已不可复原，引用时须知。
