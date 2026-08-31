# P7-C：阶段状态与浏览器证据

本切片不调用真实图片、视频、视觉、文本、语音或音乐 API，不修改 `.env`，费用 **0 元**。

## 1. 三种状态

```text
executionStage   系统实际执行到的阶段，由 workflowViewModel 从项目详情推导
viewStage        用户当前查看的阶段（右侧点击只改它）
selectedAsset    当前选中素材
```

只读查看不得改变执行状态。刷新后 `executionStage` 始终按后端数据重算。项目切换调用 `resetViewState()`，不得继承旧项目阶段。

## 2. 当前版本与历史版本

- 镜头状态只依据 `current_version_id` 对应版本。
- I2V 只需当前版本首帧；关键帧模式才要求首尾帧；T2V 不强制关键帧。
- 当前版本已有 `provider:` 真实视频（非 `provider:ffmpeg`）时：
  - 关键帧视为已越过；
  - 不得显示「镜头视频已失效」；
  - 历史版本失效只保留在历史卡片中。
- 没有 `keyframe_redraw` / `adaptation_production` 任务时，不得显示「关键帧处理中」。缺帧且无任务显示「未开始」。

## 3. 成片完成后的右侧阶段

在 5 镜 I2V（仅首帧）且当前版本视频有效、成片未过期时：

| 阶段 | 正确状态 |
|---|---|
| 文本理解 / Story Bible / 分镜设计 / 关键帧 / 镜头视频 / 成片合成 / 导出与交付 | 已完成 |
| 故事线选择（短文本） | 跳过 |
| 关键帧 | 不得为处理中 |
| 镜头视频 | 不得为已失效 |

成片过期后执行阶段回到成片合成，导出不再是已完成。合成进行中时导出为未开始，不得跟成片一起显示处理中。被跳过或尚未到达的阶段显示「跳过」或「未开始」，不得显示「处理中」。

## 4. 右侧 8 阶段展示

每个阶段同时显示：名称、中文状态、可查看/可执行、素材或任务数量、前置条件提示。`aria-label` 与状态文本是无障碍表达，颜色不是唯一状态通道。

## 5. 浏览器截图规则

- 新证据目录：`output/playwright/p7c-ui-state/`（gitignore，不提交）。
- 不得修改 `output/playwright/live-multishot/` 历史截图。
- 每张截图前等待对应 DOM 条件（阶段标题、工作区标题、中文状态、项目 ID、任务/素材摘要、关键按钮），不以固定 sleep 作为唯一同步。
- 截图前记录 DOM 文本摘要。
- 记录每张截图 SHA-256；相邻哈希相同且未声明「同阶段 UI 无变化」则 FAIL。
- 产物：
  - `browser_evidence.json`
  - `browser_dom_snapshots.json`
  - `browser_screenshot_hashes.json`

覆盖：`created`、`adaptation`、`story_bible`、`storyboard`、`first_frame`、`vision_review`、`video_partial`、`video_complete`、`assembly_running`、`assembly_complete`、`download_ready`。

视觉检查步骤只展示已写入的本地 Mock 结果，不点击「用视觉模型检查当前首帧」，避免真实 Vision 调用。

命令：

```powershell
.venv\Scripts\python.exe tools\test_p7c_ui_state_browser.py
```
