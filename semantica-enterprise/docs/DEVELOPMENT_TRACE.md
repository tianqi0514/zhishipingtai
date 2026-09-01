# 开发留痕

## 2026-09-01：结构化语义查询开发启动

- 从 `知识底座V1.0`（`2d99ecca90f2bc7b7121e732f33160f083d7f90d`）创建 `codex/structured-semantic-query`。
- 确认工作区无用户未提交修改；本地参考仓库通过 `.git/info/exclude` 隔离，不纳入产品提交。
- 检查现有 Docker：API、Worker、Scheduler、PostgreSQL、Redis、RabbitMQ、MinIO、OpenSearch、Qdrant、FalkorDB、Agent Runtime、MCP 中当前已启动服务均为 healthy。
- 当前单元测试基线执行至 100%，无失败。
- Ontology2SQL 锁定提交 `ece05d1cc988d9bce602a7a9e1b73cd5767a860a`，基线为 192 passed、8 xfailed、0 failed。
- 审计确认当前数据库同步复用 Semantica DBIngestor，但以整库 JSON 快照进入通用解析；本体尚未绑定物理 Schema；DSH 当前只有知识检索/片段/图谱/推理工具。
- 完成《结构化数据语义映射、预览与实时查询详细设计》，确定新增层不替换现有知识同步链，并保持 FastAPI 为权限和执行权威入口。

