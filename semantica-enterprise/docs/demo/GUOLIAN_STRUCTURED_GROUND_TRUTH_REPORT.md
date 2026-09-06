# 国联集团演示库结构化 Ground Truth 实算报告

测试日期：2026-09-06
数据集：`guolian-enterprise-demo`
数据声明：演示数据，不代表国联集团真实经营数据。

## 验证范围

- 独立 PostgreSQL 16 演示库：16 张基础表、1 个无主键视图。
- 独立 MySQL 8.4 演示库：16 张基础表、1 个无主键视图。
- 两套数据库使用相同逻辑 Schema、相同稳定主键和相同确定性数据。
- 20 个结构化问题均配置了只读、命名参数绑定的 `database_check`。
- 标量、单行对象、排序行集三种结果模式均由真实数据库执行验证。
- 数据库连接凭据只通过运行时环境变量注入，未写入 SQL、JSON 或报告。

## 核心确定性口径

| 指标 | PostgreSQL | MySQL | 预期 |
|---|---:|---:|---:|
| 2026 年有效采购总额 | 1,700,000.00 | 1,700,000.00 | 1,700,000.00 |
| 2026 年采购目标 | 2,100,000.00 | 2,100,000.00 | 2,100,000.00 |
| NexusOne 相关有效订单金额 | 600,000.00 | 600,000.00 | 600,000.00 |
| 有效订单数 | 5 | 5 | 5 |
| 有效供应商去重数 | 4 | 4 | 4 |
| 仅排除取消订单的金额 | 1,790,000.00 | 1,790,000.00 | 1,790,000.00 |

“有效采购”统一定义为：日期在 2026-01-01（含）至 2027-01-01（不含）之间，订单状态为 `signed`、`executing` 或 `accepted`，且存在 `approved` 审批记录。取消订单及审批待补订单不计入该口径。

## 执行结果

| 测试层 | PostgreSQL | MySQL |
|---|---:|---:|
| 容器健康检查 | 通过 | 通过 |
| Schema 初始化 | 通过 | 通过 |
| Ground Truth Schema 校验 | 通过 | 通过 |
| 20 项数据库实算 | 20/20 | 20/20 |
| 失败 | 0 | 0 |

覆盖问题包括：总额、分单位汇总、供应商排名、NexusOne 金额、集团及单位目标完成率、先执行后审批异常、高风险事件、影响项目、按月金额、最近订单、无订单供应商、双产品项目、COUNT 与 COUNT DISTINCT、SUM 与记录数、多表 Join 去重、Top 5、NULL 与时间边界。

## 可复现命令

以下命令中的 Secret 必须由调用者环境注入，不应写入 Shell 历史、文档或 Git：

```bash
export GUOLIAN_DEMO_DATABASE_PASSWORD='<运行时注入>'
export GUOLIAN_DEMO_MYSQL_ROOT_PASSWORD='<运行时注入>'

docker compose -f compose.yaml -f compose.guolian-demo.yaml \
  up -d guolian-demo-postgres guolian-demo-mysql

# 分别把连接 URL 放入环境变量后执行；工具不会打印 URL 或参数。
export GUOLIAN_DEMO_DATABASE_URL='<PostgreSQL 或 MySQL 的运行时 URL>'
python scripts/demo/verify_guolian_ground_truth.py --database-checks --json
```

应用容器内连接时默认使用：

- PostgreSQL host：`guolian-demo-postgres`
- MySQL host：`guolian-demo-mysql`
- 默认数据库：`guolian_demo`
- 默认连接用户：`guolian_demo`

数据库名和用户名可分别通过 `GUOLIAN_DEMO_POSTGRES_DATABASE`、`GUOLIAN_DEMO_POSTGRES_USER`、`GUOLIAN_DEMO_MYSQL_DATABASE`、`GUOLIAN_DEMO_MYSQL_USER` 覆写。宿主机发布端口使用独立的 `GUOLIAN_DEMO_POSTGRES_PUBLISHED_PORT` 和 `GUOLIAN_DEMO_MYSQL_PUBLISHED_PORT`，不会改变 API 容器内连接的 5432/3306。API 与 Worker 的私网来源允许列表已包含两个演示 host。

## 安全检查

- SQL 初始化文件无密码和连接串。
- 每个数据库核验仅允许单条 `SELECT`/只读 `WITH`。
- 禁止 SQL 注释、多语句、DDL、DML、系统目录访问。
- 所有查询值使用命名参数绑定。
- 核验在只读事务中执行并在完成后回滚。
- 错误报告不会回显 SQL、参数或数据库 URL。
- `supplier_contacts` 只包含虚构敏感样式值，用于后续验证服务端脱敏。

## 文件校验值

| 文件 | SHA-256 |
|---|---|
| `demo/guolian/db/postgresql.sql` | `d3274ec21888399003230d069ee6f201d1ee11f229d65f6001b22c2a81a1c985` |
| `demo/guolian/db/mysql.sql` | `81249980412338781e5511acefd64fecb4040cf4aed863b75a85e1413a888c8a` |
| `demo/guolian/structured_query_ground_truth.json` | `6ad769bd2632e988f9dc6ae9439594df66c963d8ab92ef8c98dc15d2f41ae9ca` |
