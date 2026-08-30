# 真实前端闭环预检（P7-A 护栏）

本页记录第一次真实 DeepSeek + DeepSeek Vision + MiniMax 测试之前必须满足的护栏。当前**仍未发生真实调用**。

## 1. 确认项

在发送任何付费请求前，人工确认：

- 文本：`deepseek` / `deepseek-v4-flash`，`live_strict`，共 3 次（改编、Story Bible、分镜）
- 视觉：`deepseek` / `deepseek-v4-flash-vision-exp`，1 次，项目内 JPEG/PNG 首帧
- 视频：`minimax` / `MiniMax-H3`，I2V，4 秒，768P，1 次
- 源文本固定：`春秋蝉鸣少年归`
- 总预算上限：5 元人民币
- 启动开关：进程环境 `VISIONCRAFT_ALLOW_LIVE_LLM=1`（不要把 Key 写入文档或提交 `.env`）
- 视频开关：同上，或 `VISIONCRAFT_ALLOW_LIVE_VIDEO=1`
- 禁止自动重试、备用 Provider、安全重写补救、并发第二个视频任务

## 2. DeepSeek thinking 与 max_tokens

| 请求 | thinking | max_tokens | 说明 |
|---|---|---|---|
| 文本改编 / Bible / 分镜 | disabled | 4096 | 分镜 JSON 含多镜提示词，2048 可能不够 |
| 视觉检查 | disabled | 2048 | 结构化检查结果较短 |

禁止默认无限制输出。请求计划只记录 provider、model、thinking、max_tokens、prompt_chars、call_index，不记录 Key、Authorization、完整请求体或原文。

## 3. 5 元预算门槛

本地估算，**不发真实请求**：

- 输入 token ≈ 字符数，再乘 1.30 缓冲
- 输出按 max_tokens × 1.30
- DeepSeek 按官方峰值 cache-miss 美元价，再乘 7.5 换算人民币
- MiniMax H3 768P：官方 0.50 元/秒 × 4 = 2.00 元
- 若合计可能超过 5 元：HTTP 400，`BLOCKED_BEFORE_CALL`

可用 `VISIONCRAFT_LIVE_BUDGET_CNY` 覆盖上限（仅测试）。

## 4. 本地 JPEG/PNG 首帧

- 接口：`POST /api/projects/{project_id}/shots/{shot_id}/keyframes/register-local`
- 校验魔数与大小，只接受 JPEG/PNG
- 文件复制到当前项目资产目录并登记 `first-frame` / `first_frame`
- 禁止 SVG、目录穿越、项目外绝对路径、跨项目资产
- 禁止该流程调用任何图片生成 Provider
- 前端选择本地文件后刷新预览；Vision / I2V 仅在登记成功后可用
- 预览使用 `/assets/{projectId}/...`，不展示 Data URL

`gyfy.jpg` 可以在测试时复制进临时项目目录，测试结束必须清理，不要提交进 Git。

## 5. 当前状态

护栏已落地。第一次真实前端闭环仍需用户在预算预检通过后再次确认，才会发送真实请求。
