# 真实前端闭环预检（P7-A 护栏）

本页记录 DeepSeek + DeepSeek Vision + MiniMax 真实测试的护栏。**当前修复切片不发送真实请求，费用为 0 元。** 只有本页与路线图确认通过后，才能开始下一次 5 镜头真实前端成片测试。

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

下次 5 镜测试的用户确认上限：预算 12 元；DeepSeek 文本最多 3 次；DeepSeek Vision 最多 1 次；MiniMax H3 I2V 最多 5 次（每镜只提交一次）。

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
- 本切片已支持进程级 5 次 / 12 元覆盖，以及远程任务视频资产幂等
- **尚未**进行第三次 5 镜头真实前端成片测试；修复报告确认前禁止真实 API
