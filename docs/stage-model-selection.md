# 阶段模型选择与文本 / 视觉适配器（P7-A）

本文记录 VisionCraft 文本模型、视觉模型、图片生成模型、视频模型的职责边界，以及阶段级选择、默认预选、真实调用关卡。

## 1. 模型角色

| 角色 | 用途 | 默认预选 | 说明 |
|---|---|---|---|
| 文本理解 / 文本生成 | 摘要、故事线、改编方案、Story Bible、分镜、文本提示词 | `deepseek` / `deepseek-v4-flash` | 可改选 `deepseek-v4-pro` |
| 文本高质量 | 更长上下文或更高质量的 Bible / 分镜 | `deepseek` / `deepseek-v4-pro` | 不是锁死模型 |
| 视觉理解 | 关键帧描述、一致性与质量检查 | `deepseek` / `deepseek-v4-flash-vision-exp` | 独立视觉适配器 |
| 图片生成 | 关键帧绘制 | 现有图像能力矩阵，默认火山方舟图像 | 不是视觉理解模型 |
| 视频生成 | 镜头视频 | 默认预选 `minimax`，可改 Ark / DashScope / SiliconFlow | MiniMax 只是预选 |
| 本地合成 | 成片、字幕、混音 | FFmpeg | **不出现在模型下拉框** |

官方模型 ID 以 DeepSeek 文档为准：

- 文本：`deepseek-v4-flash`、`deepseek-v4-pro`
- 视觉：`deepseek-v4-flash-vision-exp`
- 接口：`POST https://api.deepseek.com/chat/completions`
- JSON：`response_format: { type: "json_object" }`，并在 system/user 中要求 JSON
- 视觉输入只允许出现在 user 消息的 content 数组中；图片可以是 Data URL、HTTPS URL 或 Files API `file_id`
- 请求体上限 48 MiB；HTTPS 图片 URL ≤ 8192 字符

```text
默认模型 = 首次使用时的预选值
默认模型 != 固定模型
用户仍然可以在每个支持模型选择的阶段切换 Provider 和模型
```

未配置 Key 时，界面显示「未配置」，**不会**静默换成另一家 Provider。

## 2. 阶段与模型

| 阶段键 | 前端阶段 | 模型角色 |
|---|---|---|
| `text_understanding` | 文本理解 | 文本 |
| `adaptation_options` | 文本理解（短）/ 故事线选择（中） | 文本 |
| `story_bible` | Story Bible | 文本 |
| `storyboard` | 分镜设计 | 文本 |
| `vision_review` | 关键帧 | 视觉理解 |
| `keyframe_generation` | 关键帧 | 图片生成 |
| `video_generation` | 镜头视频 | 视频；镜头级选择优先于项目预选 |
| 成片 / 导出 | 成片合成、导出与交付 | 无 LLM 下拉框；FFmpeg 合成 |

当前配置存在 `workflow_model_configs`，按项目 + 阶段一行。不写入不可变 `shot_versions`（镜头视频版本继续只记录**实际使用过**的 Provider/模型）。

字段：`stage`、`provider`、`model`、`parameters`、`selected_by_user`、`is_default`、`created_at`、`workflow_run_id`。

未指定时使用该阶段默认预选。用户保存后使用用户选择。非法 Provider/模型/角色组合在请求前返回中文 400。

修改阶段模型后：

- 只把该阶段及必要下游标为失效（`projects.stale_stages`，Bible/分镜 `review_status=stale`）；
- **不删除**旧方案、旧任务、旧版本、旧成片；
- 历史卡片继续显示生成时的 Provider、模型和配置来源。

## 3. 生成模式

项目级字段 `generation_mode`：

| 值 | 行为 |
|---|---|
| `mock`（默认） | 本地确定性规划器 / 本地视觉占位，不访问远程 API |
| `live_strict` | 走真实 Adapter；失败则任务失败并说明原因 |
| `live_with_local_fallback` | 真实失败后回退规划器，并明确标记「已使用本地回退」 |

禁止把真实失败静默改写成成功。

付费关卡：即使 `.env` 里有 Key，也必须同时设置

```dotenv
VISIONCRAFT_ALLOW_LIVE_LLM=1
```

才会真正发出 HTTP。P7-A 默认不设置该变量。后续真实前端测试需要用户明确确认 Provider、模型、次数、参数和预算。

## 4. 适配器与媒体传递

- 文本：`backend/providers/llm_adapter.py` 只构造 Chat Completions 文本消息。
- 视觉：`backend/providers/vision_adapter.py` 独立拼 `image_url` content block，禁止文本函数直接加图片字段。
- 图片必须经过 `media_transfer_service`：仅当前项目资产、优先 Data URL；禁止项目外路径、跨项目资产、把本地绝对路径发给远程。
- Data URL 只存在于待发送请求的内存中。`media_transfers.request_reference`、`job_events`、`vision_reviews` 只保存脱敏元数据：`asset_id`、`asset_role`、`provider`、`model`、`transport_mode`、`mime_type`、宽高、字节数、`request_id`。
- 不得记录：完整 Base64、API Key、Authorization、完整签名 URL。

HTTPS URL 与 Files API 是部署期扩展，本地测试使用 Data URL。见 `docs/media-transfer.md`。

## 5. 接口

- `GET /api/providers/capabilities`：`llm` 为模型级列表（含 `roles`、`supports_vision`、`supports_json`、`configured`、`is_default`）；`llm_providers` 保留旧 Provider 摘要；`stages` 为各阶段默认预选；`default_video_provider` 默认为 `minimax`。
- `GET /api/projects/{id}/model-configs`
- `PUT /api/projects/{id}/model-configs/{stage}`
- `PUT /api/projects/{id}/generation-mode`
- `POST /api/projects/{id}/vision-review`

`configured` 只返回布尔值，不返回 Key、长度或前后缀。

## 6. 模型血缘

改编方案、Story Bible、分镜草案、视觉检查记录写入：

- `provider` / `model`
- `generation_mode`
- `used_local_fallback`
- `config_source`（`default` 或 `user`）
- `source`（`mock_planner` / `live_llm` / `local_fallback` / `mock_vision` / `live_vision`）

镜头视频血缘仍在 `shot_versions` 与 `video_tasks`。

## 7. P7-A 未完成

- 未发起任何真实付费 API 调用。
- 中等文本的分块/故事线分析仍以本地确定性规划器为主；确认范围后的改编方案 / Bible / 分镜已接入同一文本策略。
- 关键帧**生成**仍走现有图像占位/既有图像 Provider，尚未把用户选择接到真实 Seedream 调用。
- 视觉检查的真实调用等待人工确认。
- P5-B 超过 10,000 字仍明确拒绝。
- HTTPS URL / Files API 传递、成本配额、用户系统不在本切片。
