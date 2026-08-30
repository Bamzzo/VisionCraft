# 跨模型媒体传递层

VisionCraft 的工作流只传递图片资产的 `asset_id` / `file_path` 与语义角色，例如 `first_frame`、`last_frame`。不允许工作流直接拼接阿里、火山或 MiniMax 的图片请求字段。

`backend/services/media_transfer_service.py` 负责：

1. 验证资产属于当前项目，且本地文件存在；
2. 校验格式（PNG/JPEG/WebP）与大小；
3. 计算 SHA-256、MIME 与字节数；
4. 按配置编译为 Data URL 或公网 HTTPS URL；
5. 写入 `media_transfers`，记录资产、下游 Provider/模型、帧角色、传输方式与脱敏请求摘要。

本地开发默认值是：

```dotenv
VISIONCRAFT_MEDIA_TRANSFER_MODE=data_url
```

生产部署可以切换为：

```dotenv
VISIONCRAFT_MEDIA_TRANSFER_MODE=public_url
VISIONCRAFT_MEDIA_PUBLIC_BASE_URL=https://your-public-media-domain.example
```

公网 URL 必须由对象存储或部署服务提供。不能把 `C:\...`、`backend/data/...` 等本地路径直接传给云端模型。

每个下游 Provider 都要在自己的 Adapter 中调用本服务，并将得到的 `MediaReference.url` 编译为各自的 API 格式。若缺少首帧、格式不支持或模型不支持对应模式，必须返回明确错误，不能静默退化为文生视频。

视觉理解（DeepSeek `deepseek-v4-flash-vision-exp`）同样只接收本层输出的 Data URL 或未来的 HTTPS URL / Files API `file_id`。本地测试优先 Data URL。视觉 Adapter 不得把本地绝对路径、项目外文件或其他项目资产发给远程接口；**SVG 不能用于 Vision 或 I2V**。Data URL 只留在待发送请求内存中，`media_transfers`、`job_events` 和 `vision_reviews` 只保存 `asset_id`、角色、MIME、尺寸和传输模式。详见 `docs/stage-model-selection.md`。
