# 妙笔技术架构

## 边界

```text
React + Plate 妙笔前端
  | REST / SSE / WebSocket
FastAPI 写作执行域
  |-- 项目、事实、文稿、版本、权限、审计
  |-- 计算、影响分析、导出编排
  |-- 知识产品 Release 和证据解析
  `-- DeepSeek Harness Gateway
        |-- knowledge_* 工具
        |-- structured_* 工具
        |-- writing_* 工具
        `-- reason / algorithm 工具

Semantica: 解析、规范化、图谱、Provenance、Datalog 推演
PostgreSQL: 业务元数据与不可变版本
MinIO: 材料、快照和导出文件
Redis/RabbitMQ: 缓存、任务与取消
OpenSearch/Qdrant/FalkorDB: 检索与图谱
Hocuspocus/Yjs: 实时协同更新流
```

FastAPI 是业务权限和数据边界。DSH 只调用有短期凭据的内部工具；不持有数据库密码，不访问搜索或图数据库。Plate 只管理编辑交互，不决定事实正确性。Semantica 只负责知识和规则推演，不生成正式文稿。大模型不生成权威数值。

## 版本一致性

一次正式文稿版本固定引用：场景包版本、知识产品 Release、规则版本、公式版本、算法版本、模型调用记录和来源版本。`WritingDocumentVersion` 不可变。Yjs 更新是协作传输记录，只有保存快照后才进入业务版本。

## P0 至 P4

| 阶段 | 可验收结果 |
|---|---|
| P0 | 场景包、事实分类、人工闸门、版本和接口契约 |
| P1 | 独立 React/Plate 编辑器、文稿 CRUD、自动保存、版本、导入导出基础 |
| P2 | 知识检索、证据绑定、可信业务块、AI 修订、stale 标识 |
| P3 | Semantica 推演、公式、结构化查询、算法服务、三方案比较 |
| P4 | Yjs/Hocuspocus 协同、正式导出、影响传播、性能安全和服务器部署 |

## 故障边界

- 模型失败不删除事实、计算或已保存文稿。
- 单一检索通道失败返回降级 Warning。
- Semantica 推演失败保留先前有效图谱和运行诊断。
- 算法服务失败不返回固定演示方案。
- 协同服务中断时保留浏览器短期更新；重连后合并，正式版本不受破坏。
- 导出失败可重试，且不产生“已发布”状态。

