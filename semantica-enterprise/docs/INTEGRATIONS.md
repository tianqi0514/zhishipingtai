# REST、MCP 与 CLI

## REST/OpenAPI

登录后使用 Bearer Token。完整 Schema 见 <http://localhost:8080/docs>。

```bash
curl -sS http://127.0.0.1:8080/api/v1/search \
  -H "Authorization: Bearer $CHUANSHEN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"NexusOne 的定位","space_ids":["SPACE_ID"],"top_k":5}'

curl -N http://127.0.0.1:8080/api/v1/conversations/CONVERSATION_ID/messages \
  -H "Authorization: Bearer $CHUANSHEN_TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"content":"它支持哪些数据源？"}'
```

核心接口包括 `/search`、`/fragments/{chunk_id}`、文档/版本画像、图谱查询、数据源同步和完整 `/conversations` 会话管理。所有空间 ID 均在服务端重新做权限计算。

知识分析接口包括 `/analysis/rule-sets`、`/analysis/scenarios`、`/analysis/inference-runs`、`/analysis/inference-runs/{id}/rollback` 和 `/analysis/sparql`。

## MCP

MCP Server 使用 Streamable HTTP：`http://127.0.0.1:8091/mcp`。客户端请求头携带平台 Bearer Token。工具为：

- `knowledge_search`
- `knowledge_chat`
- `knowledge_get_fragment`
- `knowledge_graph_query`
- `knowledge_reason`
- `knowledge_sparql`
- `knowledge_get_document_profile`

MCP 层只调用 FastAPI，不持有数据库/中间件连接。超时由 `MCP_REQUEST_TIMEOUT` 控制，平台 401/403/404/5xx 会转换为结构化 MCP 错误。已通过真实 MCP ClientSession 初始化、工具枚举、Semantica 推理与 SPARQL 调用。

## CLI

镜像中安装 `chuanshen`：

```bash
export CHUANSHEN_API_URL=http://127.0.0.1:8080/api/v1
export CHUANSHEN_TOKEN='platform-access-token'
chuanshen search 'NexusOne 的定位' --space SPACE_ID --top-k 5
chuanshen chat '它支持哪些数据源？' --space SPACE_ID
chuanshen fragment CHUNK_ID
chuanshen reason RULE_SET_ID --space SPACE_ID
chuanshen sparql 'SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20' --space SPACE_ID
chuanshen sync-source SOURCE_ID
chuanshen job JOB_ID
```

Token 不应写入 shell 历史或仓库文件。CLI 对 HTTP 超时、权限错误和非 JSON 错误均返回非零退出码。search、chat、fragment、reason、sparql 已完成真实调用；同步和任务命令由 API CRUD/E2E 覆盖。
