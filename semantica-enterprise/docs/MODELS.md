# 模型配置说明

模型配置页面支持 LLM、Embedding、Reranker、Vision、ASR 的增删改查、启停、设置默认和真实连接测试。通用字段为名称、Provider、Model Name、Base URL、API Key、超时、重试、并发、温度、Max Tokens 和自定义参数。

| 类型 | 用途 | 未配置时行为 |
|---|---|---|
| LLM | Harness 对话、语义抽取、治理摘要/分类 | 对话/模型治理不可用；确定性质量分析保留 |
| Embedding | Qdrant 向量构建与查询 | 向量通道告警降级；全文/图谱继续 |
| Reranker | RRF 后第二阶段重排 | 使用 RRF 排序并显示“未配置”告警 |
| Vision | 图片描述、视频关键帧理解 | 保留 OCR/元数据，不伪造描述 |
| ASR | 音频/视频带时间戳转写 | 保留媒体元数据并标记未配置 |

API Key 保存时用 `APP_SECRET_KEY` 派生的 Fernet 密钥加密；列表和详情只返回“已配置/未配置”，更新时留空表示保持不变。连接测试调用最小真实请求：LLM/视觉走 chat completion，远程 Embedding 走 embeddings，Reranker 走 rerank，ASR 上传最小音频 multipart。重试覆盖 408/409/429/5xx，并尊重超时和重试次数。

系统默认创建的“BGE 中文向量”是通过 Semantica `TextEmbedder` 调用 FastEmbed 在 CPU 本地运行的 `BAAI/bge-small-zh-v1.5`，不需要 Base URL 或 API Key。第一次测试或向量检索会下载 ONNX 模型；Docker 将其缓存在 `application-data` Volume 中，容器重建后继续复用。测试会实际生成一条 512 维向量，加载或推理失败时状态必须为失败，禁止使用哈希向量伪装成功。

Kimi K3 可以作为默认 LLM，但密钥只从现有 Docker Secret 或加密数据库读取。Harness 插件、前端、Session JSONL、审计与测试报告均不得出现密钥。

生产建议：每个租户至少配置一个默认 LLM 与 Embedding；Reranker、Vision、ASR 按业务需要配置。保存前先执行“测试”，再设默认。更换 `APP_SECRET_KEY` 前必须实施密钥重加密迁移。
