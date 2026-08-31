# VisionCraft V1 可用性验收（P7-D）

本切片把已有能力收成「打开网页即可使用」的 V1。不调用真实 API，不产生费用，不重跑真实模型测试。

## 1. 默认模型与用户选择

```text
默认模型 = 首次使用时的预选值
默认模型 != 固定模型
```

| 阶段 | 默认预选 | 用户可改 |
|---|---|---|
| 文本理解 / 改编 / Story Bible / 分镜 | DeepSeek 文本 `deepseek-v4-flash` | 可改 `deepseek-v4-pro` 等已列出的文本模型 |
| 视觉检查 | DeepSeek Vision | 可改列出的视觉模型 |
| 镜头视频 | MiniMax | 可改 Ark / DashScope / SiliconFlow；镜头级选择优先 |
| 成片 / 导出 | FFmpeg，无模型下拉框 | — |

行为：

- 新建项目自动加载各阶段默认预选；
- 未保存时请求计划使用默认预选；
- 用户保存后显示「用户选择」，并改变后续请求计划；
- 刷新后从 `workflow_model_configs` 恢复；
- 切换项目后配置不串项目；
- 未配置 Provider 显示中文「未配置」，不会静默换成另一家；
- 历史输出继续显示实际 Provider、模型和配置来源。

现有接口已满足：`GET/PUT model-configs`、`PUT generation-mode`。本切片未新增状态机。

## 2. Mock 与真实模式

| 模式 | 页面文案 | 用户看到的行为 |
|---|---|---|
| `mock` | 本地演示（不调用真实模型） | 改编、视觉检查、镜头结果来自本地夹具，不伪装成真实模型 |
| `live_strict` | 真实模型模式（失败即失败） | 走真实 Adapter；失败即失败 |
| `live_with_local_fallback` | 真实模型模式（允许本地回退） | 失败后明确写「已使用本地回退」 |

真实访问未开通时，页面给出可执行中文提示，不出现环境变量名、Key 或 Key 长度。开发者配置仍只放在 `.env`。页面加载不会自动发出付费请求。

顶栏与项目摘要都显示当前生成模式。

## 3. 导出页与成片页

成片合成是唯一提交合成任务的地方。导出页只展示结果和跳转。

```text
无成片     → 前往成片合成
成片过期   → 返回成片合成
合成中     → 继续查看合成进度（显示当前任务与进度）
合成完成   → 预览当前成片 / 下载当前成片
有历史     → 查看历史成片
```

规则：

- 下载入口继续使用真实 `final-video`；
- 合成完成后任务中心刷新即可更新导出页，无需手动刷新整页；
- 不在导出页复制合成逻辑；
- 不自动重复提交活动合成任务；活动任务存在时复用原任务。

成片过期后，导出页不能直接重新合成，必须返回成片阶段。

## 4. 真实测试报告字段

下次真实测试清理前写入（均在 `output/`，不入库）：

```text
live_run_audit.json
live_run_lineage.json
live_run_ffprobe.json
browser_evidence.json
browser_dom_snapshots.json
browser_screenshot_hashes.json
```

审计至少包括：`project_id`、`generation_mode`、`text_calls_total`、`vision_calls_total`、`video_submits_new`、`video_tasks_reused`、`unique_remote_tasks`、`remote_tasks_completed`、`downloaded_videos`、`duplicate_submits`、`duplicate_assets`、`ffmpeg_ran`、`final_cut`、`preview_ok`、`download_ok`、`cleanup_verified`。

资产血缘至少包括：`shot_id`、`shot_index`、`version_id`、`provider`、`model`、`video_mode`、`duration_seconds`、`first_frame_asset_id`、`video_asset_id`、脱敏 `remote_task_id`、`local_file_path`、`status`。

ffprobe 至少包括：`format`、`duration`、`size`、`video codec`、`width`、`height`、`frame rate`、`audio stream`。

禁止保存 API Key、Authorization、完整 Data URL / Base64 / 签名 URL、完整远程响应、不必要的完整 Prompt。

## 5. 断点恢复统计口径

必须区分：

```text
本次新提交任务
中断前已有任务
复用任务
唯一远程任务
```

上一次真实 5 镜测试（项目已清理，不重跑）：

```text
MiniMax 新提交：4
MiniMax 复用：1
MiniMax 唯一任务：5
重复提交：0
重复资产：0
```

镜头 1 是中断前已有任务，续跑只回查原 `remote_task_id`，不算新的 API 调用。

## 6. 尚未完成

- 自动编排引擎未在后端实现；
- 通用上传（任意用户文件进入工作流）未完成；
- P5-B：超过 10,000 字仍明确拒绝；
- 部署、对象存储 HTTPS URL、用户系统、成本配额不在 V1 本切片；
- 真实 TTS / AI 配乐不在本切片。

## 7. 命令

```powershell
.venv\Scripts\python.exe tools\test_v1_usability.py
.venv\Scripts\python.exe tools\test_live_safeguards.py
.venv\Scripts\python.exe tools\test_stage_models.py
.venv\Scripts\python.exe tools\test_v1_qa_browser.py
.venv\Scripts\python.exe tools\test_p7c_ui_state_browser.py
```

本阶段：真实网络请求 **否**，费用 **0 元**。
