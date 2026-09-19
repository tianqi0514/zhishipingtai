# 架构与职责

```text
DeepSeek Work / Plate
        │ 受限插件工具、REST、SSE
        ▼
传神智库 FastAPI（权限、状态机、事务、版本、审计）
  ├─ PostgreSQL：权威对象和不可变版本
  ├─ MinIO：原文件、快照、导出文件
  ├─ OpenSearch / Qdrant：全文和向量投影
  ├─ FalkorDB：写作关系与依赖投影
  ├─ 确定性公式服务：ComputationRun
  └─ Semantica：InferenceRun、InferredFact、证明链
```

三层边界：

1. 工具层描述系统能做什么，并进行参数、权限和超时校验。
2. Skill 层约束 Agent 何时调用工具、如何处理缺值和引用。
3. 编排层在后端执行状态机、重试、闸门、绑定、传播和提交；不是提示词。

DeepSeek Work 插件只调用 FastAPI，不连接数据库和中间件，不接收数据库密码。Plate 保存块结构和编辑事件，不能取代事实、规则、计算或文稿版本的权威存储。
