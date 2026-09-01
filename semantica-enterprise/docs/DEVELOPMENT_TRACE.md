# 结构化数据语义层开发留痕

## 背景与边界

本分支从 `知识底座V1.0` 增量开发，没有推倒现有数据源、文档加工、Semantica、知识图谱或 DeepSeek Harness 链路。浏览器和 Harness 均只访问 FastAPI；Harness 不持有数据库连接信息，不直接访问 PostgreSQL/MySQL/OpenSearch/Qdrant/FalkorDB。

Ontology2SQL 锁定在 `ece05d1cc988d9bce602a7a9e1b73cd5767a860a`，用于严格 Plan/IR 与确定性编译的设计参考。生产实现按本平台已有模型、权限和 MySQL/PostgreSQL 方言重写，没有复制 BIRD Gold SQL、Oracle Evidence 或 SQLite 编译器。

## 实施阶段

| 阶段 | 主要产物 | 验证 |
|---|---|---|
| 设计 | `STRUCTURED_SEMANTIC_QUERY_DESIGN.md` | 首个独立设计提交 `65f685a` |
| Schema Registry | Schema 版本、对象、字段、Fingerprint、Diff、漂移状态 | 单元、真实 MySQL/PostgreSQL、API E2E |
| 数据预览 | 实时/快照、分页、排序、类型筛选、服务端脱敏、行详情 | 真实浏览器点击和数据库集成 |
| 本体映射 | 映射集/版本/实体/属性/关系、建议、验证、激活、回滚 | API E2E 与 UI 工作台 |
| 查询安全层 | 严格 Plan、严格 IR、MySQL/PostgreSQL 编译器、只读执行器 | 数值矩阵与注入测试 |
| DSH 集成 | 5 个结构化工具、结构化事件、引用、取消与恢复 | 15 项 DSH 合约和真实多轮会话 |
| 快照/图谱 | 行级 ContentElement、稳定 Hash、映射驱动物化 | 单元与 Semantica 合约 |
| 迁移 | `0017_structured_semantic_query.sql`、`0018_schema_fingerprint_history.sql` | 已有数据库升级、重复启动、冷构建 |
| 交付 | Compose fixture、验收种子、README、专题文档、测试报告 | 三轮回归 |

## 关键缺陷与修复

### 1. Schema Fingerprint 恢复冲突

初版把 `(source_id, schema_fingerprint)` 设为唯一。真实漂移测试发现，当 V1 → V2 → 恢复为与 V1 相同结构时，V3 无法写入，破坏 Schema 历史语义。

修复：

- 删除 ORM 唯一约束。
- 增加 PostgreSQL 专用迁移 `0018` 删除旧约束。
- 迁移执行器支持 `-- dialect:<name>` 语句前缀。
- 增加“相同 Fingerprint 可在更晚版本再次出现”的单元和 API E2E。

### 2. DSH 取消后 SDK 初始化竞态

真实浏览器点击停止后立即重试，曾出现旧 SDK client 关闭尚未完成而新 client 初始化超时。

修复：

- 用 `closingSessions` 串行化同一 Session 的关闭和重建。
- 取消统一进入 `disposeSession`。
- `turn/start` 前失败会驱逐半初始化 client。
- 初始化默认超时调整为 90 秒，同时保留环境变量覆盖。
- DSH 源码合约增加取消清理和初始化失败驱逐断言。

实际重试和后续多轮查询均成功。

### 3. 对话列表摘要为空

最近一条失败/取消的 assistant 消息可能内容为空，导致会话列表显示“暂无消息”。

修复：列表投影选择最近一条非空消息；新增投影单元测试。

### 4. 运行中计时不同步

右侧执行过程计时正常，但消息卡片一度停留在 0.0 秒。

修复：同一个 `chatTurnTiming` 每秒同时更新消息卡片和右侧执行过程；浏览器实测 2.5 秒 → 25 秒 → 完成 40 秒。

### 5. 登录 UI 合约断言陈旧

前端已支持字符串、JSON 字符串和结构化 `detail.message`，旧测试仍匹配已移除的一行三元表达式。

修复：保留更健壮的生产逻辑，更新测试检查新的 detail 归一化路径，不降低业务断言。

## 复用 Semantica

- DBIngestor 与数据库接入边界。
- 统一 ContentElement、结构化解析、Normalizer、Provenance。
- 现有治理、冲突、GraphRelease 与 FalkorDB 发布。
- 现有 Chunk、索引发布和 SearchRanker，不复制算法。

本分支新增的是组织权限下的 Schema Registry、映射控制面、实时只读查询安全层和 DSH 工具协议。

## 最终状态

- 全部必需容器健康。
- 无删卷重启后业务数据、映射、QueryRun、会话和 DSH Session 恢复。
- 最终页面控制台 0 错误，核心服务日志无未处理异常。
- 自动化和浏览器结果见 `STRUCTURED_QUERY_TEST_REPORT.md` 与 `BROWSER_TEST_REPORT.md`。
