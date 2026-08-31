# Semantica 源码功能全景指南

> 本文不是 README 转述，而是基于当前仓库源码逐模块核对后形成的功能审计。内容按“安装与配置 → 数据进入 → 解析清洗 → 切分 → 语义抽取 → 质量治理 → 知识图谱/本体 → 推理 → 向量与存储 → Agent 上下文 → 溯源与版本 → 导出与可视化 → 服务化与集成”的真实使用顺序组织。

## 0. 审阅基线与口径

| 项目 | 结论 |
|---|---|
| 仓库 | [`semantica-agi/semantica`](https://github.com/semantica-agi/semantica)，本地副本位于 [`semantica/`](semantica/) |
| 审阅分支 | `main` |
| 审阅提交 | `cce5ea177cbac29a526effa546219c48f8ec36f4` |
| 提交时间/主题 | 2026-08-28；`fix(pipeline): set_parallelism now enables dependency-layer parallel execution` |
| 包版本 | `0.6.6`，要求 Python `>=3.8`；依据 [`pyproject.toml`](semantica/pyproject.toml) |
| 审阅范围 | `semantica/` 下所有公开包导出、核心实现、CLI、Server、Explorer、MCP、Integrations，以及配置、示例、测试和架构文档；对只在子模块直接导入的能力也做了标注 |
| “每个功能”的口径 | 以可被用户调用的模块、类、方法族、后端或服务入口为功能单元；私有辅助函数合并进所属功能，不逐个罗列 `_private_method` |
| 输入/输出口径 | 优先写源码中真实接受和返回的 Python 类型/字典字段；可选依赖缺失、占位实现、随机/降级路径和文档偏差单独提示 |
| 重要提醒 | README、`docs/` 与 `main` 源码存在多处接口漂移。本文以源码为准，最显著差异见第 20 节 |

## 1. 推荐使用顺序总览

| 顺序 | 阶段 | 背景/为什么存在 | 主要功能 | 典型输入 | 典型输出 | 源码入口 |
|---:|---|---|---|---|---|---|
| 1 | 安装与配置 | 不同数据源、模型和存储后端依赖差异很大 | 安装 extras、读取配置、日志与异常体系 | 环境变量、YAML/字典、provider 凭据 | `Config`、组件配置、已注册 provider | [`core/config_manager.py`](semantica/semantica/core/config_manager.py)、[`pyproject.toml`](semantica/pyproject.toml) |
| 2 | Ingest 数据进入 | 统一文件、网页、API、流、数据库、云平台等异构来源 | 把外部来源转成带元数据的内存对象 | 路径、URL、连接参数、消息、查询 | `FileObject`、`WebContent`、`APIData`、`TableData` 等 | [`ingest`](semantica/semantica/ingest/) |
| 3 | Parse 文档解析 | 二进制或结构化载荷需要还原文本、表格、章节和媒体结构 | 格式识别、专用解析、批处理 | 文件路径、`FileObject`、字符串/bytes | 格式专属 dataclass 或标准字典 | [`parse`](semantica/semantica/parse/) |
| 4 | Normalize 标准化 | 抽取前先降低编码、格式、实体、日期、数值差异 | 文本清洗、实体/日期/数值统一、去重、语言与编码检测 | 文本、实体、日期、数值、记录列表 | 标准字符串、数值、检测结果、清洗记录 | [`normalize`](semantica/semantica/normalize/) |
| 5 | Split 切分 | 长文档不能直接交给 embedding/LLM；还需保留来源位置 | 多策略 chunk、表格切分、层次/图/语义切分、chunk 溯源 | 文本、文档列表、表格、tokenizer/模型配置 | `list[Chunk]`、`list[TableChunk]` | [`split`](semantica/semantica/split/) |
| 6 | Semantic Extract 语义抽取 | 将自然语言转换为实体、关系、三元组、事件和语义网络 | NER、关系/三元组、事件、共指、语义角色/网络、抽取验证 | 文本、chunks、实体、模型/provider | `Entity`、`Relation`、`Triplet`、`Event` 等 | [`semantic_extract`](semantica/semantica/semantic_extract/) |
| 7 | QA 质量治理 | 抽取结果常有重复、冲突和来源可信度差异 | 相似度、候选阻塞、聚类、合并、冲突检测/解决 | 实体/关系集合、来源和置信度 | 重复组、合并结果、冲突和解决方案 | [`deduplication`](semantica/semantica/deduplication/)、[`conflicts`](semantica/semantica/conflicts/) |
| 8 | KG 构图 | 把抽取对象变成可查询、可分析、可验证的图 | 图构建、验证、查询、分析、时态图、上下文图 | 文本或实体/关系/三元组 | 标准图字典、指标、路径、时态事实 | [`kg`](semantica/semantica/kg/) |
| 9 | Ontology 本体 | 给图增加概念、属性、层级、约束和复用语义 | 生成、对齐、SHACL、SKOS、OWL、评估和版本 | 数据/文本、现有本体、需求、命名空间 | ontology dict、shape graph、OWL、验证报告 | [`ontology`](semantica/semantica/ontology/) |
| 10 | Reasoning 推理 | 从图事实和规则中获得新结论，并给出解释 | 前向/后向/Rete/Datalog/SPARQL/时态/溯因推理 | 事实、规则、查询、图、时间条件 | 推导事实、bindings、`InferenceResult`、解释 | [`reasoning`](semantica/semantica/reasoning/) |
| 11 | Embeddings | 为语义搜索、聚类、链接和 GraphRAG 提供连续向量表示 | 文本 embedding、池化、批量、图/向量 embedding 管理 | 文本、列表、文档对象、模型配置 | NumPy 一维/二维数组、批处理结果 | [`embeddings`](semantica/semantica/embeddings/) |
| 12 | Stores 存储 | 将向量、属性图和 RDF 三元组持久化并提供原生查询 | Vector/Graph/Triplet 多后端适配、混合检索、SPARQL | vectors、documents、nodes、edges、triplets | IDs、搜索结果、查询行、后端统计 | [`vector_store`](semantica/semantica/vector_store/)、[`graph_store`](semantica/semantica/graph_store/)、[`triplet_store`](semantica/semantica/triplet_store/) |
| 13 | Context/Decision | Agent 需要可检索记忆、决策历史、因果链和政策检查 | 记忆 CRUD、GraphRAG、决策/先例/因果/政策 | 内容、query、decision、policy、图/向量存储 | memory IDs、检索上下文、决策链、合规结果 | [`context`](semantica/semantica/context/) |
| 14 | Provenance/Change | 知识需要可审计、可追踪、可回滚 | W3C 风格溯源、哈希链、图快照、diff、失效与恢复 | 实体/关系/chunk、活动、版本、图 | lineage、审计报告、snapshot、delta | [`provenance`](semantica/semantica/provenance/)、[`change_management`](semantica/semantica/change_management/) |
| 15 | Pipeline 编排 | 把上述步骤组成有依赖、可重试、可并行的生产流程 | DAG 构建、执行、模板、验证、调度、delta mode | handler、依赖、输入字典、执行配置 | `Pipeline`、`ExecutionResult`、步骤结果/指标 | [`pipeline`](semantica/semantica/pipeline/) |
| 16 | Export/Viz | 让结果进入下游系统或供人检查 | RDF/JSON/CSV/Parquet/OWL/LPG/报告导出；交互图表 | 图、本体、向量、报告数据 | 字符串、文件、Figure/HTML/Plot 对象 | [`export`](semantica/semantica/export/)、[`visualization`](semantica/semantica/visualization/) |
| 17 | CLI/服务/集成 | 将库能力暴露给终端、HTTP、Explorer、MCP 和 Agent 框架 | CLI 命令族、FastAPI、Explorer、MCP、Agno/CrewAI/LangChain | 命令参数、HTTP/JSON-RPC、框架对象 | 终端/JSON、API 响应、MCP tool result | [`cli.py`](semantica/semantica/cli.py)、[`server.py`](semantica/semantica/server.py)、[`explorer`](semantica/semantica/explorer/)、[`integrations`](semantica/integrations/) |

## 2. 安装、配置与公共入口

### 2.1 安装能力矩阵

| 功能 | 背景/用途 | 输入 | 输出/效果 | 源码事实 |
|---|---|---|---|---|
| 基础安装 | 获得核心数据模型与本地能力 | `pip install semantica` 或源码安装 | `semantica` Python 包及 CLI entry points | 依赖和入口以 [`pyproject.toml`](semantica/pyproject.toml) 为准 |
| LLM providers | 抽取/生成可对接不同模型平台 | extras：`openai`、`groq`、`gemini`、`anthropic`、`ollama`、`deepseek`、`litellm`、`instructor` | 相应 SDK 和 provider 可用 | provider 仍需各自 API key/endpoint |
| 解析 extras | 高级文档/OCR/Docling | `parse-docling` 等 extra | 多格式、布局、OCR 解析依赖可用 | 普通解析器和可选高级解析器分开 |
| 数据库 extras | 连接 Snowflake、Databricks、Arrow 等 | `db-snowflake`、`db-databricks`、`db-arrow` | 对应 ingestor 可实例化 | 缺少依赖时通常在实例化/调用时报可选依赖错误 |
| 图存储 extras | 使用远程 LPG 后端 | `neo4j`、`falkordb`、`neptune`、`age` | `GraphStore` 对应 adapter 可连接 | 连接参数由 backend config 提供 |
| RDF store extra | 本地高性能 RDF | `oxigraph` | `TripletStore(backend="oxigraph")` 可用 | 其他 RDF server 多通过 HTTP |
| 向量 store extras | 选择生产向量库 | `qdrant`、`weaviate`、`pinecone`、`milvus`、`pgvector`、`sqlite` | 对应 VectorStore backend | `inmemory`/部分本地路径无需远程服务 |
| 基础设施/云/监控 | 部署与运行环境集成 | `infra`、`cloud`、`monitoring` | 相应 SDK/观测能力 | 不会自动配置外部资源 |
| 可视化/GPU | 图形和硬件加速 | `viz`、`gpu` | 绘图/投影库或 GPU 库可用 | API 会随可选包存在情况降级或报错 |
| Agent 框架 | 作为其他 Agent 框架的 memory/tool/retriever | `agno`、`crewai`、`langchain` | integrations adapter 可用 | 见第 18 节 |
| Watch/Split/Explorer | 文件监听、扩展切分、Web Explorer | `watch`、各 split extra、`explorer` | 对应 CLI/服务能力 | Explorer 还需要前端静态资源/服务依赖 |

### 2.2 配置、顶层对象和懒加载

| 功能 | 背景/功能 | 输入 | 输出 | 注意事项/源码 |
|---|---|---|---|---|
| `Config` | 汇总模块配置，支持字典化访问和默认值 | 配置字典、字段值、环境变量 | `Config` 对象/子配置 | 入口见 [`core/config_manager.py`](semantica/semantica/core/config_manager.py) |
| 日志与异常 | 给所有模块提供一致错误类型和日志配置 | logger name、level、handler 等 | logger；`SemanticaError` 派生异常 | [`utils`](semantica/semantica/utils/) 还含 validator、重试、文件/时间/哈希工具 |
| 顶层 `Semantica` | 高层 facade，延迟创建 parser、ingestor、pipeline、embedding、reasoner、graph builder | `Semantica(config: Config\|dict=None, **kwargs)` | 一个带懒加载属性的 orchestrator | 构造函数并没有文档示例中的 `config_path=` 自动加载语义；见 [`core/orchestrator.py`](semantica/semantica/core/orchestrator.py) |
| `Semantica.build_knowledge_base` | 理想上把多来源走完整流水线并构图/向量化 | `sources`（单个或列表）、`**kwargs` | 字典：`knowledge_graph`、`embeddings`、`results`、`statistics`、`metadata` | 当前默认 pipeline 只有无 handler 的 `default_step`，执行时原样传递；随后又只从顶层结果找 `entities/relationships`，因此默认高层调用通常得到空 KG/embedding。生产使用应显式注册 pipeline handlers |
| 顶层懒模块 | 降低 import 成本 | `semantica.kg` 等属性访问 | 被加载的包代理 | 当前懒代理仅覆盖 `kg/ingest/embeddings/semantic_extract/visualization/pipeline/parse/normalize/export/vector_store/triplet_store/graph_store/ontology`；其余包正常导入；见 [`semantica/__init__.py`](semantica/semantica/__init__.py) |
| CLI entry points | 安装后提供不同服务形态 | shell command | 对应进程 | `semantica`、`semantica-server`、`semantica-worker`、`semantica-explorer`、`semantica-mcp` |

## 3. 数据进入（Ingest）

### 3.1 统一入口与本地/网络来源

| 功能 | 背景/功能 | 输入 | 输出 | 关键行为/限制 | 源码 |
|---|---|---|---|---|---|
| Unified ingest | 由来源类型和方法把入口统一起来 | `source`、可选 `source_type/method/config` | 统一结果字典，包含成功状态、来源类型、方法、数据与元数据 | 是便捷分发层，不会消除各 ingestor 返回类型差异 | [`ingest/core.py`](semantica/semantica/ingest/) |
| `FileTypeDetector` | 由扩展名/MIME/内容判断文件类型 | path、filename、bytes | 文件类型/MIME 识别结果 | 给 `FileIngestor` 和 parser 路由使用 | [`ingest/file.py`](semantica/semantica/ingest/) |
| `FileIngestor` | 读取单文件/目录并保留文件元数据 | 文件或目录路径、glob/递归配置 | `FileObject` 或列表；字段含 `path/name/size/file_type/mime_type/content/metadata/ingested_at` | `content` 是 bytes，解析仍在 Parse 阶段 | 同上 |
| `CloudStorageIngestor` | 从云对象存储下载后形成统一文件对象 | provider、bucket/key/URI、凭据 | `FileObject`/列表 | 依赖相应云 SDK 与权限 | 同上 |
| `WebIngestor` | 抓网页并抽取正文/链接/元数据 | URL、headers、timeout、抓取策略 | `WebContent(url,title,text,html,metadata,links,fetched_at,status_code)` | 内置 `RateLimiter`、`RobotsChecker`、`ContentExtractor`、`SitemapCrawler` | [`ingest/web.py`](semantica/semantica/ingest/) |
| Sitemap crawl | 批量发现站内 URL 并逐页采集 | sitemap URL、深度/数量/域名限制 | `list[WebContent]`/URL 集合 | 应尊重 robots 与限速；网络错误进入失败记录 | 同上 |
| `RESTIngestor` | 调任意 REST endpoint、分页和批量请求 | endpoint、method、params/body/headers/auth、pagination | `APIData(data,status,endpoint,metadata,ingested_at)`；批量时为列表 | 响应 payload 保持原结构，由后续 parser/normalizer 处理 | [`ingest/api.py`](semantica/semantica/ingest/) |
| `PublicAPIIngestor` | 发现并调用免认证公共 API | URL/API 描述、检测参数 | `PublicAPIDetection` 或 `APIData` | 支持无认证检测、示例、批量调用 | 同上 |
| Feed ingest | RSS/Atom 拉取、发现和监控 | feed URL、轮询/过滤配置 | `FeedData`，含 feed 元数据及 `FeedItem` 列表 | monitor 是轮询式输入能力 | [`ingest/feed.py`](semantica/semantica/ingest/) |
| Email ingest | 将邮件文件/邮箱消息结构化 | 邮件来源、过滤条件、附件设置 | `list[EmailData]`，含发件人/收件人/主题/body/date/附件/headers | 与 parse/email 的职责不同：此处负责进入，parse 负责内容拆解 | [`ingest/email.py`](semantica/semantica/ingest/) |
| Repository ingest | 读取本地代码库或克隆远程仓库 | local path 或 repo URL、branch、include/exclude | repo 字典，内含 `CodeFile`、`CommitInfo`、指标、依赖、提交信息 | 会分析文件而不只是下载；远程输入涉及临时 clone | [`ingest/repository.py`](semantica/semantica/ingest/) |
| Ontology ingest | 读取 RDF/OWL/本体资源 | 文件/URL、format | `OntologyData(data,source_path,format,metadata,ingested_at)` | 解析/推理仍由 ontology 模块负责 | [`ingest/ontology.py`](semantica/semantica/ingest/) |

### 3.2 表格、数据库、大数据与消息流

| 功能 | 背景/功能 | 输入 | 输出 | 关键行为/限制 | 源码 |
|---|---|---|---|---|---|
| Generic database | 统一关系数据库的表、schema 和 query 读取 | connection config、table/schema、SQL | `TableData(table_name,columns,rows,row_count,schema,metadata)` 或 query 字典 | 不同 connector 的占位符和类型映射仍由 adapter 处理 | [`ingest/database.py`](semantica/semantica/ingest/) |
| Snowflake | Snowflake 表/查询/元数据采集 | account/user/warehouse/database/schema、SQL/table | `SnowflakeData`、schema/catalog/lineage、document 导出 | 需要 snowflake extra 和外部权限 | [`ingest/snowflake.py`](semantica/semantica/ingest/) |
| Databricks | Databricks SQL/表/catalog/lineage 采集 | host/token/http_path/catalog/schema、SQL/table | `DatabricksData`、schema/catalog/lineage、document 导出 | 需要 databricks connector | [`ingest/databricks.py`](semantica/semantica/ingest/) |
| Parquet | 保留列式 schema 和分区元数据 | file/directory、columns、filters、limit、`include_data` | `ParquetData(data,row_count,columns,schema,source,metadata,ingested_at)` | 支持 Hive partition 目录；可只读 schema/metadata | [`ingest/parquet.py`](semantica/semantica/ingest/) |
| Arrow | 读取 Arrow table/IPC 数据 | Arrow source、列/过滤/限制 | `ArrowData`（数据、行列、schema、source、metadata） | 适合零拷贝/列式下游 | [`ingest/arrow.py`](semantica/semantica/ingest/) |
| XML secure ingest | 在进入阶段做安全解析和结构/校验记录 | XML file/string/directory、XSD/DTD 设置 | `XMLIngestionData(root,elements,namespaces,source_path,root_tag,validation,metadata,ingested_at)` | 使用安全的 lxml 配置，支持 XSD/DTD；与 Parse/XML 的轻量结构化输出不同 | [`ingest/xml.py`](semantica/semantica/ingest/) |
| Stream core | 把消息中间件记录包装成统一消息并交给 processor | broker 配置、topic/queue/stream、handler | `StreamMessage(content,metadata,timestamp,source,partition,offset)`，以及处理统计 | `StreamProcessor` 负责消费生命周期/handler | [`ingest/stream.py`](semantica/semantica/ingest/) |
| Kafka/RabbitMQ/Kinesis/Pulsar | 接入常见队列和事件流 | 各平台连接、订阅与 offset 配置 | 连续 `StreamMessage` 或 processor 状态 | 均需外部服务和可选 SDK；确认提交/ack 语义依 adapter | 同上 |
| MCP ingest | 从 MCP server 读取 resource 或调用 tool | server config、resource URI/tool name/arguments | `MCPData(source,server_name,data_type,content,metadata,ingested_at,resource_uri/tool_name)` | 提供 connect/list/read/call；这是“消费外部 MCP”，不是第 18 节的 Semantica MCP server | [`ingest/mcp.py`](semantica/semantica/ingest/) |
| DuckDB/Pandas/Mongo/Elastic/GDrive/HuggingFace | 面向特定生态的补充 ingestor | 对应文件、DataFrame、连接、Drive ID、dataset ID | 记录/文档/表格型结果，随 adapter 保留元数据 | 这些实现存在，但部分未从 `semantica.ingest` 顶层 re-export，需从具体子模块直接导入 | [`ingest/`](semantica/semantica/ingest/) |

## 4. 文档解析（Parse）

| 功能 | 背景/功能 | 输入 | 输出 | 关键行为/限制 | 源码 |
|---|---|---|---|---|---|
| `DocumentParser` | 简化常用文档类型的自动分发 | path、`FileObject`、单个或列表 | 单文件解析字典；batch 含 successful/failed 统计 | 当前自动 dispatch 主要覆盖 PDF/DOCX/HTML/Text；其他格式使用专用 parser | [`parse/document_parser.py`](semantica/semantica/parse/document_parser.py) |
| `DoclingParser` | 需要布局感知和更广格式时调用 Docling | 文档路径/bytes、Docling 配置 | 结构化文档、文本/表格/版面数据 | Docling 未安装时导出的是可解释错误的占位类；不是无依赖能力 | [`parse/docling_parser.py`](semantica/semantica/parse/docling_parser.py) |
| PDF parser | 提取页、文本、表格、图像和 PDF metadata | PDF path/bytes | dict：文本、pages、tables、images、metadata | OCR/高级表格能力视依赖而定 | [`parse/pdf.py`](semantica/semantica/parse/) |
| DOCX parser | 保留章节、段落、表格和文档属性 | DOCX path/bytes | dict：sections、text、tables、metadata | 面向 Word OpenXML | [`parse/docx.py`](semantica/semantica/parse/) |
| PPTX parser | 保留幻灯片、形状、notes/媒体信息 | PPTX path/bytes | `PPTXData` | 一般随后按 slide 或 section 切分 | [`parse/pptx.py`](semantica/semantica/parse/) |
| Excel parser | 提取 sheet、单元格/表格、公式和 metadata | XLSX/XLS path/bytes | `ExcelData` | 与 ingest/parquet 的列式用途不同 | [`parse/excel.py`](semantica/semantica/parse/) |
| HTML/Web parser | 将 HTML 或已抓网页转正文和结构 | HTML string/file、`WebContent` | `HTMLData` 或 Web 字典 | 清理脚本/样式并保留链接、标题、metadata | [`parse/html.py`](semantica/semantica/parse/)、[`parse/web.py`](semantica/semantica/parse/) |
| JSON parser | 解析 JSON 并保留嵌套路径/结构 | path、string、bytes | `JSONData` | 不自动替用户决定业务 schema | [`parse/json.py`](semantica/semantica/parse/) |
| CSV parser | 读取 header、rows、类型/方言信息 | path/string、delimiter/encoding | `CSVData` | 大文件是否全量载入取决于调用配置 | [`parse/csv.py`](semantica/semantica/parse/) |
| XML parser | 把 XML 变成元素/属性/namespace 结构 | path/string/bytes | `XMLData` | 若需要 XSD/DTD 与安全审计，优先 ingest/XML | 同上 |
| Image/OCR parser | 对图片做 OCR 和图像 metadata 提取 | image path/bytes、OCR config | dict 和/或 `OCRResult` | OCR engine 为可选依赖 | [`parse/image.py`](semantica/semantica/parse/) |
| Email parser | 拆 header、body、附件和 multipart | EML/message bytes | `EmailData` | 可接 ingest/email 的结果 | [`parse/email.py`](semantica/semantica/parse/) |
| Code parser | 提取代码结构、注释、imports/dependencies | 代码 path/string、language | dict，含 `CodeStructure`、comments、dependencies | 是静态结构抽取，不执行代码 | [`parse/code.py`](semantica/semantica/parse/) |
| Structured/MCP parser | 将已结构化数据或 MCP payload 映射为文档/字段 | dict/list/`MCPData` | 标准 dict/文档集合 | 适合作为统一 pipeline 边界 | [`parse/structured.py`](semantica/semantica/parse/)、[`parse/mcp.py`](semantica/semantica/parse/) |
| Media parser | 音视频容器/metadata 的入口 | audio/video path/bytes | media dict | 部分音频/视频深度理解仍是未来/占位路径，不能等同完整 ASR/视频理解 | [`parse/media.py`](semantica/semantica/parse/) |

## 5. 标准化与清洗（Normalize）

| 功能 | 背景/功能 | 输入 | 输出 | 主要选项/语义 | 源码 |
|---|---|---|---|---|---|
| `TextNormalizer` | 消除大小写、Unicode、空白和标点差异 | `str`/文本列表、normalization config | 标准化 `str`/列表 | Unicode normalization、case、空白、标点策略 | [`normalize/text.py`](semantica/semantica/normalize/) |
| `TextCleaner` | 去 HTML/控制字符/噪声并做批量清理 | string/list、cleaning rules | 清洁后的 string/list | sanitize、HTML removal、whitespace/punctuation cleanup | 同上 |
| `EntityNormalizer` | 把表述变体映射到规范实体 | entity string/dict、alias map、context | canonical string/dict、链接/消歧结果 | 别名、大小写、类型、候选消歧 | [`normalize/entity.py`](semantica/semantica/normalize/) |
| `DateNormalizer` | 统一日期格式、时区、相对日期和区间 | string/datetime/date、timezone/locale | 标准日期字符串或 datetime/区间结果 | 相对日期依当前时间/locale；时间区间保留边界 | [`normalize/date.py`](semantica/semantica/normalize/) |
| `NumberNormalizer` | 统一数量、单位、货币和科学计数 | string/int/float、locale、unit/currency config | number 或 quantity dict | 可解析百分比、单位、币种、科学计数 | [`normalize/number.py`](semantica/semantica/normalize/) |
| `DataCleaner` | 对记录集合做缺失、异常、重复和规则验证 | list/dict/table-like、rules | 清洁记录；专用调用返回 `DuplicateGroup`、`ValidationResult` | 数据级 QA，不等同实体语义去重 | [`normalize/cleaner.py`](semantica/semantica/normalize/) |
| `LanguageDetector` | 为 tokenizer/model/locale 路由识别语言 | text、候选语言 | language code 或 `(code, confidence)` | 短文本置信度可能较弱 | [`normalize/language.py`](semantica/semantica/normalize/) |
| `EncodingHandler` | 避免文件编码和 BOM 导致乱码 | bytes/string、候选 encoding | 检测 tuple、解码 string、编码 bytes 或验证结果 | 支持检测、转换、验证、BOM 移除 | [`normalize/encoding.py`](semantica/semantica/normalize/) |

## 6. 切分与来源位置（Split）

### 6.1 核心数据结构与统一切分器

| 功能/类型 | 背景/功能 | 输入 | 输出 | 关键字段/行为 | 源码 |
|---|---|---|---|---|---|
| `Chunk` | 所有下游文本片段的标准边界 | 由 splitter 创建 | chunk 对象 | `text/start_index/end_index/metadata/id`，索引用于回到原文 | [`split/base.py`](semantica/semantica/split/) |
| `TextSplitter` | 用统一接口选择一种或多种策略 | text、策略名/列表、chunk size/overlap、策略参数 | `list[Chunk]` | 提供 `split`、`split_batch`、`split_documents` | [`split/text_splitter.py`](semantica/semantica/split/) |
| `TableChunker` | 表格不能只按字符截断，需要保留 header/row 关系 | table/rows、header、size/overlap | `list[TableChunk]` 或通用 `Chunk` | metadata 保留表格上下文 | [`split/table.py`](semantica/semantica/split/) |
| `ProvenanceTracker` | 追踪 chunk 来自哪个文档和区间 | document/chunk/parent relationship | lineage/关联记录 | 是 chunk 级轻量来源追踪；全局审计见 Provenance 模块 | [`split/provenance.py`](semantica/semantica/split/) |

### 6.2 切分策略全表

| 策略族 | 策略 | 背景/功能 | 输入 | 输出/适用场景 |
|---|---|---|---|---|
| 基础长度 | recursive | 按分隔符层级递归逼近 chunk size | 文本、separator 列表、size/overlap | `Chunk[]`；通用 RAG 默认候选 |
| 基础长度 | tokens | 按 tokenizer token 数精确分段 | 文本、tokenizer、token size/overlap | 适合有上下文窗口约束的模型 |
| 基础结构 | sentences / paragraphs | 以句子或段落边界切分 | 文本、边界/合并阈值 | 更可读、较少语义截断的 chunks |
| 基础长度 | characters / words | 固定字符数或词数 | 文本、size/overlap | 最稳定、但不理解语义 |
| 模型辅助 | semantic_transformer | 用 transformer 语义变化找边界 | 文本、模型、阈值 | 主题连贯的 chunks；需要模型依赖 |
| 模型辅助 | embedding_semantic | 比较相邻单位 embedding 距离 | 文本、embedder、阈值/window | 语义断点 chunks；计算成本高于规则切分 |
| 模型辅助 | llm | 让 LLM 决定语义分段 | 文本、LLM provider/prompt/限制 | 结构质量较高但有延迟、费用、非确定性 |
| NLP | huggingface / nltk | 用外部 tokenizer/句法工具切分 | 文本、模型或 NLTK resource | 语言感知边界 |
| 信息保持 | entity_aware | 避免在关键实体跨度中间截断 | 文本、实体 spans、size | 保留实体完整性的 chunks |
| 信息保持 | relation_aware | 让关系两端尽量处于同一片段 | 文本、entities/relations、size | 提高关系抽取召回 |
| 图感知 | graph_based | 按文本单元构成的相似图切分 | units、相似图参数 | 图分区对应的 chunks |
| 图感知 | community_detection | 按图社区聚合内容 | graph/nodes、community 算法 | 社区级 chunks |
| 图感知 | centrality_based | 由节点中心度引导边界/上下文 | graph、centrality 参数 | 突出关键节点相关内容 |
| 图感知 | subgraph | 按子图组织文本或知识片段 | graph、seed/半径/大小 | 适合 GraphRAG 子图上下文 |
| 本体感知 | ontology_aware | 依据 class/property/概念边界组织 | 文本、ontology、matching config | 概念一致的 chunks |
| 层次结构 | hierarchical | 产生 parent/child 多粒度片段 | 文本、层级尺寸 | 同时支持粗粒度导航和细粒度召回 |
| 主题结构 | topic_based | 依据主题切换分段 | 文本、topic model/阈值 | 主题连续 chunks |
| 文档结构 | structural | 按标题、章节、列表、代码块等结构切分 | parsed document/文本、结构规则 | 保留文档层级与 section metadata |
| 窗口 | sliding_window | 固定窗口连续滑动 | tokens/文本、window/stride | 高召回、重复度高，适合序列上下文 |

## 7. 语义抽取（Semantic Extract）

### 7.1 标准抽取对象

| 类型 | 背景/功能 | 输入来源 | 输出字段 | 下游用途 | 源码 |
|---|---|---|---|---|---|
| `Entity` | 表示文本中的命名实体 | NER、手工对象、外部模型 | `text/label/start_char/end_char/confidence/metadata` | 去重、关系抽取、KG node | [`semantic_extract/models.py`](semantica/semantica/semantic_extract/) |
| `Relation` | 表示两个实体间的谓词关系 | relation extractor、规则/模型 | `subject: Entity/predicate/object: Entity/confidence/context/metadata` | KG edge、triplet、验证 | 同上 |
| `Triplet` | RDF 风格、最小知识陈述 | triplet extractor 或 Relation 映射 | `subject/predicate/object/confidence/metadata` | RDF/TripletStore/KG | 同上 |
| `Event` | 表示有参与者、地点、时间的事件 | `EventDetector` | `text/type/span/participants/location/time/confidence/metadata` | 事件图、时态 KG | [`semantic_extract/event.py`](semantica/semantica/semantic_extract/) |
| `Mention` / `CoreferenceChain` | 将代词/别称与同一指称对象聚合 | `CoreferenceResolver` | mention spans、代表 mention、chain、置信度/metadata | 在关系抽取前补全主体/客体 | [`semantic_extract/coreference.py`](semantica/semantica/semantic_extract/) |
| `SemanticNetwork` | 将概念及其语义连接组织成独立网络 | `SemanticNetworkExtractor` | `nodes/edges/metadata` | 网络分析、YAML 导出、可视化 | [`semantic_extract/semantic_network.py`](semantica/semantica/semantic_extract/) |

### 7.2 抽取功能与方法

| 功能 | 背景/功能 | 方法/后端 | 输入 | 输出 | 关键行为/限制 | 源码 |
|---|---|---|---|---|---|---|
| `NERExtractor` | 从自然语言识别人、组织、地点、产品、日期等实体 | `pattern`、`regex`、`rules`、`ml`(spaCy)、`huggingface`、`llm` | text 或 batch、method、实体类型、置信度阈值 | `list[Entity]`；batch 为嵌套列表/批结果 | 支持 fallback chain、ensemble voting、type/confidence filter；模型法需依赖/模型 | [`semantic_extract/ner.py`](semantica/semantica/semantic_extract/) |
| `RelationExtractor` | 判断已知实体之间的语义关系 | `pattern`、`regex`、`cooccurrence`、`similarity`、`dependency`、`huggingface`、`llm` | text、`list[Entity]`、method/config | `list[Relation]` | 通常先提供实体；支持验证、fallback 和 temporal bounds；共现不等于因果 | [`semantic_extract/relation.py`](semantica/semantica/semantic_extract/) |
| `TripletExtractor` | 直接得到 subject-predicate-object 陈述 | `pattern`、`rules`、`huggingface`、`llm` | text；可选 entities/relations/provider | `list[Triplet]` | 可内部派生实体/关系；支持质量/有效性检查和 RDF serialize（Turtle/N-Triples/JSON-LD/XML） | [`semantic_extract/triplet.py`](semantica/semantica/semantic_extract/) |
| `EventDetector` | 从句子识别事件类型、论元、地点和时间 | rule/model/LLM 配置 | text、可选 entities | `list[Event]` | 适合在构造时态图前调用 | [`semantic_extract/event.py`](semantica/semantica/semantic_extract/) |
| `CoreferenceResolver` | 解决“他/该公司/后者”等跨句指称 | text、mentions/entities、resolver config | `list[CoreferenceChain]`/已解析文本信息 | 应在跨句关系抽取前运行；模型依赖影响准确率 | [`semantic_extract/coreference.py`](semantica/semantica/semantic_extract/) | 同上 |
| `SemanticAnalyzer` | 语义角色、相似度和聚类等更高层分析 | text/entities/embeddings/config | roles、similarities、clusters 等分析字典 | 是分析层，不直接写入 store | [`semantic_extract/semantic_analyzer.py`](semantica/semantica/semantic_extract/semantic_analyzer.py) | 同上 |
| `SemanticNetworkExtractor` | 抽取节点/边、计算网络 analytics 并导出 YAML | text、extractors/config | `SemanticNetwork`、analytics、YAML | 与 KGBuilder 相比更偏轻量语义网络 | [`semantic_extract/semantic_network.py`](semantica/semantica/semantic_extract/) | 同上 |
| `ExtractionValidator` | 对实体、关系、三元组做结构和质量校验 | extracted objects、rules/schema | `ValidationResult(valid,score,errors,warnings,metrics,metadata)` | 防止低置信/结构错误对象直接进 KG | [`semantic_extract/validation.py`](semantica/semantica/semantic_extract/) | 同上 |

### 7.3 LLM Provider 层

| 功能 | 背景/功能 | 输入 | 输出 | 当前源码支持 | 注意事项 | 源码 |
|---|---|---|---|---|---|---|
| Provider factory | 抽取器不绑定某一家模型 | provider 名、model、API key、base URL、生成参数 | provider 实例 | `openai`、`gemini`、`groq`、`anthropic`、`ollama`、`huggingface_llm`、`deepseek`、`novita` | 名称必须与 factory 注册名一致 | [`semantic_extract/providers`](semantica/semantica/semantic_extract/) |
| Provider pooling | 复用连接/客户端并减少初始化 | provider config/key | 缓存的 provider | 凭据隔离和生命周期按 pool key | 同上 | 同上 |
| Custom provider registry | 让用户注册自有模型服务 | name、provider class/factory | 可被抽取器选择的新 provider | 需满足 provider 接口约定 | 同上 | 同上 |
| Structured extraction config | 统一 prompt、重试、温度、token 等参数 | config dict/dataclass | 规范化 provider/extractor 设置 | 多 provider | 外部模型输出仍需 `ExtractionValidator`；LLM 结果非确定 | [`semantic_extract/config.py`](semantica/semantica/semantic_extract/config.py) |

## 8. 去重、实体解析与冲突治理

### 8.1 去重与合并

| 功能 | 背景/功能 | 输入 | 输出 | 策略/方法 | 关键语义 | 源码 |
|---|---|---|---|---|---|---|
| Similarity calculation | 量化两个实体/关系是否重复 | entity pair、属性、可选 embeddings | `SimilarityResult` | exact、Levenshtein、Jaro-Winkler、cosine、property、relationship、embedding、multi-factor | 不同方法分数不可盲目共享同一阈值 | [`deduplication/similarity.py`](semantica/semantica/deduplication/) |
| `DuplicateDetector` | 从大量实体中产生重复候选并判定 | entities、threshold、candidate/matching config | `DuplicateCandidate`、`DuplicateGroup` | pairwise、batch、incremental、group；blocking/hybrid 候选 | 全量 pairwise 为 O(n²)，大数据应用 blocking | [`deduplication/detector.py`](semantica/semantica/deduplication/) |
| Relationship duplicate detection | 判断语义相同或完全相同的边 | relations/triplets | duplicate candidates/groups | triplet exact、semantic relationship 模式 | source/target/predicate 与语义相似需综合 | 同上 |
| Clustering | 把相互重复候选聚合为簇 | candidates/similarity graph | `ClusterResult` | graph-based、hierarchical | 传递性可能把边界实体误并，需审查阈值 | [`deduplication/clustering.py`](semantica/semantica/deduplication/) |
| `EntityMerger` | 将一个重复簇合成 canonical entity | group、merge strategy、provenance | `MergeOperation` / `MergeResult` | keep-first、keep-last、most-complete、highest-confidence、merge-all、custom | 应保留来源/别名和被合并 ID，方便回溯 | [`deduplication/merger.py`](semantica/semantica/deduplication/) |
| KG `EntityResolver` | 构图阶段将新实体链接到已有图节点 | extracted entity、graph/index/config | resolved/canonical entity 与匹配信息 | 名称、别名、类型、属性和 embedding 可组合 | 它位于 KG 包，不是文档所写的 `semantica.deduplication.EntityResolver` | [`kg/entity_resolver.py`](semantica/semantica/kg/entity_resolver.py) |

### 8.2 冲突、来源与调查

| 功能 | 背景/功能 | 输入 | 输出 | 策略/类型 | 关键语义 | 源码 |
|---|---|---|---|---|---|---|
| `ConflictDetector` | 找同一事实的互斥值、类型、关系或时间冲突 | facts/entities/relations、schema/time | `Conflict` 列表 | value、type、relationship、temporal、logical | 冲突不等于错误，需要来源与时间上下文 | [`conflicts/detector.py`](semantica/semantica/conflicts/) |
| `ConflictResolver` | 依据证据策略选择/合成可信值 | conflict、sources、strategy | `ResolutionResult` | voting、credibility-weighted、most-recent、first-seen、highest-confidence、manual-review、expert-review | manual/expert 策略会留下待审，不会假装自动解决 | [`conflicts/resolver.py`](semantica/semantica/conflicts/) |
| `SourceTracker` | 记录来源身份、可信度和事实关联 | source metadata、fact/entity links、credibility | source records、traceability/credibility 信息 | 为冲突权重和 provenance 提供依据 | [`conflicts/source_tracker.py`](semantica/semantica/conflicts/source_tracker.py) | 同上 |
| Conflict analyzer | 汇总冲突模式、严重度和影响 | conflicts、graph/source context | 分析字典/统计 | 类型、频率、影响范围 | 用于治理优先级 | [`conflicts/analyzer.py`](semantica/semantica/conflicts/) |
| Investigation guide | 生成调查清单、报告和建议 | conflict/case、evidence | checklist/guide/report | 人工调查流程 | 是决策支持，不是事实证明 | [`conflicts/investigation.py`](semantica/semantica/conflicts/) |

## 9. 知识图谱（KG）

### 9.1 构建、验证和查询

| 功能 | 背景/功能 | 输入 | 输出 | 关键行为/限制 | 源码 |
|---|---|---|---|---|---|
| `GraphBuilder` | 将文本或抽取对象组装成规范图 | raw text、`Entity`/`Relation`/`Triplet`、含 `entities/relationships` 的 dict、对象列表 | `{"entities": [...], "relationships": [...], "metadata": {num_entities,num_relationships,temporal_enabled,timestamp,entity_resolution_applied}}` | 兼容 `id/entity_id` 与 `source_id/target_id/subject/object`；可选 entity resolution、conflict、temporal、store | [`kg/graph_builder.py`](semantica/semantica/kg/graph_builder.py) |
| Raw-text construction | 便捷地从纯文本直接抽取并构图 | text、NER/relation/triplet 方法配置 | 标准图字典 | 默认 NER 为本地 `ml`；relation pattern 默认不启用、triplet pattern 默认启用。依赖缺失或规则覆盖不足会造成空/少量结果 | 同上 |
| `GraphValidator` | 构图后做结构、引用和 schema 检查 | 图字典、schema/rules | validation result、errors/warnings/metrics | entity 要求 id/entity_id、type、name；edge 要求 source/target 或 source_id/target_id 及 type；检查重复 ID、悬空边、自环、cycle、孤立点、schema | [`kg/validator.py`](semantica/semantica/kg/) |
| Query engine | 在内存图/存储上匹配节点、关系和路径 | pattern/filter/query/path endpoints | 匹配节点/边/路径/统计 | 后端查询语言能力取决于 GraphStore | [`kg/query.py`](semantica/semantica/kg/) |
| `ContextGraph` | 可变、可遍历、可保存的上下文/知识图 | nodes、edges、metadata、时间/状态 | nodes/edges、traversal/query、save/load result | canonical edge 使用 `source_id/target_id`；支持 retract/purge/tombstone 和历史状态 | [`kg/context_graph.py`](semantica/semantica/kg/) |
| Seed manager（KG） | 用受控种子实体/关系初始化图 | seed data/files/config | 初始 KG/加载报告 | 与 `semantica.seed.SeedDataManager` 是不同实现 | [`kg/seed_manager.py`](semantica/semantica/kg/seed_manager.py) |

### 9.2 图分析与预测

| 功能族 | 背景/功能 | 输入 | 输出 | 方法 | 源码 |
|---|---|---|---|---|---|
| Centrality | 衡量关键节点 | graph、weight/direction | node→score/ranking | degree、betweenness、closeness、eigenvector、PageRank | [`kg/analysis`](semantica/semantica/kg/) |
| Community | 找稠密子群/主题簇 | graph、resolution/config | community assignments/metrics | Louvain、Leiden、overlap、label propagation | 同上 |
| Connectivity | 检查组件、桥、可达性和整体连通 | graph、nodes | components、paths、bridges、connectivity metrics | connected components、shortest paths 等 | 同上 |
| Node embeddings | 从拓扑生成节点向量 | graph、walk/dimension params | node embeddings | Node2Vec | 同上 |
| Link prediction | 预测可能缺失的关系 | graph、node pair/candidate set | candidate links/scores | 拓扑/相似度方法 | 预测值应标注置信度，不能直接当事实 |
| Path/similarity analysis | 查节点路径和结构相似 | source/target/nodes | paths、distance、similarity | shortest/weighted path、neighbor similarity | 同上 |

### 9.3 时态与双时态知识

| 功能 | 背景/功能 | 输入 | 输出 | 关键语义 | 源码 |
|---|---|---|---|---|---|
| Temporal edges/snapshots | 事实会随业务时间变化 | relationship、validity bounds、snapshot time | 带时间边、snapshot graph | 区分有效时间与查询快照 | [`kg/temporal.py`](semantica/semantica/kg/) |
| `BiTemporalFact` | 同时记录“现实何时成立”和“系统何时知道” | fact、`valid_from/valid_until`、`recorded_at/superseded_at` | 双时态事实对象 | valid-time 与 transaction-time 不可混用 | 同上 |
| Point/range query | 重建某一时刻或区间内图 | timestamp 或 start/end、union/intersection | snapshot/事实集合 | range 可选择并集/交集语义 | [`kg/temporal_query.py`](semantica/semantica/kg/temporal_query.py) |
| Evolution query | 比较时段变化 | two timestamps/range | added/removed/changed facts、evolution | 适合审计和 trend 分析 | 同上 |
| Temporal consistency | 检查边界、重叠、矛盾和无效区间 | temporal facts/graph | violations/report | 先校验再推理 | [`kg/temporal_validator.py`](semantica/semantica/kg/) |
| Temporal pattern/path | 分析时序模式、演化和时间限制路径 | graph、pattern/path/time | patterns/evolution/path result | path 只在指定时间条件下成立 | [`kg/temporal_analysis.py`](semantica/semantica/kg/) |
| Allen interval reasoning | 推导 before/after/overlaps/contains 等区间关系 | intervals/facts | 推导关系、gaps、coverage | 处理区间逻辑而非简单 timestamp 比较 | [`kg/temporal_reasoning.py`](semantica/semantica/kg/temporal_reasoning.py) |
| Temporal NLP | 把自然语言时间表达与查询规范化 | “去年 Q4”等 text/query | normalized bounds/rewritten query | 受 locale、当前时间和解析规则影响 | [`kg/temporal_normalizer.py`](semantica/semantica/kg/temporal_normalizer.py) |

## 10. 本体、SHACL、SKOS 与 OWL

| 功能 | 背景/功能 | 输入 | 输出 | 关键行为/限制 | 源码 |
|---|---|---|---|---|---|
| `OntologyEngine.from_data/from_text` | 从已有数据或领域文本快速搭建概念模型 | records/graph/text、domain/config | ontology dict：`classes/properties/hierarchy/namespace/metadata` | 是高层组合入口，生成结果需评估/验证 | [`ontology/engine.py`](semantica/semantica/ontology/engine.py) |
| Class generation | 发现候选概念/类和描述 | entities/data/text、规则/LLM | class definitions | 频率/类型/模型推断均可能产生噪声 | [`ontology/generation`](semantica/semantica/ontology/) |
| Property generation | 推断 datatype/object properties、domain/range | fields/relations/schema/text | property definitions | domain/range 应由数据和专家共同验证 | 同上 |
| Hierarchy generation | 建 subclass/概念层级 | classes、similarity/rules/LLM | hierarchy/parent links | 防止 cycle 和过深/过浅层级 | 同上 |
| Ontology optimizer | 合并冗余类、修复结构、优化模型 | ontology、optimization config | optimized ontology + changes | 优化属于变更，应配版本与报告 | 同上 |
| Requirements & competency questions | 用可回答的问题约束本体目标 | domain requirements/questions | requirements model、coverage targets | 避免“先生成再猜用途” | [`ontology/requirements`](semantica/semantica/ontology/) |
| Reuse/import | 复用已有 vocab/ontology | ontology URI/file/catalog、mapping config | imported modules/terms/mappings | 需处理 license、namespace 和版本 | [`ontology/reuse`](semantica/semantica/ontology/) |
| Alignment | 对齐两个本体中的 class/property/concept | source ontology、target ontology、matching config | alignments、scores、mapping report | 自动匹配只是候选；同名不一定同义 | [`ontology/alignment`](semantica/semantica/ontology/) |
| SHACL generation | 从本体/数据生成约束 shapes | ontology/schema/data、constraint config | SHACL graph/Turtle | 约束包括 cardinality、type、pattern 等 | [`ontology/shacl`](semantica/semantica/ontology/) |
| SHACL validation | 验证 RDF graph 是否符合 shapes | data graph、shape graph | conformance、violations/report | 完整验证需要可选 `pyshacl` extra | 同上 |
| SKOS vocabulary | 管理 concept scheme、concept、broader/narrower/related | concepts/labels/hierarchy | SKOS graph/查询结果 | 适合分类表/术语表，不等同完整 OWL 本体 | [`ontology/skos`](semantica/semantica/ontology/) |
| OWL export | 输出可被语义网工具消费的本体 | ontology、format/path | OWL string/file（OWL XML/Turtle 等） | 序列化成功不代表逻辑一致 | [`ontology/owl`](semantica/semantica/ontology/) |
| Ontology evaluator | 衡量覆盖度、质量并发现 gaps | ontology、data/requirements | evaluation metrics、gaps/report | 用于迭代，不是形式化 reasoner 的替代品 | [`ontology/evaluation`](semantica/semantica/ontology/) |
| Namespace/naming | 统一 URI、prefix、命名约定 | base URI/prefix/rules、terms | canonical IRIs/names、validation | 避免同一概念产生多个 URI | [`ontology/namespaces`](semantica/semantica/ontology/) |
| Modules/imports | 拆分大型本体并声明依赖 | ontology modules/import declarations | module graph/combined ontology | 需处理循环 import 和版本 | 同上 |
| Domain templates | 用预定义领域骨架加速建模 | domain/template parameters | initial ontology structure | 模板必须适配真实领域约束 | 同上 |
| Documentation | 从 ontology 生成类/属性/关系说明 | ontology、doc config | documentation/report | 方便评审和交付 | 同上 |
| Associative classes | 将多元关系/带属性关系提升为类 | relationship pattern、properties | association class + links | 解决简单二元 edge 表达不足 | 同上 |
| LLM ontology generation | 用模型生成/补全概念模型 | text/prompt/provider/config | candidate ontology | 必须经过 evaluator、SHACL 和人工审核 | 同上 |
| Ontology version/migration | 跟踪本体版本、diff 和迁移 | old/new ontology、version IRI、migration rules | version metadata、diff、migration result | 与通用图 snapshot 分开；见 [`ontology/versioning`](semantica/semantica/ontology/) | 同上 |

## 11. 推理与解释（Reasoning）

| 功能 | 背景/功能 | 输入 | 输出 | 关键行为/限制 | 源码 |
|---|---|---|---|---|---|
| `Reasoner` facts/rules | 通用规则推理 facade | facts、rules、query/config | 推导事实字符串或 `InferenceResult(conclusion,rule,premises,confidence,metadata)` | 提供 add/remove/list facts/rules 与推理入口 | [`reasoning/reasoner.py`](semantica/semantica/reasoning/reasoner.py) |
| Forward chaining | 从现有事实不断触发规则直到稳定 | initial facts、rules、iteration limit | 所有可推导事实/推理结果 | 适合批量物化；需防规则爆炸 | 同上 |
| Backward chaining | 从目标向后找可满足前提 | query goal、facts/rules | success/bindings/证明路径 | 适合目标查询 | 同上 |
| Rete engine | 对大量规则/增量事实高效匹配 | facts、conditions、actions | fired rules/actions/results | actions 支持 assert、retract、call、event；外部 action 需注意副作用 | [`reasoning/rete.py`](semantica/semantica/reasoning/) |
| `DatalogReasoner` | 对 Horn clause 做 fixpoint 推导和变量绑定 | fact/rule strings、query、可选 `ContextGraph` | derived facts、query bindings | 可从 ContextGraph 加载；类名不是文档中的 `DatalogEngine` | [`reasoning/datalog.py`](semantica/semantica/reasoning/) |
| `SPARQLReasoner` | 对 SPARQL 查询做扩展、推理与缓存 | SPARQL、RDF graph/store、reasoning config | `SPARQLQueryResult` | 实际支持取决于 graph/store；带缓存 | [`reasoning/sparql.py`](semantica/semantica/reasoning/) |
| `GraphReasoner` | 在属性图/KG 结构上包装规则/路径推理 | graph、rules/query | inferred graph facts/query result | 连接 KG 与规则引擎 | [`reasoning/graph_reasoner.py`](semantica/semantica/reasoning/graph_reasoner.py) |
| Temporal reasoning engine | 对有时间边界的事实/规则推理 | temporal facts/rules/query time | 时态推导/有效结果 | 需保留 valid/recorded time 语义 | [`reasoning/temporal.py`](semantica/semantica/reasoning/) |
| `ExplanationGenerator` | 解释某结论由哪些规则和前提得到 | inference trace/result | `Explanation`、`ReasoningPath`、`Justification` | 用于审计和用户呈现 | [`reasoning/explanation.py`](semantica/semantica/reasoning/) |
| `AbductiveReasoner` | 从观察结果寻找可能解释 | observations、knowledge/rules、ranking config | candidate hypotheses/explanations | 溯因是“最可能解释”，不是演绎证明；实现存在但未从 `semantica.reasoning` 顶层导出 | [`reasoning/abductive.py`](semantica/semantica/reasoning/) |
| `DeductiveReasoner` | 从公理/事实演绎结论 | premises/rules/query | conclusions/proof | 实现存在但未在包顶层 re-export，需直接子模块导入 | [`reasoning/deductive.py`](semantica/semantica/reasoning/) |

## 12. Embeddings 与语义向量

| 功能 | 背景/功能 | 输入 | 输出 | 关键行为/限制 | 源码 |
|---|---|---|---|---|---|
| `EmbeddingGenerator` | 面向 Semantica 数据对象的统一文本 embedding facade | string、string list、`FileObject`、含 `content` 的 dict | 单条 `np.ndarray` 一维向量；批量二维矩阵 | 尽管模块描述提到多模态，当前核心实现是文本；见 [`embeddings/generator.py`](semantica/semantica/embeddings/) | 同上 |
| Batch processing | 控制批大小并记录失败 | documents/texts、batch size | 字典：embeddings、successful、failed、counts/metadata | 单条失败可与整批结果分离 | 同上 |
| `TextEmbedder` | 直接加载本地文本模型 | text/list、model name、device/dimensions | `np.ndarray` | 默认倾向 FastEmbed BGE；可回退 sentence-transformers；都不可用时使用 SHA-256 驱动的确定性 hash 向量，仅适合测试，不具真正语义 | [`embeddings/text.py`](semantica/semantica/embeddings/) |
| Provider store/factory | 管理远程/本地 embedding provider | provider name/config | embedding provider | factory 当前注册 OpenAI、BGE、FastEmbed；Llama provider 类存在但不是 factory 默认映射 | [`embeddings/providers`](semantica/semantica/embeddings/) |
| Pooling | 把 token/chunk 向量合成固定表示 | embeddings、mask/weights/hierarchy | pooled vector(s) | mean、max、CLS、attention、hierarchical | [`embeddings/pooling.py`](semantica/semantica/embeddings/) |
| Vector embedding manager | 批处理、缓存和一致地生成向量 | documents/texts、provider/config | vectors + IDs/metadata | 供 VectorStore 接入 | [`embeddings/vector.py`](semantica/semantica/embeddings/) |
| Graph embedding manager | 生成节点/关系的图表示 | graph/nodes/edges、algorithm config | node/edge embeddings | 区分文本属性向量与纯拓扑 embedding | [`embeddings/graph.py`](semantica/semantica/embeddings/) |

## 13. 存储与检索

### 13.1 Vector Store

| 功能 | 背景/功能 | 输入 | 输出 | 当前后端/语义 | 源码 |
|---|---|---|---|---|---|
| `VectorStore` factory | 用同一入口选择本地或远程向量库 | backend name、connection/index/collection/dimension config | backend adapter | 精确注册名：`faiss`、`weaviate`、`qdrant`、`milvus`、`pinecone`、`pgvector`、`inmemory`、`sqlite` | [`vector_store/base.py`](semantica/semantica/vector_store/)、[`vector_store/factory.py`](semantica/semantica/vector_store/) |
| `embed` | 用 store 绑定的 embedder 生成 query/document 向量 | text/list | vector/matrix | 如果 embedding 报错，当前实现会回退到随机向量；这是生产风险，会让结果不可复现/无语义，建议显式注入可靠 embedder 并禁用静默降级 | [`vector_store/store.py`](semantica/semantica/vector_store/) |
| `add_documents` | 从文本自动 embedding 后写库 | documents/texts、metadata、IDs | stored IDs | 文档和 metadata 必须保持数量对应 | 同上 |
| `store_vectors` / `store` | 直接写预计算向量 | NumPy/list vectors、metadata、IDs/namespace | stored IDs/success | 维度必须与 collection/index 一致 | 同上 |
| Search by text | 自动 embed query 并做 kNN | query string、top_k、filter/namespace | `list[SearchResult]` | `SearchResult` 字段 `id/score/metadata/vector/distance`；score 被归一为“越高越好” | 同上 |
| Search by vector | 跳过 embedder 直接检索 | query vector、top_k、filter/namespace | `list[SearchResult]` | distance metric/过滤能力取决于 backend | 同上 |
| CRUD | 维护已存向量与 metadata | ID、new vector/metadata、filter | update/delete/get/count result | 部分后端 delete/filter 有自身限制 | 同上 |
| Save/load | 持久化本地 index/配置 | path | file/load result | 主要适用于本地后端；远程后端自身已经持久化 | 同上 |
| Metadata filters | 用业务字段限制语义召回 | filter dict/expression、namespace | 过滤后的 search result | 跨后端支持程度不同 | 同上 |
| `HybridSearch` | 融合关键词/其他 retriever 与向量召回 | 多路 ranked results、weights | fused ranked results | 支持 reciprocal-rank fusion、weighted average | [`vector_store/hybrid.py`](semantica/semantica/vector_store/) |
| Decision embedding pipeline | 给决策相似性、过滤、上下文和解释提供检索 | decision/query/context config | decision candidates/context/explanation | 是 Context Decision 能力的向量侧适配 | [`vector_store/decision.py`](semantica/semantica/vector_store/) |

### 13.2 Graph Store（属性图）

| 功能 | 背景/功能 | 输入 | 输出 | 当前后端/注意事项 | 源码 |
|---|---|---|---|---|---|
| `GraphStore` factory | 屏蔽多种属性图数据库连接和基础操作差异 | backend、URI/auth/database/config | backend adapter | 精确后端：`neo4j`、`falkordb`、`neptune`/`amazon_neptune`、`age`/`apache_age` | [`graph_store`](semantica/semantica/graph_store/) |
| Node CRUD | 增删改查实体节点 | label/type、properties、ID | node ID/object/status | property 类型受后端序列化限制 | 同上 |
| Relationship CRUD | 增删改查边 | source/target ID、type、properties | edge ID/object/status | 端点需先存在，方向性由调用声明 | 同上 |
| `add_nodes/add_edges` compatibility | 批量接收 Semantica KG 格式 | node/edge lists | IDs/count/status | 处理 `source_id/target_id` 等 canonical 字段 | 同上 |
| Native query | 执行 Cypher/OpenCypher | query string、parameters | rows/records | Neptune/AGE 的支持语法与 Neo4j 不完全相同 | 同上 |
| Path/neighbors | 查询邻居、最短/约束路径 | node IDs、depth/type/filter | nodes/edges/paths | 高深度遍历需控制规模 | 同上 |
| Index/schema/stats | 管理索引并检查图规模 | label/property/index config | index result、statistics | DDL 细节取决于 backend | 同上 |

### 13.3 Triplet Store（RDF）

| 功能 | 背景/功能 | 输入 | 输出 | 当前后端/注意事项 | 源码 |
|---|---|---|---|---|---|
| `TripletStore` factory | 为 RDF 三元组和 SPARQL 提供统一入口 | backend、endpoint/repository/auth/config | backend adapter | 精确后端：`blazegraph`、`jena`、`rdf4j`、`anzo`、`oxigraph`；默认是 Blazegraph endpoint | [`triplet_store`](semantica/semantica/triplet_store/) |
| Triplet CRUD | 写入/读取/更新/删除 RDF 陈述 | `Triplet`、subject/predicate/object/graph | status/count/`Triplet` list | server 后端主要经 HTTP；Oxigraph 可本地嵌入 | 同上 |
| `execute_query` | 执行 SPARQL query/update | SPARQL string、bindings/config | SELECT rows、ASK bool、graph/result | 当前方法名是 `execute_query`，不是文档示例的 `.sparql()` | 同上 |
| Bulk loader | 高效写入大量三元组/文件 | iterable/file/format/batch config | load stats/errors | 大批量优先于逐条 HTTP | 同上 |
| Query planner/cache | 重写/缓存高频查询 | SPARQL/query key/cache config | plan/cached result/metrics | 失效策略需和写操作配合 | 同上 |
| SKOS operations | 对 vocabulary 做 RDF 存取和查询 | scheme/concept/triplets | concept/hierarchy result | 对接 Ontology SKOS | 同上 |
| `compute_delta` | 比较 named graph 版本以支持 pipeline delta mode | base graph URI、target graph URI | added/removed triple delta | 需要后端与图版本管理配合 | 同上 |

## 14. Agent Context、记忆、决策与政策

### 14.1 Context 与 Memory

| 功能 | 背景/功能 | 输入 | 输出 | 关键行为/限制 | 源码 |
|---|---|---|---|---|---|
| `AgentContext` initialization | 将向量记忆、可选知识图和决策追踪组合 | **必需** `vector_store`；可选 `knowledge_graph`、配置 | context manager | 没有 VectorStore 不能构造；GraphRAG/decision tracking 需要图 | [`context/agent_context.py`](semantica/semantica/context/agent_context.py) |
| `MemoryItem` | 记忆的标准记录 | content、metadata、type/importance/session/time 等 | memory object/ID | 记忆内容与 embedding metadata 关联 | [`context/memory.py`](semantica/semantica/context/) |
| Store memory | 写入单条/批量记忆和对话 | content/item/list、metadata/session | memory ID(s)/count | 自动向量化并写 VectorStore | 同上 |
| Retrieve | 语义检索相关记忆 | query、top_k、filters/session/time | ranked context/memory list | 返回匹配内容、分数和 metadata | 同上 |
| GraphRAG retrieval | 向量召回后扩展相关图节点/边 | query、depth/filters、top_k | vector + graph context | 只有传入 knowledge graph 才可用 | 同上 |
| Forget/update/list | 删除、修改或浏览记忆 | memory ID/filter/new content/metadata | bool/count/list | 更新 content 通常需要重算 embedding | 同上 |
| Conversations | 按 session/role 记录对话历史 | messages/session ID | stored IDs、ordered messages | 支持上下文窗口管理 | 同上 |
| Import/export/save/load | 迁移/持久化上下文状态 | path/data/format | file/state/count/report | backend 自身数据和 context metadata 的边界需确认 | 同上 |

### 14.2 决策、因果、先例与 Policy

| 功能/类型 | 背景/功能 | 输入 | 输出 | 关键字段/行为 | 源码 |
|---|---|---|---|---|---|
| `Decision` | 将 Agent/人的选择变为可查询知识 | category、scenario、reasoning、outcome、confidence 等 | decision object | 字段：`decision_id/category/scenario/reasoning/outcome/confidence/timestamp/decision_maker/embeddings/validity/metadata` | [`context/decision.py`](semantica/semantica/context/) |
| `record_decision` | 同时记录图结构、上下文与可检索向量 | Decision 或字段参数 | decision ID | decision tracking 依赖 knowledge graph | 同上 |
| Query/list decisions | 按字段、语义或时间查历史决策 | filters/query/time/category | `list[Decision]`/ranked result | 精确过滤与 embedding 相似可组合 | 同上 |
| Find precedents | 找相似场景与过去结果 | current scenario/query、top_k | precedent decisions + scores | 相似先例是参考，不代表应复制结果 | 同上 |
| Causal chain | 沿决策间原因/影响关系追踪 | decision ID、direction/depth | `list[Decision]`/causal paths | relation 可表示 caused/influenced/precedent-for 等 | 同上 |
| Decision impact | 查看下游决策/实体受何影响 | decision ID、depth/time | affected nodes/paths/summary | 依赖正确记录因果边 | 同上 |
| Policy | 把约束/规则建成可评估对象 | policy ID、conditions/rules、scope/metadata | Policy object | 可在 decision 记录前后检查 | 同上 |
| Policy compliance | 检查决策是否满足 policies | decision/scenario、policy set | compliance result、violations/explanation | 规则式结果仍需业务责任人确认 | 同上 |
| `ContextGraph` decision methods | 将 decision/policy/causal relation 存入图并跨图分析 | nodes/edges/decision/policy | query/traversal/state/cross-graph results | 支持 temporal state、retract、purge、tombstone | [`context/context_graph.py`](semantica/semantica/context/context_graph.py) |

## 15. Provenance、审计与变更管理

### 15.1 Provenance

| 功能 | 背景/功能 | 输入 | 输出 | 关键行为/限制 | 源码 |
|---|---|---|---|---|---|
| `ProvenanceEntry` | 用 W3C PROV 风格描述实体由哪个活动/Agent/来源产生 | entity/relationship/chunk/property ID、source、activity、agent、time、span、metadata | provenance record | 包含 checksum chain、validity、invalidation、来源位置等审计字段 | [`provenance/models.py`](semantica/semantica/provenance/) |
| Track entity/relationship/chunk/property | 在不同处理阶段记录派生关系 | object IDs、parent/source、activity/agent | entry ID/record | 应在每个 pipeline handler 边界调用 | 同上 |
| Batch tracking | 降低大规模记录开销 | records/list、common activity/source | entry IDs/stats | 失败需和数据事务协调 | 同上 |
| Lineage | 向上查来源和变换历史 | entity/resource ID、depth | ancestor chain/tree | 回答“它从哪里来” | 同上 |
| Descendants/revisions | 向下查被派生项和历史修订 | entry/entity ID | descendants/revision list | 回答“它影响了什么” | 同上 |
| Time/source query | 按窗口和来源过滤溯源 | time range/source/filter | matching entries/statistics | 适合审计与事故范围界定 | 同上 |
| Invalidate | 标记记录/事实不再有效 | target ID、reason/time/actor | invalidated entry/status | 不等同物理删除 | 同上 |
| Audit report | 检查覆盖度、缺口、来源和活动 | scope/time/config | audit report/metrics | 能发现无 provenance 的知识 | 同上 |
| PROV-O export | 与外部审计/语义工具互通 | entries、path/format | Turtle/PROV-O string/file | 保留 entity/activity/agent 关系 | 同上 |
| Integrity/hash chain | 检测 provenance 历史是否被篡改 | entry/range/chain | integrity/check/verify result | 校验只能说明链一致，不能证明来源事实真实 | 同上 |
| Storage | 保存 provenance 记录 | storage config/path | InMemory 或 SQLite backend | 未配置 path 时默认 InMemory；有全局/显式 storage path 时可用 SQLite | [`provenance/storage.py`](semantica/semantica/provenance/storage.py) |

### 15.2 通用版本、快照与 Delta

| 功能 | 背景/功能 | 输入 | 输出 | 关键行为/限制 | 源码 |
|---|---|---|---|---|---|
| `TemporalVersionManager` | 给图/数据状态做不可混淆的时间版本 | state/graph、label/tags/time/metadata | snapshot/version ID + checksum | 版本与 provenance 互补：版本记录状态，provenance 记录来源与活动 | [`change_management/version_manager.py`](semantica/semantica/change_management/) |
| Snapshot checksum | 检测版本内容变化/损坏 | snapshot data | checksum/verification result | 序列化一致性影响 hash | 同上 |
| Compare/diff | 计算两个版本新增、删除、变化 | base/target version IDs | diff/delta/report | 可供 pipeline delta mode 使用 | 同上 |
| Attach graph | 将 version 绑定到 RDF named graph/图 URI | version ID、graph URI | updated version metadata | TripletStore delta 依赖 graph URI | 同上 |
| Mutation history/tags | 按变更或发布标签组织版本 | operation/tag/filter | history/version list | 便于审计/发布 | 同上 |
| Prune | 清理旧版本 | retention/tag policy | removed count/report | 是破坏性存储操作，应保留受保护 tag | 同上 |
| Restore | 恢复指定快照 | version ID、confirmation/config | restored state/status | 源码含确认语义；恢复前先 compare | 同上 |
| Storage backends | 版本元数据与快照持久化 | InMemory/SQLite config | version storage | Base、InMemory、SQLite 实现 | [`change_management/storage.py`](semantica/semantica/change_management/) |

## 16. Pipeline 编排与执行

### 16.1 正确的构建和执行模型

| 功能/类型 | 背景/功能 | 输入 | 输出 | 真实源码行为 | 源码 |
|---|---|---|---|---|---|
| `PipelineBuilder` | 用 DAG 描述多个处理步骤及依赖 | name/config | builder | 正确入口；不要直接对 `Pipeline` 调 `.add_step()` | [`pipeline/builder.py`](semantica/semantica/pipeline/) |
| `add_step` | 注册步骤及其真正执行 handler | `name/step_type/config/dependencies/handler/delta_mode/...` | **`PipelineStep`** | 返回 step，不是 builder，所以不能在其后链式 `.add_step()` | 同上 |
| `connect_steps` | 给已有步骤增加有向依赖 | from/to step names | builder | 可链式调用；build 时验证 cycle/missing dependency | 同上 |
| `set_parallelism` | 开启按依赖层并行执行 | worker count/parallelism | builder | 当前提交修复后会真正启用 dependency-layer parallel execution | 同上 |
| `build` | 冻结构建结果 | builder 当前状态 | `Pipeline` dataclass | `Pipeline` 是数据对象，没有文档示例中的 `.run()` | 同上 |
| `PipelineStep` | 执行节点 | builder 创建/手工构造 | step object | 字段：`name/step_type/config/dependencies/handler/status/result/error/delta_mode/base_version_id/target_version_id/parallel_safe` | [`pipeline/models.py`](semantica/semantica/pipeline/) |
| `ExecutionEngine.execute_pipeline` | 按拓扑层执行 pipeline | `Pipeline`、input data、execution config/context | `ExecutionResult` | `ExecutionResult` 字段：`success/output/metadata/metrics/errors` | [`pipeline/execution.py`](semantica/semantica/pipeline/) |
| Step handler | 实现 ingest/parse/extract/store 等业务逻辑 | 上游输出、step config/context（按 handler 约定） | 下一步数据 | **handler 为空时 `_execute_step` 直接返回输入不做任何处理** | 同上 |
| Parallel layer merge | 并发运行同一依赖层多个安全步骤 | input **dict**、该层全部 `parallel_safe=True`、parallelism>1 | 合并后的 dict | 任一步输出不是 dict 时无法按并行分支语义合并；有不安全 step 则该层顺序执行 | 同上 |
| Retry/failure | 控制暂时错误和失败策略 | retry count/backoff/failure policy | 重试结果/errors/status | handler 应尽量幂等 | 同上 |
| Resource scheduler | 按资源约束调度步骤 | step resource requirements、limits | schedule/metrics | 控制并发而非业务正确性 | [`pipeline/scheduler.py`](semantica/semantica/pipeline/) |
| Pipeline validator | 在执行前检查步骤、依赖和配置 | pipeline | validation report/errors | 能发现 cycle/missing step 等结构问题 | [`pipeline/validator.py`](semantica/semantica/pipeline/) |
| Pipeline templates | 提供常见 DAG 骨架 | template name + config | `Pipeline`/step definitions | 模板包括 document-processing、RAG、KG-construction、ontology-generation；模板只描述步骤，仍需注册/绑定 handlers | [`pipeline/templates.py`](semantica/semantica/pipeline/) |
| Delta mode | 只处理两个版本间增删变化 | step 开启 delta、base/target version IDs，engine 注入 `version_manager` 与 `triplet_store` | delta + handler result | snapshot 必须带 graph URI；由 TripletStore `compute_delta` 计算 named-graph 差异 | 同上 |

### 16.2 源码一致的最小执行顺序

| 步骤 | 应做什么 | 输入 | 输出 |
|---:|---|---|---|
| 1 | 创建 `PipelineBuilder` | pipeline name/config | builder |
| 2 | 分别定义 ingest、parse、normalize、split、extract、validate、store handlers | Python callables | handler references |
| 3 | 用 `add_step(..., handler=...)` 添加每个节点 | name/type/handler/dependencies | `PipelineStep`；保留 builder 变量继续添加 |
| 4 | 用 dependencies 或 `connect_steps` 明确 DAG | step names | 更新后的 builder |
| 5 | 仅对真正线程安全/无共享写冲突的步骤设置 `parallel_safe=True`，再 `set_parallelism(n)` | parallel config | builder |
| 6 | `pipeline = builder.build()` 并运行 validator | builder | `Pipeline` + validation result |
| 7 | `ExecutionEngine(...).execute_pipeline(pipeline, input_data)` | pipeline、初始字典 | `ExecutionResult` |
| 8 | 只在 `result.success` 且验证通过后提交 store/version/provenance | execution output | 持久化 ID、snapshot、audit records |

## 17. 导出、报告与可视化

### 17.1 Export

| 功能 | 背景/功能 | 输入 | 输出 | 格式/注意事项 | 源码 |
|---|---|---|---|---|---|
| RDF export | 将图/三元组输出为语义网格式 | entities/relationships/triplets、namespace/path | serialized string 或 file | Turtle、RDF/XML、JSON-LD、N-Triples、N3；支持 temporal/SHACL 数据 | [`export/rdf.py`](semantica/semantica/export/) |
| JSON/JSON-LD | 面向 Web/API/通用数据交换 | graph/data、indent/context/path | JSON string/file | JSON-LD 可保留语义 context | [`export/json.py`](semantica/semantica/export/) |
| CSV | 给分析工具/批量导入输出平面表 | graph/records、output dir | entities CSV + relationships CSV | 图通常拆为两个文件 | [`export/csv.py`](semantica/semantica/export/) |
| Arrow IPC | 高效列式进程间/跨语言交换 | entities/relationships/table data | 分开的 Arrow files | compression：lz4/zstd | [`export/arrow.py`](semantica/semantica/export/) |
| Parquet | 分析湖/数据仓库列式文件 | entities/relationships/table data | 分开的 Parquet files | snappy/gzip/brotli/zstd/lz4 | [`export/parquet.py`](semantica/semantica/export/) |
| Graph formats | 用图工具直接打开 | graph、path/config | JSON/GraphML/GEXF/DOT file/string | 属性类型要适配目标格式 | [`export/graph.py`](semantica/semantica/export/) |
| OWL | 交付正式本体序列化 | ontology、format/path | OWL XML/Turtle file/string | 与 Ontology Engine 导出对接 | [`export/owl.py`](semantica/semantica/export/) |
| LPG/Cypher | 把图变成属性图建库脚本 | graph、labels/property mapping | Cypher/script | 支持 Neo4j 等 LPG | [`export/lpg.py`](semantica/semantica/export/) |
| Neo4j bulk | 大图批量导入 Neo4j | graph、output dir、mapping/config | nodes/relationships CSV + commands/report | 支持 validation/dry-run；真正导入会改外部数据库 | [`export/neo4j.py`](semantica/semantica/export/) |
| Arango | 输出 ArangoDB AQL/导入结构 | graph、collection mapping | AQL/file/report | 节点/边 collection 要一致 | [`export/arango.py`](semantica/semantica/export/) |
| YAML semantic | 人可读的 semantic network/schema/triplets | network/schema/triplets | YAML string/file | 适合配置与审阅 | [`export/yaml.py`](semantica/semantica/export/) |
| Vector export | 迁移向量和 metadata | vectors/IDs/metadata | JSON、NumPy、binary、FAISS 或目标 vector-store | 必须保留 dimension/model/metric 元数据 | [`export/vector.py`](semantica/semantica/export/) |
| Distance export | 导出相似度/距离矩阵 | matrix/items/labels | CSV、JSONL、DataFrame 或 string | 标明 metric 与“越大/越小越相似” | [`export/distance.py`](semantica/semantica/export/) |
| Report export | 生成审计、质量或分析报告 | report data/template/config | Markdown、HTML、JSON、text | 适合 QA/交付 | [`export/report.py`](semantica/semantica/export/) |

### 17.2 Visualization

| Visualizer | 背景/功能 | 输入 | 输出 | 主要视图 | 源码 |
|---|---|---|---|---|---|
| `KGVisualizer` | 人工检查图结构和群组 | graph、layout/style/filter | Figure/Plot/HTML/file（依后端） | network、communities、centrality、entity types、relationship matrix | [`visualization/kg.py`](semantica/semantica/visualization/) |
| `OntologyVisualizer` | 检查类层级、属性和语义模型 | ontology、layout/style | Figure/HTML/file | hierarchy、properties、structure、class-property、metrics、semantic model | [`visualization/ontology.py`](semantica/semantica/visualization/) |
| `EmbeddingVisualizer` | 降维观察向量分布、簇和异常 | vectors、labels/metadata、reducer | 2D/3D plot/heatmap | projection、clustering、heatmap、multimodal view | [`visualization/embedding.py`](semantica/semantica/visualization/) |
| `SemanticNetworkVisualizer` | 展示语义节点和边类型 | `SemanticNetwork`、style/layout | network plot/HTML | network、node types、edge types | [`visualization/semantic_network.py`](semantica/semantica/visualization/) |
| `AnalyticsVisualizer` | 将图指标和对比变成人可读图表 | analytics results/graphs | charts/dashboard | centrality、rankings、community、connectivity、degree distribution、comparison/dashboard | [`visualization/analytics.py`](semantica/semantica/visualization/) |
| `TemporalVisualizer` | 观察图随时间变化 | snapshots/deltas/temporal facts | timeline/dashboard/animation-like plots | evolution、timeline、pattern、snapshot、version、temporal metrics | [`visualization/temporal.py`](semantica/semantica/visualization/) |

## 18. CLI、HTTP、Explorer、MCP 与框架集成

### 18.1 CLI 命令族

| 命令/命令组 | 背景/功能 | 主要输入 | 主要输出/效果 |
|---|---|---|---|
| `info` / `doctor` / `changelog` / `shell` | 查看环境、诊断依赖、版本变化和交互 shell | flags、环境 | 终端报告/交互会话 |
| `init` / `config` | 初始化项目/配置入口 | path/template/settings | 配置/目录/提示 |
| `watch` | 监听来源变化并触发处理 | path/pattern/pipeline config | 持续事件/处理日志 |
| `ingest` | 调用 ingestion | source/type/options | JSON/文件/摘要 |
| `parse` | 调用 parser | input/format/options | parsed JSON/text/file |
| `split` | 调用 splitter | input/strategy/size/overlap | chunks |
| `normalize` | 文本/实体/日期/数值标准化 | input/type/options | normalized output |
| `extract` | 实体、关系、三元组等抽取 | text/file/method/provider | extracted JSON/file |
| `deduplicate` | 重复检测、聚类与合并 | input/threshold/strategy | groups/merged result |
| `kg build/query/stats/analyze/find-path/resolve/predict/validate` | 构图、查询、分析、解析、预测和验证 | graph/data/query/node IDs/config | graph/results/metrics/report |
| `embed generate/search/index` | 生成向量、搜索、建 index | text/files/model/store config | vectors/results/index |
| `reason run/explain/query/list` | 执行规则、解释与查询 | facts/rules/query | inferences/explanation |
| `decision record/list/query/trace/similar/impact/check` | 管理决策、先例、因果与合规 | decision/query/ID/policy | IDs/lists/chains/report |
| `temporal snapshot/query/history/distance/allen` | 时态快照、查询、历史、距离与 Allen 关系 | graph/time/intervals | snapshot/diff/relations |
| `provenance lineage/audit/export/check/invalidate/verify-chain/descendants` | 溯源和完整性 | IDs/time/path/reason | lineage/report/file/status |
| `validate shacl/conflicts/integrity` | 结构、约束、冲突和完整性验证 | graph/shapes/rules | validation report |
| `ontology generate/import/validate/shacl/...` | 本体生成、导入、约束、SKOS、对齐、健康和版本 | data/ontology/query/config | ontology/report/mappings/version |
| `visualize ...` | 按 visualizer 动态提供绘图命令 | graph/ontology/vectors/style/path | HTML/image/interactive output |
| `pipeline init/validate/run/status/stop` | 管理流水线定义和运行 | config/input/run ID | files/status/result |
| `store list/connect/stats/migrate/flush` | 管理 store 连接和迁移 | backend/config/source/target | connection/stats/migration/status |
| `backup info/create/sync/restore/schedule` | 备份与恢复流程 | target/path/schedule | backup metadata/files/status |
| `server start/stop/status` | 管理 HTTP server | host/port/config | process/status |
| `explorer start/stop/status/open` | 管理 Explorer | graph/host/port/browser flags | process/status/browser page |
| `mcp start/stop/status/list-tools/call` | 管理 MCP server 和工具调用 | transport/tool/args | JSON-RPC/tool result |
| `export` / `completion` | 导出数据或生成 shell completion | format/path/shell | file/script |

> CLI 注册以 [`cli.py`](semantica/semantica/cli.py) 下 decorator/command 定义为准。源码仓库未安装依赖时直接运行 CLI 会缺少如 `yaml` 的基础依赖；这不等同于安装后的包不可用。

### 18.2 HTTP Server 与 Explorer

| 功能 | 背景/功能 | 输入 | 输出 | 真实状态/限制 | 源码 |
|---|---|---|---|---|---|
| Core server `/api/info`、`/health` | 提供服务元信息和健康检查 | HTTP GET | JSON | 可用于 readiness/info | [`server.py`](semantica/semantica/server.py) |
| Core server `/build` | 设计上提交 KB 构建任务 | HTTP request body | accepted/status JSON | **实际 KB 构建调用在源码中被注释，当前只是 accepted/stub，不会完成知识库构建** | 同上 |
| Worker | 设计上处理后台任务 | worker config/环境 | 日志/进程 | 当前 `worker.py` 是轮询 sleep 骨架，没有真实 queue/task processing | [`worker.py`](semantica/semantica/worker.py) |
| Explorer start | 在 Web UI 中交互探索已有图 | `--graph` 指向 ContextGraph JSON、host/port、`--no-browser` | FastAPI + Web UI | 入口要求已有 graph 文件；GraphSession 载入后服务 | [`explorer`](semantica/semantica/explorer/) |
| Explorer graph APIs | 查节点、边、邻居、路径、搜索与距离 | HTTP path/query/body | JSON nodes/edges/paths/matrix/stats | 路由还含 semantic neighborhood | 同上 |
| Analytics/validation APIs | 在线计算图分析和质量 | graph session + options | metrics/validation report | 大图应考虑请求耗时 | 同上 |
| Decision APIs | 决策、chain、precedent、causal distance、compliance | decision ID/query/body | JSON decisions/paths/report | 依图中已有决策数据 | 同上 |
| Temporal APIs | snapshot、diff、patterns、bounds、distance history | timestamps/IDs/options | temporal JSON | 支持探索时态状态 | 同上 |
| Enrichment APIs | 抽取、link prediction、dedup、reason、merge | text/nodes/config | candidates/enriched graph/result | 会修改 session 的操作应配版本/审核 | 同上 |
| Import/export APIs | 向 Explorer 会话导入或导出图 | file/body/format | session update/file/JSON | 格式范围依 exporter/importer | 同上 |
| Annotation APIs | 给节点/边添加人工注释 | target ID、annotation | saved annotation/status | 可支撑人工审阅 | 同上 |
| SPARQL APIs | 对 RDF/语义数据执行查询 | SPARQL | result rows/graph | 取决于配置的 triplet graph/store | 同上 |
| Provenance/report APIs | 查询 lineage、生成 report | resource ID/scope | provenance/report JSON/file | 与全局 ProvenanceManager 对接 | 同上 |
| Vocabulary APIs | 管理 scheme、concept、hierarchy 和导入 | scheme/concept/query/file | SKOS result | 对接 Ontology SKOS | 同上 |
| Ontology registry APIs | registry、preview、load/create/search、entity、SKOS、alignment、health、SHACL、draft/proposal/comment/version | ontology IDs/body/query | ontology/validation/collaboration result | Explorer 暴露的本体协作面最完整 | 同上 |
| WebSocket | 推送图更新 | `/ws/graph-updates` connection | update events | 客户端需处理重连和事件顺序 | 同上 |
| API auth/CORS | 避免 Explorer 默认裸奔 | `SEMANTICA_API_KEY` header/env；或 `SEMANTICA_ALLOW_ANONYMOUS=true` | authenticated response | 默认要求 API key；匿名必须显式开启；CORS 有限制 | 同上 |

### 18.3 MCP

| 功能 | 背景/功能 | 输入 | 输出 | 真实入口/差异 | 源码 |
|---|---|---|---|---|---|
| Installed MCP server | 让 MCP 客户端通过 stdio 调 Semantica | JSON-RPC/MCP tool/resource calls | MCP tool result/resource content | `semantica-mcp` entry point 指向 [`semantica.mcp_server`](semantica/semantica/mcp_server/) | [`mcp_server`](semantica/semantica/mcp_server/) |
| Extraction tools | 从文本抽实体和关系 | text/method/config | entities/relations JSON | tools：`extract_entities`、`extract_relations` | 同上 |
| Decision tools | 记录/查询决策、查先例和因果链 | decision/query/ID | IDs/decisions/chains | `record_decision`、`query_decisions`、`find_precedents`、`get_causal_chain` | 同上 |
| Graph mutation/query | 添加/更新/删除/查询节点和关系 | entity/relationship/query/node ID | graph object/status/result | `add_entity`、`add_relationship`、`query_graph`、`update_node`、`delete_node` | 同上 |
| Reasoning/analytics | 执行推理和图分析 | facts/rules/query/metric | inference/analytics | `run_reasoning`、`get_graph_analytics` | 同上 |
| Export/summary | 导出或概览当前图 | format/path/options | file/string/summary | `export_graph`、`get_graph_summary` | 同上 |
| Resources | 以 MCP resource 读取稳定上下文 | resource URI | resource content | 3 类：graph summary、decisions、schema | 同上 |
| Separate top-level `mcp/` package | 仓库另有一套 MCP server/tool 实现 | MCP calls | MCP results | 还包含 `abductive_reasoning`、`extract_all`、`get_provenance`、`analyze_decision_impact` 等，但**不是 pyproject 安装 entry point 指向的服务器**；不要混用工具清单 | [`mcp`](semantica/mcp/) |

### 18.4 Agent 框架集成

| 集成 | 背景/功能 | 输入 | 输出/用途 | 源码 |
|---|---|---|---|---|
| Agno | 把 Semantica 作为 context、decision/KG tools 和共享上下文 | Agno agent、store/graph/config | `AgnoContextStore`、`DecisionKit`、`KGToolkit`、`KnowledgeGraph`、`SharedContext` | [`integrations/agno`](semantica/integrations/agno/) |
| CrewAI | 将决策和 KG 暴露为 CrewAI tool/knowledge source | Crew/agent、Semantica context/graph | `SemanticaDecisionTool`、`SemanticaKGTool`、`SemanticaKnowledgeSource` | [`integrations/crewai`](semantica/integrations/crewai/) |
| LangChain | 适配 Retriever、Tool 和 VectorStore 接口 | LangChain docs/query、Semantica store/context | retriever docs、KG/Decision tool result、VectorStore adapter | [`integrations/langchain`](semantica/integrations/langchain/) |
| OpenClaw | 用 MCP 配置和 tool 接入 OpenClaw | MCP/server config、tool args | MCP tool/config | [`integrations/openclaw`](semantica/integrations/openclaw/) |

## 19. 其他公共模块

| 模块/功能 | 背景/功能 | 输入 | 输出 | 关键事实 | 源码 |
|---|---|---|---|---|---|
| `semantica.seed.SeedDataManager` | 从 CSV/JSON/DB/API 登记和加载受控种子数据，构造 foundation graph，并与抽取结果融合 | files/records/DB/API/config | registered seed data、foundation graph、integration/quality report、export | 顶层导出名是 `SeedDataManager`，不是 docs 的 `SeedManager`；与 `semantica.kg.SeedManager` 不同 | [`seed`](semantica/semantica/seed/) |
| Seed quality | 检查种子覆盖、重复、缺失和一致性 | seed dataset、rules/schema | quality/validation report | 种子质量直接影响 entity resolution | 同上 |
| Utilities validators | 通用数据、配置、schema、实体、关系、文件、URL、email 校验 | value/object/config | bool/result/errors | 被各模块复用 | [`utils/validators.py`](semantica/semantica/utils/validators.py) |
| Utilities helpers | 文件/数据/时间/哈希/chunk/retry/import 工具 | 各类基础值 | transformed value/status | retry 适合可重试 I/O，不应用来掩盖确定性校验错误 | [`utils/helpers.py`](semantica/semantica/utils/helpers.py) |
| Progress display | 在 console/Jupyter/file 输出进度 | iterable/task/status | progress UI/log | 展示层，不影响执行语义 | [`utils/progress.py`](semantica/semantica/utils/) |
| `semantica.evals` | 文档宣称用于 KG/抽取评估 | — | — | 当前 [`evals/__init__.py`](semantica/semantica/evals/__init__.py) 为空，没有文档提到的 `KGEvaluator` 等实现；应视为未提供而不是隐藏功能 | 同上 |

## 20. README/Docs 与当前源码不一致清单

| 主题 | 文档/直觉容易得出的结论 | 当前源码事实 | 正确做法/风险 |
|---|---|---|---|
| 顶层配置 | `Semantica(config_path="...")` 会加载配置文件 | constructor 只接 `Config\|dict=None, **kwargs`，没有 config-path loader 语义 | 先自行读取 YAML/JSON 并构造 dict/Config，再传入 |
| 一键 KB | `build_knowledge_base` 默认完成 ingest→parse→KG→embedding | 默认 pipeline 步骤没有 handler，只回传输入；构图代码又从不匹配的结果层取字段，通常产生空图/空向量 | 生产使用显式 PipelineBuilder handlers，或逐模块调用 |
| Pipeline API | `Pipeline().add_step(...).run()` | `Pipeline` 是 dataclass，无这些方法 | `PipelineBuilder.add_step` → `build` → `ExecutionEngine.execute_pipeline` |
| Pipeline 链式调用 | `builder.add_step(...).add_step(...)` | `add_step` 返回 `PipelineStep` | 每次对保存的 builder 变量调用 `add_step` |
| Pipeline 模板 | 模板拿来即可运行 | 模板主要生成 step/DAG 描述，未自动提供业务 handlers | 注册 handlers 后再执行 |
| 并行 | 设置 parallelism 就能并发所有步骤 | 仅整个 dependency layer 都 `parallel_safe`、输入为 dict、parallelism>1 才并行；输出需 dict 才能合并 | 明确标安全步骤并验证合并字段冲突 |
| Vector API | `.add_vectors(...)` | 公开调用是 `store_vectors`/`store` 或 `add_documents` | 按是否已有 embedding 选择写入方法 |
| Vector 降级 | embedding 失败会显式失败 | `VectorStore.embed` 有随机向量 fallback | 显式提供可靠 embedder并监控失败；不要让随机向量进入生产索引 |
| Text embedding 降级 | 本地模型不存在时仍有语义向量 | `TextEmbedder` 最后会生成确定性 hash 向量 | 只用于测试稳定性；生产安装 FastEmbed/ST 模型 |
| 多模态 embedding | generator 已原生生成图像/音频向量 | 核心 `EmbeddingGenerator` 当前实质为文本输入路径 | 图像/音频需独立模型和适配层 |
| Triplet API | `store.sparql(query)` | 方法名为 `execute_query` | 用 `execute_query` |
| Context 构造 | `AgentContext()` 可无参数使用 | `vector_store` 是必需项 | 先创建并传入 VectorStore；GraphRAG/decision 再加图 |
| Context 查询 | `.query(mode="graphrag")` | 真实入口是 retrieve/相应 reasoning/context methods | 按源码方法选择 retrieve 或 query-with-reasoning |
| Entity resolver import | `from semantica.deduplication import EntityResolver` | dedup 包导出 DuplicateDetector/EntityMerger；EntityResolver 在 KG 包 | 从 `semantica.kg`/具体子模块导入 |
| Datalog | 类名是 `DatalogEngine` | 导出 `DatalogReasoner` | 使用真实类名 |
| Reasoner helpers | 存在 `.apply_transitivity/.apply_symmetry/.infer` 文档式便捷 API | 当前 Reasoner 接口围绕 facts、rules、chaining/query | 将传递/对称写成规则并执行对应推理 |
| Abductive/Deductive export | `from semantica.reasoning import ...` 全部可用 | 两个实现文件存在，但没有在 reasoning 包顶层 re-export | 从具体 `reasoning.abductive`/`reasoning.deductive` 导入，或自行补 export |
| Seed | `semantica.seed.SeedManager` | 实际导出 `SeedDataManager`；KG 内另有 `SeedManager` | 按用途选择，避免同名假设 |
| Evals | 已有 `KGEvaluator` 等评估器 | 当前 evals 包为空 | 自建评估或等待实现，不能按 docs import |
| REST build | `/build` 会后台完成 KB | 当前调用注释掉，只返回 accepted | 不要把 accepted 当任务完成；需补真实任务队列和状态机 |
| Worker | 已能消费后台任务 | 仅轮询 sleep 骨架 | 部署前实现 queue/claim/execute/ack/retry |
| MCP tools | 两套目录里的 tools 都由 `semantica-mcp` 暴露 | 安装入口只指向 `semantica/mcp_server.py` 的 15 tools | 客户端按运行中的 server `tools/list` 为准 |
| Explorer “100+ API” | core server 自动拥有全部接口 | 大量路由属于 Explorer 挂载面；core server 自身非常薄 | 安装 Explorer 依赖并按 Explorer 入口启动、验证 auth |
| Optional parsers | import 成功即代表底层能力可用 | 如 Docling 可导出占位类，调用时才提示依赖缺失 | 启动时做 capability/doctor 检查 |

## 21. 建议的生产落地顺序

| 阶段 | 必做动作 | 验收输出 | 不建议省略的检查 |
|---:|---|---|---|
| 1 | 锁定提交、Python 和 extras，配置密钥/连接 | 可重复安装环境、capability report | `doctor`/显式 import、provider/store connectivity |
| 2 | 选一个小型真实数据集走 ingest + parse | 带 source metadata 的解析对象 | 编码、格式、失败样本、附件/表格覆盖 |
| 3 | normalize + structural/semantic split | 带原文 span 和 source ID 的 chunks | chunk 长度分布、重叠、标题/表格保持 |
| 4 | 组合本地规则/模型与 LLM 抽取 | Entity/Relation/Triplet/Event + confidence | 抽取 validator、人工标注小金集 |
| 5 | 去重、entity resolution、冲突分析 | canonical entities、merge/conflict report | 来源、别名、被合并 ID、人工 review queue |
| 6 | GraphBuilder + GraphValidator | 无悬空边/重复 ID 的标准图 | schema、cycle/self-loop/orphan policy |
| 7 | 设计/复用 ontology，生成 SHACL 并验证 | ontology、shapes、conformance report | competency questions、alignment 人审 |
| 8 | 仅在验证图上运行 reasoning | inferred facts + explanation | 规则终止性、置信度、结论 provenance |
| 9 | 使用确定的模型生成 embeddings，再写 stores | 可重建 index、Graph/RDF store IDs | 记录 model/version/dimension/metric；禁用随机/哈希生产降级 |
| 10 | 启用 AgentContext/decision/policy | 检索评测、decision/causal/policy results | 权限、PII、删除/保留策略、先例误用 |
| 11 | 在每一步记录 provenance，并做 snapshots/delta | lineage、hash-chain、version diff | restore 演练、named graph/version 绑定 |
| 12 | 将上述调用绑定到有 handler 的 PipelineBuilder | 通过验证的 `ExecutionResult` | retry 幂等性、并行安全、失败补偿 |
| 13 | 再开放 export/Explorer/MCP/API | 可观测、受认证的服务 | API key、CORS、限流、任务状态；不要依赖 stub `/build`/worker |

## 22. 一句话结论

| 判断维度 | 源码审阅结论 |
|---|---|
| 能力广度 | Semantica 已覆盖从多源摄取到解析、语义抽取、图/本体、推理、三类存储、Agent 决策、溯源、时态、导出和 Explorer/MCP 的完整语义数据栈。 |
| 成熟度分布 | 核心数据模型、抽取/KG/ontology/store/context 能力较丰富；但顶层“一键构建”、REST `/build`、worker、evals 和部分多模态描述仍未形成闭环。 |
| 最稳妥用法 | 把它当作一组可组合模块：逐模块实例化、显式注入模型/存储、使用 `PipelineBuilder + handler + ExecutionEngine` 编排，并用 validator/provenance/version 兜底。 |
| 最大风险 | 直接照 README 的高层示例运行，可能遇到接口名漂移、无 handler 空执行、可选依赖占位或 embedding 静默降级。 |
