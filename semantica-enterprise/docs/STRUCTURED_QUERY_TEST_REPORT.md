# 结构化语义查询测试报告

## 1. 交付信息

| 项目 | 值 |
|---|---|
| 测试日期 | 2026-09-01（Asia/Shanghai） |
| 分支 | `codex/structured-semantic-query` |
| 基线 Tag | `知识底座V1.0` |
| 基线提交 | `2d99ecca90f2bc7b7121e732f33160f083d7f90d` |
| Semantica | `cce5ea177cbac29a526effa546219c48f8ec36f4` |
| DeepSeek Harness | `cd5ef8148158c3a752a658978873241fdf8e2bbc` |
| Ontology2SQL | `ece05d1cc988d9bce602a7a9e1b73cd5767a860a` |
| API 镜像 | `semantica-enterprise:0.10.0` / `sha256:12a1554443ff...` |
| Agent 镜像 | `chuanshen-agent-runtime:0.1.0-cd5ef81` / `sha256:167a128d8def...` |

报告中的测试凭据只属于 `compose.structured-test.yaml` 创建的隔离、无持久卷 fixture；生产 Secret 没有写入报告、代码或日志。

## 2. 自动化结果

| 测试层 | 命令/范围 | 结果 |
|---|---|---|
| 平台全量 | `pytest -q` | 198 collected；186 passed；12 skipped；0 failed |
| 平台单元 | `pytest -q tests/unit` | 167 passed；0 failed |
| MySQL/PostgreSQL 真实集成 | `tests/integration/test_structured_databases.py`，显式允许两个 fixture 主机 | 10 passed；0 failed |
| 结构化 API E2E | `tests/e2e/test_structured_api_live.py` | 1 passed；0 failed |
| DeepSeek Harness 插件 | `pnpm test`，在最终 Agent 镜像构建中执行 | 15 passed；0 failed |
| Semantica 源码合约 | DBIngestor、Datalog、Normalizer、Provenance、图谱/查询注入防护、结构化输出 | 216 passed；0 failed |
| Ontology2SQL 上游基线 | `backend/../.venv/bin/pytest -q` | 192 passed；8 xfailed；0 failed |

平台全量中的 12 项跳过项需要 live 服务或隔离数据库环境；对应的结构化数据库和结构化 API 用例已在独立真实环境中执行并全部通过，未用 Mock 替代数据库交互。

## 3. 覆盖矩阵

| 能力 | PostgreSQL | MySQL | 结果 |
|---|---:|---:|---|
| Schema、表、视图、字段、主外键发现 | ✓ | ✓ | 通过 |
| Schema Fingerprint 与版本差异 | ✓ | ✓ | 通过 |
| 被映射字段漂移后标记 stale 并阻止查询 | ✓ | ✓ | 通过 |
| 历史 Fingerprint 恢复为新版本 | ✓ | ✓ | 通过；迁移 `0018` 移除错误唯一约束 |
| 实时预览、分页、稳定排序、类型化筛选 | ✓ | ✓ | 通过 |
| 无主键表提示与确定性排序降级 | ✓ | ✓ | 通过 |
| 服务端敏感字段屏蔽/脱敏 | ✓ | ✓ | 通过 |
| 本体映射 CRUD、验证、激活、版本与回滚 | ✓ | ✓ | 通过 |
| 严格 Plan/IR 与 `extra=forbid` | ✓ | ✓ | 通过 |
| 方言化确定性参数化 SQL | ✓ | ✓ | 通过 |
| 数据库账号与事务双层只读 | ✓ | ✓ | 通过 |
| 查询超时、取消、行数/字节限制 | ✓ | ✓ | 通过 |
| 行级 ContentElement 与稳定 ID | ✓ | ✓ | 通过 |
| 映射驱动图谱物化 | ✓ | ✓ | 单元与合约通过 |

## 4. 数值验收

fixture 覆盖客户、产品、订单、订单明细、目标、供应商与风险事件。自动化矩阵验证总额、分组、Top N、同比、目标完成率、无订单客户、最近订单、窗口累计、`COUNT DISTINCT`、空值和时间边界。

最终浏览器与 DSH 实测：

| 问题 | 结果 | 证据 |
|---|---:|---|
| 2026 年已完成订单销售总额 | 910,000.00 | UI 自然语言查询真实执行，1 行，数据库耗时 161 ms |
| 华东地区 | 300,000.00 | DSH QueryRun `fe1fbdee-8403-4cea-aed3-58ae0b69212b` |
| 华北、华东、华南降序 | 360,000 / 300,000 / 250,000 | DSH QueryRun `9b2e474d-9b7a-40a0-bfea-c3294395f431` |
| 服务重启后再次核验三区域合计 | 910,000.00 | DSH QueryRun `721e5362-4eea-492a-8231-8c41d140e165` |

最后一轮确实经历：历史会话恢复 → 指代问题解析 → `structured_execute_query` → Plan/IR 校验 → PostgreSQL 参数化只读查询 → 结构化引用投影；不是把历史答案直接复制为新答案。

## 5. 安全结果

- 未授权租户/空间访问数据源与映射分别返回 404/403。
- `information_schema`、被禁止表、被禁止字段、敏感字段排序和受控值探查均被拒绝。
- SQL 注入内容作为绑定参数处理，fixture 表仍存在且内容未改变。
- Plan/IR 不允许物理表名、物理字段名、原始 SQL、扩展字段或任意函数。
- 数据库密码不进入浏览器、DSH、QueryRun 和引用；Harness 仅持有短期平台服务凭据。
- Docker 私网地址默认被 SSRF 策略拒绝；真实集成测试仅显式允许 `structured-postgres` 与 `structured-mysql` 两个 fixture 主机。
- 最终重启后 10 分钟内 API、Worker、Scheduler、Agent Runtime、MCP 日志未发现 `ERROR`、`Traceback`、`Unhandled` 或 `FATAL`。

## 6. 三轮回归

1. 开发回归：单元、严格 Schema、安全、迁移、API E2E；修复登录 UI 断言陈旧、Schema Fingerprint 历史唯一约束。
2. 最终镜像回归：重新构建 API/Agent 镜像；构建内 DSH 15 项测试通过；真实 MySQL/PostgreSQL 10 项通过；结构化 API E2E 通过。
3. 持久化回归：未删除 Volume，完整 `docker compose stop/start`；15 个 Schema 版本、9 个映射、15 个 QueryRun、11 个会话均保留，随后新增的重启后 QueryRun 成功。

API E2E 创建的临时知识空间、数据源、本体和映射均在 `finally` 中通过业务 API 删除；数据库中的软删除/历史审计行按平台可追溯策略保留，不会出现在活动数据源列表。固定的 `结构化经营数据验收库` 则按用户授权保留，便于后续继续测试。

## 7. 已知边界

- `compose.structured-test.yaml` 是验收环境，不应在生产部署中启用。
- 表行数来自数据库统计估算，未执行 `ANALYZE` 的小表可能显示 0；实际当前页行数始终来自真实查询。
- 自动映射建议只生成草稿；推断外键不会自动激活。
- 大表图谱物化必须由管理员选择对象和上限，不默认全量发布。
- 浏览器终验在实际 1280×720 桌面视口执行；1440/1920 的布局由已有响应式 CSS 合约覆盖，本轮未伪造浏览器截图。
