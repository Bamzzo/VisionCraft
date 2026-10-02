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
| ark i2v | 5s | [0.0]（**仅 1 条样本**） | ⚠️ **已被 §5.2c 推翻** |
| minimax i2v | 4s | [0.0] | 单段 ✅（但**几乎无运动**，见 §5.2c） |

⇒ ~~只有 `dashscope + i2v + 长时长` 这一个组合会断。~~

> ⚠️ **2026-10-02 二次更正（样本从 9 条扩到 18 条后推翻）**：真正的分界**不是 provider 品牌**，
> 而是 **「长时长 + 只给首帧（i2v）」这个输入约束**。`ark i2v 5s` 同样会断
> （`p6demo_story` 4 镜里 **2 镜**，相邻帧 PSNR 10.0 / 8.4 / 7.2，与 dashscope 废片的 7.18 同量级）。
> **结论以后按「输入约束」记，不按「provider」记**——按品牌记会随版本失效，按约束记才可迁移。**详见 §5.2c。**

推测模型的单次生成单元约 **2.5s**，长输出被实现为"多段拼接"；
且**只给首帧时没有终点锚**，第二段脱离输入画面自由生成（**此为推测，未直接验证边界**）。

**产品级后果（比技术根因更重要）**：能力表 `/api/providers/capabilities` 里 dashscope
`supported_durations = [2, 5, 10, 15]`，前端时长下拉即照此渲染 ⇒ **UI 默认给出的 5s 正是会出废片的档位**，
而 `status: completed` 一路绿灯。**用户不会知道。**

**可用配方（10-02 三条全测完后的最终判定，证据见 §5.2c）**

| 配方 | 单段 | 连续 | 收敛到尾帧 | 运动幅度 | 判定 |
|---|---|---|---|---|---|
| ① `dashscope i2v 2s` | ✓ | ✓（逐帧 36→14 平滑） | —（无尾帧） | 温和 | 干净，但短、动得少 |
| ② **`dashscope keyframes` 首+尾帧 5s** | ✓ | ✓ | ✓ **PSNR 35.8** | 大 | **✓✓ 唯一推荐** |
| ③ `minimax i2v 4s` | ✓ | ✓ | —（无尾帧） | **几乎不动** | 弱（连续但近乎静止） |

> **三条"不断"，但只有 ② 真正能用。** 判据不能只问"断没断"——还要问「**动没动**」与「**有没有落到给定终点**」。

**未验证**：4s/10s 是否同样出问题；2.5s 是否为硬边界；以上各结论的样本量（② 为 n=1 实付验证，①③ 为历史素材 n=1）。

### 5.2c ✅ 二次更正 + 三条配方最终判定（2026-10-02，样本 9→18 条，实付 3.00 元）

本节是 §5.2b 的**修正与收口**：§5.2b 的样本只有 9 条，扩到 **18 条去重素材**后，
其中一条结论被**推翻**；同时三条候选配方全部测完。

**① 根因更正：分界是「输入约束」，不是 provider 品牌。**

用**同一把尺子**（I 帧时间戳 + 相邻帧 PSNR）重扫 18 条历史素材
（证据：`probe_matrix18_iframe.json`（I 帧）+ `content_compare_m1.json`（相邻 PSNR））：
`ark i2v 5s`（`p6demo_story`，模型 `doubao-seedance-2-0-260128`）**4 镜里 2 镜也断**：

| ark shot | I 帧时间戳 | 相邻帧 PSNR 低洼 | 判定 |
|---|---|---|---|
| `458a18` | `[0.0, **2.375**]` | **10.00** @ t=2.35 | 两段 ❌ |
| `ed5bff` | `[0.0, 1.042, **2.75**]` | **8.44** @ t=1.17、**7.21** @ t=2.74 | 两段 ❌ |
| `23e84d` | `[0.0]` | 最低 15.85，无低洼 | 单段 ✅ |
| `e77588` | `[0.0]` | 最低 24.24 | 单段 ✅ |

⇒ **I 帧与 PSNR 低洼互相印证**（`458a18` 的 2.375 ↔ 2.35；`ed5bff` 的 2.75 ↔ 2.74），
且 **ark 的缝落在 t≈1.17–2.75s、dashscope 的缝落在 2.467s** ——
**两家原生生成单元时长相近（≈2.5s），长输出都被实现为"拼接"**。这与 provider 无关。

⇒ §5.2b 的「只有 `dashscope + i2v + 长时长` 会断」**被推翻**。
（对照：dashscope 废片相邻 PSNR 低洼 **7.18 / 7.71 / 8.36**，与 ark 的 7.21 / 8.44 同量级。）

| provider / 模式 | 2s | 4s | 5s |
|---|---|---|---|
| dashscope **i2v** | 单段 ✅ | — | **两段 ❌** |
| **ark i2v** | — | — | **两段 ❌**（本次更正） |
| dashscope **keyframes** | — | — | 单段 ✅ |
| dashscope **r2v** | — | — | 单段 ✅ |
| minimax **i2v** | — | 单段 ✅（但几乎不动） | — |

⇒ **真正的分界 = 「长时长 + 只给首帧（i2v）」这个输入约束**，与 provider 品牌无关。

**代码侧解释**（`video_provider.py:408-411`）：**只有 `keyframes` 模式才会把尾帧发出去**；
`i2v` 结构上就发不了尾帧 ⇒ 长时长下没有"终点约束"，模型中途换场景无人制止。
**两家的实现都靠"拼接原生单元"来满足长时长**，有没有终点锚才是会不会断的决定因素。

> ⚠️ **结论以后必须按「输入约束」记，不能按「provider 品牌」记**——
> 按品牌记会随版本失效，按约束记才可迁移。

**② 修法＝换模式，不是换 provider。** 在 `i2v`（只给首帧）下换任何 provider 都只是"换个地方断"。

**③ 三条配方最终判定**（可行性判定**全部零成本**用历史已付素材完成；仅 ② 需新花钱）

| 配方 | 单段 | 连续 | 收敛到尾帧 | 运动幅度 | 判定 |
|---|---|---|---|---|---|
| ① `dashscope i2v 2s` | ✓ | ✓（逐帧 36→14 平滑） | —（无尾帧） | 温和 | 干净，但短、动得少 |
| ② **`dashscope keyframes` 首+尾帧 5s** | ✓ | ✓ | ✓ **PSNR 35.8** | 大 | **✓✓ 唯一推荐** |
| ③ `minimax i2v 4s` | ✓ | ✓ | —（无尾帧） | **几乎不动** | 弱（连续但近乎静止） |
| ✗ `dashscope i2v 5s` | 2 段 | 断 | — | — | **废片** |
| ✗ `ark i2v 5s` | 2 段 | 断 | — | — | **废片** |

> **三条"不断"，但只有 ② 真正能用。** 判据不能只问"断没断"——还要问
> 「**动没动**」（末尾 vs 首帧要明显不同）与「**有没有落到给定终点**」（末尾 vs 给定尾帧要近）。

**④ 配方②付费单镜验证（实付 3.00 元 = 0.60×5）——通过。**
工程 `kfsmoke_dashscope_03ec8dde` / 成品 `asset_2ea0870946.mp4` / `completed`。
首帧 = 影片 shot1 现用首帧（`asset_e6705d3f66`）；**尾帧 = 复用 shot3 的首帧**
（`asset_58847162b0`，`kf3-back-into-ink`）——**同角色、同风格、同为 1280×720，天然配对，零额外素材成本**。
三层判据：I 帧 `[0.0]`（单段）；相邻 PSNR 最低 11.25、2.33s 处**不是断崖而是高点 16.2**；
**`PSNR(末帧, 给定尾帧)=35.81` vs `PSNR(末帧, 首帧)=6.69`**；
离尾帧 PSNR 逐时刻 **8.1→10.4→14.3→12.6→14.0→19.5→35.8** 单调收敛。
图：`PAID_keyframes_convergence.jpg`、`PAID_motion_profile.jpg`。

**⑤ 方法学更正（本次第三个自身错误，最重要的一条）。**
`ffmpeg -ss <t>` 抽"指定时刻"帧时，**长 GOP 素材上快速定位会解出关键帧而非第 t 帧**，
据此曾判定"配方① 2s 视频在末尾硬切"——**结论完全反了**。逐帧全解码后 PSNR 从
**36.34 单调平滑降到 13.94，全程无断崖**。
⇒ **判「有没有硬切」必须逐帧全解码**（不带 `-ss`），或至少把采样点推到**末帧本身**。
`I 帧数`（只有 1 个）＋`稀疏采样`（最后一采样 1.86s）**会双双漏掉尾部 0.3s 内的硬切**。

**⑥ 取证成本。** 三次更正（§7 的时长受控 + §5.2c 的 provider 更正 + 配方判定）
的判定**全部用历史已付素材零费用完成**（零费用侦察**省下 2 笔**）。今日新增实付 = **1 笔 = 3.00 元**
（0.72 口径 3.60）；10-02 全天合计 **≈16.42（0.60）/ ≈19.66（0.72）元**，授权 20 元未超。

**⑦ 🔴 阻在交付上的是内容层，不是技术层（本条最重要）。**
单镜技术已解决（配方②），但整片仍出不来：

1. **四张首帧本身不是连贯的视觉序列**：shot1/shot3 = 方源单人（明→暗），
   shot2/shot4 = **双人**（远景→特写）。明暗与人物数在四镜间来回跳，**剪不到一起**。
2. **首帧与分镜文案矛盾**：文案写"夜路、月光"（shot1）、"独自"（shot2/shot4），
   但 shot1 首帧是**明亮日间**、shot2/shot4 首帧是**两个人** ⇒ 属**语义层不一致**，
   正是本项目最该防的那类错误，**靠换 provider 修不了**。
3. shot2/shot4 无天然后续图，尾帧需重新设计（shot4 是收尾镜，逻辑上无后继）。

⇒ **唯一待竹木裁决的问题**：要不要**先统一内容再出片**（交付选项见 2026-10-02 工作日志 §8.7）。
技术侧已就绪，**不阻塞在能力，卡在"这四张图能不能连成一条叙事"**。

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
