# DeepSeek Harness 结构化工具

Harness 固定 commit：`cd5ef8148158c3a752a658978873241fdf8e2bbc`。结构化工具是 out-of-tree Cordis 插件的一部分，Harness 核心 Agent Loop 未修改。

| 工具 | 作用 | 关键安全边界 |
|---|---|---|
| `structured_schema_search` | 按业务词检索活动映射中的实体、属性和关系 | 仅返回当前短期 Token 授权空间 |
| `structured_get_object` | 读取业务对象、属性、关系和 Query 合约 | 不返回连接串/密码 |
| `structured_find_relation_path` | 查找已激活的关系路径 | 无路径即明确返回，模型不能编造 Join |
| `structured_inspect_values` | 有界探查单个普通字段的安全取值 | 敏感/脱敏字段禁止探查 |
| `structured_execute_query` | 提交严格 Plan/IR 并执行 | FastAPI 校验、编译、只读执行和审计 |

模型配置从平台模型中心按 Harness Session 读取；API Key 只进入 Runtime 子进程环境，不写插件、前端、Session Event 或日志。工具支持 AbortSignal、超时和错误标准化。取消会删除内存 Session 客户端并等待关闭完成；初始化失败会驱逐半初始化客户端，重试使用新 Bridge。初始化默认上限 90 秒，可通过 `DSH_INITIALIZE_TIMEOUT_MS` 调整。

面向用户只投影可核验事件：业务对象匹配、对象读取、取值探查、Plan/IR 校验、参数化编译、只读查询、结果行数、耗时、失败/取消和引用；不展示私有 Chain-of-Thought、系统提示词或内部凭据。
