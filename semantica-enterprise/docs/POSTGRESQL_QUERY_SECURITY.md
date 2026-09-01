# PostgreSQL 实时查询安全

- 使用独立只读角色；Fixture 中 `structured_reader` 无 DML/DDL 权限。
- 每次执行进入只读事务，并设置本地 `statement_timeout`。
- 表/Schema/字段来自 Schema Catalog 和活动 Manifest；不允许访问 `information_schema`、`pg_catalog` 或未授权 Schema。
- 值使用绑定参数，QueryRun 只保存类型、长度、哈希等参数摘要。
- 确定性编译器限制函数、聚合、窗口、Join 数、子查询深度、LIMIT 和返回字节。
- 取消时调用驱动连接取消能力；失败后连接回收，不复用不确定事务。
- 服务端完成 Secret 字段阻断和个人信息脱敏。

平台业务 PostgreSQL 与接入的源 PostgreSQL 权限相互独立；Harness 不持有任何数据库连接信息。
