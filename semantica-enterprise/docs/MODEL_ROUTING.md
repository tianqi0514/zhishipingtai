# 模型路由策略

配置中心的“模型路由”用于把业务场景与模型配置解耦。管理员可以新增、查看、编辑、启停和删除路由策略，并指定唯一生效策略；业务模块无需硬编码模型名称、Base URL 或凭据。

## 场景

| 业务场景 | 模型类型 | 当前验收环境 |
|---|---|---|
| 智能问答 | LLM | Qwen3.8-27B-NVFP4 |
| 图谱语义抽取 | LLM | Qwen3.8-27B-NVFP4 |
| 文档治理画像 | LLM | Qwen3.8-27B-NVFP4 |
| 结构化查询规划 | LLM | Qwen3.8-27B-NVFP4 |
| 视觉理解 | Vision | Kimi K3 视觉理解 |
| 向量化 | Embedding | BGE 中文向量 |
| 检索重排 | Reranker | 未配置，使用 RRF |
| 语音识别 | ASR | 本地 SenseVoiceSmall |

## 解析优先级

1. 抽取策略、治理策略或媒体策略显式指定的模型。
2. 当前租户生效的模型路由策略。
3. 对应模型类型的默认模型。
4. 没有可用模型时返回明确的未配置或降级状态。

显式引用的模型失效时不会静默改用其他模型，避免在不知情的情况下改变模型供应商或把本地数据发送到云端。模型路由引用已停用、已删除、其他租户或类型不匹配的模型时，后端拒绝保存。

## API

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/model-routing-policies` | 策略列表 |
| GET | `/api/v1/model-routing-policies/{id}` | 策略详情 |
| POST | `/api/v1/model-routing-policies` | 新建策略 |
| PUT | `/api/v1/model-routing-policies/{id}` | 编辑、启停或设为生效策略 |
| DELETE | `/api/v1/model-routing-policies/{id}` | 删除策略 |
| GET | `/api/v1/model-routing-policies/resolved` | 查看每个场景最终解析到的模型及来源 |

模型 API Key 不保存到路由策略，也不返回浏览器。路由只保存 `ModelConfig` 标识；真正调用时仍由 FastAPI/Worker 从加密配置读取凭据。DeepSeek Harness 仅通过 FastAPI 获取本轮问答所需的短期模型调用配置，不直接读取平台数据库。
