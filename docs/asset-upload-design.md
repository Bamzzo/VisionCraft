# 项目素材上传（P8-B）

用户可以在网页中向**当前项目**上传 JPEG/PNG、音频和 SRT，不再依赖脚本或项目外路径。本阶段不调用真实图片、视频、文本、视觉、语音或音乐 API。

## 接口

```text
POST /api/projects/{project_id}/assets/upload
```

multipart 表单：

| 字段 | 说明 |
|---|---|
| `file` | 文件流。服务端读取字节，不接受客户端绝对路径。 |
| `asset_role` | 必填角色。 |
| `shot_id` | 图片可选。必须属于当前项目。 |
| `subtitle_text` | 无文件时，由后端生成项目内 SRT。 |

保留既有 `POST /shots/{shot_id}/keyframes/register-local`，内部转发为 `asset_role=first_frame`，不另建第二套存储路径。

成功返回（不返回绝对路径、Data URL、API Key 或内部目录）：

```json
{
  "ok": true,
  "asset": {
    "id": "asset_xxx",
    "project_id": "project_xxx",
    "type": "image",
    "role": "first_frame",
    "file_path": "/assets/project_xxx/asset_xxx.jpg",
    "mime_type": "image/jpeg",
    "byte_size": 123456,
    "width": 1024,
    "height": 768
  }
}
```

`type` 实际值为资产表类型：`first-frame` / `last-frame` / `keyframe` / `reference` / `audio` / `subtitle`。`file_path` 始终是项目内公开路径 `/assets/{project_id}/asset_{id}.{ext}`。

## 角色

| asset_role | assets.type | 挂接 |
|---|---|---|
| `keyframe` | `keyframe` | 只登记，不自动挂镜头 |
| `first_frame` | `first-frame` | 可挂当前镜头，新建版本，旧版本保留 |
| `last_frame` | `last-frame` | 同上 |
| `reference_image` | `reference` | 写入当前镜头草稿 `reference_frame_path` |
| `audio` / `background_audio` | `audio` | 仅成片背景音 |
| `subtitle` | `subtitle` | 仅成片字幕 |

## 文件类型与限制

| 类别 | 允许 | 大小 | 其它 |
|---|---|---|---|
| 图片 | JPEG、PNG | ≤ 20 MB | 必须可读出宽高。Vision/I2V 传递层仍为 12 MB。 |
| 音频 | WAV、MP3、M4A、AAC、OGG | ≤ 50 MB | 时长 ≤ 600 秒，必须能被 FFprobe 读出音频流。 |
| 字幕 | SRT；或 UTF-8 文本生成 SRT | 文件 ≤ 2 MB；生成文本 ≤ 20,000 字 | 校验时间轴与顺序。 |

不支持：SVG、视频、压缩包、可执行文件、外部 URL、项目外路径、对象存储、预签名 URL、分片上传。

## 真实内容校验

不能只信扩展名或浏览器 `Content-Type`。

- 图片：JPEG `\xff\xd8\xff`、PNG `89 50 4E 47`；拒绝 SVG 与伪装文件。
- 音频：容器魔数后落盘，再用 FFprobe 确认音频流与时长；不可读则删除文件并删除 `assets` 行。
- SRT：UTF-8 / UTF-8 BOM；必须有 `HH:MM:SS,mmm --> HH:MM:SS,mmm`；开始早于结束；条目时间不得倒序。

文件名只作显示名，经安全化后最多 80 字符。磁盘名永远是 `asset_{id}.{ext}`，同名上传不会覆盖旧文件。

## 项目归属

- `project_id` 必须存在。
- `shot_id` 如提供，必须属于当前项目，否则中文 400「该素材不属于当前项目。」
- 成片音频/字幕只接受当前项目 `type=audio` / `type=subtitle`。
- 跨项目路径、`..`、客户端 `C:\...` 不会被读取或登记。
- 新上传用于新版本或新成片配置；旧镜头版本和旧成片继续保留。

## 失败清理

- 校验失败：不落盘、不写库。
- 音频 probe 失败：删除不完整文件并删除 `assets` 行。
- 写库失败：删除已写文件。
- 挂镜头失败：回滚本次上传的文件和资产行。

## 前端

镜头工作区：上传首帧 / 尾帧 / 参考图；显示文件名、预览、大小、类型、角色；上传中/成功/失败。预览使用后端项目内 URL，不保存 Data URL。无有效 JPEG/PNG 首帧时禁用 Vision 与 I2V。

成片工作区：上传背景音频与 SRT，或从文本生成 SRT；下拉只列出当前项目资产；保存后若已有成片则显示「需要重新合成」。切换项目清空本地上传状态。刷新后从后端恢复资产和成片配置。

`FormData` 上传，不手动设置 `Content-Type`。

## 与媒体传递层

上传只登记当前项目资产。Vision 与 MiniMax I2V 仍只通过 `media_transfer_service` 读取当前项目 JPEG/PNG。Data URL 只存在于发出视觉请求前的内存。`assets`、`job_events`、普通日志不得出现 Data URL、Base64、完整本地路径或签名 URL。

本阶段没有真实 API 调用，费用 0 元。
