# VisionCraft V1 演示项目与全链路验收

本页说明固定演示项目的准备方式，以及 V1 演示收口切片的验收命令。不调用付费图片、视频、语音、音乐或 LLM API。

## 项目设置落库

已创建项目可通过工作台「编辑项目设置」或接口修改：

```http
PATCH /api/projects/{project_id}
Content-Type: application/json

{
  "title": "新名称",
  "duration_seconds": 8,
  "aspect_ratio": "16:9",
  "output_resolution": "1920x1080"
}
```

规则：

- 只接受上述四个字段；未提供的字段保持不变；
- 空标题、非法时长（非 5～10 秒）、非法比例、非法分辨率返回中文 400；
- 项目不存在返回中文 404：「项目不存在。」；
- 不修改原文、风格、镜头策略，也不写入 `shot_versions`；
- 修改时长、比例或分辨率且已有成片时，将 `assembly_stale` 设为 1；
- 保存后任务中心出现 `project.refresh_required`，前端无需手动刷新。

可用分辨率：`1280x720`、`1920x1080`、`720x1280`、`1080x1080`、`720x720`。新建项目默认 `1280x720`，与既有 P6-C/E 成片规格一致。

## 固定演示项目

脚本只操作 ID 前缀为 `v1demo_` 的项目。默认不会删除用户的 `project_*` 数据。

```powershell
.venv\Scripts\python.exe tools\prepare_v1_demo.py
```

行为：

- 创建或重置唯一项目 `v1demo_main`（标题：VisionCraft V1 固定演示）；
- 使用本地短文本样本走 mock 改编：方案 → Story Bible → 分镜 → 镜头制作；
- 写入 3～4 个本地镜头夹具、关键帧、背景音和字幕；
- 本机有 FFmpeg 时真实合成一版成片；没有 FFmpeg 时跳过成片并打印安装说明，不得记为通过；
- 若仓库外存在 `gyfy.jpg` 则用作首个镜头首帧，否则生成本地 PNG，不访问外网。

重复执行只会重置 `v1demo_main`，不会堆出无控制的重复项目。

清理（只删本脚本项目）：

```powershell
.venv\Scripts\python.exe tools\prepare_v1_demo.py --clean
```

打开工作台后选择「VisionCraft V1 固定演示」，可查看 P4 改编审核、P3 版本历史、P6-E 成片配置和导出。导出页只展示当前有效成片或过期提示，合成动作仍在成片合成阶段完成。

## 全链路浏览器验收

```powershell
.venv\Scripts\python.exe tools\test_v1_demo_browser.py
```

脚本会在 8013～8018 启动当前代码的临时后端，不停止用户已有的 8000 进程。测试结束会停止该临时进程，并删除 `v1e2e_*` 以及标题以 `V1E2E` 开头的临时 `project_*` 项目。

截图写入 `output/playwright/v1-*.png`，不要提交。无 FFmpeg 时成片预览/下载/过期重合成步骤必须输出 `SKIP`。

## 接口与数据库测试

```powershell
.venv\Scripts\python.exe tools\test_project_settings.py
```
