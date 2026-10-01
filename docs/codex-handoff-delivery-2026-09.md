# VisionCraft 全盘交付与跨窗口接续说明

首次成文：2026-09-11。此后按切片持续追加（见 §14.x），**每节记录该切片落地时的 HEAD**。  
适用分支：`feat/v1-media-pipeline`  
当前提交：**以 `git log -1 --oneline` 为准**，本文不再写死哈希——此处原写 `420b6af5`，在后续提交后即过期，留着只会误导。  
远程：`origin/feat/v1-media-pipeline`  
基线状态：本地与 origin 同步；阶段 A/B/C、工程账与切片 1～4 均已推送。

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
- **`/api/health` 的 `mode` / `llm_live` 只说明 Key 已配置，不代表真实调用已授权**——这两个字段曾长期被混读成"真实调用已开通"。真实授权态与受阻原因在 `live_access`：`authorized`（三个开关各自的真假）、`keys_present`、`blocked_by`（逐条列出到底缺什么，把"没配密钥"和"没开开关"分开）。诊断文案保持不含环境变量名与密钥形状。

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

要一次跑完全部无费用用例，用回归驱动器，而不是逐条手敲上面这些命令。它会把历史夹具种成一个**模板**、给**每条用例各拷一份独立数据目录**、为需要外部服务的用例在**空闲端口**起后端、给每条用例留一份完整日志，并汇总成一份可复核的 JSON 报告：

```powershell
.venv\Scripts\python.exe tools\run_no_cost_regression.py
```

**不需要预先起任何服务**。下面三条曾经是手敲命令的绊脚石，驱动器已全部代为处理；只有逐条手动执行时才需要自己注意：

- `test_adaptation_start_refresh.py`、`test_p6b_assembly.py`、`test_ui_workbench.py`、`test_p6c_real_assembly_browser.py`、`test_p6d_assembly_browser.py`、`test_p6e_source_audio_browser.py` 这 6 个脚本**不自带后端**，需要一个服务在 `VISIONCRAFT_BASE_URL` 上响应 `/api/health`。驱动器会**为这 6 项各起一个**（在 8070+ 里挑空闲端口、跑完即停、指向该项自己的数据目录）并导出该变量（**不占用 8000，也不复用已在响应的服务**——它会把宿主的批量删除护栏对子进程关闭，而复用别人的后端就可能把验收脚本的删除动作打到真实库上）。其余用例都自带服务，或不需要服务。
- 这几个浏览器脚本会先等 `#newProjectBtn` 变成可点。该按钮在「库中一个项目都没有」时是**故意禁用**的（`frontend/js/render.js` 里 `newBtn.disabled = mode === "create" && !hasProject`），所以对**空数据库**首次执行会在点击处 30 秒超时。驱动器会先种入夹具项目（至少 3 个），手动执行时请先自行建一个项目。
- **测试脚本的目标地址由 `VISIONCRAFT_BASE_URL` 决定**，默认 `http://127.0.0.1:8000`（`tools/` 下约 30 个 `.cjs` / `.py` 脚本都读它）。这是**测试夹具**变量，不在 `.env.example` 里——那份文件只列应用自身配置。把服务起在别的端口时设这个变量即可，不必改脚本。

**历史夹具**由 `tools/seed_regression_fixtures.py` 一次建成，全部离线、零外发：

| 夹具 | id | 作用 | 缺了会怎样 |
|---|---|---|---|
| 演示项目 | `v1demo_main` | `test_cleanup_temp_project.py` 要断言"受保护项目不被清理" | 该用例的守卫断言失去对象 |
| 历史同名项目 | `project_a43afde7c5` | `test_live_2shot_create_guard.cjs` 复现旧事故的前提（该 id 无法经 API 产出，只能直插） | 复现条件不成立 |
| 普通带镜头项目 | `project_c0ffee0001` | 给清理工具守卫一个"非空且不受保护"的对象 | 两条用例会静默 skip |

`test_live_2shot_create_guard.cjs` 还要求历史项目是**界面当前选中**的那个（前端按 `updated_at` 倒序取 `projects[0]`）。该前提原先靠驱动器在跑到该项之前调一次 `--touch-only` 复位 `updated_at` 来维持——那是**共用数据库时代**的补救（前序用例只要新建了项目就会把它挤下去）。用例级隔离落地后，这一项从模板拷出的是独立的库，前提恒定成立，**该钩子与 `test_retire_remote_video_task.py` 的 `--ensure-video-task` 补种钩子已一并删除**（见 14.14）。

`test_live_2shot_wait.js` 是 Node 脚本，直接使用：

```powershell
node tools\test_live_2shot_wait.js
```

测试产生的项目必须按前缀清理，但只允许清理本次创建且不存在进行中远程任务的项目。涉及进行中远程任务时保留项目和任务记录。

## 8. 当前已知未完成边界

这些不是当前 V1 核心闭环的阻塞项，但后续需要拆阶段：

- P5-B：章节树、跨章节故事线、10,000 字以上文本完整处理。**（2026-09-28：章节树 / 逐章分块 / 按章范围 / 跨章故事线已完成 = P5-B-1，见 14.21；向量检索、Embedding、全文索引仍未做 = P5-B-2。）**
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

**2026-09-28 进展**：前半（章节树 / 逐章分块 / 按章范围 / 跨章故事线）已完成并进回归，见 14.21。
后半（向量检索 / 全文索引 / 真实 Embedding·RAG）仍未做。**注意**：本轮做的是**结构性**范围收缩，
不是语义检索；文档里凡写"检索"处都应按前者理解。

### 阶段 E：生产化与创作扩展

最后处理用户系统、权限、配额、对象存储、部署、TTS、配乐、复杂字幕和真实图片生成。

### 排期表（2026-09-20 更新）

| 顺序 | 项 | 内容 | 状态 | 费用 |
|---|---|---|---|---|
| 1 | 阶段 A | 处理保留的远程任务 | ✅ 已关闭（14.1） | 1 次查询，0 元生成 |
| 2 | 阶段 B | V1 真实 2 镜验收收口 | ✅ 已关闭（14.6） | 本地估算 5.4395 元 |
| 3 | 阶段 C | V1 发布前收口 | ✅ C-1～C-5 全部关闭：C-1 **连续两次 48/48、各 401 断言 0 失败 / 2 skip**（14.13.1、14.13.5）；C-2 `.env.example`/README 已核；C-3 前后端与浏览器侧均已闭环（14.13.2/14.13.3）；C-4 冒烟截图已出；C-5 归档已出 | 无 |
| 4 | **工程账收口**（2026-09-20 竹木定案） | 回归隔离由运行级下沉到用例级、文档口径修正、`live_2shot.cjs` 表单驱动统一到共享模块 | ✅ 已关闭（14.14） | 无 |
| 5 | **P6 演示打包**（竹木选定主攻方向） | 演示形态＝**录屏成片**。三条固定样本（闭环／同镜头多模型对比／失败恢复）与录屏讲稿已落成（14.15）；**录制本身与可选付费增强待办** | ▶ 进行中（14.15） | 零费用部分已完成；付费增强 ≤ 20 元/次且须先报清单 |
| 6 | 首尾帧路径验收 | `../task_plan.md` Phase 3 唯一未勾选项（见下） | ⏳ 顺延至演示打包之后 | **需逐次授权** |
| 7 | 阶段 D | P5-B 长文本 | ◐ **P5-B-1 完成**（14.21，2026-09-28）；P5-B-2（向量检索/RAG）顺延 | 无 |
| 8 | 阶段 E | 生产化 | ⏳ 顺延 | 无 |

### 阶段 C 之后的排期项：首尾帧（first-last-frame）路径验收

**为什么单独列出来**：它是工作区根目录 `../task_plan.md`（注意：不是本目录的 `task_plan.md`，两份同名）里 **Phase 3: Validation Before Build** 唯一未打勾的验证项——该阶段其余四项均为 `[x]`，阶段状态仍是 `in_progress`，卡的就是这一条。待勾选条目的原文是 `Validate first-last-frame paths and record visual quality, latency, and cost only after explicit cost approval.` 另外 README 此前把它写成"已完成"，属于文档声明超出实际验证的部分——现已改为如实标注。

**已就位的部分**（不是从零开始）：
- `shot_drafts` 表已有 `first_frame_path` 与 `last_frame_path` 字段，接口与草稿保存路径都已打通；
- Provider 侧已有 keyframes 模式：Ark 需要 `VOLC_VIDEO_USE_KEYFRAMES=true` 才会把 `auto` 解析为 `keyframes`；
- 演示项目 `v1demo_main` 内已存在 `shot_*_first.png` / `shot_*_last.png` 资产文件，说明本地资产形态可用。

**未闭环的部分**：从未用真实 Provider 把首尾帧路径跑通。存在"某家根本不接受尾帧参数"的可能，这同样是有效结论。

**验收口径**（执行前须逐次取得授权）：
- 固定一对首帧／尾帧 JPEG，在 **Ark、Wan、MiniMax 三家各跑一次**；
- 每家记录：是否接受尾帧参数、画面质量与首尾衔接情况、时延、是否计费及金额；
- 任一失败即停，不自动重试，不得用普通 I2V 结果冒名通过；
- 证据落到 `output/playwright/` 下的独立目录：请求参数（脱敏）、脱敏后的返回体、下载的 MP4、ffprobe 输出；
- 结论回填本文件与 README，并同步工作区根目录 `../task_plan.md` 里 Phase 3 那条勾选状态（以及该阶段 `Status` 行）。

**前提与风险**：
- 会产生真实费用，金额随时长与分辨率变化，每次调用前必须给出 Provider／模型／次数／预算／停止条件清单；
- 该路径与现有 I2V 主链路共用 `shot_versions`／`assets` 结构，验收前需确认不会污染已有的 `live_video_call_count` 记账口径。

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
- 业务代码：截至切片 2 收口，三个代码提交为 `518d63e`（门本体）、`9f4d521`（验收与调用点）、
  `73332da`（驱动器守卫），随后两个文档提交是 `bc8b5f8`、`2825d14`。此前 2026-09-20 的两个
  提交是 `3368a60`（锚点数据通路）与 `a7b13c6`（该切片的验收）。不在这里写"当前 HEAD"——
  它一天里变了六次，写死了必然过期。
- **阶段 C 收尾**：见 14.11～14.13。新增 `tools/run_no_cost_regression.py`（无费用回归驱动器，当时 48 项，经 14.16～14.19.3 后今为 **57 项**）、`tools/seed_regression_fixtures.py`（离线夹具）、`docs/stage-c-evidence-archive-2026-09-20.md`（成片证据与费用归档）；修改面覆盖后端诊断 payload、前端 `flowBusy` 与受阻原因渲染、8 个验收脚本的可观测性与缺陷修复。
- **角色/场景视觉锚点数据通路**（2026-09-20，功能切片 1）：见 14.16。新增 `backend/services/anchor_service.py`、`tools/test_anchor_assets.py`（13 断言）、`tools/anchor_ui.cjs` + `tools/test_anchor_ui_browser.py`（12 断言）；`characters.asset_id` / `scenes.asset_id` 从"字段在、无人写"变为 Bible 阶段可挂载/替换/解除。审核门当时未兑现，**已由切片 2 补上**。
- **视觉锚点审核门**（2026-09-27，功能切片 2）：见 14.17。三道人工确认关卡补齐。新增 `tools/test_anchor_review_gate.py`（16 断言）、`tools/anchor_gate_ui.cjs`（浏览器过门辅助）、`tools/test_runner_guards.py`（驱动器自检 20 断言）；`checkpoint_service` 增 `anchor_review` 节点、`adaptation_service` 增 `anchor_review_readiness` / `confirm_anchors` / `ANCHOR_REVIEW_PENDING` 拦截，`main.py` 增 `GET .../anchors/review` 与 `POST .../anchors/confirm`，前端门横幅含二次确认跳过。**参考图当时仍未接进生成链路**，**已由切片 3 补上**（见 14.18）。
- **P6 演示打包（零费用部分）**：见 14.15。新增 `tools/prepare_p6_demo_samples.py`、`tools/test_p6_demo_samples.py`、`tools/make_p6_compare_sheet.py` 与 `docs/v1-demo-script.md`；三个固定样本 `p6demo_story` / `p6demo_compare` / `p6demo_recovery` 已建在工作库中。
- `.env`、密钥、`backend/data/`、`output/` 和临时媒体不属于交付提交范围。`tmp/` 已加入 `.gitignore`——其中**只有诊断驱动器脚本**（`_*.py` / `_*.cjs`）需要保留，因为下次可能要重跑；数据目录副本、播种副本、运行日志与截图都是派生物，可随时清理。2026-09-20 已按此规则清掉 44 项派生物（26.6 MB → 74.6 KB），驱动器脚本一个未动。

## 13. 交付摘要

VisionCraft 不是“还没有接上 Provider”的原型：Provider 已接通，真实文本、视觉、视频和成片都曾跑通。截至 2026-09-20，**一次完整的真实 2 镜闭环已跑通（14 项全 PASS，见 14.6）**，此前的异步落库竞态已修复、残留远程任务已确认失效并收尾、harness 误采纳旧项目的缺陷已修补并加了独立守卫。当前剩余工作已从“恢复/收口”转为**演示打包与收尾**：P6 的三条固定样本与录屏讲稿均已落成（14.15），剩下**录屏本身**、首尾帧真实三家验收（付费）与 P5-B-2 长文本检索（见排期表）。**（2026-09-28 补记：参考图批次①已实付 3.00 元验收完成，dashscope 通过、ark 因账号欠费未验，见 14.20；P5-B-1 已完成，见 14.21。）** 平台实际账单始终无法从本地确认，任何估算值不可当作余额；**任何真实付费调用仍须在发出前先报清单（Provider／模型／镜头数／时长／分辨率／预计费用）并取得逐次授权，单次上限 20 元**。

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
2. **P6 合成／导出与演示打包**：P4-P6 remainder 中的视觉锚点审核与**演示打包**（合成／导出已由 P6-A～P6-E 在 2026-08-30 完成）。此处原先把 P3（关键帧／版本／局部重生成体验）也列为未完成，**属口径错误**：`backend/main.py:545` 的 `rollback` 与 `:574` 的 `freeze` 接口均在，`docs/v1-delivery-roadmap.md` 自 2026-08-28 起即标为已完成切片，是本文档与工作区根 `task_plan.md` 的勾选状态没有跟上。
3. **阶段 D · P5-B 长文本**：章节树、跨章节故事线、10,000 字以上处理**已完成（P5-B-1，14.21）**；
   剩余向量检索 / Embedding / 全文索引（P5-B-2），工作量较大，与竞赛演示的相关性需竹木判断。
4. **阶段 E · 生产化**：用户系统、对象存储、配额与账单、部署与监控。当前非阻塞。

**纪律提醒**：任何真实付费调用仍需竹木逐次授权；平台实际账单始终无法从本地确认，本地估算值不可当作余额。

### 14.9 阶段 C 进展：无费用回归驱动器就位，48 项中 43 项通过（2026-09-20）

本节是接续窗口的第一手实测记录。所有数字来自磁盘上的报告文件，未做任何美化。

**新增工具**：`tools/run_no_cost_regression.py`——一条命令跑完 48 项无费用检查。它会（1）把数据写进 `VISIONCRAFT_DATA_DIR` 隔离目录，（2）自动起后端并为每条用例留完整日志，（3）种入历史夹具项目，（4）汇总成 JSON 报告。**它从不删除任何目录**：每次运行分配独立时间戳目录，原因是宿主删除护栏会按轮次计数并在超阈值时直接终止进程，整目录清理曾把 runner 自己在启动阶段杀掉。

> **本节两处口径已被 14.11 推翻，勿再引用**：其一，后端**不再占用 8000、也不再复用已在响应的服务**——那 6 个脚本全都认 `VISIONCRAFT_BASE_URL`，所以改为自动选空闲端口（8070+）并导出该变量；"复用他人后端"在删除护栏被关闭的前提下有打到用户真实库的风险。其二，不再只种「一个种子项目」，而是种入三个历史夹具（`v1demo_main`、`project_a43afde7c5`、`project_c0ffee0001`）。

**本轮结果**（`output/playwright/stageC/run-20260920-130236/`，耗时 1410.1 s）：

```
checks : 43/48 passed
asserts: 358 pass / 3 fail / 4 skip
```

**5 项失败及归因**（用第二次定向复跑 `run-20260920-132713` 交叉验证「稳定 vs 偶发」）：

| # | 检查 | 两次是否复现 | 归因 | 性质 |
|---|---|---|---|---|
| 1 | `test_cleanup_temp_project.py` | 稳定复现 | 该用例断言 `v1demo_main` 必须存在；它是**工作库的夹具**，隔离空库里没有。前两句断言（返回码 1、"受保护项目"字样）其实都通过了 | 夹具前提，非缺陷 |
| 2 | `test_live_2shot_create_guard.cjs` | 稳定复现 | 该用例要复现历史事故前提：库里须预先存在 id 为 `project_a43afde7c5`、标题以 `LIVE2SHOT` 开头的项目。隔离空库没有 | 夹具前提，非缺陷 |
| 3 | `test_p6b_assembly.py` | 稳定复现 | **已定案**：本机 PATH 中有 ffmpeg，于是进入真实 concat 分支（`test_p6b_assembly.py:22` 用 `shutil.which("ffmpeg")`），但夹具第 86 行写的是假字节 `b"local-test-video"`；实测真 ffmpeg 对它报 `moov atom not found` 并失败。而 `#assemblyFreshness` 只在已有成片时渲染（`frontend/js/render.js:1132`），故 20 s 必然超时。对照：同套 `p6c` 用 lavfi 生成的真短片，PASS | **测试夹具与环境假设冲突**，非产品缺陷 |
| 4 | `test_ui_workbench.py` | 两次失败点不同 | **已定案（见 14.11）**：两个独立原因叠加。(a) `frontend/js/app.js:55` 的 `init()` 是异步的，收尾那一句才把表单模式定为「有项目则摘要态」；在它收尾前点「新建」，表单会先显示、随即被隐藏，此后 `page.fill(..., {force:true})` 在隐藏元素上**静默失效**（探针实测填进去的值是空串），提交校验挡下请求 —— 现象是等不到 POST，而不是收到错误响应。(b) 脚本切换项目时被「未保存草稿」弹窗拦下（`#unsavedModal`，消息「当前镜头有未保存的修改。」），原脚本不处理该弹窗，`waitForFunction` 等 active 类名必然超时 | 测试自身缺陷，非产品缺陷 |
| 5 | `test_p8a_browser.py` | 三次失败点各不相同 | **已定案（见 14.11，此处初判「兜底端点用错」不是真凶）**：真因两处，都在浏览器侧。**(a) 界面读数滞后**：`waitProject` 一看后端 status 翻转就返回，界面却要等 4 秒轮询或一次 `refreshProject` 才重绘；而紧随其后的等待条件是「横幅文案含『等待审核』」，范围审核与 Bible 审核的横幅**都**满足，所以它是空转，紧接着读到的执行状态仍是旧值。（b）**点击被静默吞掉**：`onResumeWorkflow` 开头 `if (!state.project || flowBusy) return;`，而按钮在 `refreshProject → renderSummary` 里会被按 `can_resume` 重新解禁，早于 `finally` 复位 `flowBusy`；落在这个窗口的点击不发请求、不报错。（后者由重试日志实证：3 次点击中 2 次被吞。）后端行为正确：直连 API 走同一序列 `storyboard → production_ready` 只用 0.2 s | 测试自身缺陷，非产品缺陷 |

第 3、5 项已在后续排查中定案（见 14.10 节），两者都**不是产品缺陷**：p6b 是夹具与"本机没有 ffmpeg"这一环境假设冲突，p8a 是测试的兜底用了一个语义不同、且带防重复保护的接口。第 4 项（`test_ui_workbench.py`）仍未定案。

### 14.10 第 3、5 项的定案过程

这两项都不是靠重跑、而是靠**旁路取证**定案的。方法记下以便复现：

**p6b** —— 两段证据合起来闭环：把夹具字节原样喂给真 ffmpeg（本机一条命令即可），得到 `moov atom not found` / `Invalid data found when processing input`；再读 `frontend/js/render.js:1132`，确认 `#assemblyFreshness` 的模板是"只有 `current`（已有成片）时才输出"。夹具造不出成片 → 元素永不出现 → 20 s 超时，因果完整。

**p8a** —— `tools/test_p8a_browser.py` 起验收后端时把 stderr 丢进 PIPE 且只在启动失败时读取（第 78-80 行），工作流内部发生什么完全看不到。因此写了一次性探针 `tmp/_p8a_api_probe.py`：隔离数据目录、强制关闭全部 LIVE 开关、直连 API 走完整条审核链并逐步计时，同时把后端 stderr 落盘到 `output/playwright/stageC/probe_p8a_backend.log`。实测 `run→scope` 2.8 s、`scope→bible` 0.2 s、`bible→storyboard` 0.2 s、`storyboard→production_ready` 0.2 s，全部成功、`shots=4`、`job_events` 13 条 —— 同一套操作后端 0.2 秒就能完成，所以失败在浏览器/测试侧。

旁证：脱离回归驱动器、单进程重跑 `test_p8a_browser.py`（`VISIONCRAFT_DATA_DIR=output/playwright/stageC/probe-p8a-solo`）**仍然失败**，且失败点比在驱动器里更靠后（通过 5 项而非 4 项）。可见它与"是否被驱动器包裹"无关，也说明失败点会随界面同步时机漂移。

> 补充（14.11）：旁路取证这一步是对的，结论"失败在浏览器/测试侧"也成立，但当时对**具体机制**的归因不完整 —— 真凶是界面读数滞后与点击被 `flowBusy` 静默吞掉，而不是兜底端点选错（那只是一个潜伏隐患，本轮一并修掉）。

**两项的共同教训**：失败信息都藏在看不见的地方 —— 作业在后端进程内失败而 access log 只记 `200 OK`；测试的兜底路径静默降级、不留日志。定位靠的是旁路取证，不是重跑。

**本轮同时完成的文档修正**：

- `README.md`：环境变量段重写（授权门表格 + `ALLOW_LIVE_LLM` 连带语义的 ⚠️ 提醒）；功能状态表把首尾帧如实标为"已完成（首尾帧模式除外）"；常见问题新增 `BLOCKED_BEFORE_CALL` 等三行；`FFmpeg` 环境要求行按 `_ffmpeg_executable()` 的真实查找顺序改写（不必改系统 PATH）。
- `.env.example`：全量重写并逐键标 `[护栏]`/`[必需]`/`[可选]`/`[遗留]`。双向比对后确认：代码读而示例缺的 `VISIONCRAFT_BASE_URL`、`VISIONCRAFT_FFPROBE` 属**测试夹具**变量，正确地不进应用配置模板（已改到第 7 节说明）；示例里代码从不读取的 `VISIONCRAFT_ENV`、`VISIONCRAFT_PROVIDER_MODE` 已标 `[遗留]` 并注明已无作用。
- 第 7 节：补上回归驱动器入口，以及三条实测踩出来的前提（6 个脚本不自带后端、空库时 `#newProjectBtn` 故意禁用、`VISIONCRAFT_BASE_URL` 决定测试目标地址）。
- 第 9 节排期表与 README 开发计划：把首尾帧那条的出处写明是**工作区根目录** `../task_plan.md` 的 Phase 3（仓库内有两份同名 `task_plan.md`，此前写法有歧义）。
- 新增 `docs/stage-c-evidence-archive-2026-09-20.md`：真实 2 镜成片的 ffprobe、哈希、审计、截图、数据库备份与费用说明归档。

**阶段 C 剩余**（14.9 当时的判断）：C-1 绿（上述 5 项）；C-4 人工网页冒烟；C-5 归档文档已出，待补回归结论。

### 14.11 阶段 C-1 收口：48/48 全绿，五项失败全部定案并修复（2026-09-20）

**结论先行**（证据：`output/playwright/stageC/run-20260920-142409/`，同时也是 `stageC/no_cost_regression_report.json`）：

```
checks : 48/48 passed
asserts: 398 pass / 0 fail / 4 skip
elapsed: 1399.6s
shared backend : http://127.0.0.1:8070 (started_by_runner=True)
```

**验收口径**（竹木定）：48 项全绿；确有特殊原因的允许 skip 但**必须带理由**。为此把 4 条 skip 逐条查到了出处 —— 结果显示这 4 条里只有 2 条是真的少跑了用例：

| # | skip 原文 | 出处 | 性质 |
|---|---|---|---|
| 1 | `SKIP: inflight_remote_tasks db_tasks=1 db_shots=1` | `tools/run_live_2shot.py:290` 的**拒绝提示**，紧邻的下一行就是 `PASS: 脚本异常且 lineage 过期时，DB inflight 仍阻止清理`（该 check 日志第 23/24 行） | **不是跳过**。那是被测工具按预期拒绝清理的输出，正是断言对象。计数器的粗口径误计 |
| 2 | `SKIP: 数据库中没有非活动任务可供断言` | `tools/test_retire_remote_video_task.py:50` | **隔离导致的覆盖缩水**。真实工作库有 8 条 `video_tasks`（7 completed / 1 failed，只读实测），这两条在真实库上会跑 |
| 3 | `SKIP: 数据库中没有 video_tasks 记录` | 同上 `:66` | 同 #2 |
| 4 | `SKIP: 视频阶段只有一个模型，无法切换` | `tools/mock_web_smoke.cjs` | **能力所限**：Mock 模式只暴露一个视频模型，"切换模型"无从演练。特殊原因成立 |

为让这类归因以后可复核而不是只留一个数字，`run_no_cost_regression.py` 的 `summarise()` 现在把**命中原文行**一并写进报告（`skip_lines`），并说明它只是提示、不是结论。

**五项失败的最终归属**（全部是测试侧问题，产品代码未因此改动）：

| 检查 | 修法 | 结果 |
|---|---|---|
| `test_cleanup_temp_project.py` | 种入夹具；并修掉测试自身的查询缺陷——原查询没排除 `cleanup_temp_project.PROTECTED`，会选中 `v1demo_main` 再去断言"工具应拒绝"，必然失败 | 6 pass / 0 fail / **0 skip**（原先 1 项 skip） |
| `test_live_2shot_create_guard.cjs` | 根因是 `app.js:55` 的异步 `init()` 竞态：点「新建」早了会被随后的 `renderAll()` 重新隐藏表单，`fill(..., {force:true})` 在隐藏元素上**静默失效**。改为等状态稳定再点 + 填后回读断言 | 10/10 |
| `test_ui_workbench.py` | 同上的 init 竞态（`openCreateForm` 稳定化 + 标题回读），外加处理脚本自己撞上的「未保存草稿」弹窗（`dismissUnsavedGuardIfAny`，并留一行可见日志） | 14/14 |
| `test_p6b_assembly.py` | 夹具与环境假设冲突：本机有 ffmpeg（`StoryCraft\.tools\ffmpeg\bin`），真实 concat 分支会跑，夹具却写假字节。改为用项目自身的探测口径 `tools.p6c_ffmpeg.ffmpeg_available()` 并按需生成**真短片** | 6 pass / 0 fail（此前 1 失败） |
| `test_p8a_browser.py` | **14.9 的初判不成立**，见下 | 11/11 |

**p8a 的真因（推翻 14.9 的归因）**。两条，都在浏览器侧：

1. **界面读数滞后**。`waitProject` 只看后端 status，一翻转就返回；界面要等 4 秒轮询或一次 `refreshProject` 才重绘。而原脚本紧接着的等待条件是"横幅文案含『等待审核』"——范围审核与 Bible 审核的横幅**都**满足，因此它是**空转**，下一行读到的执行状态仍是旧值，报出"确认范围后未进入 Bible 审核"。
2. **点击被静默吞掉**。`app.js:999` 的 `onResumeWorkflow` 开头是 `if (!state.project || flowBusy) return;`；而按钮在 `refreshProject → renderSummary` 里会按 `can_resume` 重新解禁，早于 `finally` 复位 `flowBusy`。落在这个窗口里的点击**不发请求、不报错**，现象与"后端拒绝推进"完全一致。

第 2 条由修复后的重试日志直接实证（3 次点击中 2 次被吞）：
```
WARN: 第 1 次界面点击后后端仍停在 bible_review，重试（界面 flowBusy 窗口吞掉点击）
WARN: 第 1 次界面点击后后端仍停在 storyboard_review，重试（界面 flowBusy 窗口吞掉点击）
```
定位手段是**旁路取证**：后端链路本身用一次性探针跑通（`run → scope 1.3s`、三次 resume 各 0.2s、`storyboard → production_ready`、`shots=4`，全部 HTTP 200），从而把嫌疑锁定在浏览器侧；再把 `test_p8a_browser.py` 后端 stderr 从**没人读的 PIPE** 改为落盘文件后才看得到全貌。兜底的 `POST /api/projects/{id}/resume`（不带 id）确实是个潜伏隐患——它会被 `workflow_control_service.py:192-199` 的幂等守卫原样返回——但**不是本次失败的原因**，已一并改为带 checkpoint id 并强制留日志。

**本轮同时修掉的两个隐患**：

- **跑一次回归会覆盖真实归档证据**。`tools/test_live_2shot_create_guard.cjs` 无论谁跑都往 `output/playwright/live-2shot/` 写 `create_guard_report.json`、`create_guard_backend.log`、`create-guard-1440.png`（实测：2026-09-20 14:23 的回归已把那三个文件覆盖）。现改为：有 `VISIONCRAFT_DATA_DIR`（即回归运行）就写进运行目录，人工单独跑才写归档。**已损部分**：那三个文件现为回归产物；所幸它们不在 `browser_screenshot_hashes.json` 的哈希清单内（该清单只覆盖 11 张 `NN-*.png`），未连带造成哈希不一致。
- **`start_shared_backend` 会复用已占 8000 的服务**，而它同时把宿主的批量删除护栏对子进程关掉——那条分支下验收脚本的删除动作可能落到用户真实库。经核实那 6 个脚本**全都**读 `VISIONCRAFT_BASE_URL`，于是改为自动选空闲端口（8070+）并导出该变量，**彻底删除复用分支**。注释里"一切都在隔离数据目录下"这句话，至此才是真的。另：`shared_backend.log` 从 `stageC/`（各轮共用一个路径、互相覆盖）改为各自运行目录内。

> **本节残留事项 1～3 已由 14.12 处置并实测，第 4 条仍待定——引用本节状态前请先读 14.12。**

**残留事项（未做，须竹木决定）**：

1. **`video_tasks` 夹具**：种一条 `completed` 记录即可让上表 #2/#3 真正执行。未做是因为爆炸半径待评估（`has_waiting_remote_video`、retire 工具的"活动任务数"、合成类用例都可能受影响），且当前 48/48 已达标。
2. **界面 `flowBusy` 窗口算不算产品缺陷**：按钮在 `flowBusy` 为真时仍显示可用，用户点了没反应且无提示。这超出 C-1 范围（且会牵动其他用例基线），仅记录，未改。
3. **7 个脚本仍在用"没人读的 PIPE"装后端 stderr**：`test_local_keyframe_browser.py:127`、`test_mock_video_refresh.py:269`、`test_mock_web_smoke.py:228`、`test_p7c_ui_state_browser.py:434`、`test_p8b_browser.py:181`、`test_v1_demo_browser.py:189`、`test_v1_qa_browser.py:79` —— 全部只在"启动失败"时读一次，属同一盲区。p8a 已作为样板改掉，其余未动（它们都在验收范围内，中途改动会让本轮全量作废）。
4. **隔离是运行级而非用例级**：48 项共用同一个 `VISIONCRAFT_DATA_DIR`，因此失败点会随执行顺序漂移（这一现象在 14.9 的 #4/#5 上出现过）。

### 14.12 阶段 C 收尾：夹具爆炸半径评估、flowBusy 缺陷修复、stderr 盲区清理（2026-09-20）

本轮按竹木定的三件事推进：先收完阶段 C 尾巴；种 `video_tasks` 夹具**先评估爆炸半径**；界面 `flowBusy` 吞点击**定性为产品缺陷，要修**。全部无付费调用，费用 0 元。

| 14.11 的残留 | 本轮结果 |
|---|---|
| 1. `video_tasks` 夹具未种（爆炸半径待评估） | ✅ 已评估并种入，实测 5/5、48 断言 0 失败 **0 skip** |
| 2. `flowBusy` 窗口算不算产品缺陷 | ✅ 定为产品缺陷并修复；p8a 从"3 次点击被吞 2 次"变为 **0 次** |
| 3. 7 个脚本的后端 stderr 盲区 | ✅ 统一改为落盘 |
| 4. 隔离是运行级而非用例级 | ⏳ 仍未做，见 14.12.4 |

#### 14.12.1 `video_tasks` 夹具：爆炸半径评估

**评估方法。** 把**每一个**读 `video_tasks` 的位置列出来，按它自己的筛选条件判断夹具会不会被误命中。这比"种一条跑一遍看红不红"可靠——命中与否是由条件决定的，不是运气决定的。

**三条硬约束**，少一条就会污染别的用例：

1. **行不能悬空。** `backend/database.py:20` 会执行 `PRAGMA foreign_keys = ON`，而 `video_tasks` 的 `project_id` / `shot_id` / `version_id` 三列又都是 `NOT NULL REFERENCES`（`schema.sql:232-236`、`database.py:142-168`）。指向不存在项目的行**根本插不进去**，所以容器必须真实存在 项目 → 镜头 → 版本 三级。这一条推翻了"种一条孤立记录最省事"的想法。
2. **状态必须是 `completed`。** 所有可能被误触发的守卫都筛"活动状态"（见下表）。
3. **容器必须是没有真实生成流程会碰的项目。** `video_service.py:54` 的 `task_count = SELECT COUNT(*) FROM video_tasks WHERE version_id = ?` 决定"该版本是否已有视频任务"（`prepare_version_for_generation` 据此选择复用还是克隆新版本）。挂在演示项目 `v1demo_main` 上会改变那些镜头的局部重生成行为，而它正被 v1-demo 与工作台检查真实驱动。

**受影响清单**（逐条查过，不是抽样）：

| 读 `video_tasks` 的位置 | 筛选条件 | 种 `completed` 后 |
|---|---|---|
| `workflow_control_service.py:227` `has_waiting_remote_video` | `status IN ('running','pending_remote','submitted')` | 不命中 |
| `video_service.py:486` `refresh_project_video_tasks` | `status IN ('running','pending_remote')` | 不命中 |
| `run_live_2shot.py:80` / `:268` 付费预检的活动计数 | 四种 inflight | 不命中 |
| `cleanup_temp_project.py:69` 在途任务守卫 | 四种 inflight | 不命中 |
| `video_service.py:262` 安全重试的错误上下文 | `status='failed'` + 同项目同镜头 | 不命中 |
| `video_service.py:54` / `shot_edit_service.py:327` | 按 `version_id` 计数 | **会命中 → 故不能挂真实生成流程会碰的项目** |
| `test_adaptation_workflow.py:249` | 自己创建的项目，断言 `== 0` | 不命中（容器不是它造的项目） |
| `test_retire_remote_video_task.py:46` / `:64` | 非活动行 / 任意行 | **目标：两条都真跑** |

**容器最终选 `project_c0ffee0001`**，依据是"不存在真删它的路径"：`test_cleanup_temp_project.py` 全部 5 个用例都是**拒绝或 dry-run**（`--apply` 只出现在"受保护项目"与"前缀不匹配"这两个必然失败的调用上），文件内没有删除非空项目的用例。

**实测**（`output/playwright/stageC/run-20260920-150326/`，命令加 `--only retire_remote,adaptation_workflow,cleanup,job_center,v1_demo`）：

```
checks : 5/5 passed
asserts: 48 pass / 0 fail / 0 skip        ← 这一轮 0 skip
elapsed: 78.6s
video_task fixture : project_c0ffee0001 / shot_50a2670e5c / version_5ec4df2b27 / completed
```

- `test_retire_remote_video_task.py`：**5 pass + 2 skip → 6 pass / 0 skip**，14.11 表里的 #2/#3 不再少跑用例。
- `test_adaptation_workflow.py`：10 pass，其中含 `assert tasks["n"] == 0` —— 按项目计数的断言未被污染，这是"容器选对了"最直接的证据。
- `test_v1_demo_browser.py`：18 pass，确认 `v1demo_main` 未受影响。

夹具写进 `tools/seed_regression_fixtures.py`（`ensure_video_task_fixture`，幂等），runner 在 `test_retire_remote_video_task.py` 之前补种一次（`--ensure-video-task`）——因为 `ON DELETE CASCADE` 意味着前面某个检查清理自己的项目时，可能把它一并带走。

#### 14.12.2 `flowBusy` 静默吞点击：按产品缺陷修复

**根因**（都在前端，属产品代码缺陷而非测试缺陷）：

`onResumeWorkflow` 的 `finally` 是无条件解禁：

```js
} finally {
  flowBusy = false;
  el("resumeWorkflowBtn").disabled = false;   // ← 无条件
}
```

而它前面的 `await refreshProject()` 会走 `renderSummaryFields`，按 `can_resume` 把按钮**解禁**（`render.js:320`）。于是出现一个窗口：**按钮已显示可用，但 `flowBusy` 仍为真**——落在里面的点击进入 `if (!state.project || flowBusy) return;`，**不发请求、不报错、也没有提示**。横幅按钮同理：`renderGateBanner` 整块 `innerHTML` 重建，新按钮天然可用，而 `onFlowAction` 的守卫照样吞。

**修法**：让"忙"被渲染层看见，按钮可用态**只留一个来源**。

1. `flowBusy` 从 `app.js` 的模块级变量移进共享的 `state`（`state.js`）。`render.js` 本来就 import `state`；直接读 app.js 的模块变量会形成循环依赖。
2. `render.js` 两处渲染带上它：`renderSummaryFields` 的 `#resumeWorkflowBtn`（`!canResume || state.flowBusy`）与 `renderGateBanner` 的四个 `[data-flow]` 按钮（`gateLocked`）。
3. 两个 `finally` 不再手动改 `disabled`，改为复位 `state.flowBusy` 后 `renderAll()` —— 可用态交回渲染层按 `can_resume && !busy` 统一决定。原先 `onFlowAction` 里那句 `trigger.disabled = false` 改的是**已被重绘替换掉的旧节点**，本来就不起作用。

**验证**：同一条链路修复前 3 次界面点击被吞 2 次，修复后日志里**一次重试都没有**：

```
INFO: 界面点击「继续执行」已生效（scope_review → awaiting_bible_review）
INFO: 界面点击「继续执行」已生效（bible_review → awaiting_storyboard_review）
INFO: 界面点击「继续执行」已生效（storyboard_review → awaiting_storyboard_review）
```

配套检查：`test_p8a_browser.py` 11 pass、`test_ui_workbench.py` 14 pass、`test_live_2shot_create_guard.cjs` 10 pass、`test_workflow_pause_resume.py` 8 pass、`test_mock_web_smoke.py` 18 pass。

p8a 脚本里"点完回查、没生效就重试"的逻辑**保留**——它现在是一条兜底断言，而不是在掩盖缺陷。

#### 14.12.3 7 个脚本的后端 stderr 盲区

同一模式的 7 个脚本（`test_local_keyframe_browser.py`、`test_mock_web_smoke.py`、`test_mock_video_refresh.py`、`test_p7c_ui_state_browser.py`、`test_p8b_browser.py`、`test_v1_qa_browser.py`、`test_v1_demo_browser.py`）都把后端输出写进 `stderr=subprocess.PIPE`，且**只在启动失败时读一次**。作业在进程**内**失败时，后端说的话一句都取不到——失败只能从浏览器侧描述成"状态没变"，把"后端拒绝了"和"后端压根没被调用"混为一谈；无人排空的管道写满还会反过来阻塞服务。p8a 的归因当时就是被这一点卡住的。

现统一改为落盘到运行目录（`VISIONCRAFT_DATA_DIR`，缺省 `stageC/`），并把日志路径打进输出。7 个文件用一次性补丁脚本批量改，**锚点要求唯一匹配才写入**——dry-run 恰好拦下了两种结构差异（失败提示文案为"冒烟后端…"而非"验收后端…"），避免了误改。补丁脚本本身不进交付物。

#### 14.12.4 仍未处置

1. **隔离是运行级而非用例级**（14.11 第 4 条）：48 项共用同一个 `VISIONCRAFT_DATA_DIR`，所以同一个缺陷在不同轮次可能停在不同的检查上。夹具与 `--touch-only` / `--ensure-video-task` 钩子只压住了这个问题的**症状**，没有解决根因；要真正消除，得让每项用例拿到自己的数据目录。
2. 余下的 skip 只剩 1 条：`mock_web_smoke.cjs` 的"视频阶段只有一个模型，无法切换"，属 Mock 能力所限。


### 14.13 阶段 C 收口完成：最终全量 48/48、C-3 浏览器侧闭环（2026-09-20）

#### 14.13.1 最终全量回归

`run-20260920-150736/`：

```
checks : 48/48 passed
asserts: 400 pass / 0 fail / 2 skip
elapsed: 535.7s
```

相对 14.11 那一轮（398/0/4），断言多了 2 条、skip 少了 2 条——减少的正是 14.12.1 里那两条"隔离导致覆盖缩水"（`test_retire_remote_video_task.py` 从 5 pass + 2 skip 变成 6 pass / 0 skip）。

**剩下 2 条 skip 的逐条归因**（原文行已随报告入库，见 `skip_lines` 字段）：

| skip 原文 | 性质 |
|---|---|
| `SKIP: inflight_remote_tasks db_tasks=1 db_shots=1` | **不是跳过**。它是 `tools/run_live_2shot.py:290` 的**拒绝提示**，紧邻的下一行就是该守卫的 `PASS`；runner 的计数器按"含 SKIP 字样就计一笔"的粗口径误计。runner 现已把命中的原文行写进报告，并注明它只是提示、不等于结论 |
| `SKIP: 视频阶段只有一个模型，无法切换` | Mock 能力所限，特殊原因成立 |

#### 14.13.2 C-3 的浏览器侧：`blocked_by` 此前是空转字段

C-3 原本只完成了后端 payload（`authorized` / `keys_present` / `blocked_by` + 改写后的 `hint`）。核查界面时发现：

- `hint` **有**被消费（`render.js` 的 `generationModePickerHtml`）；
- 但 `blocked_by` **没有任何界面消费**（全仓 grep 只有后端定义处），也就是说"缺密钥"与"缺授权"这份分开上报的逐条原因，**永远到不了用户眼前**。这与 C-3 的目的（诊断与错误提示核实）恰好相反——后端做了区分，界面又把它抹平成一个笼统的"不可用"。

修法：`generationModePickerHtml` 在「已选真实模式且 `live_access.ready === false`」时，把 `blocked_by` 渲染成一份渐次列表（`[data-live-blocked]` + `.live-blocked-list`），放在说明段落之后；CSS 只补 6 行列表内边距。

配套新增一条回归检查（写在 `tools/mock_web_smoke.cjs` 里，因为它已经是"默认模型/生成模式"这一段的覆盖者）：保存 `live_strict` → 断言 `data-live-ready="false"` → 断言 `[data-live-blocked]` 文本含"授权未开启"→ 截一张 `01b-live-blocked-1440.png` → 切回 `mock`。

`--only mock_web_smoke,stage_models,safeguards` 实测 **3/3、57 pass / 0 fail**，其中 `test_mock_web_smoke.py` 从 18 pass 变为 **19 pass**。

实拍结果与后端 payload 一致：本机密钥已配置，所以列表只报三条"真实调用授权未开启"（文本/视觉/视频），没有误报"未配置密钥"——这正是把两个门槛分开上报的意义所在。

#### 14.13.3 C-3 的完整口径（此后不欠）

| 层面 | 状态 | 证据 |
|---|---|---|
| 后端 payload | ✅ 分开上报 `authorized` / `keys_present` / `blocked_by`，且不含任何环境变量名（`test_stage_models.py` 对此有断言） | `backend/providers/capabilities.py` |
| `/api/health` 语义 | ✅ `mode` / `llm_live` 只表示 Key 已配置，不代表已授权；`live_access` 与 `note` 一并返回 | `backend/main.py:139-149` |
| 界面渲染 | ✅ `hint` 与 `blocked_by` 都消费 | `render.js` `generationModePickerHtml` |
| 浏览器侧验证 | ✅ 新增回归检查并实测通过，附截图 | `run-20260920-151819/`、`mock-smoke/01b-live-blocked-1440.png` |

#### 14.13.4 提交后复跑抓到第三个竞态实例：`test_local_keyframe_browser.py`

**过程值得记下来**：C-3 改动了共用渲染层 `frontend/js/render.js`，旧的全量数字不再覆盖新代码，于是在**已提交的 HEAD 上**重跑全量（`run-20260920-152129/`）。结果 **47/48**：

```
[37/48] test_local_keyframe_browser.py ... FAIL 29.2s
  page.waitForSelector: Timeout 20000ms exceeded.
  waiting for locator('#projectForm:not(.hidden)') to be visible
  at main (tools/local_keyframe_ui.cjs:99:16)
```

**与前面的五项失败同属一类，是同一 `init()` 竞态的第三个实例**（前两个是 `test_live_2shot_create_guard.cjs` 与 `test_ui_workbench.py`）。原代码是这样等的：

```js
await page.waitForFunction(() => {          // ← 表单或摘要**任一**可见就通过
  const form = document.querySelector("#projectForm");
  const summary = document.querySelector("#projectSummaryPanel");
  return Boolean(form && summary && (!form.classList.contains("hidden") || !summary.classList.contains("hidden")));
}, null, { timeout: 15000 });
if (!(await page.locator("#projectForm").isVisible())) await page.click("#newProjectBtn");
await page.waitForSelector("#projectForm:not(.hidden)");   // ← 一次性等待，撞上 init 收尾就超时
```

前一句的等待条件太松（上一轮渲染就满足，等于空转），于是后一句的一次性等待正好落在 init 收尾把表单模式重置回摘要态的窗口里。**它是偶发的**——同一次改动前的 `run-20260920-150736/` 里这一项还是 PASS。这也解释了为什么它会潜伏到现在。

**顺带抽了共享模块 `tools/ui_project_form.cjs`**（`openCreateForm` 稳定等待 + `fillProjectForm` 填入并读回校验）。同一份逻辑此前在三个脚本里各写了一份——各留一份就等于第四个还会再踩一次；`mock_web_smoke.cjs` 是第四处（无条件点「新建」+ 一次性等待 + `force` 填入，本轮侥幸未触发），一并统一。四处调用点现在都是同一个函数。

`--only local_keyframe,ui_workbench,create_guard,mock_web_smoke` 实测 **4/4、52 pass / 0 fail**（`run-20260920-153231/`）。

**这条经历本身就是结论**：48/48 是**单次运行**的结果，不是"这个缺陷不存在"的证明。改动共用代码后必须在新 HEAD 上复跑；对偶发项，一次 PASS 不足以定案。

#### 14.13.5 再复跑又换了一项：第五个实例，于是改为全量清扫

在 `bca0323` 上再跑全量（`run-20260920-153453/`）→ **47/48**，这次是 `test_adaptation_start_refresh.py` 在 `adaptation_start_refresh.cjs:50` 等 `#projectForm:not(.hidden)` 超时。

**同一根因的第五个实例。** 两次连续全量各有一项偶发失败（前一次 #37、这次 #21），说明此前那两个"48/48"是运气好，不是这一缺陷类不存在。

于是不再逐次打地鼠，改为**全量清扫**。判据很直接：`tools/*.cjs` 里凡是出现 `#titleInput` / `#sourceTextInput` 的，就是驱动新建表单的脚本。

| 文件 | 原写法 | 处置 |
|---|---|---|
| `adaptation_start_refresh.cjs` | 点一次「新建」+ 一次性等 5s | 改走共享辅助（**本次失败项**） |
| `v1_qa.cjs` | 按 `:not(.hidden)` 判一次可见 → `force` 点击 → 一次性等 | 改走共享辅助 |
| `v1_demo.cjs` | 按单个 class 判定是否点击 → 一次性等 | 改走共享辅助 |
| `ui_workbench.cjs` | 第二处「点新建 + 一次性等」，服务于"新建只清空表单、不改当前项目"这条断言 | 改走共享辅助。表单在该处确为隐藏，辅助仍会真点一次，**断言强度不变** |
| `live_2shot.cjs` | locator + `force` 填入，且自带**第五份** `openCreateForm` | 于 **14.14.6** 改走共享辅助（付费驱动器，不在 48 项内，本轮只做静态核对） |
| `p6b/p6c/p6d/p6e`、`p7c_ui_state`、`p8b_assets`、`mock_video_refresh`、`p8a_pause_resume` | 只有 `waitForSelector("#newProjectBtn")`（页面加载等待），项目经 API 创建 | **无需改**：不驱动表单 |

清扫后静态核对：`openCreateForm` / `fillProjectForm` 的使用方共 7 个，require 全部齐备。

> **一处自己犯的错，值得记**：第一遍清扫漏了 `v1_demo.cjs` 的 `require`，验证跑当场报 `ReferenceError: openCreateForm is not defined`（6/7）。这正是"改完必须实测"的价值——静态看代码是"对的"，跑起来才露。

**连续两次全量全绿**（同一份代码，两次独立运行）：

```
run-20260920-154915/ : 48/48   401 pass / 0 fail / 2 skip   567.5s
run-20260920-155904/ : 48/48   401 pass / 0 fail / 2 skip   567.2s
```

### 14.14 工程账收口：回归隔离由运行级下沉到用例级（2026-09-20）

本节对应 14.12.4 第 1 条——那条当时写明"夹具与钩子只压住了症状，没有解决根因"。

#### 14.14.1 根因有直接证据，不只是推断

旧机制下 48 项共用一个数据库。逐个开只读连接查那两次全绿运行的库，能看见**检查确实把状态留在了库里**：

| 运行 | 根目录共享库 | 残留的非夹具项目 | per-check 目录 |
|---|---|---|---|
| `run-20260920-154915` | 有 | `project_88c52ef597` | 0 |
| `run-20260920-155904` | 有 | `project_0b70ceff48` | 0 |
| `run-20260920-161641`（新机制） | **无** | — | 35 |

两个 id 都不属于夹具（夹具是 `v1demo_main` / `project_a43afde7c5` / `project_c0ffee0001`），也没有被创建它的检查清理掉——**留在库里供后面每一项观察**。而留下的是哪一个、叫什么名字，每次运行都不同。这就是"同一个缺陷停在不同检查上"的机制本身；`--touch-only` 与 `--ensure-video-task` 两个钩子，只是把其中最刺眼的两条症状按回去。

#### 14.14.2 改法：模板 + 每项一份

- 运行目录下建 `_template/`：`tools/seed_regression_fixtures.py` 跑一次（实测 12.9s），产出含 3 个夹具项目的完整库（含一版真实 FFmpeg 成片）。
- 每条**需要数据库**的检查：`shutil.copytree` 拿一份自己的目录（48 项中 35 项）。
- **只有 6 项**需要驱动器提供后端（`SERVER_DEPENDENT`）。这是改造前实测确认的：`BASE = os.environ.get("VISIONCRAFT_BASE_URL", ...)` 这一模式恰好只出现在那 6 个文件里，其余脚本自起服务并继承自己的 `VISIONCRAFT_DATA_DIR`。驱动器为这 6 项**各起一个**后端、跑完即停。
- 报告 schema 升到 `visioncraft.no_cost_regression.v2`，新增 `isolation` 段（模板路径、模板是否种入成功、拥有独立目录的用例数、需要驱动器后端的清单），每条结果带自己的 `data_dir`。
- 模板构建失败改为**直接终止**（返回 1），不再只 WARN：起点不可信时，后面 35 项的结果没有意义。
- 删除 `seed_project_if_empty`、`touch_guard_fixture`、`ensure_video_task_fixture` 与 `--no-seed`。

#### 14.14.3 实测：连续两次全绿，且钩子已删

| 运行 | 结果 | 断言 | 耗时 |
|---|---|---|---|
| `run-20260920-161641` | 48/48 | 401 pass / 0 fail / 2 skip | 602.1s |
| `run-20260920-162702` | 48/48 | 401 pass / 0 fail / 2 skip | 594.9s |

与隔离前同口径的两次（`run-154915` / `run-155904`：401/0/2）断言数**完全一致**，而那两个钩子已经删掉了——说明那两条前提确实由模板接管，不是被漏掉。

隔离生效的取证（对 `run-20260920-161641` 的 35 个库逐个只读查询）：

- 根目录没有 `visioncraft.db`；
- 34 个库的项目集合**完全相同**（3 个夹具、8 个镜头）；
- 唯一例外是 `test_live_2shot_create_guard.cjs` 的库：多出 1 个它自己创建的项目 `project_8c83d8d190`。在旧机制下，**这一个正是留在共享库里的那种残留物**（见 14.14.1 表）。

#### 14.14.4 代价

- 每次全量 +约 30s（6 个后端启动 + 35 次目录拷贝）：567s → 602.1s / 594.9s。
- 每个 run 目录约 34 MB。**run 目录从不删除**，全量跑多了会在 `output/` 下累积；`output/` 已在 `.gitignore` 内。

#### 14.14.5 一并修掉的三处口径

1. P3 被两个规划文件误标为未完成（本节第 9 节第 5 项、工作区根 `../task_plan.md`）——已核实接口存在并勾回。
2. 本节（原文第 7 节）描述旧机制的三处文字：单后端、"种入夹具项目"、`--touch-only` 复位。
3. `README.md` 的 §无费用回归。

#### 14.14.6 `live_2shot.cjs` 的表单驱动统一（未实测）

14.13.5 那张表里它标的是"不改"，本轮改掉了。它自己实现了一遍 `openCreateForm`（**第五份**，写法是"点一次 + 一次性等待"，比共享版更弱），并在填入时用 `page.fill(..., { force: true })`——正是会静默失效的那种写法。

改动：删掉本地实现，改 `require("./ui_project_form.cjs")`；填入走 `fillProjectForm`（带读回校验）；调用处补回它原本依赖的 `waitAppReady(page)`。

**它没有实测**：付费驱动器，跑一次就是真实扣费，不在本轮授权范围内。只做了静态核对——语法检查通过、共享模块导出可 `require`、全仓 grep 确认 `function openCreateForm` 只剩共享模块一份定义。**下次真实 2 镜测试时会顺带验证它**；若那时报错，先看这一处。

#### 14.14.7 仍未处置

- 每项独立目录解决的是**顺序依赖**，不是"用例自身有缺陷"（如 `test_p6b_assembly.py` 用假字节夹具去撞真 ffmpeg），后者是另一类问题，第十四节已有的定案不变。
- `tmp/` 下的一次性探针（`probe_isolate.py`、`probe_isolation_effect.py`、`probe_old_runs.py`）不进交付物，结论已写入本节。

两次都全绿才是可引用的事实。单次 48/48 此前出现过两次，而紧接着的两次都各挂一项——**所以"跑一次绿了"和"这事儿完了"之间还差一次复跑**。


### 14.15 阶段 P6 演示打包（一）：三条固定演示样本落成（2026-09-20）

方向由用户定案：先收工程账（14.14），再做 **P6 演示打包**，形态＝**录屏成片**。

#### 14.15.1 素材其实早就在，缺的只是"整理"这一步

路线图 P6 第 240 行要求三条固定演示样本：短文本完整闭环、不同模型同镜头对比、失败恢复案例。此前只有第一条以**本地夹具**（FFmpeg lavfi 彩条）形式存在——`v1demo_main` 的镜头视频是红/蓝/绿/黄四色色块，演示不了任何画质。

逐个开只读连接查工作库，发现三条样本需要的**真实素材全部已经落盘**：

| 样本 | 真实来源 | 落盘位置 |
|---|---|---|
| 短文本完整闭环 | `project_5fdac03f50`，2026-08-28 ark `doubao-seedance-2-0-260128` 真实生成 4 镜（各 5.09s / 1280×720） | `backend/data/projects/project_5fdac03f50/` |
| 同镜头多模型对比 | `i2v_ark_b97ea96a` / `i2v_dashscope_612ff237` / `i2v_minimax_f12c30c7`，2026-08-28 三家各一次 | 各自项目目录 |
| 失败恢复案例 | `project_a43afde7c5`，2026-08-31 `MiniMax-H3` 真实失败（HTTP 400 invalid task_id 2013） | `video_tasks` 表 + `output/playwright/archive-2026-09-20-failed-2shot/` |

**三家对比的前提经过验证，不是假设**：三份 `video_tasks.submit_payload` 里的提示词 sha256 完全一致（同一句 prompt），首帧 base64 的 sha256 也一致（同一张图）。所以"同镜头对比"这个口径成立。

#### 14.15.2 新增三件工具

- `tools/prepare_p6_demo_samples.py`：幂等准备 `p6demo_story` / `p6demo_compare` / `p6demo_recovery` 三个样本（`--clean` 只删 `p6demo_*`）。改编走本地 mock 规划器补齐方案/Bible/分镜各阶段数据，然后**用真实产物替换掉夹具视频**，最后用本地 FFmpeg 真实合成成片。
- `tools/make_p6_compare_sheet.py`：三家各抽第 1 秒的一帧横向拼成对比图（带 `ark 1280x720` 等标签），产出 `output/playwright/p6demo/compare-three-providers.png`。讲稿第 3 节引用它——**这是"同镜头不同模型"最直观的一张证据**：构图远近、人物比例、蛊虫与光雨位置全都不同。
- `tools/test_p6_demo_samples.py`：无费用断言（已接入驱动器，48 → 49 项）。

设计上有两点值得记住：

1. **源资产路径不跟随 `VISIONCRAFT_DATA_DIR`。** 检查跑在隔离数据目录里，而真实产物住在工作库，所以源路径固定在 `backend/data/projects`（可用 `P6DEMO_SOURCE_PROJECTS_DIR` 覆盖）。源资产缺失时整项 SKIP 并说明原因——那是外部前提，不是能在隔离目录里种出来的夹具。
2. **`--source-projects-dir` 参数**让样本能建在隔离目录、源资产仍读工作库，验收因此不会污染真实数据。

#### 14.15.3 顺手抓到的两个"看起来对、其实是假的"

1. **样本 B 一开始有 4 个版本，不是 3 个。** 改编流程会给每个镜头建一个自动版本，它不是"某家模型的输出"。第一版脚本直接在上面追加三家 → 版本历史里混进一条没有 provider/model 的记录。修法：挂三家之前先清空该镜头的版本并置空 `current_version_id`。
2. **`video_tasks` 有 `UNIQUE(provider, remote_task_id)`。** 样本 C 若照抄真实 `remote_task_id` 会与 `project_a43afde7c5` 的现存记录冲突。处置：`remote_task_id` 用标注为演示的 id，但 **`error_code` / `error_message` / `status_payload` 保留真实原文**（"错误原文一字未改"这条口径不能打折）。文档与测试都按这个边界写。

#### 14.15.4 实测

新增检查单独跑：`PASS 41.4s（39 pass / 0 fail）`；补上对比图断言后为 **41 项**。

全量回归（49 项）：

| 运行 | 结果 | 断言 | 耗时 | 第 34 项 |
|---|---|---|---|---|
| `run-20260920-170449` | 49/49 | 440 pass / 0 fail / 2 skip | 687.7s | 39 pass（无对比图断言） |
| `run-20260920-171633` | 49/49 | **442** pass / 0 fail / 2 skip | 736.9s | 41 pass |
| `run-20260920-172906` | 49/49 | **442** pass / 0 fail / 2 skip | 835.3s | 41 pass |

第一轮跑的是 39 断言的旧版检查，后两轮是 41 断言的新版——**口径不同的两轮不算"连续两次"**，所以补跑了第三轮。后两轮断言数完全一致（442/0/2）且全绿，才是这里的定案依据。

两台 skip 与 14.14 同口径，不是新增。

三家输出规格（回归里是**断言**，不是印象）：

| Provider | 模型 | 请求 | 实际输出 |
|---|---|---|---|
| ark | `doubao-seedance-2-0-260128` | 720p / 5s / 16:9 | 1280×720，5.09s |
| dashscope | `wan2.7-i2v` | 720P / 2s | 1110×828，2.02s |
| minimax | `MiniMax-H3` | 768P / 4s | 1024×768，4.46s |

**三家互不相同**——这本身就是这一段演示要讲的东西：逐镜头选模型是刚需，工作台必须把"请求的"和"实测的"分开记录。

#### 14.15.5 交付物与诚实性边界

新增 `docs/v1-demo-script.md`（录屏讲稿，三段各写：点什么、讲什么、看什么证据），并明确划出三条线：

- **真实付费产物**：A 段 4 镜、B 段三家、C 段失败记录，都是 2026-08 的真实调用；
- **本地生成**：A 段的 sine 背景音、本地 SRT 字幕、FFmpeg 合成、mock 改编；
- **会花钱的动作**：重跑真实三家约 2～3 元；用真实 Provider 演示"恢复成功"一次视频调用；重生成 A 段 4 镜约数十元量级。

授权口径由用户 2026-09-20 定：**单次消费 ≤ 20 元，且每次真实调用前先报清单**（Provider / 模型 / 镜头数 / 时长 / 分辨率 / 预计费用），点头才跑。

#### 14.15.6 仍未处置

- **录屏成片本身还没录**：讲稿与样本就绪，录制需要用户在场（涉及演示节奏与讲解）。
- **可选的真实录屏增强**（一次受控真实生成，≤ 5 元）已写进讲稿第 7 节，**未执行**，等单独授权。
- 首尾帧真实三家验收：仍未动。P5-B 长文本：**P5-B-1（章节树 / 按章范围）已于 2026-09-28 完成**，见 14.21；向量检索部分仍未动。

### 14.16 角色/场景视觉锚点数据通路（功能切片 1，2026-09-20）

#### 14.16.1 为什么先做这个

2026-09-20 竹木把方向从「P6 录屏」改为「先完善项目功能」，随后在四个候选里选定**锚点 + 审核门**。核查（有硬证据）表明缺口其实是两件事：

1. **锚点是半成品。** `characters.asset_id` / `scenes.asset_id` 字段在、前端 `assetPathById` 预览位也在，但**全仓没有任何流程会写入这两个字段**——`_sync_bible_cards` 按 `project_id + name` 匹配后 INSERT 时写死 `asset_id = NULL`，UPDATE 也不碰该列。实测工作库 7 个项目 12 个角色，`asset_id` **全为 NULL**。
2. **审核门缺一道。** `checkpoint_service.REVIEW_NODES` 只有 `storyline_review / scope_review / bible_review / storyboard_review / quality_gate`，**没有锚点门**；`assert_batch_generation_allowed` 只拦到「分镜未确认」，确认分镜后就能直接批量生成——中间没有"先花小钱看一张样片"的关卡。

两者天然是一件事（没锚点就无可审的样片），但按路线图 §5「一次只解决一个阶段的一个可验收切片」拆成两步：**本切片只做数据通路**（零状态机改动，爆炸半径小），审核门留作切片 2。

#### 14.16.2 交付内容

- 新增 `backend/services/anchor_service.py`：`attach_anchor` / `detach_anchor` / `list_anchors`，目标可按 id 或**名称**定位（名称是 Bible 卡片与 `characters` 表的既有一致口径），错误码区分 `INVALID_KIND` / `TARGET_NOT_FOUND`(404) / `TARGET_MISMATCH` / `ASSET_MISMATCH` / `ASSET_NOT_IMAGE` 等。
- `asset_upload_service.py` 新增两个上传角色 `character_anchor` / `scene_anchor`：**必须带 `anchor_name`**、**不接受 `shot_id`**；挂载失败时整批回滚（`_rollback_asset`），不留没人引用的孤儿素材。
- 三个端点：`GET/POST /api/projects/{id}/anchors`、`DELETE /api/projects/{id}/anchors/{kind}/{target}`。**解除只清外键，不删素材文件**——删素材是不可逆动作，不放在这个接口里。
- 前端：Bible 阶段角色/场景卡片内嵌锚点块（上传/替换/解除 + 缩略图），`resolveAnchor` 从 `workflowViewModel.js` 导出供渲染层复用。

设计上有一处必须记牢：**占位图 `create_placeholder_svg` 没有 `mime_type`**，所以判"是不是图片"只能 `mime.startswith("image/") OR type in _IMAGE_TYPES` 双条件——只看 mime 会把旧演示项目的锚点图误判成"非图片"。

#### 14.16.3 修掉的三个缺陷（两个是真·产品缺陷）

浏览器验收第一版没通过，逐条取证后定案：

| # | 现象 | 真因 | 性质 | 修法 |
|---|---|---|---|---|
| 1 | 上传后区块显示「已挂载」，块内却没有缩略图 | 预览被渲染成 `.anchor-block` 的**兄弟节点**；而 Bible 表单页没有别处展示这张图 | 产品缺陷 | 缩略图改由 `anchorControlsHtml(anchor, { withPreview: true })` 渲染**进块内**；素材详情面板已有大图，那里不传该选项以免重复 |
| 2 | 点「解除锚点」无任何反应 | `[data-anchor-clear]` 分支只挂在 `onInspectorClick`（绑 `#assetDetail`），而 Bible 阶段的锚点块渲染在 `#stageWorkspace`（由 `onWorkspaceClick` 处理）→ 点击被静默丢弃 | 产品缺陷 | 在 `onWorkspaceClick` 开头补同源分支，置于卡片选择之前 |
| 3 | 断言报「解除锚点不应删除素材：0 -> 1」 | 用例把基准值取在了**上传前**，上传后自然是 +1 | 用例缺陷 | 基准改取「上传后」，并把断言加强为「上传恰好新增 1 条素材」 |

**#2 是靠静态读代码查出来的，不是等它失败**：这正是本项目反复踩到的那个 bug 类——「控件挂在 A 容器、处理器绑在 B 容器」，点击不报错、不发请求、不留日志。写新控件的 click 分支时，先确认它所在容器的处理器是谁。

取证纪律照旧：`anchor_ui.cjs` 第一次 FAIL 时没有重跑猜因，而是用 DIAG 旁路取证打出 `executionStage` / nav 状态 / 卡片数，才定位到「Bible 阶段渲染的是表单式编辑器 `bibleStageHtml`，压根不产生 `.asset-card`」——锚点控件原先只挂在通用素材详情里，走不到那条路径。

#### 14.16.4 截图证据的修正

原实现用 `page.screenshot({ fullPage: true })`，但应用是**固定高度外壳 + 内部滚动区**，`fullPage` 拉不出工作区内容——三张截图实际只拍到工作区顶栏与一个 toast，**根本没有锚点块**。改为先 `scrollIntoViewIfNeeded` 再对 `.anchor-block` 单独截图（`anchor-0X-*-block.png`），未挂载/已挂载两态都能看清缩略图与按钮。

#### 14.16.5 实测

单项验收：

| 检查 | 断言 | 两轮实测 |
|---|---|---|
| `tools/test_anchor_assets.py` | 13 | 13 pass / 0 fail（25.4s，两轮一致） |
| `tools/test_anchor_ui_browser.py` | 5 | 5 pass / 0 fail（10.3s / 10.6s） |

全量回归（**51 项**，49 → 51）：

| 运行 | 结果 | 断言 | 耗时 |
|---|---|---|---|
| `run-20260920-194939` | 51/51 | 460 pass / 0 fail / 2 skip | 731.3s |
| `run-20260920-200211` | 51/51 | 460 pass / 0 fail / 2 skip | 689.8s |

**两轮口径完全一致（同为 51 项 / 460 断言）且都全绿**，这才是可引用的事实。断言数可交叉验证：上一轮基线 442 + 13（后端）+ 5（界面）= 460，与实测吻合——说明新增检查一条都没被静默跳过。2 条 skip 与 14.14 / 14.15 同口径，不是新增。

> 以上是 2026-09-20 当时的测量，**已过期**：**最近一次拿到两轮同口径全绿**的基线是
> **57 项 / 541 断言**（见 14.19.4）。这里保留原数字，因为它们是被取代的取证记录，不是表述错误。

`tools/run_no_cost_regression.py` 的接入点是三处：python 组在 `test_p6_demo_samples.py` 后插入 `test_anchor_assets.py`；browser 组在 `test_local_keyframe_browser.py` 后插入 `test_anchor_ui_browser.py`；`SERVER_DEPENDENT` 加入后者（**6 项 → 7 项**）。前者用 `fastapi.testclient.TestClient` 在进程内自起服务，因此**不进** `SERVER_DEPENDENT`。

#### 14.16.6 仍未做（切片 2 范围，勿读成已闭环）

- **锚点试生成审核门还没有。** 本切片只让锚点"能挂上去"，**没有让它成为花钱前的关卡**：现在确认分镜后依然可以直接批量生成。（**已由切片 2 补上**，见 14.17——本条里的"现在"指 2026-09-20。）
- 切片 2 的爆炸半径已探明：`production_ready` 被 `workflow_control_service.py:192`、`workflowViewModel.js:173`、`render.js` 及 `test_adaptation_workflow.py` / `test_medium_text_adaptation.py` / `local_keyframe_ui.cjs` / `test_p6d_assembly.py` / `test_p6c_real_assembly_browser.py` 等多处断言，**必须先改测试再改实现**。
- 锚点与镜头参考图是两个作用域，切片 2 需要决定"镜头缺参考图时是否自动取用锚点"，本切片只提供数据，未接自动取用。（**已由切片 3 回答**，见 14.18：不二选一——参考图模式下锚点图与镜头参考图按「镜头参考图 → 角色 → 场景」顺序并列发出；**首帧位置仍只认 `shot_versions.first_frame_path`**，"缺首帧时自动取用锚点"至今未实现。）

---

### 14.17 视觉锚点审核门（功能切片 2，2026-09-27）

**性质：** 补欠账。Phase 2 冻结的「三道人工确认关卡」此前只兑现了两道（确认改编范围、
确认 Story Bible、确认代表镜头）——`checkpoint_service.REVIEW_NODES` 里没有锚点门，
`assert_batch_generation_allowed` 只拦到「分镜未确认」，所以**确认分镜之后可以直接批量
生成**，"先看一张样片再花钱"这一关并不存在。本切片把它补上。

#### 14.17.1 落地的语义（甲方案：完整下沉状态机）

- 新增一等审核节点：`anchor_review`，新状态 `awaiting_anchor_review`，进
  `REVIEW_NODES` / `REVIEW_STATUSES` / `NODE_FOR_STATUS` / `PAUSE_REASON` 四张表。
- **确认分镜不再直接落 `production_ready`**，而是停在门口；`assert_batch_generation_allowed`
  在该状态抛 `ANCHOR_REVIEW_PENDING`（与「分镜未确认」用**不同**错误码，排错时能区分）。
- 放行走 `POST /api/projects/{id}/anchors/confirm`；就绪度走 `GET .../anchors/review`，
  并随项目 payload 的 `anchor_review` 字段下发。
- **放行口径（竹木选定「锚点就绪 + 显式确认」）：** 项目里有角色/场景时，**至少挂上一个**
  锚点才能点「确认锚点并开始制作」；`allow_without_anchors: true` 是显式跳过；没有角色也
  没有场景的空镜项目直接放行，不硬卡。刻意**不要求挂齐**——Bible 可能产出不重要的配角或
  抽象场景，要求全部出图会把流程锁死；`missing` 只用于界面提示还差谁。要收紧为「必须挂齐」，
  把 `anchor_review_readiness` 的 `ready` 从 `bool(attached)` 改成 `not missing` 即可。
- **重做分镜会重新过门**：这是「每一次批量生成前」的关卡，不是一次性标签。
- `confirmed_readonly` 刻意不动（`workflow_control_service` 那份 `PAST_STORYBOARD` 不含门内
  状态），否则门内阶段会变只读，反而挂不了锚点。

#### 14.17.2 测试先行与爆炸半径

先写 `tools/test_anchor_review_gate.py`（16 项断言，同时充当这道门的规格），先红后绿。它覆盖：
分镜确认后进门（非 `production_ready`）、批量生成被拦、有角色未挂锚点时确认被拒、挂锚点后
放行、无目标项目直接通过、`anchor_review` 检查点可暂停恢复、双端幂等、重生成分镜后重新进门、
全程零真实调用。

改实现本身只是几十行，**真正的成本在爆炸半径**：`production_ready` 被 17 处调用点（13 个文件）
和 30+ 处断言引用。按「先改测试再改实现」的原则同步了 `test_adaptation_workflow.py`、
`test_workflow_pause_resume.py`、`test_anchor_assets.py`，以及 5 个浏览器脚本、
3 个准备脚本（`prepare_p6_demo_samples.py` / `prepare_v1_demo.py` / `seed_regression_fixtures.py`
——它们的语义是"就绪项目"，必须显式过门，否则含义会悄悄变成"停在门口"）。

为避免 5 个浏览器脚本各写一份过门逻辑，新增共享模块 `tools/anchor_gate_ui.cjs`（照
`tools/ui_project_form.cjs` 的先例）。

#### 14.17.3 回归逼出的三个真缺陷（都不是测试问题）

1. **幂等分支把门口的 paused 检查点顺手 `complete` 了。** 门内重复点确认分镜时，幂等分支
   会把 `anchor_review` 那个 paused 检查点一起结掉，项目仍在门口，恢复却报 `NO_CHECKPOINT`。
   修法：幂等分支只完成**非锚点门**的 paused 检查点。
2. **就绪后仍显示"悬空跳过指令"。** 点过一次「跳过」武装后若又挂上锚点，横幅还在写
   「再点一次『确认跳过』即会放行」，而那个按钮已经不在横幅里——界面上不存在可执行该指令
   的对象。**这条是从验收截图里看出来的**（`anchor-05-gate-ready-banner.png`），不是靠断言。
3. **二次确认可被就绪度回摆绕过。** 武装状态原本跨动作留存，`0 → 1 → 0` 的就绪度回摆会让
   失效的武装重新算成有效："先武装、去挂锚点、再摘掉、回来一次点击"就能绕过二次确认——
   等于把刚建好的守卫自己拆了。

修法（2、3 一起）：就绪（或无需锚点）时把跳过入口与提示一并撤掉；武装连同当时的就绪度
快照一起记（`anchorSkipSignature` 公式只写一份，渲染层与处理层同源）；挂载/解除锚点、
离开这道门三个动作显式撤销武装（`disarmAnchorSkip`）。**只靠指纹不够**——0→1→0 会回到
同一个指纹，所以两处都要有。

两条都有断言守着：`anchor_ui.cjs` 的「就绪后不留悬空指令」「就绪度变化后武装失效」。

#### 14.17.4 顺手修掉的过度承诺（诚实性）

门的文案原本写着「挂上参考图，用于跨镜头保持一致」「跳过之后，批量生成不会参考任何角色/
场景参考图」——**后半句在暗示不跳过就会参考，而这是假的**。实测确认：全仓只有就绪度查询
在读 `characters.asset_id` / `scenes.asset_id`（`adaptation_service.py:523/526`），
**生成链路一次都没读**（`video_provider.py` 只用 `version["first_frame_path"]`）。

所以这道门现在保证的是「**用户在花钱前做过锚点决策**」，**不是**「参考图已经在维持一致性」。
横幅改为写明边界，并加断言钉住这句话（`尚未参与生成`）——将来真接进生成时，必须同时改
文案和那条断言。同类过度承诺还有一处：Bible 卡片里「镜头缺参考图时可取用锚点，不必逐镜头
上传」，一并改掉。

**这就是切片 3 的正题：把参考图接进生成链路**（图生图 / 参考图条件）。不同 Provider 对
参考图的支持不同，且需要付费三家验收才能声称"有效"，所以不在本切片硬做。

#### 14.17.5 驱动器（无费用回归）暴露的三个自身缺陷

这三个是**自己踩自己**，但它们的症状伪装得极像产品缺陷，必须记下来。

**（1）把「端口上有人应答」当成「后端是我们的」。** 端口被上一轮残留的 uvicorn 占着时，
我们自己的子进程因 `EADDRINUSE` 退出，而 `/api/health` 照样由残留进程回 200 → 用例跑在
**别人的数据目录**上，跑到一半对方消失，表现为"后端中途没了"的 `ECONNREFUSED`。
判据必须改成**子进程自己宣布开始服务**（日志里的 `Uvicorn running on http://127.0.0.1:<port>`），
且端口要比对。

实测取证（成功/失败两份日志对照）：`Application startup complete` **端口被占时照样会打印**
（uvicorn 先跑 lifespan 启动、之后才 bind 端口），所以**不能用它**；只有 `Uvicorn running on`
那一行是成功独有的。另外加了端口冲突时换端口重试（最多 3 次）与 `atexit` 收尸。

**（2）两个回归实例并行。** `TaskStop` 只结束了外层 shell，`run_no_cost_regression.py`
作为**孤儿继续跑**，而且 `a; b` 链式命令还会在第一个死掉后自动进入下一步——实测一度有
**两个实例并行十几分钟**，把一项失败伪装成产品缺陷（我先按"用例竞态"查了一段，靠进程表
才定位到真因）。现在加单实例锁：锁里记 pid，持有者存活则拒绝启动并指明 pid，残留/损坏的
锁按过期接管，正常退出与异常退出都释放。

两者都由 `tools/test_runner_guards.py`（20 项断言）钉住。**为什么值得单独立一个用例：**
「连续两次全绿」这句话的成立前提就是"两次都是干净的单实例运行"——锁不牢，那两个数字本身
就不可引用。

**（3）断言计数被自报汇总行抬高一条。** 驱动器的 `summarise()` 是**按 token 粗计**：一行里
出现独立的 `pass`/`fail`/`skip` 就记一笔（这是为了能把工具自己的拒绝文案也照实记下来，代价是
"计数 ≠ 有检查被跑"）。而我新写的 `test_runner_guards.py` 收尾打印了
`runner guards: 20 pass / 0 fail` ——它被当成第 21 条断言。

实测量化（逐份日志比对 `counts['pass']` 与真正的 `PASS:` 行数）：**53 项里只有这一项有偏差，
其余 52 项 counted == real**。修法是让收尾行避开独立 token（`runner guards: 20 ok / 0 failing`），
顺便把"脚本只打印 `PASS:`/`FAIL:` 行、不打印自报汇总行"这条仓库约定写进 `summarise()` 的文档串。

**代价是一轮已经全绿的运行作废**：口径从 507 变成 506，按本项目"断言数不同的两轮不能配对"
的规矩，那一轮的 507 只能弃用、重跑两轮。这条规矩不只在防别人，也在防自己——修掉一个显示行
也必须重新取证，否则可引用的数字就带着一个说不清的 +1。

#### 14.17.6 仍未做（切片 3，勿读成已闭环）

- **参考图没接进生成链路**（见 14.17.4）。门目前只保证"决策发生过"。（**已由切片 3 补上**，见 14.18。）
- **镜头缺参考图时是否自动取用锚点**：仍未决定、未实现。（**已由切片 3 回答**：参考图模式下两者并列发出、不二选一；仍未实现的是**首帧**的自动兜底。）
- mock 规划的实体抽取有噪声：演示样本里会出现 `他想`、`转折空间` 这类名字（
  `adaptation_planner.plan_story_bible` 的启发式 + 兜底占位词）。真实 LLM 规划不会这样，
  但**基于 mock 的截图/界面文案会带上它们**，讲的时候要如实说是 mock 数据。
  这也正好说明"至少挂上一个"的口径是对的：噪声实体不会把流程锁死。

#### 14.17.7 一并修正的旧口径

- `README.md` 的回归项数 `51 → 53`（两次都在同一天：先随锚点界面新增的
  `test_anchor_ui_browser.py` 断言变成 52，再随驱动器自检 `test_runner_guards.py` 变成 53）。
  README 同时补写了驱动器的两条硬约束（只认自己启动的后端、带单实例锁），
  这两条现在是对使用者的承诺，不只是内部实现细节。
- `docs/v1-demo-script.md`：「48+1 项无费用回归」→ 实测 **53 项**；`test_p6_demo_samples.py`
  的「39 项断言」→ 实测 **41 项**；并新增第 3 段「视觉锚点审核门（现场新建一次性项目）」，
  同时把"参考图尚未参与生成"写进诚实性附注。
- `docs/v1-delivery-roadmap.md`：切片 2 的验收项数 `test_anchor_review_gate.py` 写的是 15，
  实测 **16**；并补上 `tools/anchor_ui.cjs`（12 项断言）这个此前没记的界面验收入口。

#### 14.17.8 实测（连续两轮，同口径全绿）

单项验收（只列本切片新增或改动的项；**不是全部 53 项**）：

| 检查 | 断言 | 结果 |
|---|---|---|
| `tools/test_anchor_review_gate.py` | 16 | 16 pass / 0 fail |
| `tools/test_anchor_assets.py` | 13 | 13 pass / 0 fail |
| `tools/anchor_ui.cjs` | 12 | 12 pass / 0 fail（**由 `tools/test_anchor_ui_browser.py` 用 node 调起**，不是两项：那一项报的 12 条就是这份脚本的） |
| `tools/test_runner_guards.py` | 20 | 20 pass / 0 fail |

全量回归（**53 项**，51 → 53）：

| 运行 | 结果 | 断言 | 耗时 |
|---|---|---|---|
| `run-20260927-130103` | 53/53 | 506 pass / 0 fail / 2 skip | 795.8s |
| `run-20260927-131511` | 53/53 | 506 pass / 0 fail / 2 skip | 730.7s |

**两轮口径完全一致（同为 53 项 / 506 断言）且都全绿**，这才是可引用的事实。断言数可交叉验证：
上一轮基线 483 + 3（锚点门界面的新增断言）+ 20（驱动器自检）= **506**，与实测吻合——说明新增检查
一条都没被静默跳过。

**另有一轮已全绿但被弃用**：`run-20260927-124548`（53/53、**507** 断言、727.8s）。它跑在
14.17.5(3) 那个"自报汇总行抬高计数"修好**之前**，按本项目「断言数不同的两轮不能配对」的规矩
只能弃用。同一天里出现 507 与 506 两个数，将来若有人对不上，看这一条即可。

2 条 skip 复核（都是**在档的日志级计数**，不是覆盖缩水）：

- `test_live_safeguards.py`：`SKIP: inflight_remote_tasks db_tasks=1 db_shots=1` 是
  `run_live_2shot.py` 守卫**自己的拒绝文案**，紧邻下一行就是该守卫的 PASS
  （"脚本异常且 lineage 过期时，DB inflight 仍阻止清理"）。
- `test_mock_web_smoke.py`：`视频阶段只有一个模型，无法切换`——mock 下只有一个视频模型，
  该子步骤没有可切换对象。

> **本节数字（53 项 / 506 断言）已被 14.18.1 取代**：切片 3 加入 `test_reference_generation.py` 后
> 为 54 项 / 516 断言；再经切片 4 与本次两处修复后为 57 项 / 541 断言（见 14.19.4）；
> **再经 P5-B-1 与长文本章节 UI / 视频可用性诊断两处改动后，当前基线见 14.22。**
> 本节保留原样，因为它是当时的取证记录，不是表述错误。

### 14.18 切片 3：参考图接进生成链路（2026-09-27）

**问题**：切片 2 的门只保证"决策发生过"。实测全仓只有就绪度查询读 `characters.asset_id` /
`scenes.asset_id`；更要紧的是 `shot_versions.reference_frame_path`（镜头参考图）**前端能选、
上传能写、生成链路从来没读过**——`VideoAssetRequest` 只有 `first_frame_path` / `last_frame_path`，
三家的 content 构造里都没有参考图的位置。

**做法**：新增 `reference` 视频模式（登记进 `MODE_REQUIREMENTS`，`requires_reference: true`）。
选它时按「镜头参考图 → 角色 → 场景」收集（新增 `anchor_service.collect_reference_images`；
顺序即语义——DashScope 用「图1、图2」按 media 数组顺序指代参考素材），经既有的
`prepare_image_reference(role="reference_image")` 发出。传输层本来就是 role 自由的，没有新增通道，
只是终于有人调用它了。

**三家规则不同，按 provider 分流**（本节事实全部来自官方文档，不是推测）：

| Provider | 参考图参数 | 与首帧能否并存 |
|---|---|---|
| 火山 Seedance 2.0 | `role=reference_image` | **互斥**——官方原文：首帧 / 首尾帧 / 全模态参考是「3 种互斥场景，不可混用」 |
| 阿里 Wan 2.7 R2V（本次新增的模型） | `type=reference_image` | **可并存**——官方："When used with subject references, two modes apply" |
| MiniMax H3 | 无此参数 | **明确拒绝**（`REFERENCE_NOT_SUPPORTED`） |

互斥/并存被做成能力声明 `reference_includes_first_frame`，界面据此提示「本模式下该 Provider
不接收首帧」，免得用户以为首帧还在起作用。

**为什么必须分流而不是"都发参考图"**：往 payload 里多塞一个字段很容易，但两个坑都是真的——
① Seedance 混用会被云端拒单；② MiniMax 根本没有这个参数，静默丢掉挂好的图等于骗用户。

**无费用验收**：`tools/test_reference_generation.py`（10 项断言，进程内、不联网）——模式已登记 /
ark 声明支持而 minimax 诚实声明不支持 / 不支持时明确拒绝 / 缺图报 `MISSING_REFERENCE_IMAGE` /
ark payload 只有 `reference_image` 不带首帧 / dashscope payload 两者并存 / minimax 抛错 /
收集顺序为镜头参考图 → 角色 → 场景。

**界面同步（成对改动）**：门横幅原写「参考图尚未参与生成」，接通后不再成立，已改为写明生效范围；
`tools/anchor_ui.cjs` 里钉着那句话的断言同步改写。**这两处必须一起改**——只改一边就是在制造假绿。

**顺带修掉一个既有偶发**：`anchor_ui.cjs` 最后一步「重新挂上锚点」偶发超时。取证路径值得记：
后端 access log 里那次上传**连 POST 都没有**（说明 change 事件根本没到处理器），而不是请求失败。
根因是 Bible 区整块 `innerHTML` 重渲染，解除锚点后的刷新与 `events` 轮询都可能恰好在
`setInputFiles` 与 change 之间把 input 换掉。修法是测试侧重试并保留最终失败可见性。

#### 14.18.1 实测（连续两轮，同口径全绿）

全量回归 **54 项 / 516 断言 / 0 fail / 2 skip**（53 → 54，本切片 +1 项 / +10 断言）：

| 运行 | 结果 | 断言 | 耗时 |
|---|---|---|---|
| `run-20260927-141740` | 54/54 | 516 pass / 0 fail / 2 skip | 794.1s |
| `run-20260927-143107` | 54/54 | 516 pass / 0 fail / 2 skip | 820.4s |

新项排在 `[37/54] test_reference_generation.py`，10 pass / 0 fail（0.6s），10 条 PASS 与 14.18 正文
列的契约一一对应。断言数可交叉验证：上一轮基线 506 + 10（本切片新增）= **516**，与实测吻合——
说明新增检查一条都没被静默跳过。（驱动器自身的 20 条自检排在 `[38/54]`，已含在 506 里，不重复计。）

2 条 skip 与 14.17.8 **同口径、同出处**（逐项日志复核）：`test_live_safeguards.py` 的
`SKIP: inflight_remote_tasks db_tasks=1 db_shots=1` 是 `run_live_2shot.py` 守卫自己的拒绝文案
（紧邻下一行才是该守卫的 PASS），`test_mock_web_smoke.py` 的 `SKIP: 视频阶段只有一个模型，无法切换`
是 mock 下唯一的视频模型没有可切换对象。都不是覆盖缩水。

**尚未验证（必须如实说）**：以上全是 payload 层的事实。三家**真实调用的画面效果尚未付费验收**——
"参考图是否真的在维持一致性"只有真调一次才知道，交付文档与讲稿都不得把它讲成已验证。

### 14.19 切片 4：付费闸门覆盖全部视频 provider（2026-09-27）

**问题**（核查出来的，不是猜的）：闸门 `assert_live_video_allowed` **只**在 `_generate_minimax_video`
里被调用，而 `generate_video_asset` 对 `ark` 与 `dashscope` 是**直接分发**——没有授权开关、没有每项目
次数上限、没有预算校验。切片 3 恰好要在那两家的参考图模式上花钱，等于**最该拦的两家反而没拦**。
成本估算也同样只按 MiniMax 定价（0.50 元/秒），而 ark 的 Seedance 2.0 在 720p 且有输入视频时是
**1.208 元/秒**——差 2.4 倍，一个全局单价必然低估其中一家。

`docs/real-live-test-preflight.md` 通篇只写 MiniMax，正是这个缺口的证据。该文档已同步更正：
第 1 节加更正说明，新增第 8 节写清覆盖范围与单价出处。

**做法**：

1. 闸门上移到 `generate_video_asset` 的分发环：对每个**有密钥**的候选 provider，在打开 HTTP 之前
   依次检查「授权开关 → 每项目次数上限 → 预算」。**没有密钥的候选不计数、不拦**，直接跳过——
   否则会为一个根本不会调用的 provider 白扣一次名额。
2. `BudgetBlockedError` **原样上抛**：它是对这个项目的授权/预算判定，不是 provider 故障。被兜底成
   「所有 live video providers failed」会让人以为换个 provider 就能绕过去，而真的换一家继续试，
   就是在越过预算花钱。
3. `_generate_minimax_video` 里那道**移除**：留着会让 MiniMax 被扣两次名额，同时另外两家继续不受管。
4. 价目表 `VIDEO_PRICE_CNY_PER_SECOND` 按 provider 与分辨率取价，未登记项取最贵档（高估是安全方向）；
   `estimate_closed_loop_cny` 与 `check_live_video_budget` 都接受 `provider`，并带出
   `video_unit_cny` / `price_basis`，报清单不必再手工核算。
5. **默认口径刻意不动**：不传 provider 时仍按 MiniMax 计价，既有 25 条断言钉在 0.5 元/秒 上，一条未改。
   「支持多家」不能变成「悄悄换了默认那一家的数」。
6. 回查（refresh）不过闸：它不提交、不产生新费用。

**单价出处**（官方文档，核对日期 2026-09-27）：

| Provider | 分辨率 | 有输入 / 无输入（元/秒） | 出处 |
|---|---|---|---|
| `minimax` H3 | 768P | 0.50 / 0.50 | MiniMax 官方价（本项目既有口径） |
| `ark` Seedance 2.0 | 720p | 1.208 / 1.988 | 火山引擎「视频生成增强版」算子价目 |
| `dashscope` Wan 2.7 R2V | 720P | 0.60 / 0.60 | 阿里云百炼 wan2.7-r2v 价目 |
| `siliconflow` | 任意 | 0.50 / 0.50 | **未取到公开价**，按 MiniMax 档保守取值（待核实） |

ark 另一张按 token 的方舟价目（输出 480p/720p、输入含视频 28 元/百万 token）折算约 1 元/秒，
**量级一致**，可作交叉印证。`siliconflow` 那行是**假设不是报价**，且本项目未配置其密钥、该通道不会被选中。

**顺带核实**：`wan2.7-r2v` 在阿里云百炼官方文档里**就是原名**（快照 `wan2.7-r2v-2026-06-12`，
支持最多 5 个图/视频混合参考）→ 切片 3 用的 id 是对的。但本项目的 `DASHSCOPE_API_HOST` 是专属
MaaS 域名，该模型**是否已在该端点开通仍需实调确认**：id 正确不等于账号可用。

**无费用验收**：`tools/test_video_provider_guard.py`（10 项断言，进程内、不联网）。仍按「先写测试」的
纪律：首跑 `ImportError` 为红（`VIDEO_PRICE_CNY_PER_SECOND` 尚不存在），实现后转绿。其中两条是
**正面控制**，用来排除"拦下来的其实是别的东西"：

- 授权后 ark 提交确实打到 transport（`posts == 1`）；
- 每项目上限为 1 时，同项目第二次提交被拦且 `posts` 仍为 1、零新增 `video_tasks`。

**受影响面回归**（各自独立数据目录）：`test_live_safeguards`（25 断言）、`test_reference_generation`
（10）、`test_stage_models`、`test_media_transfer`、`test_provider_capabilities`、`test_job_center`、
`test_shot_versions`、`test_v1_usability`、`test_anchor_review_gate` 全部保持绿。

#### 14.19.1 无费用全量复核（**两轮都跑成 54/55，同一项复现**）

两轮独立全量复核**都没跑绿**，而且失败得一模一样——所以这不是"偶发"，也**不能**用"机器慢"解释掉：

| 轮次 | 结果 | 断言 | 耗时 | 失败项 |
|---|---|---|---|---|
| `run-20260927-151205` | 54 / 55 | 520 pass / 0 fail / 2 skip | 3018.3s | `test_p6d_assembly_browser.py`（exit 1，65.4s，3 pass / 0 fail） |
| `run-20260927-160343` | 54 / 55 | 520 pass / 0 fail / 2 skip | 3225.2s | **同一项**（exit 1，67.6s，3 pass / 0 fail） |

两轮的**选中项数、通过项数、断言数、skip 数、失败项、以及失败项自己留下的 PASS 条数（3）全部一致**；
两轮里真正跑到过的断言**一条都没有失败**。

失败原文两轮逐字相同（`logs/test_p6d_assembly_browser.py.log`）：
`page.waitForFunction: Timeout 10000ms exceeded. @ tools/p6d_assembly.cjs:105`。
那一行等的是「保存成片配置后，前端自己重渲染出『已过期』」（`#assemblyFreshness` ←
`render.js:1233` ← `project.assembly_stale`）。它是该检查的第 4 条断言，所以这一项只留下 3 条 PASS。

> ⚠️ **本节的结论已被下面 14.19.2 推翻，此处保留仅作过程记录——它是这份文档里一个被自己的误读带偏的判例。**
> 当时的关键证据（「access log 紧邻 PUT 之后没有 GET」）**不成立**：那个 GET 确实发出去了，
> 只是在检查失败、驱动器关掉后端之前还没返回，所以一行日志都没落下（uvicorn 只在响应写完时记行）。
> 「慢只会让请求晚到，不会让请求消失」这句话本身没错，但**这里的问题恰恰就是「晚到」**。

**当时的证据（两轮一致，但被误读）**：把后端 access log 按完成序读出来，两轮都是
`PUT /assembly-settings 200 OK` → `GET .../events?after_id=40`（那是 `attachEvents()` →
`startEventStream()` 建的 EventSource）→ 之后在那个窗口里再没有别的行，尤其没有
`GET /api/projects/{id}`。

**当时的结论（已作废）**：「前端没有在 PUT 之后重新读过项目，于是 `#assemblyFreshness` 停在旧值上，
等多久都不会变。」——**错**。带探针的定向复现（见 14.19.2）显示前端**确实**回读了项目，
`#assemblyFreshness` 也**确实**翻成了「已过期」，只是发生在保存后 **9.9–12.5 秒**，
而用例写死等在 10 秒。

**当时的根因候选（两条都被证伪，留作教训）**：

1. ~~`el("jobMessage")` 取不到元素 → 抛异常被 `catch` 吞掉，于是 `refreshProject()` 没执行~~；
2. ~~`refreshProject()` 静默返回——`isLiveSession`（`jobObserver.js:49`）要求
   `ctx.observedProjectId === projectId`，不一致时不发请求也不报错~~。

两条都没有发生：探针里页面内 `fetch` 日志记录到了那次 `GET /api/projects/{id}`，`PAGEERRORS` 为空，
`#jobMessage` 也拿到了元素（文案被写进去了）。**教训**：当「日志里没有请求」和「请求很慢」都能解释同一个
现象时，先做一次能直接看到请求的旁路取证，别在两种静默失效里挑一个写进文档。
（另记一条与本案无关但同类的通道：`refreshProject` **没有「请求序号单调」守卫**，而
`startEventPolling()`（`app.js:1605`）每 4s 还会调它，两个刷新并发时晚发先到会把旧快照盖回去；
本案的日志并不能证明这一条，写在这里是为了下次别漏掉这个可能。）

#### 14.19.2 真根因与修复：一次项目读取要付「镜头数 × 单次 ffprobe 价」（2026-09-27 当日完成）

**根因（实测，非推断）**：本机单次 `ffprobe` 要 **0.9–1.1 秒**（进程启动 + 杀软扫描），而
`get_assembly_status`（`video_service.py:1162-1168`）**为每个就绪镜头**调一次
`_has_audio_stream` → `_ffprobe_json`。于是：

| 端点 | 实测（4 镜项目） |
|---|---|
| `/api/health` | 3–27 ms |
| `GET /api/projects/{id}` | **3.9–6.0 s** |
| `GET /api/projects/{id}/assembly` | **7.9–9.1 s** |
| `GET` / `PUT /api/projects/{id}/assembly-settings` | **4.1–5.3 s** |

两个 settings 端点**只是做存在性检查，却调用完整的 `get_project()`**，于是各多付一次逐镜探测。
`PUT`（4.4–6.8s）+ 前端回读（4–6s）= **9.9–12.5s**，跨在用例写死的 10s 上 → 两轮都在同一负载下失败，
而同一探针在别的轮次又能过——**这就是它看起来「偶尔」的来历**。

**排除过的三个候选**（都做过实验，别重走）：

- **SSE 常开连接造成 SQLite 锁等待**：`journal_mode=delete`、`busy_timeout=5000` 很像是，但
  「无 SSE / SSE 常开 / SSE 停掉」三种情况实测同为 151–196ms → 无关；把 ffmpeg 注入 PATH 也无关。
- **代理 `HTTP_PROXY=127.0.0.1:58179`**：`urllib.proxy_bypass('127.0.0.1')` 为 False，确实会叠加延迟，
  但用 node `http`（不理环境代理）复核同样是 4.2s → 不是根因（浏览器侧代理本来就是关的）。
- **payload 体积**：`/api/projects/{id}` 首字节 1221ms / 仅 9011 字节，而 `/js/render.js` 首字节 96ms /
  **116922 字节** → 与体积无关，是服务端首字节前的等待。

**修复（三处，最小改动）**：

1. `_ffprobe_json`（`video_service.py:1298`）按 **(绝对路径, mtime_ns, 字节数)** 缓存成功结果；
   文件被换掉或改写自动失效，失败不缓存（保留「每次都会重试」的既有语义），上限 512 条。
2. 四个成片端点（`main.py` 的 `assembly_status_endpoint` / `get_assembly_settings_endpoint` /
   `put_assembly_settings_endpoint` / `assemble_video_endpoint`）的存在性检查
   `get_project()` → 廉价的 `_project_exists()`。
3. `onSaveAssemblySettings`（`app.js:1143`）**就地把 PUT 响应的 `stale`/`settings` 应用到
   `state.project` 并立即 `renderAll()`**，回读降级为后台校准。即便回读被会话守卫静默跳过或变慢，
   界面也已经是对的——`stale` 本来就是服务端在同一个响应里给出的权威值。

**验收（先红后绿）**：新用例 `tools/test_project_read_budget.py`（9 条断言）用**预算**而不是墙钟，
钉住「同一批文件只许起一次 ffprobe 进程」「存在性检查不得装配整个项目」。它第一次跑**是红的**
（`FAIL: 同一批未改动文件二次读取应复用探测结果，实测又探测 4 次`）——但那次红是**测试自己的计量点错了**：
它包了 `_ffprobe_json`，而缓存命中时那个函数照样会被进入。真实读数：冷读 4 次探测 / 5019ms，
二次读 **0 次探测 / 181ms**。改包 `_QUERY_RUN`（= `subprocess.run`，只统计带 `-show_streams` 的调用）
后 9 条全绿。`test_p6d_assembly_browser.py` 随之恢复：8 条 PASS + 驱动 PASS、89.8s、返回码 0，
其中 `PASS: 保存配置后无需手动刷新即可显示待重新合成` 正是此前卡住的那条。

**这一轮读数与「推算值」的收场**：Slice 4 之后两轮都是 **54/55**；加上 14.19.2 新登记的一条
（→ 56 项）后，本窗口第一轮是 **55/56**，两轮的断言都是 **525 pass / 0 fail / 2 skip**。
此前推算过的 55 项 / 526 断言只是**预期值**，现已被实测取代：**以 525 为准，526 不再引用**。
两轮都有失败项、且失败项不同（先是 p6d、后是 ui_workbench），所以都不是偶发 ——
后者的真身见 14.19.3。**57 项的两轮读数已回填在 14.19.4：两轮同为 57/57、
541 pass / 0 fail / 2 skip，口径一致且全绿。**

**顺带记两条观测**（都不是本切片引入的）：

- 驱动器 atexit 里的 `release_run_lock()`（`lock.unlink()`）会被宿主的批量删除护栏拦下
  （`SAFE_DELETE_BULK_CONFIRM_REQUIRED` count 112 > 阈值 50），于是**每轮退出都会留下一个没清掉的锁文件**；
  下一轮按"残留锁过期接管"继续。不影响结果，但排错时别把它误读成"有第二个实例在跑"。
- 历史 48 份 `no_cost_regression_report.json` 里，「verdict 是 FAIL 但 counts 显示 N pass / 0 fail」
  这个形状在界面类检查上反复出现（`test_anchor_ui_browser` 5 次、`test_local_keyframe_browser` 2 次、
  还有 `test_v1_demo_browser`、`test_adaptation_start_refresh`）。本次是它第一次被**复现**下来，
  也就从"偶发"改判为"**同一条静默失效路径的多个受害者**"。

**仍未做（勿读成已验证）**：

1. ~~p6d 这条保存路径要先修~~ —— **已完成**：三处修复见上，p6d 用例恢复（8 PASS + 驱动 PASS、
   89.8s、返回码 0）。同一轮回归又暴露出一个更底层的缺陷（`database is locked`），其取证与修复见 14.19.3。
2. 三家真实调用依旧未付费验收（见 14.18 末）。本切片补的是**护栏**，护栏不等于验证——
   它只保证"万一要花，先拦得住"，不保证"参考图真的维持了一致性"。
3. 两轮同口径全绿仍要在**当前 HEAD** 上重跑才算数（Slice 4 之后的第一轮全量已跑，读数见上）。

#### 14.19.3 真根因之二：并发写把读打成 500（SQLite busy 等待不够）（2026-09-27 当日完成）

修完 14.19.2 之后重跑全量，**失败项换了**：从 `test_p6d_assembly_browser.py` 换成
`test_ui_workbench.py`（55/56；该项 `4 pass / 0 fail` 却返回码 1 —— 说明是**脚本崩了**，不是断言失败）。
后端日志给出真身：

```
sqlite3.OperationalError: database is locked
  project_service.py:337 get_project → validate_assembly → SELECT * FROM projects
```

**触发时序**：`tools/ui_workbench.cjs:237` 是 `POST /api/projects/{id}/run`，紧接着第 238 行
`waitProject()` 立刻轮询 `GET /api/projects/{id}`。而 `/run` 端点用的是 FastAPI 的
**BackgroundTasks** —— 后台任务在**响应发送之后**才开始执行，于是「POST 返回」与「第一个读」
天然重叠在**后台任务的第一批写事务**上。这不是本次改动引入的时序，是**接口形状**决定的。

**为什么以前不撞**（这一点值得记下来）：`get_project` 的读要跑到第 337 行才碰 `validate_assembly`，
而改造前它会先在 `get_assembly_status` 里逐镜跑 ffprobe（4 镜 ≈ 4.2s）。等它跑到锁点，
后台那批写**早就提交完了**。14.19.2 把读提速到 0.2s，反而让读**正好落进写窗口**。
换句话说：**提速把一扇一直开着的窗暴露了出来，窗本身是既有的。**

> 与 14.19.2 的排除清单不冲突：那里排除的是「SSE 常开连接引发锁等待」，
> 这里发现的是「后台写事务与并发读相撞」—— 两回事。

**竞争窗口有多大（实测，不是估的）**：

- 一次性探针（照失败用例的动作）重复 3 轮（2200 字中等文本，落在 medium 区间），
  60 次 GET 采样**全部 200**，但**每轮第一次 GET 都要 1.5–1.8s** —— 那就是它被写事务挡住的时间。
  干净机器上 5 秒够用，所以只表现为「偶发」。
- 一条反直觉的事实：**标称 5000ms 的 `busy_timeout`，在这台机器上的真实放弃时刻约 7.4s**。
  SQLite 的每轮重试都要做一次文件锁系统调用，实际 elapsed 被放大到标称值的 ~1.5 倍。
  三处独立测量互证：`output/lock_control_ab5000.txt` 的 7.447s、另一次独立探针实测的 7.35s、
  `tools/test_sqlite_lock_tolerance.py` 红轮的 7.3s。

**修复**：`database.py` 的 `connect()` 显式声明 `PRAGMA busy_timeout = 20000`，不再依赖
`sqlite3.connect` 的隐式 5 秒（`BUSY_TIMEOUT_MS` 常量）。**没有改用 WAL** —— 仓库里有三处
`shutil.copy2(DB_PATH, backup)` 的备份路径（`cleanup_temp_project` / `restore_project_from_backup` /
`retire_remote_video_task`），WAL 下这样复制主库文件会丢掉 `-wal` 里尚未 checkpoint 的数据，
收益不抵风险。

**验收（先红后绿，确定性而非偶发）**：新用例 `tools/test_sqlite_lock_tolerance.py`
（登记进驱动器 → **57 项**）用另一个连接 `BEGIN EXCLUSIVE` **持写锁 10s**，在窗口内发一次 GET，
断言它**不是 500**、而是等到锁释放后返 200。它同时钉住三件事：

| 断言 | 作用 |
|---|---|
| 常量 `BUSY_TIMEOUT_MS` 显著大于持锁时长 | 配置护栏：常量被调小就立刻指出 |
| 打印连接上**真实生效**的 `busy_timeout` | 常量写对、PRAGMA 漏掉时，这两个数会分叉 |
| 读必须真的等待（耗时 ≥ 持锁 × 0.7） | 防「根本没撞上锁」的假通过 |

同口径对照（同一个脚本，只差 `connect()` 里那一行 PRAGMA）：

| 设置 | 连接上真实值 | 持锁 10s 时的读 |
|---|---|---|
| 无 PRAGMA（= 隐式 5000） | 5000ms | **FAIL** 7.3s → 500 |
| 有 PRAGMA | 20000ms | **PASS** 10.2s → 200 |

跨进程旁证（一次性探针，独立进程持锁）：5000 → 持锁 9s 让读 7.447s 后 500；
20000 → 同条件读 9.268s 后 200。两个实验互相印证。

**过程中差点写错的结论**：用例第一版 `HOLD_SECONDS = 6.5`，在「无 PRAGMA」时**照样 PASS** ——
因为 6.5s 落在 7.4s 的真实上限之内，两种设置都能等到，**用例失去分辨力**。那不是「用例通过了」，
是「用例测不出」。把持锁改成 10s 才拿到上面那张同口径表。

**边界**：这条修的是**容错窗口**，不是「消灭写事务」。窗口从 ~7.4s 放宽到 ~29s，
对实测 1.5s 的真实竞争有约 19 倍裕量；但若将来出现「单个写事务本身就要几十秒」的路径，仍会重现，
届时该做的是**拆小事务**或**改 WAL（并同时修那三处备份）**，而不是继续加大这个数。

**一次性探针的去向**：上面提到的 `repro_lock` / `lock_control` / `probe_busy` 都是当轮的一次性诊断脚本，
**结论写进本节后已删除**，与本项目既有做法一致（见 §14.14 与 `docs/stage-c-evidence-archive-2026-09-20.md` 第 4 条）。
可复核的落点是：真正复现这条缺陷的是**已提交的** `tools/test_sqlite_lock_tolerance.py`，
它的同口径 A/B 表就在上面；探针本身不承担长期取证责任。


#### 14.19.4 修复后的全量复核（**连续两轮同口径全绿**，2026-09-27 当日完成）

前面 14.19.1 记录的是两轮都跑成 `54 / 55`（同一项复现）。两处根因修完后重跑，拿到本窗口的基线：

| 运行 | 结果 | 断言 | 耗时 |
|---|---|---|---|
| `run-20260927-200842` | **57/57** | **541 pass / 0 fail / 2 skip** | 3085.7s |
| `run-20260927-210126` | **57/57** | **541 pass / 0 fail / 2 skip** | 3085.5s |

**两轮口径完全一致（同为 57 项 / 541 断言 / 2 skip）且都全绿**，这才是可引用的事实。
断言数可交叉验证：上一窗口实测 525，本窗口 +6（`test_sqlite_lock_tolerance.py` 新增）
+10（`test_ui_workbench.py` 从崩溃时的 4 pass 恢复满额 14 pass）= **541**，与实测吻合。

**2 条 skip 的出处**（从本轮报告 JSON 的 `skip_lines` 字段直接取，不是照抄旧结论；两轮同位置）：

| 用例 | skip 原文 | 是不是"有检查被跳过" |
|---|---|---|
| `test_live_safeguards.py` | `SKIP: inflight_remote_tasks db_tasks=1 db_shots=1` | **不是**。这是诊断打印（说明夹具里有 1 条 inflight 任务），紧邻的下一行就是 `PASS: 脚本异常且 lineage 过期时，DB inflight 仍阻止清理` |
| `test_mock_web_smoke.py` | `SKIP: 视频阶段只有一个模型，无法切换` | 是子步骤跳过，理由写明：mock 下只有一个视频模型，没有可切换对象 |

本窗口一并完成的两项修复（详见 14.19.2 / 14.19.3）：

1. **项目读取成本**：`_ffprobe_json` 按 `(绝对路径, mtime_ns, 字节数)` 缓存 + 四个成片端点
   的存在性检查换 `_project_exists()` + 保存成片配置后就地应用 PUT 响应。效果：
   同一批未改动文件的二次读取从 ~4.2s 降到 **181ms**，p6d 用例恢复（9 pass / 0 fail，66.3s / 69.1s）。
2. **并发写把读打成 500**：`connect()` 显式 `PRAGMA busy_timeout = 20000`。效果：
   `test_ui_workbench.py` 恢复满额（14 pass / 0 fail）。

**新增的两项用例都先跑成红再变绿**：`test_project_read_budget.py` 用「探测次数预算」而不是墙钟
（机器一忙就假失败）；`test_sqlite_lock_tolerance.py` 用另一连接真的持写锁 10s，断言读**不是 500**。
后者第一版持锁 6.5s 时**在缺 PRAGMA 的情况下也报绿**——6.5s 落在真实上限 ~7.4s 之内，
两种设置都能等到，属「用例测不出」而不是「用例通过」；改成 10s 才恢复分辨力。

**边界（勿读成已闭环）**：这两轮证明的是**无费用回归全绿**。参考图链路与付费闸门
**仍未经过任何真实付费调用验收**——护栏不等于验证。本窗口零费用，20 元额度一分未动。

> 上面这句在 2026-09-28 被部分推翻：参考图链路**已做了一次真实付费验收**（见 14.20），
> 结论是 dashscope 通过、ark 因账号欠费未验。原文保留，作为"当时的确没验过"的记录。

### 14.20 切片 5：参考图真实付费验收（① 批次）——dashscope 通过，ark 因账号欠费未验（2026-09-28）

竹木在本轮明确授权「今天跑①」（参考图批次）。实际只发生**一次**真实付费调用，
**花掉 3.00 元**（不是清单里估的 9.04 元——ark 那一半根本没提交，见下）。

**运行器**：新增 `tools/run_reference_smoke.py`（本次提交）。两种模式：

- `--live`：建一个 `refsmoke_<provider>_<hex>` 项目，登记角色锚点图 → 建镜头版本（`video_mode=reference`）
  → 走**真实** `generate_shot_video` → 把清单、外送记录、任务、资产、镜头版本一起写成 JSON 报告，
  落在 `<数据目录>/reference-smoke/reference-<provider>-<project>.json`。
- `--refresh <video_task_id>`：**只回查**同一 `remote_task_id`，不提交、不产生费用。
  这是"断线不重提"纪律的直接复用（见 14.3 / 5.5）。

**取证过程中的五个坑**（都不是产品缺陷，是脚本写错，但值得记）：

1. 报告里写 `assets.kind` / `assets.title` / `assets.source` —— 实表只有 `type` / `name` / `file_path`，首跑崩在 `no such column: kind`。
2. `media_transfers` 没有 `project_id`，必须 `JOIN assets a ON a.id = mt.asset_id WHERE a.project_id = ?`。
3. `refresh_remote_video_task` 返回的是对象不是 dict，直接塞进 JSON 会 `not JSON serializable`，要取 `.video_path`。
4. `video_price_per_second()` 的 `resolution` 是**关键字参数**，位置传参会报参数错。
5. **最关键的一条**：`generate_shot_video` **不向外抛异常**，失败只写进 `jobs.error_message`。
   于是报告里 `error` 恒为 `null`、`video_tasks` 为空，看上去像"没提交也没失败"。
   加了一段 `jobs` 查询把 `job_error` 暴露出来后，真因才露出来。**这是本项目第二次踩到同一个形状
   （失败信息藏在进程内的数据行里，不在调用栈上）** —— 排错时要先查任务表，不要靠重跑。

**① 批次实测结论**

| 项 | dashscope | ark |
|---|---|---|
| 模型 | `wan2.7-r2v` | `doubao-seedance-2-0-260128` |
| 结果 | **成功** | **失败（未提交）** |
| 报告 | `reference-smoke/reference-*.json`（另有 `refresh-vt_90b0e699f8.json`） | 同目录 `reference-ark-*.json` |
| `video_tasks` | `vt_90b0e699f8`，`status=completed`，`cloud_status=succeeded` | **0 条** |
| 提交内容 | `media` 同时含 `reference_image` 与 `first_frame`（两条 `media_transfers` 同源同一张锚点图） | `media` 只含 `reference_image`（`reference_includes_first_frame=false`） |
| 产出 | `asset_76529b075f.mp4`，1,933,845 字节 | 无 |
| 实际费用 | **3.00 元**（0.60 × 5s） | **0 元** |
| 失败真因 | — | job 表原文：`Video API HTTP 403: {"error":{"code":"AccountOverdueError","message":"The request failed because your account has an overdue balance."…}}` |

**对 dashscope 产出做的独立核验**（不采信运行器自报规格）：

- `ffprobe` 实测：`h264` 1110×828 / 150 帧 / **5.038005 s** / `aac` 22050Hz 218 帧 / 1,933,845 字节。
- 首帧与参考图对比图：`output/reference-smoke/dashscope-reference-vs-output.png`（左参考图、右输出首帧）。
  人工比对：人物身份（长发遮单眼、白面、黑袍）、构图（左侧绿蝶、右肩金虫）、水墨质感**均一致**，
  背景纹理与发丝方向随提示词（风、推镜）变化 —— 这正是"参考图模式维持一致性但不冻结画面"的预期。

**这次验收真正回答了三个此前未知的问题**（14.18 里挂着的）：

1. `wan2.7-r2v` 在本项目的**专属 MaaS 端点**上**确实已开通**（此前只知道模型 id 正确，不知道账号可用）。
2. dashscope 的 `reference_image` 与 `first_frame` **可以并存**（提交 payload 实测两条都在）。
3. ark 的参考图模式**尚未验证**——不是"不支持"，是**账号欠费**（HTTP 403 `AccountOverdueError`）。
   **需要竹木给火山方舟账号充值后重新授权**，才能补上这一格。

**仍然没做的**：

- ② 批次（首尾帧，估 ≈11.54 元）**未授权**，未跑。① 与 ② 不能合并成一次——合计会越过单次 20 元上限。
- ark 参考图：待充值后补验。
- minimax：**无参考图参数**，本就不参与这个批次（见 14.18），不是"没验"。

### 14.21 切片 6：P5-B-1 长文本章节树与按章范围（2026-09-28）

P5-B 此前在文档里一直写作"未实现"，且 `start_adaptation_workflow` 对超过 10,000 字的文本**直接拒绝**。
本轮把它拆成两半，只做完前半：

| 部分 | 内容 | 状态 |
|---|---|---|
| **P5-B-1** | 章节树、逐章分块、按章选范围、跨章故事线、1 万–10 万字全流程 | **已完成（本轮）** |
| **P5-B-2** | 向量检索、全文索引、真实 Embedding / RAG 召回 | **未做** |

**要说清楚的一点**：P5-B-1 的范围收缩是**结构性的**（靠章节偏移与章节树裁剪），
**不是语义检索**。文档里凡出现"检索"二字的地方，指的都是这一类结构性收缩，不要读成 RAG。

**已实现**

- `medium_text_planner.parse_chapters()`：识别 `第N节：标题`（兼容行首半角空格、行首 `\u3000`、文件头 BOM，
  数字支持中文数字与阿拉伯数字）。**无缝**是硬约束：第 N 节 `end_offset` 就是第 N+1 节 `start_offset`，
  最后一节覆盖到文末。无标记时按 `CHAPTER_FALLBACK_CHARS = 10,000` 硬切并吸附句子边界。
- `segment_source(..., chapters=...)` 改为**逐章独立分块**：每章在自己的区间里从头切起，
  所以块**不可能**越过章节边界（比"切完再判断归属"更可靠——后者要裁剪，裁剪本身会再引入缝隙）。
  块上记 `chapter_index`。
- 新增表 `source_chapters`；`source_chunks.chapter_index`、`story_events.chapter_index`、
  `adaptation_scopes.chapter_ids_json` 三处留章节归属。新库由 `schema.sql` 建，老库由 `_ensure_column` 补列
  （两条路都实测过，见下）。
- `save_adaptation_scope(..., chapter_ids=[...])`：**章节是长文本的主选择器**，块与事件都从章节推出来；
  显式传入的事件若落在勾选章节之外会被**剔除**——只勾第 10 节却提交第 20 节的事件，那不是范围，是范围漏了。
  同时放宽了一条旧约束：**按章节定范围不再要求先选故事线**（P5-A 的故事线是为"单块内取前 60% 事件"设计的，
  到 10 万字尺度会退化；章节入口就是来替代它的，不该再被它挡住）。
- 前端 `needsScopeStage()`：把 `medium` 与 `long` 收到同一个"范围选择"阶段，长文本渲染章节树勾选面板。

**入口处的两个真缺陷（纯函数全绿也没用）**

这一轮最值得记的不是章节树，而是：**纯函数和测试都写对了，工作流入口仍然把长文本挡在门外。**

1. `start_adaptation_workflow` 里还留着 `if scale == "long": raise AdaptationError("TEXT_TOO_LONG", …)`。
   只改 `run_medium_analysis` 而不改这里，`/run` 就永远进不去长文本分支。
2. 拆出 `over_limit` 之后，**超过 10 万字的文本会掉到短文本路径**——不报错、不分章，
   直接把 10 万字整本塞进改编。这比拒绝更糟，而且没有任何测试会红。已补显式拒绝。

**无费用验收**：`tools/test_long_text_adaptation.py`（新，已登记进驱动器），八项：

| 断言的到底是什么 | 结果 |
|---|---|
| 真实语料解析出 30 节，首尾相接无缺口，每节都有摘要 | PASS |
| 无标记文本回退分章，仍然无缝 | PASS（2.4 万字 → 3 章） |
| 131 个块**全部**落在单一章节内，`chapter_index` 与实际归属一致 | PASS |
| 长文本分块后全文仍被完整覆盖（无缺口） | PASS |
| 95,618 字走完分析：30 节 / 131 块 / 131 事件 / 3 条故事线 | PASS |
| 至少一条故事线跨章（实测跨度 `[18, 30, 14]`） | PASS |
| 只选中间 3 节：`scoped_text` 10,133 字，范围外章节标题一个都没混进来 | PASS |
| **HTTP 端到端**：建项目 → `/run` → 按章选范围 → 跨项目章节 id 被拒（≥400）→ 确认范围 → 3 个改编方案全部引用 scope 内原文 | PASS |

语料取 `<工作区根>/output/test_texts/蛊真人100000字.txt`（95,618 字 / 30 节），缺失时整项 SKIP 并写明原因，不当作通过。

**全部「先红后绿」**：第一版测试跑成 `ImportError: list_chapters`（接口不存在），实现后才转绿；
`over_limit` 那条断言是**先看它失败**才补的拒绝分支。

**另外两条守住的旧契约**

- `tools/test_medium_text_adaptation.py` 里那条"超过 10,000 字应被拒绝"的断言**已过期**，
  改为两条新断言：12,000 字**进入章节分析**（实测立起 2 章）；101,000 字**仍被拒绝且说明上限**。
  不删旧断言而改写它，是为了让"契约变了"这件事在 diff 里看得见。
- `tools/test_workflow_view_model.mjs` 增加长文本用例。其中一条是真正的钉子：
  `status=awaiting_scope_review + text_scale=long` 时 `executionStage` 必须是 `storyline`——
  这条在旧语义下会掉回 `text`，也就是"10 万字项目没有任何地方能圈定范围"。

**建库两条路都实测**：一次性探针在**空数据目录**上跑 `init_db()`，确认 `source_chapters` 表与三处 chapter 字段齐备
（老库靠 `_ensure_column`，新库靠 `schema.sql`，实际是后者先建、前者兜底）。

**结构图与计数同步**：`docs/visioncraft-diagrams.md` 图 6 加 `source_chapters` 节点、表数 26→27，
图 7 用例数 57→58（静态 7 + Node 6 + Python 31 + 浏览器 14）；改完重跑**真实渲染校验 18/18**
（playwright 1.55.1 + Chromium 1193 + mermaid 11）。

> **该行已过期**：紧接着又补了图 11（P5-B-1 长文本，含 2 个 mermaid 块），块数 18→20，
> 重跑结果是 **20/20**。详见 `docs/visioncraft-diagrams.md` 文末"渲染校验"段——那里也顺手改了
> 一个旧笔误：早期把**块号**当**图号**写（"图 6 = 1189×1290"里的 1189×1290 其实是**图 4** 的第一块），
> 现在统一写明"块 N"与图号的对应关系。

### 14.22 长文本章节 UI 的浏览器断言 + 视频可用性诊断口径（2026-09-28）

两件事都发生在 P5-B-1 之后、同一批提交里。**项数不变（仍 58），只增断言。**

**① 长文本章节 UI 进浏览器验收**（`tools/adaptation_start_refresh.cjs`，不新增检查项）

本节之前，长文本章节面板只有 `node --check` 级别的语法保证——HTML 生成错了、勾选提交不上去，
任何用例都不会红。现在这一段钉住：12 节带**真实章节标记**的语料（无标记会退化成硬切，
测不到章节树）→ 渲染出 12 个 `[data-chapter-check]` → 勾中间 3 节 → 保存 → scope 只有 3582 字
（全文 14334）→ **刷新后仍勾着 3 节**。7 条子 PASS + 1 条驱动 PASS。

**这一段写错三次，三个坑都记进 `PITFALLS.md` 了**：

1. **用例自己读到错数**：`/共\s*([\d,]+)\s*字/` 匹配的是整块 `innerText`，先撞上章节树头部的
   「章节树（12 节 · 共 14334 字）」——于是"只勾 3 节"被算成"带进了全文"，报出 14333/14334。
   **读数与实际相反，方向也是反的。** 改成先 `slice` 到目标句之后再匹配。
2. **用例撞上界面重绘**：真跑第一次失败是"勾 3 节、存下 2 节"（scope 2388 字）。旁路取证
   （页面内装 MutationObserver 数 `#stageWorkspace` 的重绘）显示：**一次长文本分析前后整块重绘 13 次**
   （后台任务事件触发 `renderAll()`）。勾选状态只活在 DOM 里，**重绘恰逢勾选就抹掉**。
   独立探针连跑三次都没撞上窗口（`checked=3`、PUT 确实带了 3 个 id）——
   **"复现不了"不等于"没问题"**。
3. **等静止的判据写反**（就是这条让第二轮全量跑成 57/58，见下）：第一版要求"必须**观察到过**变化
   且静默 1.5s"，于是在界面**本来就已静止**时永远等不到 → 驱动器里 30s 超时。
   **手跑两次都过**（那时任务事件还在重绘，掩盖了缺陷），一进驱动器就必失败。
   改为"观察期内一次变化都没有，也算静止"后单跑 8 PASS / 0 FAIL。

> ⚠️ **由此暴露的产品隐患（未修，已单列）**：**未保存的勾选不跨界面重绘**。
> 章节勾选与中等文本的事件勾选是同一个形状——用户在流程尚有余温时勾选，任何一次重绘都会清空它。
> 这不是 P5-B 引入的（中等文本一直如此），但 P5-B 让它更容易被撞到。
> 修法是给勾选加"草稿态"（渲染优先读草稿，保存/切项目时清），属独立小改动，**待竹木定**。
> 本轮只让用例先等界面静止以避免误报，**没有掩盖问题**——这条明确写在用例注释与 `PITFALLS.md` 里。

**② 视频可用性诊断口径修正**（`capabilities._live_access_payload`）

`/api/health` 的 `live_access` 原先 `keys_present` 只填 `deepseek` / `minimax`，`video_ready` 只看
`minimax` 一家。于是**只配了 ark / dashscope 的机器会被报成「视频：未配置访问密钥」——
而分发环照样会把请求发出去**（切片 4 之后，闸门与候选筛选都对全部 provider 生效）。
**诊断说没有、实际有，比没有诊断更糟。**

修法：把"哪家配了密钥"下移到 `video_provider.video_key_status()`——**与分发环用的
`_provider_key_present(_)` 同源，单一出处**，不在诊断侧重写一遍判定；`video_ready = 授权 且 任一家有密钥`；
`keys_present` 扩成 `{deepseek, siliconflow, ark, dashscope, minimax}`。
断言加在 `tools/test_provider_capabilities.py`：三组（全空 / 只 ark / 只 dashscope），
并断言 **payload 不含任何环境变量名**（沿用既有约束）。测试用的环境变量操作**刻意不调
`init_environment()`** ——它会 `load_dotenv` 把 `.env` 里的真实密钥填回被清空的变量，用例就测不到"没有密钥"这一支。

**本轮回归（同口径两轮全绿，可引用）**：

| 运行 | 结果 | 断言 | 耗时 |
|---|---|---|---|
| `run-20260929-112714` | **59/59** | **563 pass / 0 fail / 1 skip** | 838.2s |
| `run-20260929-114125` | **59/59** | **563 pass / 0 fail / 1 skip** | 779.0s |

**上一版基线（已作废，仅留档）**：`run-20260928-105835` 与 `run-20260928-111249` 均为 58/58、555 pass / 0 fail / 2 skip。
作废原因不是失败，而是 §14.24 改了检查内容：新增一个 Node 检查文件（+1 项、+6 断言）、
`local_keyframe_ui.cjs` 加一条真行为断言（+1）、`mock_web_smoke.cjs` 修掉一个**静默分支**（该检查 19→20 pass、1→0 skip）。
**改了检查内容，前一轮基线即作废**——这是本仓库的既有纪律。

交叉验证：555（上一基线）+ 6（新 Node 检查）+ 1（本地首帧新增断言）+ 1（mock 冒烟静默分支补回）= **563**，
skip 2 → 1 正好是那个补回的分支，与实测一致。

**中间那一轮失败也记下来**：`run-20260928-104159` = **57/58**，`551 pass / 0 fail / 2 skip`，788.4s，
唯一失败项 `test_adaptation_start_refresh.py`（exit 1、43.2s），根因就是上面第 3 条。修好后重跑才拿到上表。
按纪律，**失败那一轮不参与配对**，两次全绿必须都在修复后的修订上。

**同一天的中间轮 `run-20260929-111142` 也不参与配对**：59/59、`562 pass / 0 fail / 1 skip`、770.2s。
它绿得很干净，但那是**在修静默分支之前**的口径——skip 从 2 变 1 就是它暴露的（详见 §14.24）。
"全绿"本身不构成配对资格，**断言条数对不上就不是同口径**。

### 14.23 切片 7：首尾帧真实付费验收（② 批次）——dashscope + minimax 通过且「收束」成立，ark 仍欠费未提交（2026-09-29）

**授权口径**：竹木 2026-09-29 给「付费授权上限 **60 元 / 单批 ≤ 20 元**」，并在此前明确「除录屏外我全自动接管」。
据此把**上限判定为授权**，取消原定的「每批开跑前等竹木回一个字」——改为**跑前把清单写进文档、不再等回字**。
这是本次**唯一一处扩权**，竹木一句话即可收回。

**先补上一个缺口**：`keyframes` 模式在 provider 层**早已实现**（三家都真的拼了 `last_frame`，能力矩阵也标了
`requires_last_frame`），但两个付费运行器一个写死 `i2v`、一个写死 `reference`——**这条路从来没有被提交过**。
新写 `tools/run_keyframes_smoke.py`：报价档、`--refresh` 只回查不重提、按 provider 取合法时长档，
并自带一条防线（**首尾帧是同一文件就拒跑**，防住"拿同一张图测收束"这种假验收）。

**尾帧图换过一次，值得记**：第一版取 9-28 参考图验收那条视频的末帧，结果它与首帧**构图几乎相同、只是略推近**——
用它区分不了「收束」与「忽略」。改用演示项目 `p6demo_story` 一条镜头的末帧（**正面→背影、亮场→暗场**），裁到与首帧同比例（4:3）后判据才立得住。
`output/keyframes-smoke/end-frame.jpg`，`sha1_16 = 9f76f5d084307fce`（首帧 `gyfy.jpg` = `4fda98c6eccf9306`）。
**一个不能证伪的判据，等于没做验收。**

**四格结果**（日志 `output/live-20260929/`）：

| 格 | provider | 模型 | 档位 | 本地状态 | 结果 | 实付 |
|---|---|---|---|---|---|---|
| 批1 | `ark` 参考图 | `doubao-seedance-2-0-260128` | 720p / 5s | `no-task` | ❌ HTTP 403 `AccountOverdueError` | **0.00** |
| 批2a | `dashscope` 首尾帧 | `wan2.7-i2v` | 720P / 5s | `completed` | ✅ 5.062s，收束成立 | 3.00 |
| 批2b | `minimax` 首尾帧 | `MiniMax-H3` | 768P / 4s | `completed` | ✅ 4.458s，收束成立 | 2.00 |
| 批2c | `ark` 首尾帧 | `doubao-seedance-2-0-260128` | 720p / 5s | `no-task` | ❌ HTTP 403 `AccountOverdueError` | **0.00** |

**合计实付 5.00 元**（预算 17.08；ark 两格未提交，省下 12.08）。

**「收束」的判定方式**——只看 `status: completed` 不算通过，provider 完全可能忽略尾帧、只按首帧生成后照样返回 completed。
所以对每条产物抽三帧（起/中/末），与两张输入横排：`输入首帧 | 输出起 | 输出中 | 输出末 | 输入尾帧`
（`output/keyframes-smoke/convergence-dashscope.jpg`、`convergence-minimax.jpg`）。
两家都呈现 **正面（左绿蝶+右金虫）→ 中途转身 → 末帧背影走入水墨暗处**，末帧构图与输入尾帧一致；
**可证伪点**是：若忽略尾帧，第 3、4 格应仍是正面亮场——它们不是。抽帧脚本 `tmp/verify_keyframes_convergence.py`（一次性探针，未提交）。

**顺带核出的两条事实**：
1. **同一个 provider 在不同模式下可能用不同模型**：`dashscope` 参考图走 `wan2.7-r2v`，首尾帧走 `wan2.7-i2v`。报价必须按模式读，不能按 provider 记死。
2. 默认闸门 `DEFAULT_BUDGET_CNY=5.0` **装不下 ark 的单次 6.04**，正式跑必须显式设 `VISIONCRAFT_LIVE_BUDGET_CNY=20`（否则被闸门拦下，不是失败而是拒绝提交）。

**ark 那条 403 与账面冲突**（只有竹木能解）：控制台读数欠费 **¥0.00**、可用余额 **¥31.43**，API 判定却是 `AccountOverdueError`。
两条 Request id：`021790650188282b3954f28f435bddee30da27ebc9f6d6ead9b79`（参考图）、`021790650336364f5028489cb353b460b901382cc7748695bfb29`（首尾帧）。
三种可能：结清到解冻有延迟 / 同账号另有未结清项（子账号·其他计费项·其他 region）/ 需人工申请解冻。

**结论口径（别越界）**：
- ✅ 可以说：**首尾帧路径已由两家真实验收通过，并验证了「收束到给定尾帧」**。
- ❌ 不能说「首尾帧三家全部验证」——ark 那格是**未知**，不得由 dashscope / minimax 外推。
- ❌ 不能说「ark 可用」——两次实测都是 403。

**未验格子从 4 格降到 2 格**（`ark` 参考图 + `ark` 首尾帧），且这 2 格同源、属于**账号状态**而非代码缺陷。

### 14.24 默认视频 provider 改为 dashscope，并修掉 provider 自动修正的反向判断（2026-09-29）

竹木问"这个策略里用到的是不是都是已验过的资源、今天能不能直接跑通"，并据此拍定**默认视频 provider 从
`minimax` 改为 `dashscope`**。答案是能，而且逐格对得上——但要点是**按模式读，不是按 provider 记**：

| 模式 | 落到的模型 | 验收记录 |
|---|---|---|
| `t2v` | `wan2.7-t2v` | 2026-08 三家 T2V 各完成一次付费异步任务并下载 MP4（`task_plan.md` / `progress.md`） |
| `i2v` | `wan2.7-i2v` | 2026-08-28 三家同一首帧对比 |
| `keyframes` | `wan2.7-i2v` | 2026-09-29 本日志 §14.23（5.062s、收束成立、3.00 元） |
| `reference` | `wan2.7-r2v` | 2026-09-28 §14.20（3.00 元） |

注意 `keyframes` 与 `i2v` **用的是同一个模型**，而 `reference` 用另一个——所以"这家验过了"这句话必须落到模式上。

**改默认值顺带关掉的缺口**：默认是 `minimax` 时，minimax 没有参考图能力，于是"不指定 provider 直接走
参考图模式"会抛 `UNSUPPORTED_MODE_FOR_MODEL`。换成 `dashscope` 后四种模式都有落点。

**修掉一个界面缺陷（第二个真缺陷）**：`frontend/js/render.js` 的 `syncVideoDraft` 用
`options.some((item) => item.id === provider)` 判断"要不要修正 provider"，而 `videoProviderOptions()`
返回**全部** provider（不支持的只标 `disabled`）——只要 provider 是已知 id，判断**恒为真**、修正永不触发。
后果：手动选 minimax 再把模式切到参考图，界面留着 minimax、模型下拉被过滤成空，**提交时才报错**。
现已抽成导出的纯函数 `resolveVideoProvider(options, providerId, defaultProviderId)`：可用则保留用户选择 →
否则退到默认（若可用）→ 再退到第一个可用的 → 都没有则**原样返回**，把问题暴露给界面而不是替用户猜。
**「未配置」仍不等于「不支持」**：未配置但支持该模式的 provider 算可用，是否拦在开 HTTP 之前由后端闸门决定。

**测试侧的三处改动**（这是本次最该记住的部分）：

1. `tools/test_video_provider_resolution.mjs`（新，6 条）：钉住修正语义，并**专门保留一条断言复现旧缺陷的形状**
   ——证明"存在性判断"在该 fixture 上恒为真。否则改完之后没人知道原来错在哪。
2. `tools/local_keyframe_ui.cjs` 加**真行为**断言：先选 minimax、再把模式切到参考图，断言 provider 被换掉
   且模型下拉非空。**纯函数测试防不住有人把调用点改回旧写法**，所以这一步必须落在浏览器里。
3. `tools/mock_web_smoke.cjs` 的"视频阶段应预选 MiniMax"**写死了默认值**，改配置后必挂（而那不是产品回归）。
   改为读 `/api/providers/capabilities` 的 `default_video_provider` 再比对面板文字——**断言与配置同源**。
   顺带一个细节：面板上显示的是 provider **id**，不是中文标签，第一版改成比对标签是错的。

**针对性回归**（`--only`，5 项 / 45 断言全绿）：新增 Node 6 条、mock 冒烟 19 条、本地首帧浏览器 10 条、
`test_provider_capabilities` 5 条、`test_v1_usability` 5 条。`test_v1_usability` 的
`test_default_preselects` 用 `_without_env("VISIONCRAFT_VIDEO_PROVIDER")` 显式摘掉变量，测的是**代码内建默认值**，
所以它没有被这次配置变更影响——这是个好设计，值得沿用。

**检查侧又抓到一个缺陷：全绿，但断言条数悄悄变了。** 第一轮全量回归 **59/59、0 fail**，
`asserts` 却是 `562 pass / 1 skip`——对比上一基线是 `555 pass / 2 skip`。**skip 少的那一条，正是一条检查没跑。**
逐行 diff 两轮 `logs/test_mock_web_smoke.py.log` 才定位到：少的是 `SKIP: 视频阶段只有一个模型，无法切换`，
而且**没有 PASS 补上来**。根因是那段检查自己写的

```js
if (options > 1) { if (next && next !== current) { ...; pass(...); } }   // 内层 if 没有 else
else { skip(...); }
```

默认 provider 换成 `dashscope` 后，视频阶段出现 3 个模型（`wan2.7-t2v` / `wan2.7-i2v` / `wan2.7-r2v`），
而**阶段默认模型恰好是排第二位的 `wan2.7-i2v`**，于是 `nth(1) !== current` 为假 → 两个分支都不走 →
一条断言凭空消失，而整轮**仍是绿的**。已改成「找第一个与当前值不同的选项」，找不到才 skip
（并把当前值写进 skip 文案）；修后该项 = 20 pass / 0 fail / 0 skip。

**由此得到三条规矩**（已写进 `PITFALLS.md`）：
① 每轮都要对**断言条数**，不只对 checks 是否全绿；条数变化必须能解释到具体某一条；
② 写检查时**每个分支都要打印 PASS/FAIL/SKIP**——允许"什么都不打印"的分支，等于允许断言消失；
③ **改夹具或配置就要重估条数**（配置改动会改变夹具路径，路径变了就可能绕过断言）。

### 14.25 修掉「未保存的章节勾选不跨重绘」（2026-09-29）

**这不是"勾看起来消失了"，而是"保存了错误的范围"。** 根因两段接在一起：

1. `frontend/js/app.js` 的 `collectMediumScopePayload()` **直接从 DOM 读勾选**
   （`[data-chapter-check]:checked`）；
2. `render.js` 的 `renderAll()` 是**整块替换** `stageWorkspace` 的 innerHTML，
   而后台任务事件会调用它——一次长文本分析实测触发 **13 次**。

于是落在重绘窗口里的勾选会消失，用户接着点「保存范围」，提交上去的是重绘后的空范围。

**修法照抄仓库里已有的草稿态范式**（`assemblyDraft` / `stageEdit`），不发明新机制：

| 位置 | 改动 |
|---|---|
| `state.js` | 新增 `scopeSelectionDraft: { projectId, dirty, values }`，在 `resetViewState()` 里清空（= 切项目清空） |
| `render.js` | `storylineStageHtml()` 新增 `scopeDraftLive`：草稿新鲜且属于当前项目时，勾选与「修改说明」按**草稿**渲染 |
| `render.js` | 草稿生效时，预览区**不再**拿旧的 `scoped_text` 宣称"系统将把以下选中范围交给后续改编"，改为如实提示"勾选已改动、尚未保存" |
| `app.js` | 新增 `scopeSelectionValues()` / `savedScopeValues()` / `scopeSelectionEqual()` / `updateScopeSelectionDraft()` / `effectiveScopeSelection()`；`collectMediumScopePayload()` 改为优先取草稿 |
| `app.js` | `onWorkspaceInput` 增加 `[data-chapter-check]` / `[data-event-check]` / `#scopeUserNote` 分支，**只写草稿、不重绘**（重绘会让正在输入的说明框丢焦点） |
| `app.js` | 只在**该清的动作**上清草稿：`save-` / `confirm-medium-scope` / `recommend-scope` / `regen-medium` / `select-storyline` / `regen(analysis\|storyline)`。不在每个动作上清，是为了让无关动作（如视觉检查）不吞掉未保存的勾选 |

**预览那一处是顺带修掉的界面缺陷**：勾选改了但没保存时，旧 `scoped_text` 还在，界面照它宣称"将把以下范围交给后续改编"——
那是在说一件假话。现在这一档改成如实说明。

**测试的非空虚设计**（`tools/scope_draft_ui.cjs` + `tools/test_scope_draft_browser.py`，9 条断言）：
用例分两层，缺一层都不成立——① 用户可见层：重绘后勾还在、且出现"尚未保存"提示；
② 真正结算层：此时点「保存范围」，**服务端落库的 `chapter_ids` 必须等于用户勾的那一节**。
关键是另加一条**非空虚保证**：重绘前在元素上挂内存标记，重绘后该标记必须消失。
没有这一条，一个根本没重绘的环境也能让 ① 通过，用例就失去分辨力。

**并做了证伪检验**（这是本次最该保留的习惯）：临时把 `render.js` 的 `scopeDraftLive` 硬置为 `false`，
重跑用例——它在预期位置失败：

```
PASS: 刷新触发了整块重绘（元素已被重建，标记消失）
Error: 重绘后未保存的勾选丢了——这正是要修的缺陷
```

随后**还原并核对文件指纹**（`render.js` sha256 前缀回到 `4d93c035…`，与修复版一致，无残留标记）。

**用例自身也踩了一个坑，值得记**：第一版在服务端落库后**立刻**读 DOM，而保存后的那次 `renderAll()`
还没跑完，读到的是保存前的旧 DOM —— 于是把"用例读太早"误报成"草稿没清掉"，**失败信息指向了错误的地方**。
现在改成先等一个**正向信号**（界面开始按已落库的 `scoped_text` 说话）再断言，并把工作区末尾文案写进失败信息。

### 14.26 切片 8：P5-B-2 第一层——章节感知索引 + FTS5 字面召回（2026-09-29）

**先把边界说死：这一层不是 RAG。** 它没有任何语义理解，只解决一件事——
**"字面就写在原文里"的块，不能召不回来。**

#### 为什么原来会漏召

原有检索只有一条路：ChromaDB 里 384 维**本地 hash embedding**（字符二元组哈希）
取 `limit*3` 个候选，再用 `_lexical_score` 按"查询字符出现在文档里的比例"重新加权。
注意那是**字符集合**、不看顺序，而且候选集完全由向量路径给定。

实测（95,618 字语料）选一个在原文里字面出现 2 次的片段 `你已经中了我的独门毒蛊`：
**向量 top-18 一个都没召回**，也就是说没有全文索引时，这条查询返回的全是**不含该片段**的块。

#### 改了什么

| 位置 | 改动 |
|---|---|
| `backend/database.py` | 新增 `_ensure_source_chunk_fts()`：建 FTS5 虚拟表 `source_chunk_fts(project_id UNINDEXED, chunk_id UNINDEXED, text, tokenize='trigram')`；建表失败（SQLite 未编入 FTS5）只告警、不阻断启动，检索退回纯向量路径 |
| `backend/services/memory_service.py` | ① 索引单位从「硬切 `source_text`(900/120)」改为 `_source_text_units()`：**有 `source_chunks` 就用它**（章节感知，块不跨章），没有才退回硬切；metadata 带 `chapter_index` / `chapter_title` / `chunk_id` |
| 同上 | ② 新增 `index_source_chunk_fts()`（按项目重建）、`_fts_phrase()`、`_fts_match_expression()`、`_fts_candidates()` |
| 同上 | ③ `index_project_memory()` 末尾一并重建全文索引；`search_project_memory()` 改为**向量候选 ∪ FTS 候选**后按同一套分数排序，条目新增 `fts_hit` 标记 |
| `backend/services/medium_text_service.py` | `_persist_analysis()` 在**事务之外**重建索引——该函数整批删掉并重建 `source_chunks`，索引必须跟着换 |

#### 四个实测出来的边界（都不是推理）

1. **trigram 对短于 3 字的查询静默返回 0 条。** `方源`（2 字）→ 0 条，不报错。
   所以 `_fts_match_expression()` 对 <3 字的片段返回 `None`，由调用方退回字面重合打分——
   **"索引答不了"不等于"没有匹配"**，不能静默变空。已有一条断言专门钉这个。
2. **查询必须整体短语化转义。** 裸引号（`x"y`）会让 FTS5 抛 `unterminated string`；
   而 `方源*`、`方源 OR 春秋`、`NEAR(方源 山)` 在 trigram 下**既不是通配也不是布尔**
   （实测都返回 0 条）。统一短语化后，用户输什么就查什么，不崩也不被误解析。
3. **短语匹配是连续子串语义。** 用最小例子核实：把同一批字符打乱顺序不命中，
   因此每个 FTS 候选都**真的包含**该片段——用例据此断言"每个候选文档都含查询片段"。
4. **bm25 不当分数用。** 它的量级随语料规模变，跨语料不可比；FTS 只负责"找得到"，
   排序仍由 `lexical*0.8 + vector*0.2` 决定，字面命中另加**有上限的固定加成 0.05**。

**索引与内容同源是第二道守卫**：`_persist_analysis` 会整批删块，索引会留下悬空行。
所以检索一律 `JOIN source_chunks` ——即使某次忘了重建，也不会返回已经不存在的块。已实测：
删掉目标块后，检索立刻不再返回它。

#### 实测（`tools/test_source_chunk_retrieval.py`，9 条断言，零费用）

- 语料 95,618 字 → **131 块 / 30 节**；全部 131 块进向量索引，全文索引 **131 行**与块一一对应；
  **分析一结束索引就已新鲜**（不靠外面再点一次"建索引"）。
- 跨项目隔离：项目 B 只取前 40,000 字（该片段在第 45,798 字之后、B 里没有），
  A 的片段在 B 里查不到。
- **召回增益**：字面出现 2 次的片段，向量 top-18 两个都不含它；FTS 补齐后这两块排到
  **第 1、2 位**且都真的包含该片段，章节归属分别落在**第 16、15 节**（并与章节偏移交叉核对一致）。
- 2 字查询：全文索引侧为空（trigram 下限），**合并检索仍有 6 条结果**（向量路径兜住）。
- 短文本项目：没有章节块 → 全文索引 0 行，检索行为不变（无回归）。
- HTTP 冒烟：`/memory/index` + `/memory/search` 正常，`x"y` / `方源*` / `方源 OR 春秋` 均不 5xx。

#### 仍**未**做的（勿读成已闭环）

- **真实语义 embedding 没换**：仍是本地 hash embedding（0 费用），换本地 bge 类模型或云端
  embedding API 是**竹木的待定决策**，本次按"零费用、不引新依赖"的默认值执行。
- **没有语义召回**：同义词、改写、指代一律召不回来。这一层解决的是字面漏召。
- 索引仍是**按项目全量重建**，没有增量更新；也没有跨项目/全局检索。

#### 回归（连续两轮同口径）

`61/61 checks`、**581 pass / 0 fail / 1 skip**（旧基线 59/59 · 563 pass；
新增 `test_scope_draft_browser.py` 9 条 + `test_source_chunk_retrieval.py` 9 条 = +18，
563 + 18 = 581，且 skip 数不变 —— 条数变化可解释到具体来源，符合 §14.24 立的三条规矩）。

**结构文档同步**：`docs/visioncraft-diagrams.md` 改图 6（增 `source_chunk_fts` 节点、
写清 27 张业务表 + 6 个 FTS 对象 = 33 个 table 对象）、图 7（58 → 61 项）、
新增**图 12**（检索层通路 + 五条设计取舍）、并改掉"没有向量检索、全文索引"那句旧边界。
渲染校验脚本已**提交**为 `tools/check_mermaid_docs.cjs`（本轮 **22/22 块可渲染**，
并记录"尺寸随视口变、跨探针不可比"的口径）。

### 14.27 无人版演示录屏产出（2026-09-29）

竹木出门前授权"按你的规划自动接管"；路线图 §8.5 明确"**无人在场则由我录自动演示版**"，
所以这是被授权的交付动作，不是自作主张。

**产物**：`output/demo/visioncraft-demo.mp4` —— h264 / 1440×900 / **133.6s** / 2.3MB /
**无声**（无音轨）。原始录屏 `output/demo/raw/*.webm`（vp8，10.5MB）保留，便于重编码。
**费用 0 元**：子进程的 `VISIONCRAFT_ALLOW_LIVE_LLM / _VISION / _VIDEO` 三个开关在启动前被摘掉，
所以"录着录着真花钱"在结构上不可能。**隔离性**：跑在 `output/demo-session/data`，
不碰竹木的真实项目库。

**素材来源（都是真的、都是免费的）**
- 全流程段：`prepare_v1_demo.prepare()` 的夹具——改编走 **mock 规划器**，关键帧/视频/配乐/字幕由
  **ffmpeg lavfi** 生成，四镜成片是真实 MP4（探测：`h264 1280x720 5.07s audio=aac`）。
- 长文本段：工作区根真实语料，`95618 字 / 30 节 / 131 块`，章节树、按章范围、字面检索全部本地算。

**录到哪 8 个场景**：工作台 → 八阶段扫览 → 镜头视频（**可见新默认 `dashscope` /
"阿里百炼 Wan 2.7 I2V"**，即 §14.24 的默认值变更真的落到了界面上）→ 成片合成
（`#assemblyPanel video` 真实预览）→ 导出交付 → 长文本章节树按节圈范围 → 按字面"蛊虫"检索。

**核验方式是"看产物"，不是只看 ffprobe**：一个"全白屏"的视频同样能通过 ffprobe，
所以另抽 5 帧（6s / 45s / 78s / 112s / 128s）**逐帧目视**，确认渲染的是真实界面：
6s 见项目列表 + 阶段轨 + 文本理解面板；45s 见 Story Bible 已确认态；78s 见三张镜头卡与
provider 面板；128s 见章节树（第十八~二十一节，含字数/偏移/块数）+ 已填 "蛊虫" 的检索框。
（帧文件在 `output/demo/frames/`，`output/` 已 gitignore，不入库。）

**版本控制**：`tools/record_v1_demo.py` + `record_v1_demo.cjs` 已提交（`e7343aa`）。
与 `v1_demo.cjs` **刻意分开**：那个是**验收**（断言、截图、失败即红），这个是**演示**
（有停顿、滚动、无断言）。把断言塞进演示会让两者都变脆。

#### 顺带澄清一个会误导人的现象：两轮日志 `elapsed` 完全相同

Round A / Round B 两轮全量回归的 `elapsed` 都是 **806.7s**，与 `checks / asserts` 两行一样
**逐字节相同**，第一眼像是"第二轮其实没跑、日志被覆盖"。**这是错的**：两轮共 **86 行不同**
（逐项耗时、夹具 `seeded_at`、`Run data dir` 全不同；逐项耗时和 776.9s vs 777.9s）。
用时间戳反推也能对上：A 夹具 `12:09:10` → 报告 mtime `12:22:37` ≈ 807s；
B `12:23:02` → `12:36:29` ≈ 807s —— 两轮都真跑了，`elapsed` 也没算错。
**规矩**：`elapsed` 既不是独立性证据、也不是跨轮可比指标；判"是否真跑过"用 run 目录 +
夹具时间戳 + 逐项耗时。已入 `.workbuddy/memory/PITFALLS.md`。

#### 本轮仍未完成（勿读成已闭环）

- **ark 参考图补验**：账号 HTTP 403 `AccountOverdueError`，充值后仍被拒 → **等竹木联系客服**。
- **部署与非核心扩展**（原 P7 段）：未开始。
- **推送**：本地领先 `origin/feat/v1-media-pipeline` 10 个提交（代理未起，按约定攒着）。


## 14.28 自我证伪：ark「账号已解冻」这个结论是错的（2026-09-29 晚）

**起因**：竹木问“我们真的没有排查一下那个说明 ark 的方法了吗”。上一轮我用两个免费探针
（`GET` 不存在的任务 id → 404；`POST` 但 `model` 传不存在的名字 → 404）判定“账号闸门已放行”，
并据此把路线图里的“火山方舟充值”改标为**不再是阻塞项**。**那个判读是错的。**

**错在哪（定序）**：2026-09-29 19:3x 用**合法模型**（`doubao-seedance-2-0-260128`，`/models` 里
`status=None`）配 **`content:[]`**（结构非法，绝不可能生成视频）打**同一个创建接口**，返回

```
HTTP 403  {"error":{"code":"AccountOverdueError","type":"Forbidden",
  "message":"The request failed because your account has an overdue balance.
             Request id: 0217906817757904de2cf568a9a04a3957d59acb492788b3934fd"}}
```

**不是 400**。于是同一路径上同日三次请求构成定序证据：

| 请求体 | 命中层 | 返回 |
|---|---|---|
| 非法模型 + 有 content | 模型解析 | `404 InvalidEndpointOrModel.NotFound` |
| **合法模型 + 空 content** | **账号闸门** | **`403 AccountOverdueError`** |
| 合法模型 + 合法 content | —— | （**不跑**：会真建任务、真计费） |

=> 内部顺序是 **模型解析 → 账号闸门 → 参数校验**。**模型解析排在闸门之前**，所以
“非法模型名得 404”**根本走不到闸门**，那条探针是**空转的**，与账号状态无关。
上一轮我把“晚一层的 404”误读成“闸门放行”，这是**把错误码与其所在层脱钩**读出来的假阳性。

**安全性**：两次探针提交前后各查一次 `GET /contents/generations/tasks`，`total` 恒为 `0`
—— 本探针零副作用、零费用，**没有**产生任何任务。

**结论**：`reference@ark` / `keyframes@ark` 仍是**账号闸门**造成，**且当前无法验收**。
所以**先别花那约 12 元**——闸门未开时提交必然 403、0 元退回。

**带客服的线索**：官方 FAQ 原文“代金券使用失败，报错 `AccountOverdueError` … 如需使用代金券，
**需保证账号余额大于等于 0**”，欠费定义为“**可用额度（含账户余额和代金券）小于待结算账单**”。
故“欠费 ¥0.00 / 可用余额 ¥31.43”与判欠费**未必矛盾**：若那 ¥31.43 主要是**代金券**而现金余额为负，
API 就会判欠费 → 建议核**现金余额**，并拿上表 Request id 找客服。

**已同步修正**：预检 §13.7 重写、路线图 §8.4/§8.5 相应段落改回“阻塞项”、项目记忆与
`PITFALLS.md` 增补定序判例、skill `api-credential-vs-account-triage` 的核心判据与最小例更正。

## 14.29 ark 退役：把“用不了”从投放面拿掉（2026-09-29 22:0x）

**触发**：竹木在方舟控制台实测到平台门槛 —— **账户余额低于 200 元不允许开通
`doubao-seedance-2-0-260128`**，于是决定放弃 Seedance 2.0。逐层排查过程见
`docs/real-live-test-preflight.md` §13.8 → §13.9 → §13.10，收口在 **§13.11**。

**这不是“又一次失败”，是一次范围收缩**：ark 与 dashscope 在能力矩阵里**并列四模式全覆盖**，
而 dashscope 四模式均已真实验收（参考图 3.00 元；首尾帧 3.00 + 2.00 = 5.00 元）。
退役 ark **无能力空洞**，只少了“同一模式第二家可选”。

**核心判据（为什么不能只改文档）**：动手前先读代码确认 ark 是怎么“露面”的。结论是
`frontend/js/render.js::videoProviderOptions()` 直接用 `state.capabilities.video` 渲染 `<select>`，
而 `label` 只在 `mode !== "live-ready"` 时才补“（未配置）”。密钥在、`mode === "live-ready"`，
于是**「火山 Seedance」就是一个不带任何后缀的就绪选项**，选中后必然 404。所以必须动投放面，
而不是把它记成“已知问题”。

**改动**（三处投放面 + 一处校验；逐处都写了“不改会怎样”的注释）：一句话 —— 能力表移除条目、
诊断名单去掉 ark、隐式兜底链去掉 ark、校验层新增 `RETIRED_VIDEO_PROVIDERS` 与 `PROVIDER_RETIRED`
（带可操作提示，而不是“未知 provider”）。完整表格见预检 §13.11。

**刻意保留**：适配器实现、单价表里 ark 的档位（“未登记取最贵档”的保守基准）、适配器互斥规则的既有单测。
它们都不是“广告”，且 `test_video_provider_guard.py` 正靠显式 `provider_override="ark"` 验证
“闸门在网络之前拦下”——删了就丢一段护栏覆盖。

**一个有意留下的“当前不可达分支”**：ark 退场后，能力表里已无 `reference_includes_first_frame=False`
的 provider，`validate_video_generation` 里那条分支当前没有 provider 会走到。这是退役的必然结果，
已写进代码注释，避免下次有人当它是 bug；规则本身仍有 `test_reference_generation.test_payloads` 钉着
（它直接调 `_ark_content_items`，不经能力表）。

**测试同步**：ark 原先在多个用例里充当“代表 provider”（唯一四模式全通）。退役后代表权交给 dashscope。
⚠️ **第一版只改了 3 个文件，是回归抓出爆炸半径比预想大**：`test_stage_models.py`（1 处）与
`test_shot_versions.py`（4 处）也直接用 `provider="ark"` 调 `validate_video_generation` /
`prepare_version_for_generation`（`validate_video_generation` 的调用点是 `video_service` 与
`shot_edit_service` 两个 prepare 函数），退役后同样报 `PROVIDER_RETIRED`。**这正是“改共用代码必须跑
全量回归”的判例**——`ark` 在源码里出现几十次，靠 grep 分不出“存字符串”（无害）与“调校验”（会红）。
最终同步 **5 个文件**：`test_provider_capabilities.py`（7 处）、`test_reference_generation.py`（4 处）、
`test_video_provider_resolution.mjs`（4 处）、`test_stage_models.py`（1 处）、`test_shot_versions.py`（4 处），
并把 ark 那几支改写成可证伪的退役断言 —— 例如“只配 ark 密钥的机器必须报视频不可用”，
这条在退役当天由假变真，正是本次改动的判据。

**逐项计数零漂移**（“回归可信”的证据）：受影响 5 个用例的 PASS 行数与退役前基线**逐一相同** ——
capabilities 5 / reference 10 / mjs 6 / stage_models 13 / shot_versions 11；全量断言总数仍为
**61 项 / 581 pass / 0 fail / 1 skip**（与基线 `run-20260929-122249` 同口径）。

**退役顺带揭开的一个真缺陷（已修）**：能力表**首位 provider 变了**会静默改掉前端新建项目表单的
**默认单镜时长**。`renderCapabilities()` 用 `videos.flatMap(item => item.supported_durations)` 生成时长下拉，
`app.js::resetProjectForm()` 取**第一个选项**作默认值。ark 在首位时默认 5（合法）；ark 退场后首位换成
dashscope（时长 `[2,5,10,15]`）⇒ 默认变成 **2**，而后端 `ProjectCreate.duration_seconds` 是 `ge=5` ⇒
**建项目直接 422**。全量回归一次抓出 **6 个浏览器用例全红**（每个恰好一条 `POST /api/projects 422`），
而它们的表面症状都是 `waitForFunction` 超时——**很像环境抖动**，靠“逐项读后端状态码”才定性。
修法：时长选项**过滤到 `[5,10]` 并升序**，首项恒为 5（并覆盖“能力未加载”的竞态）。修后 6 项条数回到基线
（8/20/10/14/12/19）。**教训：「默认值取列表第一项」的 UI，正确性隐含依赖列表顺序**——改后端 provider
顺序会静默改前端默认值，这类耦合 grep 不出来，只有跑真实浏览器流程才暴露。

**一处必须自我更正**：本轮之前我对这条线的判读被推翻过两次（“账号已解冻”→ 实为探针空转；
“重发 key 无效”→ 其前提“key 与控制台同账号”从未验证）。两次的教训是同一个：**先确认错误码属于哪一层，
再确认那一层是不是排在前面**，否则就是拿“晚一层的错误”当“前面那层已放行”的证据。
已入 `.workbuddy/memory/PITFALLS.md` 与 skill `api-credential-vs-account-triage`。
