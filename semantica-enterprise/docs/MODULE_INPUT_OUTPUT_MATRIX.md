# 模块输入输出矩阵

| 模块 | 主要用户 | 输入 | 真实输出 | 主要下游 | 验收证据 |
|---|---|---|---|---|---|
| 组织机构 | 系统管理员 | 上级组织、名称、编码 | `OrgUnit` 层级 | 用户、应用、审计 | 组织树与外键 |
| 用户与角色 | 系统管理员 | 账号、组织、角色 | `User/UserRole` | 登录、菜单、空间权限 | 不同账号实测 |
| 空间与授权 | 知识管理员 | 空间、用户/角色、权限 | `KnowledgeSpace/SpaceGrant` | 文档、搜索、图谱、会话 | 越权 403/404 |
| 模型服务 | 系统管理员 | Provider、模型、Secret | 加密 `ModelConfig` 和连接状态 | 治理、Embedding、Agent、OCR/ASR | 真实最小请求 |
| 数据接入 | 知识维护人员 | 连接配置、Secret、游标 | `SourceConnector`、同步 `Job` | 文档版本 | 同步结果与 Audit |
| 文档资产 | 知识维护人员 | 文件/同步载荷 | `Document/DocumentVersion` | 解析和治理 | MinIO 与版本 Hash |
| Semantica 解析 | Worker | 当前文档版本、解析策略 | `ContentElement`、解析摘要 | Chunk、画像、抽取 | 解析器/页码/结构路径 |
| 知识加工 | Worker | Element、切片/抽取策略 | Chunk、实体、事实、事件 | 索引、图谱、治理 | Stable ID、Hash、Provenance |
| 自动治理 | Worker | 文档内容、治理策略、模型 | `DocumentProfile`、质量问题 | 人工治理、搜索标签 | 确定性与模型结果分离 |
| 人工治理 | 知识管理员 | 自动结果、人工决定 | Overlay、Decision、KnowledgeRelease | 当前检索、图谱、应用 | 影响预览/发布/回滚 |
| 本体 | 数据/知识管理员 | 类、属性、关系词条 | `Ontology/OntologyTerm` | 数据库映射、图谱 | 版本和引用 |
| Schema 发现 | 数据管理员 | 只读数据源 | Schema Version、Fingerprint、Diff | 预览、映射、漂移 | MySQL/PG 真实 Catalog |
| 语义映射 | 数据管理员 | Schema、本体、建议 | Mapping Version | Plan/IR、物化 | 验证/激活/回滚 |
| 结构化查询 | 员工/Agent | 问题、Plan、IR | 参数化只读结果、QueryRun | 回答与数据引用 | 标准数值比对 |
| 混合检索 | 员工/Agent | Query、空间、通道 | QueryRun、排序 Chunk | 问答、MCP、CLI | 通道分、RRF、权限回查 |
| 智能问答 | 普通员工 | 消息、历史、空间 | DSH Session Event、回答、引用 | 反馈、审计 | 多 Step/SSE/恢复 |
| 知识图谱 | 分析人员 | 当前 GraphRelease、编辑 | 实体、事实、关系发布 | 图谱检索、Analyze | FalkorDB 与来源片段 |
| 知识分析 | 分析人员 | 规则、场景、事实 | 推理事实、证据、发布/回滚 | 图谱、问答、应用 | Datalog/SPARQL/Provenance |
| 服务开放 | 应用开发人员 | 用户 Token、调用参数 | REST/MCP/CLI 结果 | 外部应用 | 三种真实调用 |
| 知识供给 | 应用负责人 | 空间当前 Release | 不可变 Product Release/Alias | 能力场景 | Manifest/Checksum |
| 能力场景 | 应用负责人 | 知识供给、模型、工具、策略 | 不可变 Scenario Version | 运行与评测 | 版本/授权 |
| 上线测试 | 应用负责人 | 测试集、期望证据、门禁 | Evaluation Run/Case Result | 接入发布 | Recall/MRR/NDCG |
| 接入发布 | 应用管理员 | Grant、Credential | 短期应用 JWT | 外部调用 | 撤销即时失效 |
| 运行反馈 | 业务用户 | 评分、类型、证据 | Feedback/CurationCase | 人工治理、重新评测 | 完整闭环 |
| 审计日志 | 审计人员 | 所有状态变更 | `AuditEvent`/Invocation/QueryRun | 追责与合规 | 人、时间、对象、动作 |
