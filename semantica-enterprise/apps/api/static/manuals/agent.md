# DeepSeek Harness 接入手册

## 适用范围

DeepSeek Harness 是传神智库内置的对话 Agent Runtime。浏览器和外部系统只调用 FastAPI；不应直接访问 8090 端口。

- 容器内地址：http://agent-runtime:8090
- 平台入口：/api/v1/conversations
- Harness 锁定版本：cd5ef8148158c3a752a658978873241fdf8e2bbc
- 插件：out-of-tree chuanshen-knowledge Cordis 插件

## 调用流程

- 用户通过 FastAPI 创建会话并发送消息。
- FastAPI 根据用户、租户和知识空间签发短期内部凭据。
- Harness 运行 Agent Loop，并通过 Knowledge Tool API 检索知识。
- FastAPI 以 SSE 返回检索、工具、引用和回答事件。
- Harness Session Event Log 保存 Agent 历史，平台保存业务会话投影。

## 知识工具

- knowledge_search
- knowledge_get_fragment
- knowledge_graph_query
- knowledge_get_document_profile
- knowledge_reason
- knowledge_list_spaces

工具只返回结构化 JSON，支持超时、取消、错误标准化和审计字段。Harness 不直接连接业务数据库、对象存储、搜索引擎或图数据库。

## 模型与安全

LLM、Base URL、参数和 API Key 全部来自传神智库“配置中心 / 模型服务”。API Key 不写入 Harness Session Event，也不通过浏览器下发。

内部服务凭据从 /run/secrets/agent_service_secret 读取。凭据必须至少 32 字节，并由 Docker Secret 管理。禁止把 8090 端口直接暴露到公网。

## 验证

```bash
docker compose ps agent-runtime
docker compose logs --tail=100 agent-runtime
docker compose exec -T agent-runtime npm test
```

若问答失败，依次检查默认 LLM 配置连接测试、agent-runtime 健康状态、内部短期凭据、Knowledge Tool 调用事件和 FastAPI 会话日志。
