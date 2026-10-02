# 真实多模型运行审计（P7-B）

本页收口第三次 5 镜头真实前端成片测试的证据口径，并规定**下次**真实测试必须在清理前保存脱敏审计报告。

本切片**不发送真实 API，费用 0 元**。不得把上一次真实测试重新算成本次调用。

## 1. 状态词

| 词 | 含义 |
|---|---|
| `PASS` | 真实执行并通过 |
| `FAIL` | 实际失败 |
| `SKIP` | 没有执行 |
| `BLOCKED_BEFORE_CALL` | 发送请求前被护栏阻止 |

## 2. 计数口径（禁止混用）

| 字段 | 含义 |
|---|---|
| `text_calls_total` | DeepSeek 文本真实调用次数 |
| `vision_calls_total` | DeepSeek Vision 真实调用次数 |
| `video_submits_new` | 本次新提交任务 |
| `preexisting_remote_tasks` | 中断前已有任务 |
| `video_tasks_reused` | 复用任务（只回查，不重新提交） |
| `unique_remote_tasks` | 唯一远程任务 |
| `remote_tasks_completed` | 已完成的远程任务数 |
| `downloaded_videos` | 实际落库的镜头视频数 |
| `duplicate_submits` | 同一镜头重复提交数 |
| `duplicate_assets` | 同一远程任务重复视频资产数 |
| `ffmpeg_ran` / `final_cut` / `preview_ok` / `download_ok` / `cleanup_verified` | 成片与清理布尔结果 |

关系：

```text
本次新提交任务 + 复用任务 = 唯一远程任务
```

不要把复用任务重复算作新的 API 调用。不要把「4 次新提交」写成「5 次新提交」。5 是唯一远程任务数。

下次真实测试清理前必须写入：

```text
output/playwright/live-multishot/live_run_audit.json
output/playwright/live-multishot/live_run_lineage.json
output/playwright/live-multishot/live_run_ffprobe.json
output/playwright/live-multishot/browser_evidence.json
output/playwright/live-multishot/browser_dom_snapshots.json
output/playwright/live-multishot/browser_screenshot_hashes.json
```

## 3. 第三次真实 5 镜测试（已完成，不重跑）

项目 `project_9ab7c27740`（文本：春秋蝉鸣少年归）。Playwright 在镜头 1 已提交后断开，续跑只回查原 `remote_task_id`。

| 项 | 值 |
|---|---|
| 真实网络请求（当时） | 是 |
| DeepSeek 文本真实调用 | 3 |
| DeepSeek Vision 真实调用 | 1 |
| MiniMax 新提交 | **4**（镜头 2～5，续跑时提交） |
| MiniMax 复用任务 | **1**（镜头 1，中断前已提交） |
| MiniMax 唯一远程任务 | **5** |
| 完成远程任务 | 5 |
| 重复提交 | 0 |
| 重复资产 | 0 |
| FFmpeg / ffprobe | 成片约 22.3 秒、1280×720、h264 |
| 本阶段是否重跑 | **否** |
| 本阶段费用 | **0 元** |

临时项目已按规则精确清理。**数据库复核证据不能事后查询。** 新版本测试必须在清理前写入：

```text
output/playwright/live-multishot/live_run_audit.json
output/playwright/live-multishot/live_run_lineage.json
output/playwright/live-multishot/live_run_ffprobe.json
output/playwright/live-multishot/browser_evidence.json
output/playwright/live-multishot/browser_dom_snapshots.json
output/playwright/live-multishot/browser_screenshot_hashes.json
```

上述文件与截图、`result.json`、`final-cut.mp4` 均在 `output/`，**不得提交 Git**。

审计允许：项目 / 镜头 ID、Provider、模型、模式、时长、分辨率、version_id、脱敏 remote_task_id、本地路径、调用次数、新提交/复用/唯一任务、ffprobe 摘要。

审计禁止：API Key、Authorization、完整 Data URL / Base64 / 签名 URL、完整 API 响应、不必要的完整 Prompt。

## 4. 清理前验证清单

清理临时项目之前必须为真：

```text
5 个 shots
5 个唯一 remote_task_id
5 个 video_tasks
5 个逻辑视频资产
1 个 final-video
0 个 duplicate_remote_groups
0 个 duplicate_assets
0 个 secret_leak
```

清理后确认：临时项目目录与数据库记录不存在；`v1demo_main` 仍在；`.env` 仍存在；审计报告和截图仍保留。

命令：

```powershell
.venv\Scripts\python.exe tools\verify_live_multishot.py
.venv\Scripts\python.exe tools\verify_live_multishot.py cleanup
```

项目已清理时可用 `reconstruct` 从 leftover 文件重建脱敏报告，不得当作新的真实调用。

## 5. 第四次真实实拍（2026-10-02，4 镜 live i2v）：跑通了，但**验收未通过**

- **项目**：`project_ade652fc91`「青茅山夜路 · live 4 镜 · 20261002-125752」，`live_strict`，4 镜 × 5s，720P。
- **provider / 模型**：`dashscope:wan2.7-i2v`（i2v 是唯一可行模式，见 `pre-live-checklist.md` §10）。
- **产物**：`output/playwright/live-4shot-20261002/`（`output/` 已 gitignore，**不提交**）。
- **计量（应用自报 meters 实测，非估算）**：`text=3` / `vision=1` / `video=4`；`video_tasks=4` 唯一、`reused=0`、`duplicate=0`。
  不变量 `video_submits_new + reused == unique_remote_tasks` **成立**。授权 20 元 / 单批 ≤4 次视频调用 **未超**。

### 5.1 费用（估算，实扣以阿里云控制台为准）

| 项 | 计算 | 金额 |
|---|---|---|
| 视频 ×4 | 4 × 5s × **0.60 元/秒**（代码内登记档，`live_budget.py`） | **12.00 元** |
| 文本 ×3 | 单次 ≈0.063 元（FLASH 4096 max_tokens × COST_BUFFER 1.30） | ≈0.19 元 |
| 视觉 ×1 | 单次 ≈0.030 元（2048 max_tokens） | ≈0.03 元 |
| **合计** | | **≈12.22 元** |

- 若 50s 免费额度覆盖本轮 20s 视频用量，视频部分实付 **0**，合计 ≈ **0.22 元**。**免费额度未验证**。
- 本机**无账单接口**，`actual_charged` 必须由控制台确认；此表只给估算，不得当账单引用。

### 5.2 🔴 验收判定：**未通过**（这条最重要）

四条视频**全部**在 **t≈2.467s 硬切**：前段忠实输入首帧，后段换成**无关场景/人物**。
`status: completed` 与 provider 的 `SUCCEEDED` **都不代表可用**。

证伪判据（三件套，全部落盘）：
1. 输入首帧 vs 视频首帧 PSNR **22–30 dB / SSIM 0.84–0.95** ⇒ 首帧被真的用上了（`i2v_fidelity.json`）。
2. 起/中/末三帧 PSNR **7–13 dB**（≠inf）⇒ 画面在动（`quant_evidence.json`）。
3. **每条恰好 2 个 I 帧，位于 `0.0` 与 `2.467`**，并伴随 `0.1667s` 级 PTS 异常间隔 ⇒ 两段独立编码被缝合
   （`cut_source_probe.json`）。定性靠眼：`i2v-contact-sheet-4x4.jpg`、`drift-4shots-stacked.jpg`。

**已排除「我方流水线」**：下载是纯字节拷贝（`video_provider._fetch_video_bytes` = `read()`→`write_bytes`，无转码/拼接）；
提交体最小且规范（`video_tasks.submit_payload` 原文可查）；每镜单任务、无重复、四片互异；本地无挂帧拼接步骤。

**未定位**：为何第二段完全无关。候选机制见 `PITFALLS.md`（同名条目）；
决定性实验 = **单镜重跑 `duration=2`（≈1.20 元）看硬切是否消失**，**须先经竹木批准**。

### 5.2b ✅ 根因已定位（2026-10-02，受控实验，实付 1.44 元）

**结论：`dashscope:wan2.7-i2v` 在 `duration=5` 下会生成两段独立片段再拼接；`duration=2` 不会。**
即 **问题出在请求时长 × i2v 模式**，不是本流水线、也不是"输入图太好/太差"。

**受控设计（唯一变量 = 时长）**

| 维度 | 值 |
|---|---|
| 输入首帧 | `project_ade652fc91/asset_e6705d3f66.jpg`，sha256 `d6723994a6a7ab55`（两侧**逐字节相同**，70017 B） |
| provider / 模型 / 模式 | 均 `dashscope` / `wan2.7-i2v` / `i2v` |
| 分辨率 / 帧率 | 均 1280×720 / 30fps |
| **唯一差异** | duration：**5s（已有）** vs **2s（新跑 `asset_6c87f76bce.mp4`）** |
| 容器 | `i2v_dashscope_612ff237`（1 镜冒烟容器，避免改动主项目） |

**结果**

| 判据 | 5s | 2s |
|---|---|---|
| **I 帧时间戳** | **[0.0, 2.467]**（两段） | **[0.0]**（单段） |
| 相邻帧 PSNR 最低 | 11.4 dB | 17.8 dB |
| 内容（13 帧等间隔） | 前半山雾 → **漂移进黑暗森林** | **全程同一场景+同一人物+绿蝶连续移动** |
| 判定 | **两段拼接** ❌ | **单段完整** ✅ |

并排图：`CTRL_5s_vs_2s_stacked.jpg`（上=5s，下=2s）。原始行图在 `strip_cmp/CTRL_sameImg_*.jpg`。

**交叉验证（零成本，借用历史付费素材）**：仓库里 9 条视频**共用同一张输入图**
（`p6demo_compare` 三家对照 + `i2v_*` 冒烟 + `kfsmoke` + `refsmoke`，首帧 sha 全为 `fde81a1bb8a10aa6`）：

| provider / 模式 | 时长 | I 帧 | 结论 |
|---|---|---|---|
| dashscope **i2v** | 5s | [0.0, 2.467] | 两段 ❌ |
| dashscope **i2v** | 2s | [0.0] | 单段 ✅ |
| dashscope **keyframes** | 5s | [0.0] | 单段 ✅ |
| dashscope **r2v** | 5s | [0.0] | 单段 ✅ |
| ark i2v | 5s | [0.0] | 单段 ✅ |
| minimax i2v | 4s | [0.0] | 单段 ✅ |

⇒ **只有 `dashscope + i2v + 长时长` 这一个组合会断。** 推测模型的单次生成单元约 **2.5s**，
超过即被实现为"两段拼接"，第二段脱离输入画面自由生成（**此为推测，未直接验证**）。

**产品级后果（比技术根因更重要）**：能力表 `/api/providers/capabilities` 里 dashscope
`supported_durations = [2, 5, 10, 15]`，前端时长下拉即照此渲染 ⇒ **UI 默认给出的 5s 正是会出废片的档位**，
而 `status: completed` 一路绿灯。**用户不会知道。**

**可用配方（按已验证程度排序）**
1. dashscope i2v **duration=2**（已验证单段；但整片变短）
2. dashscope **keyframes + 自选末帧**，5s（已验证单段；需两张图）
3. 换 provider：ark / minimax 的 4–5s i2v 均单段（ark 已退役，minimax 仍可用）

**未验证**：4s/10s 是否同样出问题；是否 2.5s 为硬边界；2s 结论是否可复现（当前 n=1）。

### 5.3 六条发现

| ID | 级别 | 摘要 |
|---|---|---|
| F1 | 流程认知（省钱） | live 分镜路径**不生成占位帧**（`first_frame_path`/`last_frame_path` 均为 `None`）；预检 §10「首末帧都是 .svg」仅适用 mock 路径 ⇒ 清 `ARK_API_KEY` 是保险非必需 |
| F2 | 用法契约（真实拦停 1 次） | 脚本调 `/shots/{id}/video` **必须显式带 `first_frame_path`**，否则 400 `MISSING_FIRST_FRAME`（挂帧只写 `shot_versions` 不写 `shot_drafts`，校验读 draft；前端 `app.js:872` 自己补了）。**本轮未扣费** |
| F3 | 产品缺陷（元数据） | 真实 1280×720 的首帧，`assets.width/height` 写成 **720×1280**（写反）；视觉复核记录随之写错 |
| F4 | 数据卫生 | 每个首帧 **2 条资产行**（1 条带元数据 + 1 条 `role=None` 全空壳）指向同一 `file_path` |
| F5 | 交付口径 | 成片**默认无音轨**（`DEFAULT_ASSEMBLY_SETTINGS.audio_enabled=False`），但 4 段素材都带真实 AAC ⇒ 属默认行为，非丢帧 |
| F6 | 失败副作用 | 首次 400 之前已把 `shot_drafts.video_mode` 由 `t2v` 改成 `i2v` ⇒ 失败会留下被污染的草稿（非事务性） |

### 5.4 清理

本项目的清理条件按 §4 口径改口径后执行：`4 shots / 4 unique remote_task_id / 4 video_tasks /
4 video 资产 / 1 final-video / 0 duplicate`。**须先经竹木看过产物再清。**
