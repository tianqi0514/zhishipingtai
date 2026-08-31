# 人工治理 P0–P3 设计与实现

## 目标与边界

本模块在 Semantica 自动治理之后提供人工修正能力，不替代、复制或覆盖 Semantica 的解析、切片、抽取、归一化、冲突检测、溯源和发布算法。人工操作以追加式决定保存；系统在读取、加工或发布时合成“当前有效值”。因此可以同时回答“模型当时给出了什么结果”“人工为什么修正”“当前业务实际使用什么值”，并能回滚。

模块位于“知识资产 / 治理工作台”，不是新的顶层导航，也不是审核流程。用户拥有知识空间写权限即可治理；决定可直接生效或提交后台发布。

## 阶段范围

| 阶段 | 业务能力 | Semantica 复用 | 平台新增 | 生效方式 |
|---|---|---|---|---|
| P0 | 治理决定、批次、待办、历史、回滚、来源指纹 | ProvenanceManager | CurationBatch、Decision、Overlay、Case | 画像即时投影；其他对象进入任务 |
| P1 | 摘要、分类、文档类型、标签、关键词、主要对象、时间范围人工修正 | 自动画像与确定性质量评分 | 自动值/生效值并列、当前/后续版本范围 | API 读取时合成，不改 DocumentProfile |
| P2 | 实体字段修正、事实修正、屏蔽、实体合并/拆分、事实冲突待办 | EntityNormalizer、DuplicateDetector、冲突检测、图谱校验 | must-link/cannot-link、有效实体/事实 | 发布新的图谱与组合发布 |
| P3 | 内容元素修正/屏蔽、Chunk 文案修正/屏蔽/调权、检索投影 | Splitter、语义抽取、SearchIndexer、RRF、向量复用 | 有效元素/Chunk、curation_boost | 元素重新加工；Chunk 重建搜索快照 |

## 数据模型

| 表 | 用途 | 是否修改自动结果 |
|---|---|---|
| `curation_batches` | 一次业务操作及其发布状态 | 否 |
| `curation_decisions` | 追加式决定，含前后值、原因、范围、指纹、操作者和替代链 | 否 |
| `curation_overlays` | 每个对象字段当前生效决定的物化指针 | 否 |
| `curation_cases` | 自动质量问题和事实冲突形成的治理待办 | 否 |
| `knowledge_releases` | 将 GraphRelease 与 IndexRelease 配对为业务发布 | 否 |

`scope` 支持：

- `version_only`：仅当前版本；适合内容和检索修正。
- `document_future`：当前文档及后续版本；适合稳定的文档分类和标签。
- `space`：知识空间长期约束；适合实体合并、拆分和图谱字段。

对象在创建决定时生成来源指纹。客户端提交了旧指纹时，服务返回冲突，要求刷新后重新治理。API Key、内部令牌和模型秘密不进入决定、事件或溯源记录。

## 有效投影

| 对象 | 自动源 | 可人工治理字段 | 下游影响 |
|---|---|---|---|
| 文档画像 | `DocumentProfile` | summary、classification、document_type、tags、keywords、main_objects、time_range | 文档详情与画像服务 |
| 内容元素 | `ContentElement` | text、status | Semantica 切片、抽取、治理、索引、图谱 |
| Chunk | `Chunk` | text、status、boost | OpenSearch、Qdrant、排序、引用片段、Agent 工具 |
| 实体 | `CanonicalEntity` | 名称、类型、别名、属性、置信度、状态 | 图谱页面、FalkorDB、Agent 图谱工具 |
| 事实 | `Fact` | 主体、关系、客体、值、置信度、状态、有效期 | 图谱、推理证据、Agent 图谱工具 |
| 实体组合 | 两个规范实体 | must_link、cannot_link | Semantica 聚类和后续规范实体映射 |

自动行始终保留。`effective_*` 解析器批量读取当前 Overlay，并返回带 `field_origins`、决定 ID 和有效 Hash 的投影。检索片段、前端详情和 Harness Knowledge Tool 都读取有效投影，避免 UI 与 Agent 使用不同知识。

## 发布与一致性

1. 内容元素修正或屏蔽提交 `process_knowledge` 强制任务。
2. Worker 使用有效 ContentElement 调用 Semantica Splitter、抽取、归一化和冲突处理。
3. Chunk 级修正直接提交 `curation_publish`，无需重复语义抽取。
4. 发布前先失效证据已被屏蔽的 `InferredFact`。
5. Semantica Analyze/Datalog 与只读 SPARQL 使用有效实体、有效事实和有效名称，不直接读取被人工屏蔽或尚未合成的自动值。
6. 使用有效实体和事实发布不可变 `GraphRelease`。
7. 使用有效 Chunk 发布不可变 OpenSearch/Qdrant `IndexRelease`。文本未变化时复用旧向量；文本变化时以有效 Hash 形成新 Point ID。
8. 两个发布成功后创建 `KnowledgeRelease` 组合指针，并将治理批次标记为 published。
9. 任一步失败时批次和任务显示 `publish_failed`，旧发布继续可用；强制重加工会恢复旧 Chunk/Fact 当前投影并将新生成的未发布行标记为 `superseded`。

`curation_boost` 写入 OpenSearch 和 Qdrant Payload。全文检索使用 `field_value_factor`，向量检索在候选扩大后应用同一权重并重新排序。某个检索通道不可用时，原有降级逻辑继续生效。

## 实体合并与拆分

合并不直接改写自动实体和事实：

1. 创建空间级 must-link 约束并记录保留实体。
2. 为被合并实体添加状态屏蔽决定。
3. 为关联事实添加主体或客体有效值决定。
4. 重发布图谱和搜索组合版本。
5. 后续文档重加工时，约束传给 Semantica 聚类，并把被合并名称映射到保留实体。

拆分会用 cannot-link 替代原 must-link，并恢复两个实体及自动事实端点。所有决定形成替代链，可在治理历史中继续追踪。

## API

| 方法 | 地址 | 用途 |
|---|---|---|
| POST | `/api/v1/curation/batches` | 新建治理批次 |
| GET | `/api/v1/curation/workbench` | 业务工作台指标、筛选待办、文档列表和当前发布 |
| GET | `/api/v1/curation/batches`、`/curation/batches/{id}` | 归并后的业务批次列表与详情 |
| GET | `/api/v1/curation/targets/search` | 搜索当前用户可读的文档、内容、片段、实体和关系 |
| POST | `/api/v1/curation/profiles/{version_id}` | 原子提交画像多字段修正和处理原因 |
| GET | `/api/v1/curation/summary` | 待办、决定和发布摘要 |
| GET/PUT | `/api/v1/curation/cases`、`/curation/cases/{id}` | 待办列表与完成/忽略/重开 |
| GET/POST | `/api/v1/curation/decisions` | 决定历史与新增决定 |
| POST | `/api/v1/curation/decisions/{id}/rollback` | 回滚当前生效决定 |
| POST | `/api/v1/curation/batches/{id}/rollback` | 一次回滚批次内所有当前决定，并只发布一次 |
| POST | `/api/v1/curation/entities/pair` | 实体合并、拆分、must-link、cannot-link |
| GET | `/api/v1/knowledge/releases` | 图谱、索引和组合发布历史 |

所有接口先校验租户和知识空间权限。浏览器只访问 FastAPI；Worker 和 Harness 均不获得绕过权限的数据库入口。

## 页面验证顺序

1. 进入“知识资产 / 治理工作台”，默认在“待处理”按高优先级处理自动质量问题和冲突。
2. 点击左侧问题卡片，在右侧核对系统生成值、当前生效值、来源、依据和影响；接受、修正、忽略或重新打开。
3. 没有待办时点击“查找知识并治理”，搜索并打开文档画像、原始内容、检索片段、实体或关系。
4. 在文档画像点击“人工修正”，用标签组件和日期控件修改业务值，选择当前版本或以后版本并填写原因。
5. 打开原始内容，修正文案或屏蔽元素；在发布记录或任务中心观察重新加工的真实百分比。
6. 打开检索片段，修正文案、调优先级或屏蔽；等待治理发布任务完成。
7. 进入知识图谱，编辑节点/关系，或使用“实体合并 / 拆分”。
8. 回到“人工调整”，确认多字段操作按一个业务批次归并；进入“发布记录”查看组合发布。
9. 从批次回滚预览确认影响对象和模块后执行回滚；在检索、图谱和智能问答中验证恢复后的有效投影。

完整业务操作说明见 [治理工作台业务与操作说明](GOVERNANCE_WORKBENCH_GUIDE.md)。

## 业务化工作台实现（2026-08-31）

- 页面结构改为“待处理 / 人工调整 / 发布记录”，默认待处理，不再并排堆放技术表格。
- 待办采用问题列表与详情双栏布局，支持状态、优先级、类型、文档和关键词筛选。
- 指标卡可直接切换到对应业务视图；无知识、无待办和无调整分别提供可执行空状态。
- 前端不显示 UUID、内部英文代码和原始 JSON；后端统一返回对象、字段、操作、范围和状态的业务名称。
- 文档画像多字段修改由 `/curation/profiles/{version_id}` 原子提交。标签、关键词和主要对象使用 Token 组件，时间范围使用日期控件。
- 非标准时间范围（例如仅年份）会提示系统识别值；未操作日期控件时保持原值，避免无意清空。
- 人工调整以 `CurationBatch` 为单位归并，显示操作者、对象、字段、原因、范围、影响和字段级前后值。
- 发布记录关联真实 Celery Job、KnowledgeRelease、GraphRelease 和 IndexRelease；失败发布可重试。
- 回滚默认按业务批次执行，弹窗先展示目标和影响模块；自动产物与旧发布均不删除。

## 迁移与升级

数据库升级文件为 `packages/platform/sql_migrations/0015_human_curation.sql`。启动 API 时先由 SQLAlchemy 创建新增表，再以迁移登记方式创建索引；重复启动不会重复执行。升级不删除 Volume，也不改变已有 DocumentProfile、ContentElement、Chunk、CanonicalEntity 或 Fact 数据。

## 2026-08-31 开发与验收留痕

| 轮次 | 执行内容 | 结果 |
|---|---|---|
| 第一轮 | 102 项单元测试、19 项 Semantica 合约、API CRUD、P3 Overlay/组合发布、图谱 CRUD、M10 三路检索 | 全部通过 |
| 第二轮 | 浏览器真实打开治理画像、内容元素、Chunk 调权、实体合并拆分；完成一次画像“保存→生效→历史回滚” | 自动值恢复，活动决定为 0；控制台错误/警告为 0 |
| 第三轮 | 不删除 Volume 执行 `docker compose stop/start`；检查 12 个服务；重跑 P3 组合发布与图谱 CRUD | 12/12 healthy；前向/回滚发布和图谱 8 次发布通过 |
| 开放服务 | REST、MCP、CLI、Harness 插件事件和安全边界 | REST 5 条、MCP 7 工具、Harness 302 事件、CLI 三项及 6 项安全边界通过 |

专项命令：

```bash
docker compose run --rm --no-deps -v "$PWD:/workspace" -w /workspace api pytest -q
python3 tests/e2e/curation_p3_smoke.py
bash tests/e2e/graph_crud_smoke.sh
bash tests/e2e/m10_platform_smoke.sh
docker compose exec -T agent-runtime npm test -- --runInBand
```

强制重新加工的兼容性回归还验证了：已有 Citation 引用的 Chunk 不再被删除；旧 Chunk 标记为 `superseded`，相同稳定 ID 的 Chunk 原行复用，因此历史引用不断链。对尚未发布向量索引的新空间，人工图谱治理允许完成图谱单独发布，并在任务结果中返回 `space_has_no_search_index` Warning；首个文档完成后再建立组合发布。
