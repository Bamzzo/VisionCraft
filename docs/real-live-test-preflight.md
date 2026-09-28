# 真实前端闭环预检（P7-A 护栏）

本页记录 DeepSeek + DeepSeek Vision + MiniMax 真实测试的护栏。**P7-B 本切片不发送真实请求，费用为 0 元。** 不得把上一次真实 5 镜测试重新算成本次调用。详细口径见 `docs/live-run-audit.md`。

## 1. 默认安全值（未覆盖时）

代码常量保持保守，避免误开付费：

| 项 | 默认 |
|---|---|
| 文本 | 最多 3 次 DeepSeek |
| 视觉 | 最多 1 次 DeepSeek Vision |
| 视频 | `MAX_VIDEO_CALLS = 1` |
| 预算 | `DEFAULT_BUDGET_CNY = 5.0` |
| 视频规格 | MiniMax H3、I2V、4 秒、768P |

未设置环境变量时，闭合估算按 **1 次** MiniMax 计费。超过任一限制返回 `BLOCKED_BEFORE_CALL`，且不会打开 HTTP。

> **2026-09-27 更正**：上面的默认值一直是对的，但**闸门此前只在 MiniMax 一条链路上执行**——`ark` 与 `dashscope` 是直接分发的，既没有授权开关、也没有每项目次数上限、也没有预算校验（本页通篇只写 MiniMax 就是证据）。切片 3 恰好要在那两家的参考图模式上花钱，所以这条已经修掉：闸门现在挂在 `generate_video_asset` 的分发环，**全部视频 provider 共用**；单价也改成按 provider 取。见第 8 节。

## 2. 受控环境变量覆盖（仅当前进程）

5 镜头真实测试启动前可在 PowerShell 中设置（**不写入 `.env`，不提交 Git**）：

```powershell
$env:VISIONCRAFT_LIVE_MAX_VIDEO_CALLS="5"
$env:VISIONCRAFT_LIVE_BUDGET_CNY="12"
$env:VISIONCRAFT_ALLOW_LIVE_LLM="1"
```

| 变量 | 作用 |
|---|---|
| `VISIONCRAFT_LIVE_MAX_VIDEO_CALLS` | 覆盖视频提交上限，解析失败或 ≤0 回退为 1；硬顶 5 |
| `VISIONCRAFT_LIVE_BUDGET_CNY` | 覆盖预算上限，解析失败或 ≤0 回退为 5.0 |
| `VISIONCRAFT_ALLOW_LIVE_LLM` | 授权真实 LLM / 视频（视频也可另用 `VISIONCRAFT_ALLOW_LIVE_VIDEO=1`） |

提高视频次数会按次数重算 MiniMax 费用。只改次数、不提高预算时，第一条真实请求前就会 `BLOCKED_BEFORE_CALL`。

已完成的第三次 5 镜测试确认上限：预算 12 元；DeepSeek 文本最多 3 次；DeepSeek Vision 最多 1 次；MiniMax H3 I2V 最多 5 次（每镜只提交一次）。若再次真实测试，仍只在当前进程覆盖这些变量。

## 3. 调用前检查

每次真实文本 / 视觉 / 视频请求前都会重新检查：

- 累计文本、视觉、视频提交次数
- 按当前次数上限估算的闭合费用（文本 ×3 + 视觉 ×1 + MiniMax × 视频上限 × 4 秒）
- 预算余额

模型、时长、分辨率或单价变化导致预计费用超过当前预算时，必须在第一条真实请求前阻止。

## 4. DeepSeek thinking 与 max_tokens

| 请求 | thinking | max_tokens | 说明 |
|---|---|---|---|
| 文本改编 / Bible / 分镜 | disabled | 4096 | 分镜 JSON 含多镜提示词，2048 可能不够 |
| 视觉检查 | disabled | 2048 | 结构化检查结果较短 |

请求计划只记录 provider、model、thinking、max_tokens、prompt_chars、call_index，不记录 Key、Authorization、完整请求体、Data URL、Base64 或签名 URL。

## 5. MiniMax 远程任务幂等

去重键：`provider` + `remote_task_id`。

- `video_tasks` 已有 `UNIQUE(provider, remote_task_id)` 与 `result_path`
- `assets` 增加 `source_task_id`、`source_remote_task_id`（可空，迁移不删历史）
- 首次完成与回查共用 `ensure_remote_video_asset`
- 已有可用本地文件：直接返回原路径，不下载、不 INSERT、不新建文件
- 记录在而文件丢失：允许一次受控重下并更新原记录
- 跨项目任务 / 资产、镜头或版本不匹配：拒绝
- 重复回查不追加第二个 `asset.ready`
- 历史库中已有的重复视频资产本次**不删除**，新逻辑不再制造重复
- 不把完整 API 响应、签名 URL 或密钥写入数据库或日志

## 6. 本地 JPEG/PNG 首帧

- 接口：`POST /api/projects/{project_id}/shots/{shot_id}/keyframes/register-local`
- 可将同一项目内 JPEG/PNG 挂接到多个镜头，不调用图片生成 Provider
- 禁止 SVG、目录穿越、项目外绝对路径、跨项目资产
- `gyfy.jpg` 只复制进临时项目资产目录，测试结束精确清理，不要提交进 Git

## 7. 当前状态

- 第一次单镜头真实闭环已完成（含重复资产问题，历史重复不做破坏性清理）
- 第二次 5 镜真实测试在启动前被默认 `MAX_VIDEO_CALLS=1` 与 5 元预算阻断，**未发请求、0 元**
- 第三次 5 镜头真实前端成片测试**已完成**：中断后只回查镜头 1 的原远程任务；**新提交 4 次，复用 1 个，唯一远程任务 5 个**；FFmpeg 成片已通过 ffprobe。临时项目已清理，数据库不能事后复核。
- P7-B 起，真实测试结束必须在清理前写入脱敏 `live_run_audit.json` / `live_run_lineage.json` / `live_run_ffprobe.json`。本切片不重跑真实 API。

## 8. provider 覆盖与单价（2026-09-27 补）

**闸门位置**：`backend/providers/video_provider.py` 的 `generate_video_asset` 分发环。对每个**有密钥**的候选 provider，在打开 HTTP 之前依次检查：

1. 授权开关——`VISIONCRAFT_ALLOW_LIVE_VIDEO=1` 或 `VISIONCRAFT_ALLOW_LIVE_LLM=1`；
2. 每项目视频次数上限——`VISIONCRAFT_LIVE_MAX_VIDEO_CALLS`，默认 1、硬顶 5；
3. 预算——`VISIONCRAFT_LIVE_BUDGET_CNY`，默认 5.0。

- **没有密钥的候选不计数、不拦**，直接跳过：否则会为一个根本不会调用的 provider 白扣一次名额。
- 被拦下时抛 `BudgetBlockedError`（`BLOCKED_BEFORE_CALL`）并**原样上抛**，不会被兜底成「所有 live video providers failed」——那样会让人以为换个 provider 就能绕过去，而真的换一家继续试，就是在越过预算花钱。
- **回查（refresh）不提交、不产生新费用**，因此不过这道闸；这一点与「断点恢复只回查原任务」的既有约定一致。
- 计数是**按提交尝试**扣的，与既有 MiniMax 口径一致：闸门在 HTTP 之前扣，提交失败也不退还。保守方向。

**单价（元/秒）**：`backend/providers/live_budget.py` 的 `VIDEO_PRICE_CNY_PER_SECOND`。未登记的分辨率按该 provider 最贵档取，未登记的 provider 按全表最贵档取——高估是安全方向，低估才是事故。

| Provider | 分辨率 | 有输入视频 | 无输入视频 | 出处 |
|---|---|---|---|---|
| `minimax`（H3） | 768P | 0.50 | 0.50 | MiniMax 官方价；本项目既有口径，测试钉着 |
| `ark`（Seedance 2.0） | 720p | **1.208** | **1.988** | 火山引擎「视频生成增强版」seedance_2.0 算子价目 |
| `ark`（Seedance 2.0） | 480p / 1080p / 4k | 0.562 / 3.014 / 6.22 | 0.924 / 4.958 / 10.108 | 同上 |
| `dashscope`（Wan 2.7 R2V） | 720P / 1080P | 0.60 / 1.00 | 0.60 / 1.00 | 阿里云百炼 wan2.7-r2v 价目（参考输入不额外计费） |
| `siliconflow` | 任意 | 0.50 | 0.50 | **未取到公开价**，按 MiniMax 档保守取值（待核实） |

- **为什么不能只有一个单价**：MiniMax 0.50 与 ark 1.208 相差 2.4 倍。一个全局数字必然低估其中一家，而低估的后果是"以为还够、其实已经超"。
- `ark` 那一行取自 LAS 视频生成算子价目；火山方舟另一张按 token 的价目（输出 480p/720p、输入含视频 28 元/百万 token）折算下来约 1 元/秒，**量级一致**，可作为交叉印证。
- **`siliconflow` 那两行是假设，不是报价**：本项目实际未配置 `SILICONFLOW_API_KEY`，该通道不会被选中；真要启用前必须先把单价核实掉。
- **默认口径刻意不变**：不指定 provider 时闭合估算仍按 MiniMax 计价（`estimate_closed_loop_cny` 的 `video_provider == "minimax"`）。「支持多家」不能变成「悄悄换了默认那一家的数」。
- **清单能力**：`estimate_closed_loop_cny(..., provider="ark", resolution="720p", video_seconds=5)` 会带出 `video_unit_cny` / `video_cny` / `video_price_basis`；`check_live_video_budget` 的 plan 同样带 `unit_cny` 与 `price_basis`。报「Provider / 模型 / 镜头数 / 时长 / 分辨率 / 预计费用」时不必再手工核算。

**顺带核实的一件事**：`wan2.7-r2v` 这个模型 id 在阿里云百炼官方文档里**就是原名**（快照 `wan2.7-r2v-2026-06-12`，支持最多 5 个图/视频混合参考），所以切片 3 用的 id 是对的。但本项目的 `DASHSCOPE_API_HOST` 是专属 MaaS 域名，该模型**是否已在该端点上开通仍需实调确认**——id 正确不等于账号可用。

**核对日期 2026-09-27，单价会变**：真实调用前应重新核对官方价目，不要把本页数字当长期有效。

## 9. 两批真实验收的清单（2026-09-27 实测，纯计算，未发请求）

用 `estimate_closed_loop_cny()` 直接算出，**没有打开任何 HTTP**：

| Provider | 分辨率 | 时长 | 视频单价 | 视频费 | 闭合合计 | 默认 5 元预算内？ |
|---|---|---|---|---|---|---|
| `minimax` H3 | 768P | 4s / 5s | 0.50 | 2.00 / 2.50 | 2.2394 / 2.7394 | 是 |
| `ark` Seedance 2.0 | 720p | 4s / 5s | 1.208 | 4.832 / 6.04 | **5.0714 / 6.2794** | **否** |
| `dashscope` Wan 2.7 R2V | 720P | 4s / 5s | 0.60 | 2.40 / 3.00 | 2.6394 / 3.2394 | 是 |

闭合合计 = 视频费 + 0.2394（闭合估算里预留的 3 次文本 + 1 次视觉）。

> **一个必须先处置的数字**：`ark` 720p 连 4 秒都已经超过默认预算 5 元（4s 合计 5.0714，`within_budget: false`）。而 ark 恰恰是参考图模式在两家里最贵的那家。**要跑它就必须显式把预算抬上去**，否则闸门会在第一条真实请求前 `BLOCKED_BEFORE_CALL`——这是设计如此，不是故障。

**按 5 秒镜头估的两批清单**（这是报给用户的最小口径，尚未获得授权）：

| 批次 | Provider | 单次 | 小计 |
|---|---|---|---|
| ① 参考图模式（切片 3） | `ark`（互斥，不发首帧） | 6.04 | |
| | `dashscope`（与首帧并存） | 3.00 | **≈ 9.04** |
| | `minimax`（接口无此参数） | — 跳过，不计数 | |
| ② 首尾帧 | `ark` | 6.04 | |
| | `dashscope` | 3.00 | |
| | `minimax` | 2.50 | **≈ 11.54** |

两批各自都在 20 元以内，但**合计约 20.6 元会越线**，所以必须分成两批、两批分别授权，不能合并成一次跑。

**跑前要在当前进程设置的开关**（不写 `.env`、不提交）：

```powershell
$env:VISIONCRAFT_ALLOW_LIVE_VIDEO="1"
$env:VISIONCRAFT_LIVE_MAX_VIDEO_CALLS="3"   # 一批三家；默认 1、硬顶 5
$env:VISIONCRAFT_LIVE_BUDGET_CNY="20"       # 默认 5.0 装不下 ark
```

**验收要看的是画面，不是状态码**：① 参考图模式下人物/场景是否真的维持了一致性；② 首尾帧是否真的按给定的两帧收束。两批都要在清理前落脱敏 `live_run_audit.json` / `live_run_lineage.json` / `live_run_ffprobe.json`（见第 7 节），并把真实产物留在项目目录里以便复核。

**还有一条属于"实调才知道"的**：`wan2.7-r2v` 的模型 id 在官方文档里是对的，但本项目的 `DASHSCOPE_API_HOST` 是专属 MaaS 域名，**该模型是否已在该端点上开通仍需实调确认**——id 正确不等于账号可用（见第 8 节末）。

## 10. 批次①的实跑结果与账目（2026-09-28）

竹木授权「今天跑①」后实际执行，**只用了一次真实付费调用**。报告与产物落在：

- `<数据目录>/reference-smoke/reference-ark-refsmoke_ark_59c51f0a.json`
- `<数据目录>/reference-smoke/refresh-vt_90b0e699f8.json`（零费用回查）
- `<数据目录>/projects/refsmoke_dashscope_9f75dc8f/asset_76529b075f.mp4`
- `output/reference-smoke/dashscope-reference-vs-output.png`（左参考图 / 右输出首帧）

| Provider | 结果 | 实际费用 | 说明 |
|---|---|---|---|
| `dashscope` `wan2.7-r2v` | **成功** | **3.00 元**（0.60 × 5s） | `video_tasks` 1 条 `completed`；提交 payload 里 `reference_image` 与 `first_frame` **并存**；ffprobe 实测 h264 1110×828 / 150 帧 / 5.038005s / aac |
| `ark` Seedance 2.0 | **失败，未提交** | **0 元** | HTTP 403 `AccountOverdueError`（**火山方舟账号欠费**），`video_tasks` 0 条 |
| `minimax` | 跳过 | 0 元 | 接口本就没有参考图参数，不参与本批次 |

**① 批次实际花掉 3.00 元，不是估的 9.04 元**——ark 那一半压根没提交出去（欠费在开 HTTP 前就被对方拒了）。

**两个被这次实跑解答、以及一个新增的待办**：

1. ✅ `wan2.7-r2v` 在本项目**专属 MaaS 端点**上**已开通**（此前只确认了模型 id 正确）。
2. ✅ dashscope 的 `reference_image` 与 `first_frame` **可以并存**。
3. ⏳ **ark 参考图模式仍未验证**，卡在**账号欠费**上。**需要给火山方舟账号充值后再单独授权补跑**——
   这不是能力问题，不要写成"ark 不支持参考图"。

**回顾性提醒**：本条也说明第 9 节那套预计口径的用途是"授权前的上限"，不是"事后账目"。
真实开销可能远低于预计（这次只有估计的 1/3）。

