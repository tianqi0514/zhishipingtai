# 结构化数据语义查询使用说明

本功能把 MySQL/PostgreSQL 从“只能同步成 JSON 文档”升级为三种可独立启用的能力：知识索引、实时语义查询和图谱物化。详细架构见 [详细设计](STRUCTURED_SEMANTIC_QUERY_DESIGN.md)。

## 业务操作顺序

1. 在“数据接入”新增 MySQL 或 PostgreSQL，配置只读账号并测试连接。
2. 打开数据源详情，执行“结构发现”，核对表、视图、字段、主键和外键。
3. 在“数据预览”查看实时数据；需要核对上次同步内容时切换“最近同步快照”。
4. 创建或选择业务本体，在“语义映射”将表映射为业务对象、字段映射为属性、外键映射为关系。
5. 校验映射；只有管理员显式激活且 Schema 未漂移的版本才能用于查询。
6. 在“查询测试”输入自然语言问题，核对 Semantic Query Plan、Query IR、参数化 SQL 摘要和真实结果。
7. 在“知识服务—智能问答”选择对应空间，直接询问金额、数量、排名、分组或跨表问题。

## 平台如何执行

模型只负责生成使用语义 ID 的严格 Plan/IR；Pydantic 使用 `extra=forbid` 拒绝扩展字段、物理表名、字段名和 SQL。FastAPI 校验用户/租户/空间、当前 Schema 和活动映射，再由 `PostgreSQLCompiler` 或 `MySQLCompiler` 确定性生成 SQLAlchemy AST、参数化 SQL并通过只读连接执行。结果保存为 `StructuredQueryRun` 和 `StructuredQueryCitation`。

## 三种模式的差异

| 模式 | 数据时间 | 适合问题 | 是否依赖语义映射 |
|---|---|---|---|
| 实时预览 | 当前源库 | 查看当前行数据 | 否，但依赖结构发现和预览策略 |
| 实时语义查询 | 当前源库 | 聚合、排名、统计、跨表计算 | 是，且必须是当前活动版本 |
| 同步快照/知识索引 | 最近同步 | 名称、描述、备注和全文检索 | 否；映射可增强行级实体化 |
| 图谱物化 | 最近发布 | 稳定实体和关系 | 是；无稳定身份的行不物化 |

## 可核验输出

每个结构化引用包含数据源、数据库方言、查询时间、数据新鲜度、映射版本、Schema 版本、QueryRun ID、返回行数、截断状态和管理员可见的带占位符 SQL。参数摘要只显示类型、长度或哈希，数据库密码和敏感值不进入浏览器、Harness Session 或日志。

## 验收数据

先启动 `compose.structured-test.yaml`，再执行：

```bash
docker compose -f compose.yaml -f compose.structured-test.yaml up -d
docker compose -f compose.yaml -f compose.structured-test.yaml exec -T \
  -e STRUCTURED_FIXTURE_PASSWORD=structured_fixture_password \
  api python tests/e2e/seed_structured_acceptance.py
```

脚本幂等创建“结构化经营数据验收库”，包含 7 个业务实体、16 个属性和 4 条关系，不打印 Fixture 密码。
