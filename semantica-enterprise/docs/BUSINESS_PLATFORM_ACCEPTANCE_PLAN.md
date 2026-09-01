# 国联集团组织级知识底座全业务验收计划

## 1. 验收目标

本轮验收以“国联集团明日正式启用”为假设，不以页面可打开或接口返回 200 作为通过标准。每个业务结论都必须同时具备业务对象、权威版本、来源证据、权限边界和可重复测试。

验收基线：

- 平台提交：`c88594bf0e064d3314086bb0c8f707233f0e4729`
- 工作分支：`codex/full-business-platform-validation`
- Semantica：`cce5ea177cbac29a526effa546219c48f8ec36f4`
- DeepSeek Harness：`cd5ef8148158c3a752a658978873241fdf8e2bbc`
- Ontology2SQL：`ece05d1cc988d9bce602a7a9e1b73cd5767a860a`
- 测试日期：2026-09-01

## 2. 未修改代码的基线

| 检查项 | 基线结果 | 说明 |
|---|---:|---|
| Python 测试收集 | 198 | 186 通过、12 因真实外部依赖未配置而跳过、0 失败 |
| DSH 插件合约 | 15/15 | 必须在 Agent Runtime 的 `pnpm test` 环境运行 |
| Docker 平台服务 | 12/12 healthy | 另有无持久卷的 MySQL/PostgreSQL fixture 2/2 healthy |
| 当前业务数据 | 4 空间、21 文档、15 数据源 | 是历史技术验收数据，不作为本轮集团标准答案 |
| 当前组织数据 | 2 个组织、1 个用户 | 不足以验证集团层级和角色隔离 |

宿主机直接执行 DSH Node 测试会因 `/opt/deepseek-harness` 和 TypeScript loader 约定误报缺少模块，正式验证命令固定为：

```bash
docker compose exec -T agent-runtime sh -lc \
  'cd /opt/deepseek-harness/packages/integration/chuanshen-knowledge && pnpm test'
```

## 3. 权威边界

1. FastAPI/PostgreSQL 是身份、租户、空间、权限、当前版本和应用授权的权威入口。
2. Semantica 负责解析、切分、抽取、归一化、Provenance、SearchRanker、Analyze、Datalog 和 SPARQL；平台只做版本、权限和投影适配。
3. DeepSeek Harness 负责 Agent Loop、工具编排、Session Event、流式回答、取消与恢复；只能调用平台内部工具 API。
4. OpenSearch、Qdrant 和 FalkorDB 是可重建投影；查询结果返回前必须回到 PostgreSQL 校验当前版本和权限。
5. MySQL/PostgreSQL 实时查询只接受本体 ID 表达的严格 Plan/IR，由平台确定性编译为参数化只读 SQL。

## 4. 验收顺序

| 阶段 | 业务任务 | 关键完成证据 |
|---|---|---|
| 0 | 基线与保护 | Git 干净、服务健康、迁移记录、当前数据盘点 |
| 1 | 集团初始化 | 组织树、8 类角色、测试用户、空间授权矩阵 |
| 2 | 模型与策略 | 真实连接测试、Secret 不回显、未配置能力真实降级 |
| 3 | 知识接入 | 多格式文件、14 类数据源、同步任务与文档可互相追踪 |
| 4 | 自动加工 | Element、Chunk、画像、实体、事实和三路索引都有证据 |
| 5 | 人工治理 | 修改、影响预览、发布、问答生效、回滚恢复 |
| 6 | 结构化语义 | Schema、预览、映射、Plan、IR、SQL、引用、漂移 |
| 7 | 知识服务 | 检索排序、四组多轮 Agent 问答、证据不足拒答 |
| 8 | 图谱与 Analyze | 图谱 CRUD、规则推导、发布、回滚和 SPARQL |
| 9 | 开放与应用 | REST/MCP/CLI、两个应用、评测、凭据、反馈闭环 |
| 10 | 恢复与交付 | 不删卷重启、持久化复验、文档、提交和推送 |

## 5. 通过规则

- HTTP 200 只证明传输成功，不证明业务正确。
- 数值结果必须与 `BUSINESS_STANDARD_ANSWERS.md` 的精确值比较。
- 文档结论必须校验版本、页码/结构位置和 Chunk。
- 图谱和推理结论必须校验来源事实与 Provenance。
- 页面 Toast 必须在 PostgreSQL、Worker、索引或 Session Event 中找到对应变化。
- 无外部账号的连接器只能标记“协议级自动化验证”，不能标记“真实企业账号通过”。
- P0/P1 必须修复；当前范围内 P2 优先修复；不能修复的范围外事项必须写明业务影响与启用条件。

## 6. 回归策略

1. 第一轮：集团初始化、知识进入、治理、结构化、检索与问答，发现并修复主链路断点。
2. 第二轮：REST/MCP/CLI、两个应用、上线测试、反馈治理、浏览器全菜单点击。
3. 第三轮：保留 Volume 停止并恢复服务，复验权限、版本、索引、图谱、会话、映射、应用和反馈。

每次修复从最窄单元测试开始，逐级扩大到 API、容器、浏览器；最终必须再次执行关键全链路。
