# 结构化数据语义映射、预览与实时查询详细设计

状态：开发基线设计  
分支：`codex/structured-semantic-query`  
知识底座基线：`知识底座V1.0` / `2d99ecca90f2bc7b7121e732f33160f083d7f90d`  
Ontology2SQL 参考版本：`ece05d1cc988d9bce602a7a9e1b73cd5767a860a`

## 1. 业务目标

本能力让业务人员可以在不编写 SQL、不接触数据库凭据的情况下，完成以下工作：

1. 查看 MySQL、PostgreSQL 当前可用的表、字段、主外键和有限样本。
2. 在平台内安全预览实时数据，并与最近一次知识同步快照明确区分。
3. 把数据库表和字段映射到传神智库已有本体，使本体成为业务语义层。
4. 由模型表达业务问题和计算意图，由确定性程序验证并编译 SQL。
5. 在智能问答中组合文档依据、图谱事实和实时数据库结果。
6. 继续复用 Semantica 的采集、解析、归一化、图谱和溯源能力。

标准操作路径为：

`配置连接 → 测试连接 → 发现结构 → 预览当前数据 → 建立语义映射 → 验证并激活 → 查询测试 → 选择知识索引或图谱物化 → 智能问答`

数据库数据源具有三个互不替代的工作模式：

| 模式 | 数据时效 | 适用问题 | 主要产物 |
|---|---|---|---|
| 知识索引 | 最近同步 | 名称、描述、备注、制度关联等文本问题 | 文档版本、行级元素、Chunk、搜索索引 |
| 实时语义查询 | 实时 | 金额、数量、排名、聚合、同比、跨表计算 | QueryRun、结构化结果、数据引用 |
| 图谱物化 | 最近发布 | 稳定实体、关系浏览、规则分析 | 规范实体、事实、GraphRelease、Provenance |

任一模式失败不得覆盖其他模式已经发布的有效版本。

## 2. 审计结论与复用边界

### 2.1 现有实现

- `packages/semantica_adapter/ingest.py` 已通过 Semantica `DBIngestor` 采集 PostgreSQL；MySQL 因 Semantica 0.6.6 标识符引用问题使用窄适配读取行，同时继续使用 Semantica Schema Exporter。
- 当前数据库同步形成一个包含 `schema` 和 `tables` 的 JSON 文件，整份内容使用 SHA-256 去重。
- 通用 JSONParser 将该对象处理为单个 record；尚未形成表级、行级稳定元素。
- `Ontology` 与 `OntologyTerm` 是业务词表，尚无到表、字段、主键、外键的严格绑定。
- DeepSeek Harness 已作为独立 Docker 服务运行，通过 Cordis 插件调用 FastAPI 内部 Knowledge Tool API；Harness 不直接访问业务数据库和搜索引擎。
- FastAPI 已负责租户、空间权限、短期 Agent Token、模型配置和审计。

### 2.2 Semantica 复用点

- 数据库知识快照：`DBIngestor` 与 Schema Exporter。
- 数据库存档解析：JSON/结构化数据解析器，并在平台适配层转换为统一 ContentElement。
- 行级文本进入既有 Splitter、Semantic Extract、Normalizer、治理、SearchRanker、向量存储与 Provenance。
- 图谱物化生成平台事实后，继续使用既有归一化、冲突治理、GraphRelease 和 Provenance 发布链。
- 不复制 Semantica 的解析、实体归一化、RRF、图谱发布和溯源算法。

### 2.3 Ontology2SQL 参考边界

采用的设计思想：

- 语义身份和物理存储严格分离。
- 映射 Manifest 可审计、可封存并带稳定哈希。
- Query Plan 与 Query IR 使用 `extra=forbid` 的严格类型。
- 模型只能引用语义 ID，不能提供物理表名、字段名或 SQL 片段。
- SQL 由已激活映射和确定性编译器生成。

必须重写的部分：

- Ontology2SQL 的现有编译执行目标主要是 SQLite/BIRD，不视为 MySQL 或 PostgreSQL 支持。
- 生产实现使用参数绑定、方言标识符引用、只读事务、权限和脱敏。
- 映射复用传神智库的 Ontology/OntologyTerm、租户、知识空间和审计模型。
- Agent 集成使用 DeepSeek Harness Cordis 工具，不引入评测 Agent Loop、Gold SQL 或 Oracle Evidence。

首版只借鉴契约和校验结构；若后续直接复制源码片段，必须在文件头注明来源，并保留 Apache-2.0 LICENSE/NOTICE。

## 3. 系统边界

```text
浏览器
  └─ REST/SSE ─> FastAPI（身份、空间权限、映射、编译、执行、审计）
                    ├─ Schema/Preview ReadOnlyExecutor ─> MySQL/PostgreSQL
                    ├─ Semantica adapter ─> 文档/图谱/索引发布链
                    └─ 内部 Agent API <─ 短期凭据 ─ DeepSeek Harness Cordis 插件
```

硬性边界：

1. 浏览器和 Harness 均不能取得连接串、密码或解密后的 Secret。
2. Harness 只提交严格 Plan/IR，不能提交 SQL。
3. FastAPI 只从已发现 Schema 和已激活映射解析物理标识符。
4. 预览、实时查询、Agent 查询共用安全连接基础设施，但使用不同权限策略。
5. 所有数据库错误先标准化、脱敏，再写审计或返回前端。
6. 实时预览正常不能放宽过期映射的查询限制。

## 4. 数据模型

### 4.1 DataSourceSchemaVersion

每次结构发现产生不可变版本：

- `tenant_id`、`space_id`、`source_id`
- `version_number`
- `schema_fingerprint`
- `catalog`：数据库、Schema、表、视图、字段、键、索引、注释、有限统计
- `diff_from_previous`
- `status`：`current`、`superseded`、`failed`
- 数量指标与 `discovered_at`

Schema 指纹只基于规范化结构元数据，不包含样本值、密码和易漂移的行数估算。

### 4.2 DataPreviewPolicy

每个数据库数据源至多一个当前策略：

- 实时预览开关
- 允许/禁止的对象与字段
- 敏感字段和脱敏规则
- 默认排序、默认/最大分页
- 文本截断、完整单元格、精确 COUNT 权限
- 查询超时、最大筛选数、最大返回字节

策略的服务端默认值优先保护数据；前端不是安全边界。

### 4.3 SemanticMappingSet / SemanticMappingVersion

`SemanticMappingSet` 是用户看到的映射对象；`SemanticMappingVersion` 是不可变 Manifest：

- 绑定 `source_id`、`space_id`、`ontology_id`
- 绑定 Schema Version/Fingerprint
- 保存实体、属性、关系、主键、Join 谓词和依据
- `mapping_hash` 使用规范 JSON 计算
- 状态：`draft`、`validating`、`active`、`stale`、`retired`、`failed`
- 保存验证报告、创建人、激活人和时间

编辑已经激活的映射时创建新草稿版本，不原地修改历史版本。激活新版本后旧版本转为 retired；回滚通过复制目标历史版本生成新的活动版本，保留完整时间线。

### 4.4 StructuredQueryRun

记录一次真实执行：

- 用户、租户、空间、数据源、会话和消息关联
- 原始问题、Plan、Plan Fingerprint、IR、IR Fingerprint
- Mapping/Schema 版本和哈希
- 方言、占位符 SQL、脱敏参数摘要
- 引用的语义对象和物理对象
- 状态、行数、截断、字节、耗时、数据时间
- 标准化错误、取消请求和时间戳

不保存数据库密码和敏感明文参数。

## 5. Schema Registry

### 5.1 发现

使用 SQLAlchemy Inspector，并保留 Semantica Schema Exporter 摘要作为合约对照。发现内容包括表/视图、字段、类型、可空、默认值、PK、Unique、FK、索引和注释。行数使用方言安全估算；只有显式授权时才执行精确 COUNT。

样本仅用于映射建议，遵守预览策略，单字段去重并限制数量；禁止把高风险字段样本写入 Schema Catalog。

### 5.2 Diff 与漂移

规范化对象键为 `schema.object`，字段键为 `schema.object.column`。Diff 分类：

- 对象/字段新增、删除
- 字段类型兼容或不兼容变化
- 主键、外键、唯一键和注释变化
- 字段改名候选（仅提示，不自动重绑定）

若激活映射引用的字段删除、类型不兼容、主键或 Join 谓词改变，则该映射变为 `stale`，实时语义查询返回 409；仅新增无关字段时保持可用并返回 Warning。

## 6. 数据预览与安全策略

### 6.1 请求模型

预览请求只接受：

- Schema Catalog 中的对象 ID
- 页码和 page size
- 受控排序字段与方向
- 最多 10 个类型化筛选条件
- `live` 或 `snapshot` 模式

不接受 SQL、表达式、函数、JOIN、子查询或任意 WHERE 字符串。

### 6.2 只读执行

实时预览由服务器构建等价于以下形态的 SQLAlchemy AST：

```sql
SELECT <允许字段>
FROM <已发现且允许的对象>
WHERE <类型化并参数绑定的条件>
ORDER BY <受控稳定字段>
LIMIT <page_size + 1>
OFFSET <受控偏移>
```

- PostgreSQL 设置 `READ ONLY` 和局部 statement timeout。
- MySQL 设置只读事务和执行超时（能力不支持时使用客户端超时与连接回收降级）。
- 默认每页 20，最大 100；多取一行判断 `has_next`。
- 优先主键稳定排序；无主键时使用非大对象字段组合并明确警告。
- 返回值在服务端完成二进制摘要、文本截断、JSON 规范化和敏感字段脱敏。

### 6.3 敏感字段

字段名规则产生默认保护建议：

- `password/passwd/secret/token/api_key/access_key/private_key/credential`：禁止预览、筛选、排序、探查和 Agent 使用。
- `id_card/bank_card/mobile/phone/email`：默认服务端脱敏。

管理员只能在策略范围内进一步收紧；若允许调整建议，也不得开放 Secret/密码/私钥类别。日志、QueryRun 和错误消息统一经过脱敏函数。

### 6.4 同步快照

快照模式读取当前 DocumentVersion 的数据库专用行级 ContentElement，不访问源数据库。没有已同步版本时明确返回 `snapshot_unavailable`。实时和快照响应均返回来源模式、查询时间、同步时间和版本。

## 7. 语义映射 Manifest

### 7.1 核心对象

- Source/Object：只引用已发现 Schema 的对象 ID。
- Entity：必须绑定现有 OntologyTerm（`term_type=class`）。
- Entity Fragment：绑定表/视图并声明 grain、身份字段和角色。
- Attribute：绑定现有 property/metric 类型 OntologyTerm 和物理字段。
- Relationship：绑定 relation 类型 OntologyTerm、起止实体和等值 Join 谓词。

### 7.2 确定性校验

激活前必须验证：

1. Schema 指纹仍为当前版本。
2. 所有物理对象和字段存在且被授权。
3. 每个实体恰有一个 primary fragment。
4. 身份字段存在；图谱物化实体必须具有 PK/Unique/管理员指定复合键。
5. 属性绑定属于目标实体 Fragment。
6. Join 两侧字段存在、类型兼容、方向和实体端点一致。
7. 所有 OntologyTerm 存在、启用且属于同一租户/空间可用本体。
8. Manifest 无重复 ID，哈希有效。

模型建议只能创建草稿，不能自动激活。

## 8. Semantic Query Plan

Plan 是业务计算合同，使用语义 ID，不含物理信息。必含：

- 原始问题、意图、返回字段、实体、关系路径
- 筛选、分组、聚合、排序、LIMIT
- 统计主体、结果粒度、时间范围、空值与去重策略
- 分子、分母、计算步骤、假设和证据约束

同比、环比、差值、比例、百分比、排名、多阶段聚合和 Top N 后聚合必须提供拓扑有序的 calculation steps。Plan 校验拒绝未映射对象、关系路径缺失、输出顺序不连续、计算输入未声明和统计粒度不完整。

## 9. Query IR

IR 使用 Pydantic `extra=forbid`，支持实体绑定、属性、字面量、布尔表达式、between/in/null、聚合、受控函数、case/cast、子查询、exists、window、投影、Join、group/having/order/distinct/limit/offset。

约束：

- 只能引用当前活动 Manifest 的 entity/attribute/relationship ID。
- 不能出现物理表名、物理字段名、原始 SQL和任意函数名。
- 每个 IR 对照 Plan 进行范围校验，不能引入 Plan 未声明对象。
- 字面量只进入参数集合，不内联到 SQL。
- 保存规范化 fingerprint 以供审计和重放验证。

## 10. 确定性编译器

建立共享 Compiler Core 和 `PostgreSQLCompiler`、`MySQLCompiler`：

1. 从活动映射解析 Entity Fragment。
2. 从 Relationship Mapping 解析受控 Join。
3. 从 Attribute Binding 解析字段。
4. 通过 SQLAlchemy Core 构建表达式树。
5. 使用目标方言编译占位符 SQL并保留参数字典。

白名单覆盖常用聚合、日期和窗口函数；禁止 DDL、DML、MERGE、多语句、系统库、存储过程、文件函数、任意函数、未授权对象、过量 Join 与深层子查询。服务端强制最大 LIMIT。

编译输出包含 dialect、sql_template、parameters、referenced objects/columns、mapping/schema version 和 query fingerprint。普通用户引用详情只显示业务摘要；有管理权限时才显示物理详情。

## 11. 安全只读执行器

连接参数继续来自 `SourceConnector.config` 和加密 `secret_encrypted`，不重复存储。执行器负责：

- 网络目标校验与允许名单
- 连接/语句超时
- 只读事务
- 最大行、字节、Join、子查询深度
- 取消信号和并发信号量
- 结果服务端脱敏
- 方言错误标准化
- StructuredQueryRun 审计

取消是协作式的：查询运行状态持久化 cancel request；活动连接优先调用方言取消/关闭，Worker/请求协程观察取消标志。连接丢失时数据库超时仍构成最终保护。

## 12. DeepSeek Harness 集成

在现有 out-of-tree Cordis 插件中增加：

- `structured_schema_search`
- `structured_get_object`
- `structured_find_relation_path`
- `structured_inspect_values`
- `structured_execute_query`

每个工具使用 `defineTool()` 严格声明输入输出，`ctx.tools.register()` 注册，执行时传递 `exec.signal`，只调用带短期空间范围 Token 的 FastAPI 内部接口。工具返回规范 JSON，不能要求模型解析文本 ID。

Harness 仍负责多步 Agent Loop、工具编排、Session Event、流式回答、取消和恢复。FastAPI 负责 Plan/IR/映射验证、SQL 编译、执行、脱敏、审计和业务会话投影。

数字、金额、排名、实时状态和聚合问题必须经过成功的 `structured_execute_query` 才能回答；指标口径问题可先 `knowledge_search` 再执行结构化查询。工具事件投影为可核验执行轨迹，不展示私有 Chain-of-Thought。

## 13. 行级快照与图谱物化

数据库专用解析器识别 Semantica DBIngestor 结果：

- 每张表生成 `table` 元素。
- 每行生成 `record` 元素。
- `structural_path` 为 `tables/<object>/rows/<稳定行键>`。
- 元数据保存 source/schema/table/PK/row key/columns/sync time；不保存凭据。
- Element ID 的稳定身份范围继续使用 Document ID；行键进入结构路径，内容 Hash 进入 Chunk 增量复用。

无主键时使用规范字段组合 Hash 作为当前快照行键，并标记 `unstable_identity`；允许检索但默认禁止图谱实体物化。

图谱物化根据活动映射确定性生成实体和事实，然后进入既有 Semantica Normalizer、治理、发布和 Provenance。图谱发布失败时保留上一版 GraphRelease，且不影响实时查询。

## 14. API 与页面

后端按需求提供 Schema、预览策略、实时/快照预览、行知识状态、映射 CRUD/版本/验证/激活/回滚、Plan/IR 验证、编译、执行、取消和 QueryRun 查询接口。

数据库数据源详情页具有：数据概览、数据预览、结构发现、语义映射、同步记录、查询测试六个页签。非数据库来源保持现有详情，不出现数据库专属页签。

预览页面三段式布局：左侧表/视图，中间服务端分页表格，右侧字段或行详情抽屉。宽表只在表格内部横向滚动。所有弹窗取消不触发表单提交。

## 15. 迁移和兼容

- 使用 append-only SQL migration，并保持 `Base.metadata.create_all` 支持全新安装。
- 迁移必须能对现有 PostgreSQL 数据卷重复执行。
- 新外键和索引不改变现有 Document、SourceConnector、Ontology 和会话数据。
- 不通过删除 Volume 处理迁移。
- 新 API 保持原 `/sources` 和同步 API 行为兼容。

## 16. 测试与验收矩阵

1. 单元：Schema 规范化、Diff、敏感识别、预览构建、分页筛选、映射审计、Plan/IR、方言编译、参数绑定、注入拒绝和脱敏。
2. 合约：Ontology2SQL 锁定版本测试；Semantica DBIngestor/结构化解析/增量/Provenance；DSH 插件工具注册、结构化输出、取消和凭据隔离。
3. 集成：真实 MySQL 与 PostgreSQL fixture，含主外键、无主键、JSON、大文本、NULL、敏感字段和 Schema 漂移。
4. API E2E：连接、发现、预览、映射、激活、查询、漂移、stale、回滚、权限和清理。
5. 浏览器：数据库数据源全流程、无横向页面溢出、服务端脱敏、映射工作台、查询测试、智能问答双类引用和 Console。
6. 回归：功能回归、重建集成回归、不删 Volume 的冷重启持久化回归。

测试报告必须区分真实数据库执行、协议级验证和未覆盖限制，不能以 Mock 替代 Semantica、MySQL、PostgreSQL 或 DSH 合约结论。

## 17. 分阶段实现顺序

1. Schema Registry、Preview Policy、迁移和单元测试。
2. 安全实时预览、快照预览和数据源六页签。
3. Mapping Manifest、建议、验证、激活、漂移和版本回滚。
4. Plan、IR、MySQL/PostgreSQL 编译器和只读执行器。
5. DSH 工具、智能问答事件与结构化引用。
6. 数据库专用行级 ContentElement、增量验证和图谱物化。
7. Docker、API E2E、浏览器测试、三轮回归和交付文档。

