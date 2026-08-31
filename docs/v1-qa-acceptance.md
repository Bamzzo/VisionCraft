# VisionCraft V1-QA 验收说明

> 本阶段只收口界面质量、状态一致性和智能体工作流展示。不新增付费 API，不引入新的后端状态机。

## 1. 三种状态

```text
executionStage   系统真实执行到的阶段，由 workflowViewModel 从项目详情、任务和素材推导
viewStage        用户当前查看的阶段，存放在 state.js；点击右侧阶段栏只改变它
selectedAsset    用户当前选中的素材卡片
```

关系：

- 查看阶段可以落后或超前于执行阶段；
- 刷新后按项目恢复 `viewStage` 和 `selectedAsset`，`executionStage` 始终重新推导；
- 项目切换时先 `resetViewState()`，再恢复该项目自己的查看缓存，旧项目的任务、素材和配置不得带入新项目。

## 2. 阶段状态定义

右侧固定 8 个阶段：

1. 文本理解
2. 故事线选择
3. Story Bible
4. 分镜设计
5. 关键帧
6. 镜头视频
7. 成片合成
8. 导出与交付

| 状态 | 含义 |
|---|---|
| 未开始 | 尚未到达，也没有可展示的当前有效产物 |
| 处理中 | 该阶段有排队/运行/等待远端任务 |
| 等待审核 | 产物已生成，需要用户确认范围、Bible、分镜等 |
| 已完成 | 已通过该阶段，下游可继续使用当前有效产物 |
| 已修改 | 已完成但当前草稿或成片规格相对产物已变化 |
| 已失效 | 上游已重做，本阶段历史结果仅供查看 |
| 失败 | 该阶段任务或镜头失败 |
| 跳过 | 当前文本规模不需要此步骤（短文本的故事线选择） |

短文本的改编方案显示在「文本理解」；中等文本显示在「故事线选择」。旧导航 id `adaptation` 会映射到上述阶段，不改变后端 `project.status`。

成片有效且未过期时，执行阶段推进到「导出与交付」。成片过期后执行阶段回到「成片合成」，历史成片仍可查看。

## 3. 接口复用

本阶段未新增 HTTP 接口。视图全部由现有项目详情、任务、事件和成片摘要派生：

- `GET /api/projects`
- `GET /api/projects/{id}`
- `GET /api/projects/{id}/events`
- `GET /api/projects/{id}/job-events`
- `GET /api/projects/{id}/assembly`
- `GET /api/projects/{id}/assembly-settings`
- 既有 P3 / P4 / P5-A 改编、分镜、镜头与合成接口
- 既有 `GET /api/projects/{id}/export/json` 与 `export/markdown`

导出与交付页只读展示当前 `final-video`、项目规格和交付检查，不复制一套合成状态机。路径：

| 导出页状态 | 主按钮 |
|---|---|
| 无成片 | 前往成片合成 |
| 成片过期 | 返回成片合成 |
| 合成中 | 继续查看合成进度 |
| 当前有效 | 预览当前成片、下载当前成片 |
| 有历史 | 查看历史成片 |

下载仍指向真实 `final-video`。活动合成任务存在时复用原任务，导出页不会再次提交。

## 4. 乱码与布局排查

| 项 | 结果 |
|---|---|
| HTML / CSS / JS / Python / Markdown | 以 UTF-8 读写，前端 `<meta charset="utf-8">` |
| JSON 中文 | 经 `textContent` 或 `escapeHtml` 输出，不拼接未转义 HTML |
| 长标题 / 项目 ID / 模型名 / 错误 / URL | `overflow-wrap: anywhere`，Grid/Flex 子项 `min-width: 0` |
| 媒体区 | `aspect-ratio: 16 / 9` |
| 1100×900 | 无横向滚动 |
| 1440×900 | 三栏工作台稳定 |
| 1920×1080 | 宽屏稳定 |
| 动画 | 新增 toast / skeleton / 进度微光均受 `prefers-reduced-motion: reduce` 约束 |

核心错误信息不截断，失败原因允许换行。证据截图：`output/playwright/v1-qa-*.png`（不入库）。

## 5. 浏览器验收尺寸

- 1100×900 窄窗口：`output/playwright/v1-qa-narrow-1100.png`
- 1440×900 工作台：`output/playwright/v1-qa-empty-1440.png`、`v1-qa-video-1440.png`、`v1-qa-export-1440.png`
- 1920×1080 宽屏：`output/playwright/v1-qa-1920.png`

命令：

```powershell
node tools/test_workflow_view_model.mjs
.venv\Scripts\python.exe tools\test_v1_qa_browser.py
.venv\Scripts\python.exe tools\test_p7c_ui_state_browser.py
```

## 6. P7-C 阶段状态推导（当前版本优先）

- 关键帧：I2V 只需当前版本首帧；T2V 不强制；已有当前版本真实视频也算越过关键帧。没有关键帧任务时显示「未开始」，不是「处理中」。
- 镜头视频：只看 `current_version_id` 对应版本。当前版本视频有效时，不得因历史版本或 `video_invalid` 显示「已失效」。
- 成片完成后：执行阶段为「导出与交付」；关键帧、镜头视频、成片合成、导出均为「已完成」。
- 成片过期：执行阶段回到「成片合成」，导出不再是已完成；历史成片仍可查看。
- 页面刷新按后端项目详情重推 `executionStage`；项目切换先 `resetViewState()`。
- 浏览器新证据：`output/playwright/p7c-ui-state/`，详见 `docs/p7c-ui-state-evidence.md`。本阶段真实网络请求为否，费用 0 元。

## 7. 仍存在的原型边界

- 自动编排引擎未在后端实现，「暂停流程」仍只在本页会话生效；
- 真实 TTS、AI 配乐、用户系统和部署不在本阶段；
- 导出页不重新合成，过期成片需回到「成片合成」；
- 未开始阶段可以查看说明，但不能绕过审核或制作前置条件。
- 自动编排、通用上传、P5-B（超 1 万字）和部署仍未完成。
