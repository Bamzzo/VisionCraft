# VisionCraft V1：交付路线图与验收规范

> 本文是 V1 后续开发的唯一执行依据。若产品方向或范围发生变化，先更新本文和工作区根目录的规划文件，再修改代码。

## 1. V1 交付目标

将一段小说或剧本改编为一支 30～60 秒剧情短片。用户可以在三个关键节点参与创作：确认改编范围、确认视觉锚点、确认代表镜头；并可逐镜头修改描述、时长、参考图和生成模型。

V1 的核心不是“一次端到端生成”，而是一个可控、可回退、可追溯的创作流程：

```text
文本导入 → 改编范围 → Story Bible → 分镜 → 关键帧 → 单镜头视频 → 合成与导出
```

## 2. 已完成基线（不可回退）

- 保留 FastAPI、SQLite、LangGraph、异步任务、镜头版本、FFmpeg 合成的既有基础。
- `assets`、`media_transfers`、`video_tasks` 已记录素材、传递血缘与云端任务。
- 已实测火山 Seedance、阿里 Wan 2.7 I2V、MiniMax H3 使用同一 JPEG 首帧生成成功。
- `data_url` 是本地开发默认传递方式；部署后可切换为 `public_url`。
- Provider 调用必须经过 Adapter；业务工作流不得直接拼厂商图片字段或读取图片文件。
- 真实视频任务必须持久化远程任务 ID，超时后刷新同一个任务，禁止重新提交替代刷新。

## 3. 阶段路线

### UI-0～UI-1：页面信息架构与交互基础

**设计依据：** `docs/ui-layout-interaction-design.md`。

在继续 P5-B 或 P6 大范围开发前，先完成页面信息架构和交互基础建设：三栏工作台、完整阶段导航、素材缩略图/单素材视图、新建项目与创建项目分离、阶段编辑与下游失效、实时任务中心联动。第一阶段允许使用遵循真实数据合同的前端演示状态，确认交互后再接入真实接口。

**不可改变的核心规则：** `executionStage`、`viewStage`、`selectedAsset` 分离；阶段点击只改变查看内容；上游重做清除当前有效下游结果但保留历史版本；新任务只能引用当前有效版本。

**验收：** 通过桌面端浏览器自动化验证项目切换、阶段查看、素材选择、脏状态按钮、暂停/继续、下游失效、任务进度和无需刷新显示结果；同时完成中文编码、长文本换行和三栏布局检查。

### P0：回归基线与开发护栏

**目标：** 每次修改前后都能确认既有三 Provider、媒体传递和任务恢复没有回归。

**开发内容：**

- 统一项目级回归命令与文档。
- 完善 Provider capability 的字段定义与前端消费约定。
- 添加非收费测试：资产归属、缺少首/尾帧、模型不支持模式、Data URL 脱敏、任务状态转换。

**验收：**

- `tools/test_media_transfer.py` 通过。
- 后端编译、FastAPI 导入、数据库迁移通过。
- 不运行收费 API 时，所有 Adapter 请求体可在本地被构造并校验。

### P1：镜头级模型选择与能力约束

**目标：** 用户在每个镜头中选择生成方式、Provider 和模型；系统只显示有效组合。

**状态：** 2026-08-28 已完成可验收切片（无费用验证）。

**已实现：**

- `/api/providers/capabilities` 统一返回 `supported_modes`、模型列表、时长/比例/分辨率，以及 `mode_requirements`。
- `POST /api/projects/{id}/shots/{shotId}/video` 接受 `{ video_mode, provider, model, duration_seconds }`；请求级选择优先于环境默认值。
- 后端用同一能力矩阵校验：无首帧不能 I2V，无尾帧不能首尾帧，不支持的模式/时长/比例直接 400，不提交云端任务。
- 切换 Provider/模型会新建 `shot_version`，旧视频和旧版本保留。
- 镜头检查器按能力矩阵过滤选项，并展示 Provider、模型、模式、时长、分辨率和关键帧来源。
- 无费用测试：`tools/test_provider_capabilities.py`。

**开发内容：**

- 扩展前端 API：`generateVideo(projectId, shotId, { video_mode, provider, model })`。
- 镜头检查器增加：视频模式、Provider、模型、分辨率/时长说明、首帧/尾帧状态。
- 消费 `/api/providers/capabilities`，依据 `supported_modes`、时长、分辨率和比例禁用无效项。
- 请求级选择优先于环境默认值；未选择时仍使用默认 Provider。
- 生成结果卡显示 Provider、模型、版本和来源关键帧。

**验收：**

- 无首帧不能提交 I2V；无尾帧不能提交首尾帧。
- 切换 Provider 后实际调用对应 Adapter，旧结果不被覆盖。
- 同一镜头可展示至少两次不同模型生成的版本。

### P2：实时任务中心与自动更新

**目标：** 用户无需手动刷新即可看到工作流、关键帧和视频任务进度及结果。

**架构决策：** REST 提交命令，SSE 推送状态，低频轮询作为断线兜底；暂不引入 WebSocket。

**开发内容：**

- 新增 `job_events`，持久化任务阶段历史：`stage`、`status`、`progress`、`message`、`shot_id`、脱敏详情、时间。
- 改造 `update_job`：更新 `jobs` 当前快照时追加事件。
- SSE 改为推送结构化事件：`job.update`、`asset.ready`、`job.failed`、`project.refresh_required`。
- 前端建立统一任务状态：全局任务条、镜头局部状态、可展开时间线。
- `pending_remote` 状态下提供自动低频刷新与“立即刷新云端任务”入口；禁止自动重新提交。
- 视频/图片资产完成后自动拉取局部项目数据并更新预览。

**验收：**

- 提交后 1 秒内页面显示排队/处理中。
- 页面不刷新时，任务状态和素材预览会自动变化。
- SSE 断线后仍能轮询恢复；页面刷新后从 SQLite 恢复任务状态。
- 单镜头失败不会阻塞其他镜头；错误显示可理解且可执行。

**状态：** 2026-08-28 已完成可验收切片，并完成实时监听生命周期修复（无费用验证）。

**已实现：**

- `job_events` 持久化任务阶段历史；`jobs` 仍是当前快照；`update_job` 统一追加事件。
- 结构化 SSE：`snapshot`、`job.update`、`asset.ready`、`job.failed`、`project.refresh_required`，并带心跳；循环中不再完整读取项目。
- `GET /api/projects/{id}/job-events` 作为 SSE 断线后的增量轮询入口。
- 前端全局任务条、镜头局部状态、可展开时间线；`asset.ready` 后才刷新项目预览。
- `waiting_remote` 仅调用既有云端查询刷新，并明确提示不会重复提交或重复计费。
- **监听生命周期：** 递增 `observerToken` + `observedProjectId`。`stopObservation()` 会关闭 `EventSource`、停止增量轮询与云端回查定时器，但不清空已持久化的项目任务历史。切换项目、删除项目或进入无项目状态时先完整停止再加载。SSE / 轮询 / `refreshProject` 写回前必须同时满足：会话 token 仍有效、`project_id` 等于当前项目、连接实例仍是当前连接。
- **空闲策略：** 当前项目没有活跃本地任务且没有 `waiting_remote` 时，停止轮询与回查，并关闭 SSE，避免本地 Demo 空闲连接长期占用。切换项目时仍会先释放旧连接。
- **SSE 测试边界：** 生产流仍可能是长连接；自动化使用 `GET /api/projects/{id}/events?once=true` 与 `collect_sse_opening()` 推送 snapshot 与已有事件后结束，验证 `text/event-stream`、`id:`、`event: job.update` 与结构化 JSON，避免 TestClient 卡在无限流。

**技术债（不在本切片展开）：** 能力矩阵的时长/比例/分辨率仍主要在 Provider 级，需补齐模型级 `supported_durations / supported_ratios / supported_resolutions`。新增代码不得回到前端硬编码模型规则。

### P3：关键帧、版本与局部重生成闭环

**目标：** 用户能对单镜头做可控创作，而不必重跑整个项目。

**状态：** 2026-08-28 已完成可验收切片（无费用验证）。

**设计决定：**

- `shot_versions` 是不可变生成快照，禁止用编辑动作覆盖旧版本。
- 可编辑内容存放在独立表 `shot_drafts`（每镜头一行）。选择该策略是为了让检查器输入可以反复保存，而不污染已经冻结的 `shot_versions`；切换镜头/项目时把草稿写回服务器，避免静默丢失。
- 用户确认「基于当前草稿生成新版本」或提交「仅重生成此镜头」时，才按实质字段差异冻结新版本。无实质修改则复用当前版本。
- 「回滚」只修改镜头的 `current_version_id`（并回填草稿指针），不复制资产、不删除新版本、不重跑视频、不创建云端任务。
- 局部重生成只绑定该镜头的新 `version_id`。若版本已有视频或 `video_tasks`，再克隆一个无视频的新版本，旧视频仍可预览。
- 若项目已有成片资产，镜头版本变化后仅将 `projects.assembly_stale=1` 标记为需要重新合成，不自动执行合成。
- 关键帧与参考图继续走媒体传递层：业务只传资产路径与语义角色，不在草稿/版本/`job_events` 中写入 Data URL、签名 URL 或 API Key。
- `waiting_remote` 仍只查询已保存的同一个 `remote_task_id`。

**已实现：**

- 版本快照补齐运镜、时长、参考图、变更摘要；草稿与当前版本、版本历史可通过编辑器接口读取。
- API：保存草稿、冻结版本、按 `version_id` 提交单镜头视频、回滚指针；提交前走 P1 能力矩阵（I2V 缺首帧等在入队前拒绝）。
- 镜头检查器展示当前版本号、未保存修改、首/尾/参考图、Provider/模型/模式/时长与视频状态；可保存草稿、冻结版本、仅重生成此镜头、查看历史并回滚。
- 局部生成接入既有 P2 任务中心；失败后草稿与新版本保留。

**验收：**

- 修改一个镜头仅影响该镜头及下游视频，不重新生成其他镜头。
- 可以从较新版本回滚到旧版本，旧视频仍可预览。
- 任一视频可追溯到镜头版本、首/尾帧、Provider 和模型。

**技术债（不在本切片展开）：** 模型级能力矩阵仍未细化；关键帧「重绘」仍走既有立即写版本的兼容接口，检查器中的选帧默认写入草稿。

**状态：** 2026-08-28 已完成 P4-A 短文本审核闭环（无费用 mock/fixture，未调用真实 LLM）。

**P4-A 已实现：**

- 状态机：`created → adaptation_options_ready → awaiting_scope_review → story_bible_ready/awaiting_bible_review → storyboard_draft_ready/awaiting_storyboard_review → production_ready`。
- 节点表现为阶段，而不是角色聊天：文本理解 → 改编方案 → Story Bible → 分镜。每步有结构化输出、审核状态、checkpoint 与 `review_records`。
- 确定性 mock 改编器从原文抽取句子、称谓、冲突词，生成 2～3 个逻辑不同的方案、完整 Bible 与 4～8 个带引用的分镜；结构化合同与前端不依赖真实 LLM。
- 三次审核：选范围、编辑/确认 Bible、编辑/确认分镜。确认只前进；「修改后重生成」只失效必要下游，不删除已有 P3 镜头版本、视频或资产。
- 分镜未确认前禁止批量生成视频；P3 单镜头局部生成不被阻断。
- 前端步骤导航：选择故事范围 → 确认 Story Bible → 审核分镜 → 制作镜头。刷新可恢复审核步骤、已选方案、Bible 与分镜。
- 无费用测试：`tools/test_adaptation_workflow.py`。

**未来真实 LLM：** 必须先由用户确认 Provider、模型、单次输入规模和预算后才能接入；接入时替换 `adaptation_planner` / LLM 边界即可，不改审核数据合同与前端。

**P4 其余节点（视觉锚点试生成审核、批量生成）不在本切片。**

### P5：分级长文本适配

**目标：** 支持最多 10 万中文字符导入，但以“选择故事线后局部创作”替代黑箱全文压缩。

| 输入规模 | 处理策略 | 状态 |
|---|---|---|
| 0～1,500 字 | 直接分析与分镜（P4-A） | 已完成 |
| 1,501～10,000 字 | 确定性分块、事件提取、2～3 条候选故事线、用户确认范围后交给 P4 | **P5-A 已完成（2026-08-28，无费用 mock）** |
| 10,000～100,000 字 | 章节/事件层级摘要、向量检索、跨章节故事线 | **P5-B 尚未实现** |

**P5-A 已实现：**

- 持久化 `source_chunks`、`story_events`、`storylines`、`adaptation_scopes`；下游 `adaptation_options` / Bible / 分镜记录 `scope_id`。
- `POST /run` 按字数路由：短文本仍走 P4；中等文本进入 `awaiting_storyline_review`；超过 10,000 字中文拒绝并说明 P5-B 未实现。
- P4 `plan_adaptations` / `plan_story_bible` / `plan_storyboard` 消费已确认 scope 的 `scoped_text`，不再必然使用全文。
- 无费用测试：`tools/test_medium_text_adaptation.py`。

**P5-B 明确未做：** 章节树、向量检索、全文索引、跨章节故事线、真实 Embedding/RAG API。

**验收（P5 全量仍待 P5-B）：**

- 10 万字导入不将全文一次传入模型。
- 用户能按章节、人物或事件选择改编范围。
- 分镜能展示相关原文依据；选定范围外内容不无故进入短片。

### P6：多镜头合成、导出与演示包装

**目标：** 将若干真实视频片段稳定合成为 30/45/60 秒可播放短片。

**状态：** P6-A / P6-B 本地合成合同已完成。P6-C 真实 FFmpeg 验收已于 2026-08-30 完成本地闭环。P6-D 本地音频、字幕与成片包装基础闭环已于 2026-08-30 完成。P6-E 原声链路已于 2026-08-30 完成：开启「保留原视频音频」时先规范化各镜头再拼接音视频；无音轨镜头不伪造原声；原声可与本地背景音按音量混音；刷新后恢复已保存配置。无 FFmpeg / 无字幕滤镜或字体时测试必须 `SKIP`，不得把跳过或夹具伪装写成通过。

**P6-C 已实现：**

- 真实 lavfi 四镜头夹具生成与 ffprobe 校验：`tools/p6c_ffmpeg.py`、`tools/test_p6c_real_assembly.py`；
- 服务层真实合成、替换镜头、并发复用与入队前 400；工作区便携 FFmpeg（如 `../.tools/ffmpeg/bin`）可在不改系统 PATH 时被发现；
- 浏览器真实预览/下载：`tools/test_p6c_real_assembly_browser.py`（仅在本机有 ffmpeg/ffprobe 时才写 `output/playwright/p6c-real-*.png`）；
- 可重复演示入口：`tools/prepare_p6c_demo.py`（无 FFmpeg 时 SKIP）。

**当前输出范围：** 合成命令统一到 1280×720、24fps、yuv420p、libx264。默认仍使用 `-an`（与 P6-C 一致）。P6-D/P6-E 允许在成片层可选保留镜头原声、混入**当前项目本地音频**并烧录**本地字幕/SRT**，不修改镜头视频版本，不接入 TTS 或音乐生成。设计见 `docs/assembly-audio-subtitle-design.md`。

**P6-D 已实现：**

- 项目级 `assembly_settings`：字幕开关/文本/SRT、背景音频开关/资产路径/音量、是否保留原视频音频、字号与位置；
- `GET/PUT /api/projects/{id}/assembly-settings`，`GET .../assembly` 返回配置摘要、校验错误与能力提示；`POST .../assemble` 使用已保存配置；
- 音频不足循环、过长裁剪；字幕与音频临时文件成功/失败均清理；失败不登记 `final-video`、不误清 `assembly_stale`；
- 前端成片工作区配置区、保存后无需刷新即可看到待重新合成、合成中锁定配置；
- 无费用测试：`tools/test_p6d_assembly.py`、`tools/test_p6d_assembly_browser.py`（无 FFmpeg 或缺少字幕滤镜/字体时必须 `SKIP`）。

**P6-E 已实现：**

- 开启保留原声时：逐镜头规范化视频/音频后拼接，有音轨镜头保留原声，无音轨镜头该段静音且不把静音报告为原声；
- 原声可单独输出，也可与背景音 `amix`；背景音循环或裁剪到成片时长；
- `GET /assembly` 增加 `source_audio_available` / `source_audio_shot_count` / `source_audio_used`；
- 前端刷新恢复原声/背景音/字幕配置，未保存修改显示脏状态，切换项目不串配置；
- 无费用测试：`tools/test_p6e_source_audio.py`、`tools/test_p6e_source_audio_browser.py`。

**P6-A 已实现：**

- 合成前拒绝没有视频的镜头、占位视频和失效的本地视频文件；
- FFmpeg 临时 concat 文件在成功或失败后都会清理；
- 合成结果为空时任务失败，不登记无效成片资产；
- 成功后登记 `final-video` 资产并清除 `projects.assembly_stale`；
- 通过 `asset.ready` 事件通知任务中心和前端刷新成片预览；
- 合成失败、缺失镜头和不可用视频使用中文且可执行的错误信息；
- 无费用测试：`tools/test_assembly.py`、`tools/test_assembly_http.py`。
- P6-C 真实 FFmpeg 测试：`tools/test_p6c_real_assembly.py`、`tools/test_p6c_real_assembly_browser.py`（无 FFmpeg 时必须 `SKIP`，不得记为 `PASS`）。
- P6-D 本地音频/字幕测试：`tools/test_p6d_assembly.py`、`tools/test_p6d_assembly_browser.py`。
- P6-E 原声保留与混音测试：`tools/test_p6e_source_audio.py`、`tools/test_p6e_source_audio_browser.py`。

**开发内容：**

- 合成前校验：视频有效、比例/分辨率/时长、镜头排序。
- 统一规格、基础转场、片头/片尾。P6-D 已提供本地音频/字幕包装；完整旁白、配乐生成与字幕编辑器仍属后续。
- 成片预览、下载、合成任务状态与失败说明。
- 固定演示样本：短文本完整闭环、不同模型同镜头对比、失败恢复案例。
- V1 演示收口：`PATCH /api/projects/{id}` 项目设置落库、固定 `v1demo_main` 演示项目、全链路 Playwright 验收。见 `docs/v1-demo-project.md`。

**验收：**

- 4～10 个镜头可合成 30/45/60 秒 MP4。
- 替换一个镜头后只重新合成，不重生成其他视频。
- 浏览器预览与下载可用，失败素材不会进入成片。

### V1 演示收口（固定样本、项目设置、全链路验收）

**状态：** 2026-08-30 已完成本地无费用切片。不调用付费图片、视频、语音、音乐或 LLM API。

**已实现：**

- `PATCH /api/projects/{project_id}` 只允许修改标题、目标时长、画幅比例、输出分辨率；未提供字段保持不变；
- 空标题、非法时长/比例/分辨率返回中文 400；项目不存在返回中文 404；
- `projects.output_resolution` 默认 `1280x720`，未修改时成片规格与 P6-C/E 一致；
- 影响成片规格的修改在已有 `final-video` 时设置 `assembly_stale=1`，不改写 `shot_versions`、视频任务、成片历史和素材；
- 保存后写入 `project.refresh_required`，任务中心和工作区自动同步；
- 固定演示项目脚本 `tools/prepare_v1_demo.py`：只创建/重置 `v1demo_main`，`--clean` 只删除 `v1demo_*`；
- 全链路浏览器验收 `tools/test_v1_demo_browser.py`（截图仅 `output/`，无 FFmpeg 时成片步骤必须 `SKIP`）。

**验收：**

- `tools/test_project_settings.py`
- `tools/test_v1_demo_browser.py`
- 既有 P6 与改编回归命令仍通过。

### V1-QA：界面质量、状态一致性与智能体工作流展示

**状态：** 2026-08-30 已完成本地无费用切片。不调用付费图片、视频、语音、音乐或 LLM API，不新增后端状态机。

**已实现：**

- 右侧固定 8 个阶段：文本理解、故事线选择、Story Bible、分镜设计、关键帧、镜头视频、成片合成、导出与交付；
- `executionStage` / `viewStage` / `selectedAsset` 继续分离；点击阶段只切换查看；
- 阶段节点用中文状态、标记、`aria-label`、素材/任务数量表达，不只依赖颜色；
- 短文本改编方案并入文本理解，中等文本并入故事线选择；有效成片后 frontier 到达导出与交付；
- 未开始阶段仍显示，可看简报，非法操作被禁用并给出「请先…」提示；
- 刷新后恢复最近项目与 `viewStage`；项目切换不串任务和素材；
- 无新接口。导出页只读复用项目详情、`assembly` 摘要、成片资产和既有导出 JSON/Markdown。

**验收：**

- `node tools/test_workflow_view_model.mjs`
- `tools/test_v1_qa_browser.py`
- 既有 UI 工作台、V1 演示和 P2～P6-E 回归仍通过。

### P7-A：文本模型、视觉模型与阶段级模型选择

**状态：** 2026-08-30 已完成本地无费用切片。不发起真实付费 LLM / 视觉 / 视频 API 调用。

**已实现：**

- 模型角色分离：文本（`deepseek-v4-flash` / `deepseek-v4-pro`）、视觉（`deepseek-v4-flash-vision-exp`）、图片生成、视频（默认预选 MiniMax，可切换）、FFmpeg 不进下拉框；
- `/api/providers/capabilities` 增加模型级 `llm`、`stages`、`generation_modes`；`configured` 只返回布尔值；
- 项目表 `workflow_model_configs` 与 `generation_mode`（`mock` / `live_strict` / `live_with_local_fallback`）；
- 文本 / 视觉 Adapter 边界；JSON 失败可诊断；严格真实失败即失败，允许回退时明确标记「已使用本地回退」；
- 视觉请求只使用项目内资产与 Data URL；Data URL 不入库、不进 job_events；
- 工作台各文本/视觉/视频阶段可选择模型；成片阶段不显示 LLM 选择；
- 真实 HTTP 另需 `VISIONCRAFT_ALLOW_LIVE_LLM=1` 与人类确认。详见 `docs/stage-model-selection.md`。

**验收：**

- `tools/test_stage_models.py`
- `tools/test_provider_capabilities.py`
- 既有 P1～P6-E 与改编回归命令仍通过。

### P7-A 护栏：预算上限与本地 JPEG/PNG 首帧

**状态：** 2026-08-31 已完成本地无费用护栏与 5 镜测试启动修复。本切片不发起真实付费 API 调用。

**已实现：**

- DeepSeek 文本强制 `thinking disabled` 与 `max_tokens=4096`；视觉保持 thinking disabled 并限制 `max_tokens=2048`；
- 默认仍为 1 次视频调用、5 元预算；可用进程环境 `VISIONCRAFT_LIVE_MAX_VIDEO_CALLS` 与 `VISIONCRAFT_LIVE_BUDGET_CNY` 覆盖（不写 `.env`）；
- 闭合估算按当前视频次数上限计算 MiniMax 费用，不能只改次数绕过预算；超限返回 `BLOCKED_BEFORE_CALL`；
- 镜头工作区可登记本地 JPEG/PNG 为首帧，并可挂接到同一项目的多个镜头；不走图片生成 Provider；SVG 不能用于 Vision 或 I2V；
- MiniMax 同一 `provider + remote_task_id` 只登记一个逻辑视频资产；回查复用本地文件；丢失文件才受控重下；不重复 `asset.ready`；
- 历史重复视频资产不删除；真实 HTTP 仍需 `VISIONCRAFT_ALLOW_LIVE_LLM=1`（视频可另用 `VISIONCRAFT_ALLOW_LIVE_VIDEO=1`）。

**验收：** `tools/test_live_safeguards.py`、`tools/test_local_keyframe_browser.py`

**真实测试（当时，不计入本切片）：** 第三次 5 镜头真实前端成片已完成。镜头 1 中断后复用原远程任务；镜头 2～5 为续跑新提交。计数为新提交 4、复用 1、唯一远程任务 5。成片已通过 FFmpeg 与 ffprobe。临时项目已清理。

### P7-B：真实运行审计、断点恢复证据与报告收口

**状态：** 2026-08-31 已完成本地无费用收口。**不发送真实 API，费用 0 元。** 不得把上一次真实测试重新算成本次调用。

**已实现：**

- 报告字段拆分 `video_submits_new` / `video_tasks_reused` / `unique_remote_tasks`，禁止把 4 次新提交写成 5 次新提交；
- 清理前写入脱敏 `live_run_audit.json`、`live_run_lineage.json`、`live_run_ffprobe.json`（`output/`，不提交 Git）；
- 进行中的同一版本视频任务再次点击生成时，只回查原 `remote_task_id`，不创建第二条 `video_tasks`；
- mock transport 覆盖：已提交 → 断开 → 再进入 → 只查询原任务 → 不重复下载 / INSERT / `asset.ready` → 剩余镜头可继续。

**验收：** `tools/test_live_safeguards.py`、`tools/verify_live_multishot.py`、`docs/live-run-audit.md`

### P7：部署和非核心扩展（最后）

- 部署时将媒体传递切为对象存储 HTTPS URL 或厂商文件上传。
- 旁白、配乐、字幕、用户系统、权限、成本配额和实验室模型接入。
- 这些均不得阻塞本地可演示 V1。

## 4. 统一状态与失败处理

### 本地任务状态

`queued → running → completed | failed | waiting_remote`

`waiting_remote` 表示云端已接收任务但本地等待已结束。它只允许查询同一个 `remote_task_id`，不允许自动再次提交。

### 视频任务状态

`submitted → running → pending_remote → completed | failed | cancelled | expired`

### 处理规则

| 场景 | 系统动作 |
|---|---|
| 缺少首帧/尾帧 | 请求前阻止，给出补齐素材入口 |
| Provider 参数不支持 | 能力矩阵前置约束，不消耗额度 |
| 本地网络或轮询超时 | 保留远端任务 ID，后续刷新 |
| 云端排队 | 标记等待，自动低频刷新 |
| 内容安全拒绝 | 保留失败证据，建议修改描述，不盲重试 |
| 用户对画面不满意 | 新建镜头版本，不覆盖旧结果 |

## 5. 每次开发的固定流程

1. 阅读本路线图、当前 Git 状态与相关模块；
2. 明确本次只解决一个阶段的一个可验收切片；
3. 先写/更新无费用测试，再实现；
4. 运行编译、数据库迁移、接口与前端交互验证；
5. 涉及付费 API 时，先说明 Provider、模型、参数、次数、预期成本和目的，等待用户确认；
6. 保存脱敏报告、任务 ID、结果路径和回归结论；
7. 更新路线图、测试记录和 Git 提交；
8. 只在工作区干净、验证通过后推送。

## 6. 回归样本与质量指标

### 固定样本

- `蛊真人1500字.txt`：短文本完整闭环；
- `蛊真人8000字.txt`：分块与候选事件；
- `蛊真人30000字.txt`：范围选择；
- `蛊真人100000字.txt`：导入与索引压力；
- `gyfy.jpg`：三 Provider I2V 兼容性回归。

### 质量评价

- **可用性：** 是否产出可播放成片；
- **叙事性：** 是否看得出角色、目标、冲突和镜头顺序；
- **一致性：** 人物、服装、道具、画风是否稳定；
- **可控性：** 局部修改是否只影响必要下游；
- **可靠性：** 超时、断线、失败后能否恢复；
- **成本：** Provider、模型、镜头时长与重试次数是否被记录。

## 7. 人类确认关卡

Agent 可以自动实现、测试、提交代码和运行无费用验证。以下行为必须等待用户明确确认：

- 新的付费图片/视频/语音 API 请求或扩大测试矩阵；
- 删除任何已有项目、素材、数据库记录或远程资源；
- 部署到外部平台、绑定域名、公开访问或上传用户素材；
- 选择最终视觉审美、故事改编范围、演示成片与成本预算；
- 修改或提交 `.env`、密钥、令牌、真实下载签名 URL。
