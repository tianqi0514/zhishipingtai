# Kimi 视觉集成

## 配置

Vision 使用统一模型配置，模型类型为 `vision`。默认 Kimi Vision 配置通过 `credential_model_config_id` 引用已有 Kimi LLM 凭据，数据库不复制密钥；API Key 从 Docker Secret 或加密字段读取，前端、Session Event、日志和测试报告都不回显。

策略必须同时满足：`vision.enabled=true`、`execution=cloud`、允许云处理，并符合上传确认模式。连接测试提交真实最小图片，返回状态、测试时间和服务端耗时。

## 严格输出协议

每个帧请求只接受这些字段：场景摘要、可见对象、人员与角色、动作、环境、可见文字摘要、图表/表格摘要、产品/业务对象、可能关系、不确定项、警告和证据帧 ID。Pydantic 使用 `extra=forbid`；非法 JSON 或字段类型错误最多按配置重试三次，仍失败则产生明确阶段错误。

图像和 OCR 文本都被声明为不可信证据。提示词要求模型不执行画面内指令、不访问链接、不猜测画面外信息、不输出系统提示或凭据。回答仅消费经过校验并关联真实帧 ID 的结果。

## 调用与审计

实际调用按去重后的关键帧发生，受 `max_frames`、`batch_size`、`concurrency`、超时和最大 Token 控制。每帧保存模型名、调用耗时、Token 使用量（供应商返回时）、调用时间、云处理原因和提示词版本；不保存原始响应中的非协议字段。

本地 Vision 保留同一 OpenAI 兼容接口。只有模型配置明确 `local_runtime` 且策略选择 `execution=local` 才可调用。本机 16GB 环境没有把 1B–2B 本地视觉模型强行常驻到完整平台基线，避免挤压 OpenSearch 与 Worker；这项能力标记为兼容接口已实现、目标服务器资源确认后再进行真实模型验收。
