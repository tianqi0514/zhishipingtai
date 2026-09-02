# 媒体时间线、检索与引用

## 时间线元素

ASR、OCR、Vision 不生成互不关联的附件结果，而是统一为 ContentElement。每个元素至少保留文档/版本、处理运行、策略版本/Hash、媒体校验和、模型配置引用、结构路径和来源；时间类元素额外保留 `time_start/time_end`，帧保留 `frame_ids/scene_index`，场景/章节保留父级和证据。

## 检索

媒体元素和普通文档一起进入 Semantica 分块、OpenSearch、Qdrant 与 FalkorDB。混合检索结果向后兼容地增加：

- `media_type`
- `start_seconds/end_seconds`
- `time_label`
- `scene_index/frame_ids`
- `fragment_url/media_url`

RRF 和可选重排仍决定最终顺序。某通道不可用时保留其他通道并返回 Warning。

## 引用交互

召回卡片显示 `mm:ss–mm:ss` 或 `hh:mm:ss`。点击回答引用后，前端定位右侧真实召回项并打开片段；媒体详情使用权限化 Range API 加载音视频，设置 `currentTime` 跳转并高亮对应转写、场景或关键帧。浏览器不会获得 MinIO 永久 URL。

DSH 只看到 FastAPI 返回的结构化知识工具结果，不读取 MinIO、PostgreSQL或搜索引擎。最终引用在 FastAPI 侧校验片段存在且用户有知识空间权限；模型不能凭文本自行构造可访问的媒体地址。
