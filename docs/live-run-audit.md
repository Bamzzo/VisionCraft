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
| `video_submits_new` | 本次进程新提交的 MiniMax 任务数 |
| `video_tasks_reused` | 中断后复用、只回查不重新提交的任务数 |
| `unique_remote_tasks` | 唯一 `provider + remote_task_id` 数量 |
| `remote_tasks_completed` | 已完成的远程任务数 |
| `downloaded_videos` | 实际落库的镜头视频数 |
| `duplicate_submits` | 同一镜头重复提交数 |
| `duplicate_assets` | 同一远程任务重复视频资产数 |

关系：

```text
video_submits_new + video_tasks_reused = unique_remote_tasks
```

不要把「4 次新提交」写成「5 次新提交」。5 是唯一远程任务数。

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
