# VisionCraft 全盘交付与跨窗口接续说明

更新时间：2026-09-11  
适用分支：`feat/v1-media-pipeline`  
当前提交：`420b6af51526cc9e4655bc7d746228a9144b07cb`  
远程：`origin/feat/v1-media-pipeline`  
基线状态：本地与远程同步，无 VisionCraft 后端或测试进程运行。本文创建后成为唯一未跟踪文件，尚未提交。

> 本文是交给下一个开发智能体的交付资料。所有“真实调用”与“本地 Mock”必须严格区分；不能把估算费用当成平台账单，也不能把审计占位字段当成真实完成证据。

## 1. 产品目标与当前结论

VisionCraft 是一个影视创作工作台，目标是把小说或剧本文本逐步转换为剧情短片：

```text
文本导入
  -> 改编范围
  -> Story Bible
  -> 分镜
  -> 关键帧
  -> 单镜头视频
  -> 多镜头成片
  -> 预览与下载
```

核心产品原则：

- 用户在改编范围、视觉锚点、代表镜头等节点参与确认。
- 每个镜头有版本；新结果创建新版本，不覆盖历史。
- 上游修改只使必要下游失效，历史版本、任务和成片保留。
- `executionStage`、`viewStage`、`selectedAsset` 分离；点击查看阶段不改变后端执行状态。
- 默认模型只是首次预选，用户可以按阶段保存 Provider/模型。
- 真实模式失败即失败；允许本地回退时必须明确标记回退。
- 真实远程任务必须按 `remote_task_id` 回查，不能把查询失败当成重新生成理由。

当前结论：

- 本地 Mock 网页主流程已经可重复演示。
- 真实 DeepSeek 文本、DeepSeek Vision、MiniMax 视频和 FFmpeg 成片链路都曾成功验证。
- V1 的核心“真实可生成成片”能力已具备。
- 最近一次新的 2 镜真实测试没有完成：镜头 1 已提交 MiniMax，测试脚本因异步落库竞态中断；竞态修复已完成并提交，但该真实任务后续状态尚未确认。
- 当前不应直接重新生成镜头 1，也不应把最近一次测试记为完整 PASS。

## 2. 技术形态

### 后端

- Python + FastAPI
- SQLite
- LangGraph/工作流基础
- 后台任务、SSE 和轮询
- Provider Adapter 隔离文本、视觉、图片和视频调用
- 工作区便携 FFmpeg/ffprobe：`.tools/ffmpeg/bin/`

### 前端

- 原生 HTML/CSS/JavaScript
- API 封装在 `frontend/js/api.js`
- 状态在 `frontend/js/state.js`
- 页面渲染在 `frontend/js/render.js`
- 业务交互在 `frontend/js/app.js`
- 任务观察在 `frontend/js/jobObserver.js`
- 阶段状态推导在 `frontend/js/workflowViewModel.js`

### 主要数据对象

- `projects`：项目配置、生成模式、执行状态、过期状态。
- `shots`：镜头定义和 `current_version_id`。
- `shot_versions`：镜头版本、首尾帧、视频、Provider/模型。
- `assets`：图片、视频、音频、字幕、成片和血缘。
- `video_tasks`：本地任务和远程任务 ID、状态、结果路径。
- `job_events`：任务中心事件，要求脱敏。
- `workflow_model_configs`：项目 + 阶段一行的模型选择。
- `workflow_checkpoints`：审核暂停/继续节点。
- `assembly_settings`：项目级字幕、背景音、原声和包装配置。
- `vision_reviews`：视觉检查脱敏元数据，不保存 Data URL/Base64。

## 3. 提交与阶段历程

以下提交均在 `feat/v1-media-pipeline`，后一个提交建立在前一个提交之上。

| 提交 | 主要内容 | 结果 |
|---|---|---|
| `6ed921b` | 阶段级模型选择、DeepSeek 文本/视觉 Adapter、能力矩阵 | 已推送；Mock 验证通过 |
| `31cb84a` | 真实测试预算护栏、本地 JPEG/PNG 首帧护栏 | 已推送；真实测试前阻断规则通过 |
| `26d4195` | 多镜头预算覆盖、远程视频资产幂等基础 | 已推送；后续审计继续加强 |
| `ae0edba` | 真实运行审计、断点恢复证据、计数口径拆分 | 已推送；Mock 断点恢复通过 |
| `42bfc23` | 右侧阶段状态和浏览器证据修复 | 已推送；P7-C 通过 |
| `f82d2f6` | V1 可用性和导出页路径收口 | 已推送；V1 usability 通过 |
| `1b7e872` | 后端审核暂停、继续、checkpoint、断点恢复 | 已推送；P8-A 通过 |
| `b5ab700` | 项目资产上传：图片、音频、SRT | 已推送；P8-B 通过 |
| `81e90c5` | 本地 Mock 网页冒烟测试与旧测试合同修复 | 已推送；工作区当时干净 |
| `889d055` | 手动镜头数严格遵守 `requested_shot_count` | 已推送；2/3 镜无费用验证通过 |
| `353984d` | 真实测试编排、清理和审计强化 | 已推送；Mock 验证通过 |
| `420b6af` | 修复视频任务异步落库竞态 | 当前 HEAD；Mock 验证通过 |

## 4. 已实现能力

### 4.1 文本改编与审核

- 短文本改编方案、Story Bible、分镜草案。
- 中等文本分块、故事线候选、范围确认。
- `created -> running -> awaiting_scope_review -> awaiting_bible_review -> awaiting_storyboard_review -> production_ready`。
- 后端 checkpoint 支持暂停、继续、刷新恢复和幂等确认。
- 旧 checkpoint 被替代后不能使流程倒退。

### 4.2 模型选择

默认预选：

- 文本：DeepSeek `deepseek-v4-flash`
- 视觉：DeepSeek `deepseek-v4-flash-vision-exp`
- 视频：MiniMax `MiniMax-H3`
- 成片：本地 FFmpeg，不显示 LLM 下拉框

用户可以按阶段切换并保存配置；未配置 Provider 显示“未配置”，不静默切换到别家。配置按项目隔离，刷新后恢复。

### 4.3 真实调用安全边界

- 默认阻断真实 HTTP，必须有进程级 LIVE 开关并经过用户明确确认。
- 文本 thinking disabled，`max_tokens=4096`。
- 视觉 thinking disabled，`max_tokens=2048`。
- 视频调用次数和预算在请求前检查。
- 默认视频上限 1 次、默认预算 5 元；测试时可临时覆盖，不能写入 `.env`。
- Key 只用于运行时配置，不返回前端、不写审计、不写任务事件。
- 真实失败不伪装成 Mock 成功。

### 4.4 图片和视觉检查

- 当前项目可上传 JPEG/PNG 首帧、尾帧、参考图。
- Vision/I2V 只能使用当前项目资产。
- SVG、项目外路径、跨项目资产和外部 URL 被拒绝。
- Data URL 只在待发送请求内存中存在，持久化记录脱敏。
- 本地首帧上传不调用图片生成 Provider。

### 4.5 视频生成和远程任务

- MiniMax I2V 已真实验证。
- 每个镜头的当前有效版本独立保存视频。
- 同一 `provider + remote_task_id` 只对应一个逻辑视频资产。
- 断线后只回查原任务，不重新 POST 生成。
- 进行中的任务必须保留项目和本地任务记录，不能为了清理临时项目而删除恢复依据。
- 当前镜头数：手动模式严格等于 `requested_shot_count`；模型建议只能在 auto 模式决定数量。

### 4.6 成片

- FFmpeg concat 多镜头合成。
- 输出统一到项目分辨率，默认 `1280x720`、24fps、yuv420p、H.264。
- 可选背景音频：短音频循环，长音频裁剪。
- 可选字幕：上传 SRT 或纯文本生成简单 SRT，并烧录字幕。
- 可选保留镜头原声：无原声镜头补静音；原声可以与背景音混音。
- 成片配置保存后已有成片标记 `assembly_stale=1`。
- 重新合成产生新的 `final-video`，旧成片保留历史。
- 合成失败不登记无效成片、不误清理 stale。

### 4.7 网页工作台

- 三栏工作台。
- 右侧 8 阶段：文本理解、故事线选择、Story Bible、分镜设计、关键帧、镜头视频、成片合成、导出与交付。
- 阶段名称、中文状态、可查看/可执行、素材/任务数量和前置提示同时展示。
- 导出页按无成片、过期、合成中、当前有效分流。
- 任务中心支持 SSE/轮询更新。
- 刷新、项目切换、未保存状态和窄窗口均有测试覆盖。

## 5. 真实测试记录

### 5.1 第一次真实前端测试

- 文本：DeepSeek 3 次成功。
- Vision：DeepSeek Vision 1 次成功。
- 视频：MiniMax 1 次成功。
- 问题：模型生成 5 镜，而当时预算只允许 1 次视频，因此没有继续成片。

### 5.2 真实 5 镜多镜头测试

这次曾完成完整真实闭环：

- DeepSeek 文本 3 次。
- DeepSeek Vision 1 次。
- MiniMax 唯一远程任务 5 个。
- 新提交 4 个，镜头 1 为中断前已有任务并复用。
- 重复提交 0，重复资产 0。
- 视频下载 5 个。
- FFmpeg 合成成功。
- 浏览器预览和下载成功。
- 成片约 22.33 秒、1280x720、H.264；当次没有音轨。

证据目录曾为：`output/playwright/live-multishot/`。临时项目已清理，只能用脱敏报告、审计文件和成片复核，不能再从数据库现场查询。

### 5.3 2 镜测试：分镜数量问题

第一次 2 镜尝试中，用户设置手动 2 镜，但 live 分镜使用了改编方案的 `suggested_shot_count`，产生 5 镜，因此在任何 MiniMax 请求前停止。

修复：

- `manual` 严格使用 `requested_shot_count`。
- 模型返回过多确定性裁剪。
- 模型返回过少确定性补齐，补齐镜标记 `source_type=count_normalized`。
- `auto` 才使用模型建议。

修复提交：`889d055`，无费用测试通过。

### 5.4 2 镜测试：第一次异步落库竞态

第二次 2 镜尝试中，镜头 1 视频 POST 返回 200，但测试脚本立即读取项目；后端任务还未完成异步写入 `video_tasks`，脚本误报“没有 video_task”并停止。

当次事实：

- 文本 3 次真实调用。
- Vision 1 次真实调用。
- MiniMax 镜头 1 提交 1 次。
- 镜头 2 未提交。
- 没有成片。

修复提交：`420b6af`。

修复内容：

- POST 后有限轮询等待任务落库。
- 等待期间不再次提交。
- 已有远程任务只 refresh/query。
- 任务未完成时保留项目。
- `remote_tasks_completed` 只统计真实 completed。
- running/pending 归入 `remote_tasks_inflight`。
- 费用审计按实际已发生调用估算，平台费用保持“无法确认”。

### 5.5 最近一次 2 镜测试：当前残留事项

最新一次新测试也在镜头 1 POST 返回 200 后因测试脚本竞态中断，之后后端才把任务写入 SQLite。

项目和任务目前按最近报告保留：

- 项目：`project_a43afde7c5`
- 项目标题前缀：`LIVE2SHOT`
- 镜头：`shot_e84099874d`
- 远程任务脱敏 ID：`4367…0538`
- 原始任务 ID：`vt_e0800b9609`
- 镜头 1：已提交，原报告为 `video_running` / `running` / `cloud_status=submitted`
- 镜头 2：未提交
- 成片：没有
- 预览/下载：没有

注意：本地后端已经关闭；云端任务当前状态没有被确认。不能把任务状态写成 completed，也不能重新生成镜头 1。后续只有两条合法路径：

1. 授权后只查询同一远程任务；若完成，再在保留项目中登记并继续镜头 2。
2. 放弃该任务，但不应在没有明确清理授权和状态确认前擅自补发镜头 1。

旧任务 `4367…3964` 已明确放弃，不查询、不下载、不恢复。

## 6. 已发现问题与处理结果

| 问题 | 根因 | 处理结果 |
|---|---|---|
| 默认模型不清楚是否锁定 | 预选与用户选择未区分 | 已区分并持久化 |
| 缺少文本模型 | 早期只有视频/图像方向 | 已接入 DeepSeek 文本 Adapter |
| Vision 数据泄露风险 | 图片请求边界不统一 | 已通过媒体传递层和脱敏记录收口 |
| SVG 不可用于 Vision/I2V | 本地 Mock SVG 与真实 API 类型不匹配 | 已禁止 SVG |
| 成片完成后阶段状态错误 | I2V 被误判为必须首尾帧，历史版本覆盖当前状态 | 已修复 `executionStage` 推导 |
| 审核暂停无法继续 | 前端状态与后端执行脱节 | 已使用后端 checkpoint |
| 用户不能从网页上传素材 | 早期依赖脚本/外部文件 | 已实现 P8-B 项目资产上传 |
| 手动 2 镜变成 5 镜 | live 分镜只读 `suggested_shot_count` | 已在 `889d055` 修复 |
| 远程视频重复提交/重复登记 | 回查与下载路径缺乏幂等 | 已加入 remote task 复用和资产去重 |
| POST 200 后立即找不到任务 | 后端异步落库，测试立即 GET | 已在 `420b6af` 修复测试编排并用 Mock 覆盖 |
| 进行中任务被清理 | 测试异常后无条件删临时项目 | 已改为进行中任务保留 |

## 7. 验收体系

每次修改后的推荐顺序：

1. 单元/服务合同测试。
2. Mock Provider 和 HTTP 接口测试。
3. 前端视图模型测试。
4. Playwright 本地 Mock 浏览器测试。
5. FFmpeg/ffprobe 本地媒体测试。
6. 真实测试前预算和活动任务预检。
7. 用户明确授权后的最小真实 API 测试。
8. 真实远程任务恢复、审计、预览和下载复核。

常用无费用命令：

```powershell
.venv\Scripts\python.exe tools\test_storyboard_shot_count.py
.venv\Scripts\python.exe tools\test_live_safeguards.py
.venv\Scripts\python.exe tools\test_mock_video_refresh.py
.venv\Scripts\python.exe tools\test_mock_web_smoke.py
.venv\Scripts\python.exe tools\test_upload_assets.py
.venv\Scripts\python.exe tools\test_p8b_browser.py
.venv\Scripts\python.exe tools\test_v1_usability.py
.venv\Scripts\python.exe tools\test_v1_qa_browser.py
.venv\Scripts\python.exe tools\test_p7c_ui_state_browser.py
.venv\Scripts\python.exe tools\test_p6c_real_assembly.py
.venv\Scripts\python.exe tools\test_p6d_assembly.py
.venv\Scripts\python.exe tools\test_p6e_source_audio.py
.venv\Scripts\python.exe tools\test_workflow_pause_resume.py
.venv\Scripts\python.exe -m compileall -q backend
node --check frontend\js\api.js
node --check frontend\js\app.js
node --check frontend\js\jobObserver.js
node --check frontend\js\render.js
node --check frontend\js\state.js
node --check frontend\js\workflowViewModel.js
node tools\test_workflow_view_model.mjs
git diff --check
```

`test_live_2shot_wait.js` 是 Node 脚本，直接使用：

```powershell
node tools\test_live_2shot_wait.js
```

测试产生的项目必须按前缀清理，但只允许清理本次创建且不存在进行中远程任务的项目。涉及进行中远程任务时保留项目和任务记录。

## 8. 当前已知未完成边界

这些不是当前 V1 核心闭环的阻塞项，但后续需要拆阶段：

- P5-B：章节树、向量检索、Embedding、跨章节故事线、10,000 字以上文本完整处理。
- 用户系统、登录、权限和多租户。
- 成本配额、账户余额、平台账单同步。
- 对象存储、CDN、HTTPS URL、厂商 Files API。
- 通用视频上传、大文件分片。
- TTS 旁白、AI 配乐。
- 复杂字幕时间轴编辑器。
- 真实图片生成 Provider 的完整前端闭环。
- 部署、监控、生产环境安全加固。
- 自动编排仍有产品边界；审核暂停已接入，但远程任务不能通过本地“暂停”。

## 9. 下一阶段推荐路线

### 阶段 A：处理当前保留任务

只在得到授权后查询 `4367…0538`，禁止生成镜头 1。根据结果：

- `succeeded/completed`：安全下载/登记镜头 1，继续镜头 2 一次，完成 FFmpeg 成片。
- `running`：保留项目和任务，只记录状态，不重复提交。
- `failed/expired/not_found`：停止，不自动补发镜头 1。

### 阶段 B：V1 真实验收收口

若当前任务无法恢复，再重新进行全新 2 镜测试。必须重新做预算预检，因为平台实际账单不在本地，历史估算不能代替余额。

固定约束：

- 文本 `春秋蝉鸣少年归。`
- 手动 2 镜
- `live_strict`
- DeepSeek 文本 3 次
- DeepSeek Vision 1 次
- MiniMax 2 次，4 秒，768P，I2V
- 首帧为项目内 JPEG/PNG
- 图片生成 0 次
- 失败即停
- 每镜最多一次 POST
- 最后 FFmpeg、预览、下载

### 阶段 C：V1 发布前收口

- 完整无费用回归。
- 启动说明、环境变量示例、Provider 诊断和错误提示。
- 人工网页冒烟。
- 一次可复核的真实 2 镜或 3 镜成片证据。
- 审计、ffprobe、截图、下载文件和费用说明归档。

### 阶段 D：P5-B 长文本

先写数据和成本合同，再实现章节树、范围选择、检索和故事线；不得让 P5-B 影响当前短文本 V1 稳定性。

### 阶段 E：生产化与创作扩展

最后处理用户系统、权限、配额、对象存储、部署、TTS、配乐、复杂字幕和真实图片生成。

## 10. 跨窗口交接规则

新智能体启动后先做以下事情：

1. 阅读本文。
2. 阅读 `docs/v1-delivery-roadmap.md`、`docs/real-live-test-preflight.md`、`docs/live-run-audit.md`、`docs/asset-upload-design.md`、`docs/workflow-pause-resume-design.md`。
3. 检查 `git status --short --branch` 和 `git log -1 --oneline`。
4. 检查 8000 附近本地进程，但不要随意关闭非 VisionCraft 进程。
5. 检查 `output/playwright/live-2shot/` 和项目 `project_a43afde7c5` 的现状，只读即可。
6. 在任何真实 API 前，重新做预检并向用户说明 Provider、模型、次数、预算和停止条件。
7. 不读取或打印 Key；不把 `.env` 提交到 Git。
8. 不把历史真实测试、Mock 测试、费用估算写成当前新测试结果。
9. 发生代码问题时先用 Mock 重现，再做最小修改和回归。

交接时优先使用以下报告格式：

```text
当前提交 / 分支 / 工作区：
真实网络请求：
平台实际费用：
本地费用估算：
当前活动远程任务：
当前项目与资产：
本轮已验证：
本轮未验证：
阻断项与根因：
修改文件与提交：
下一步及所需用户确认：
```

## 11. 安全和用户确认边界

智能体可以自动做：

- 代码阅读、修改、提交和推送。
- 无费用测试、Mock、HTTP 本地测试、FFmpeg 本地测试。
- 生成脱敏审计和清理本次创建的临时资源。

必须等待用户明确确认：

- 新的真实图片、文本、视觉、视频、语音或音乐 API 请求。
- 扩大真实测试镜头数、调用次数或预算。
- 查询或恢复已有远程任务（即使只是 GET，也要说明会使用 Key）。
- 删除已有项目、素材、任务或远程资源。
- 修改或提交 `.env`、Key、Token、签名 URL。
- 部署、公开访问、上传用户素材或绑定外部服务。

## 12. 当前交付文件状态

- 本文：`docs/codex-handoff-delivery-2026-09.md`，本次新建，**已于 2026-09-20 纳入版本控制并推送**（此前"保持未跟踪"的约定作废，见 14.8）。
- 业务代码：当前 HEAD `a1563e0` 已提交并推送，与 origin 同步。
- `.env`、密钥、`backend/data/`、`output/` 和临时媒体不属于交付提交范围。

## 13. 交付摘要

VisionCraft 不是“还没有接上 Provider”的原型：Provider 已接通，真实文本、视觉、视频和成片都曾跑通。截至 2026-09-20，**一次完整的真实 2 镜闭环已跑通（14 项全 PASS，见 14.6）**，此前的异步落库竞态已修复、残留远程任务已确认失效并收尾、harness 误采纳旧项目的缺陷已修补并加了独立守卫。当前剩余工作已从“恢复/收口”转为**面向交付的收口与验收归档**（见 14.8 的下一步候选）。平台实际账单始终无法从本地确认，任何估算值不可当作余额；任何真实付费调用仍需逐次授权。

---

## 14. 2026-09-20 追加记录（接续窗口必读）

本节由接续窗口在 2026-09-20 写入，覆盖第 5.5 节中"任务状态未被确认"的表述。

### 14.1 第 5.5 节残留任务已查询：失效

经用户明确授权，只发 1 次查询（零生成费用）：

```text
GET https://api.minimaxi.com/v2/query/video_generation/436764667060538
-> HTTP 400 bad_request_error: invalid params, invalid task_id (2013)
   request_id 06fe8f6d03411586041c21721c59fc1a
```

判定 `failed/expired`，按第 9 节阶段 A 规定停止，未补发镜头 1，未提交镜头 2。查询副作用：`shots` 镜头 1 变为 `video_failed`；`video_tasks` 行仍为 `running`（刷新异常路径只改 shots 与 job）。证据：`output/playwright/live-2shot/resume_report.json`。

### 14.2 本次窗口新增工具（均已提交）

| 文件 | 用途 |
|---|---|
| `tools/live_2shot_resume_plan.js` / `tools/live_2shot_resume.cjs` | 接续保留项目：默认只回查；提交需 `--allow-submit=<shot_id>`；失败即停 |
| `tools/retire_remote_video_task.py` | 收尾平台侧已失效的任务行（保留行与 remote_task_id、写 error_code、改前备份库） |
| `tools/restore_project_from_backup.py` | 从数据库备份恢复项目行与资产文件（写入前备份、外键显式校验） |
| `tools/cleanup_temp_project.py` | 仅允许删除"本轮新建 + 空 + 无进行中任务"的项目 |
| `tools/test_live_2shot_project_guard.js` 等 | 上述工具的只读/无网络测试 |

### 14.3 误删事故与修复（重要）

一次重跑中，`live_2shot.cjs` 新建项目后以侧边栏 `.project-item.active` 取当前 id，而同名旧项目被选中，工作流跑在旧项目上并被收尾清理删除。修复已提交（`ddb2467`）：

- 记录本轮开始前的项目 id 集合，新建后断言 working id 必须是新 id；标题增加时间戳后缀。
- 失败 job 只有 `created_at` 晚于本轮开始时刻才允许中断流程。
- 清理只接受本轮新建且非受保护的 id。

旧项目已用 12:12:31 的备份恢复（行 + 资产文件 sha256 校验通过）。

### 14.4 当前残留状态与下一步

- `project_a43afde7c5` 已恢复，现场与第 5.5 节一致：镜头 1 `video_failed`、镜头 2 `keyframes_ready`；`vt_e0800b9609` 状态 `running`/`submitted`、`remote_task_id` 保留为证据。
- 因此 `run_live_2shot.py` 预检的"活动远程任务数"仍为 1，**阶段 B 直接跑会被 BLOCKED_BEFORE_CALL**。若确认不再需要该证据行，用 `tools/retire_remote_video_task.py` 收尾后再走阶段 B。
- 阶段 B 首次尝试已中止（采纳了旧项目），本轮真实开销仅 DeepSeek 文本 2 次；该轮 `live_run_audit.json` 中的 text 3 / vision 1 / video 1 是旧项目遗留计数，**不是本轮发生额**。

### 14.5 采纳旧项目的深层根因与修复（重要，会重复发生）

14.3 的防线（"新建 id 必须是新 id"）在 12:26 的重跑里**立刻抓住了问题**：新建了 `project_fb86dacf7d`，但读回的 working id 仍是 `project_a43afde7c5`，守卫在建项后第一时刻中止，本轮真实调用为 0（该项目 text/vision/video 计数全 0）。

根因（三层叠加，缺一不成立）：

1. 页面加载时前端自动选中 `state.projects[0]`（`frontend/js/app.js` 的 `loadProjects()`），当时最新项目正是旧同名项目，因此它处于 active。
2. 原等待条件 `#summaryFields.innerText.includes(TITLE.slice(0, 8))` 用 `"LIVE2SHO"` 作 needle，**被旧项目标题直接满足**，等于没有等待。
3. 创建请求返回后立刻读 `.project-item.active`，读到的还是上一次渲染的状态；随后 `renderAll()` 才把新项目设为 active。

修复（已改 `tools/live_2shot.cjs`）：

- 项目 id **只取自 `POST /api/projects` 的应答体**（`create_project` 返回 `get_project(id)`，顶层含 `id`），不再从 DOM 猜。
- 保留 `assertFreshCreatedId` 作为第二道网。
- 新增"等待侧栏中出现该 id 且带 `active`"的等待（20s 超时即失败退出），取代原先的标题前缀等待；该条件不可能被同名旧项目满足。

零外发自检 `tools/test_live_2shot_create_guard.cjs`（自带本地后端，LIVE 三开关强制为 0）10 项全 PASS，其中两项是实证：`title.slice(0,8)` 在创建前**就已被旧同名项目满足**（复现旧缺陷条件）、创建后唯一 active 项就是新 id（旧同名项目未被采纳）。

### 14.6 阶段 B 完成：全新 2 镜真实闭环 PASS（2026-09-20 12:29–12:33）

前置：12:25 用 `tools/retire_remote_video_task.py` 再次收尾 `vt_e0800b9609`（备份 `db-backup-20260920-122539.db`），活动远程任务 1→0；预检 `ok=true`，含缓冲 5.4394 / 8 元。

运行 `tools/run_live_2shot.py`，耗时 3 分 58 秒，退出码 0，**14 项全 PASS**（建项 → 改编方案 → Story Bible → 分镜 → 确认分镜 → 上传首帧 → 视觉检查 → 镜头 1 I2V → 镜头 2 I2V → 合成 → 预览 → 下载 → 清理）。

| 项 | 实测 |
|---|---|
| 临时项目 | `project_ee43a20710`（`LIVE2SHOT-042935 春秋蝉鸣少年归`），`created_project_id == project_id` |
| 真实调用 | 文本 3 / 视觉 1 / 视频提交 2（`unique_remote_tasks` 2、`completed` 2、`inflight` 0） |
| 重复 | `duplicate_submits` 0、`duplicate_assets` 0、`duplicate_remote_groups` 0 |
| 远程任务 | 4437…1512、4436…8269，均 `succeeded`，两镜 `video_ready` |
| 成片 | `final-cut.mp4` 1280x720 h264 24fps，8.9583s，1,589,233 字节，无音轨 |
| 本地估算 | 5.4395 元（含缓冲 5.4394，上限 8）；**平台实际费用无法确认** |
| 清理 | `cleanup_verified=true`；临时项目行与目录已删；`retain_for_resume=false` |

回归与副作用核查：保留项目 `project_a43afde7c5` 跑前跑后指纹一致（11 个项目 / 2 shots / 1 video_task / 4 assets / 资产目录文件名集合相同），未被采纳也未被清理；8040–8048 端口空；后端日志无密钥字面量与 `Authorization`；归档 `archive-2026-09-20-stageB-guard-abort/`（9 文件，含 14.5 那轮的证据）。

### 14.7 推送问题已解决：根因是本机装了两个 git（2026-09-20 12:40）

- 提交 `a1563e0` 之后新增：`tools/live_2shot.cjs`（14.5 修复）与 `tools/test_live_2shot_create_guard.cjs`；`docs/codex-handoff-delivery-2026-09.md` 经竹木确认**已纳入版本控制**（本节即该提交的一部分），此前“保持未跟踪”的约定作废。
- 根因（实测，非推断）：本机装了两个 git。**PortableGit 2.55**（WorkBuddy 自带，PATH 中靠前）的 `credential.helper=helper-selector` 是图形选择器，非交互 shell 给不出凭据，因此 `git push` 必然报 `could not read Username`；系统另装 **Git for Windows 2.49**（`C:/Program Files/Git/cmd/git.exe`），其 `credential.helper=manager`（GCM）能正常读取 Windows 凭据管理器里的 GitHub 凭据。
- 凭据一直是有效的：GCM 查询 `github.com` 返回 `username=Bamzzo` 与长度为 40 的 `password`。
- 旁证：`refs/remotes/origin/feat` 的 mtime = `2026-08-31 22:50:49`，比最后一次提交 `420b6af`（22:50:03）晚 46 秒，即上次成功推送的时间戳；当时走的是系统 git，故 Codex 从未遇到此问题。此前“代理问题”的判断不成立。
- 解法（**零配置改动**）：`GIT_TERMINAL_PROMPT=0 "C:/Program Files/Git/cmd/git.exe" push origin <branch>`。
- 结果：`420b6af..a1563e0` 推送成功，`ahead=0 behind=0`；并直接读远端对象验证 8 个新文件均在远端树中（远端 `tools/` 共 70 个文件）。
- **反面结论**：不要把 `credential.helper` 改成 `manager`（仓库级或全局均不可）。实测 PortableGit 挂 manager / GCM 绝对路径会**挂起 2 分钟以上不返回**（已手动终止），把快速失败变成永久卡住。
- 该结论已写入用户级长期记忆，后续任何会话执行 push 都会直接用系统 git。

### 14.8 当前状态与下一步（2026-09-20）

第 9 节的**阶段 A 与阶段 B 均可视为已关闭**：阶段 A 的回查确认残留任务失效（14.1），阶段 B 的全新 2 镜真实闭环 14 项全 PASS（14.6）。

**仓库状态**：分支 `feat/v1-media-pipeline`，HEAD `a1563e0` 与 origin 同步，工作区干净。

**残留数据（不影响下一阶段，无需处理）**：`project_a43afde7c5` 镜头 1 为 `video_failed`、镜头 2 为 `keyframes_ready`；`vt_e0800b9609` 已 retire 为 `failed`/`invalid_task_id` 且保留 `remote_task_id` 作证据。该项目不会再被 harness 采纳（14.5 的三道守卫），可作为只读现场长期保留。

**下一步候选（按建议优先级）**：

1. **阶段 C · V1 发布前收口**（建议优先）：完整无费用回归、启动说明与 `.env.example`、Provider 诊断与错误提示、人工网页冒烟、一次可复核的成片证据与费用说明归档。这是把“技术闭环已通”变成“可交付演示”的最短路径。
2. **P6 合成／导出与演示打包**：`task_plan.md` 中仍未完成的 P3（关键帧／版本／局部重生成体验）与 P4-P6 remainder（视觉锚点审核、合成、演示打包）。
3. **阶段 D · P5-B 长文本**：章节树、向量检索、Embedding、跨章节故事线、10,000 字以上处理。工作量较大，与竞赛演示的相关性需竹木判断。
4. **阶段 E · 生产化**：用户系统、对象存储、配额与账单、部署与监控。当前非阻塞。

**纪律提醒**：任何真实付费调用仍需竹木逐次授权；平台实际账单始终无法从本地确认，本地估算值不可当作余额。

