# 知识分析业务接口

所有接口位于 `/api/v1`，继承平台登录、租户和知识空间权限。现有规则集、规则、场景、运行和 SPARQL 接口保持兼容。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/analysis/readiness?space_id=...` | 当前空间数据准备状态 |
| GET | `/analysis/vocabulary?space_id=...` | 实体类型、关系词表、数量与有限样例 |
| GET | `/analysis/templates?space_id=...` | 只读业务模板及当前空间匹配状态 |
| POST | `/analysis/rules/match-preview` | Semantica 规则校验和真实数据试运行 |
| POST | `/analysis/guided-setups` | 原子创建规则集、规则、场景和首次运行 |
| GET | `/analysis/tasks` | 面向业务页面的分析任务列表 |
| GET | `/analysis/tasks/{id}` | 分析任务详情与最近运行 |
| POST | `/analysis/tasks/{id}/run` | 执行已有分析任务 |
| GET | `/analysis/runs/{id}/diagnostics` | 零结果及证据诊断 |
| GET | `/analysis/runs/{id}/impact` | 发布或撤回影响 |
| GET | `/analysis/runs/{id}/comparison` | 与同任务上次成功运行比较 |
| POST | `/analysis/runs/{id}/publish` | 从预览创建新的发布运行 |
| POST | `/analysis/runs/{id}/rollback` | 兼容业务术语的撤回入口 |
| POST | `/analysis/visual-query` | 普通条件生成并执行受控 SPARQL |

规则试运行与正式运行都调用 `packages.semantica_adapter.analyze.run_graph_inference`。试运行只返回安全截断后的样例，不持久化推导事实。
