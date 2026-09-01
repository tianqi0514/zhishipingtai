# REST / OpenAPI 接入手册

## 连接信息

- API 根地址：http://服务器地址:8080/api/v1
- OpenAPI 页面：http://服务器地址:8080/docs
- 鉴权方式：Authorization: Bearer ACCESS_TOKEN
- 数据格式：application/json；对话流使用 text/event-stream

## 获取访问令牌

```bash
curl -sS http://服务器地址:8080/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"你的账号","password":"你的密码"}'
```

从响应的 access_token 字段取得短期令牌。不要把令牌写入代码仓库或日志。

## 发起知识检索

```bash
curl -sS http://服务器地址:8080/api/v1/search \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"产品的主要定位是什么？","space_ids":["SPACE_ID"],"top_k":5}'
```

响应包含最终排名、全文/向量/图谱分数、融合分、重排分、引用片段和可核验检索轨迹。space_ids 仍会在服务端按当前用户权限重新校验。

## 发起流式对话

先创建会话，再向会话发送消息：

```bash
curl -sS http://服务器地址:8080/api/v1/conversations \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"外部系统会话","space_ids":["SPACE_ID"]}'

curl -N http://服务器地址:8080/api/v1/conversations/CONVERSATION_ID/messages \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"content":"它支持哪些数据源？"}'
```

## 常用接口

- POST /search：融合检索
- GET /fragments/{chunk_id}：读取引用片段
- POST /conversations：创建会话
- POST /conversations/{id}/messages：流式问答
- POST /knowledge/graph/query：图谱查询
- GET /versions/{id}/profile：文档治理画像
- POST /analysis/sparql：只读知识查询
- POST /structured-query/natural-language：基于激活本体映射的安全结构化查询

结构化查询请求只传 `mapping_version_id`、自然语言 `question`、`execute` 和 `max_rows`。调用者不能提交任意 SQL；平台会验证 Plan/IR 并使用参数绑定执行只读查询。

遇到 401 需要重新登录，403 表示当前用户无权访问目标空间，429 或 5xx 应按指数退避重试。
