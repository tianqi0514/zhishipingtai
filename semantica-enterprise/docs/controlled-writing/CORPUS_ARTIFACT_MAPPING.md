# 语料包产物映射

| 规范产物 | 当前权威副本 | 投影或快照 | Agent 读取方式 |
|---|---|---|---|
| manifest、meta、signature | PostgreSQL `writing_corpus_packages*` | manifest JSON | corpus manifest/artifacts 工具 |
| provenance、evidence | PostgreSQL `writing_evidence` | 无独立当前值副本 | Evidence 工具 |
| outline、style profile、section templates | `writing_corpus_package_versions` | 不可变版本 JSON | corpus artifacts 工具 |
| normalized source、assets | MinIO 文档对象 | 文档版本引用 | 受权限保护的片段/对象接口 |
| chunks、offset map | PostgreSQL `chunks`、`content_elements` | OpenSearch/Qdrant 投影 | 检索工具 |
| skeletons | `writing_corpus_package_versions.skeletons` | 不可变脱敏骨架 | corpus artifacts 工具 |
| graph nodes、relations、claims | 写作图谱 PostgreSQL 表 | FalkorDB 发布投影 | 写作图谱工具 |
| rules、derivation | 规则版本、推演运行表 | 图谱推导投影 | 推演解释工具 |
| decisions、conflicts、gaps | 治理/对齐表 | 审计投影 | 治理和继承工具 |
| gate report | 写作生成运行质量报告 | 无重复权威副本 | validate 工具 |
| node/term index | FalkorDB / OpenSearch | 可重建索引 | 搜索和路径工具 |

实现原则是一项权威事实只有一个当前版本；语料包只保存不可变快照和引用，不复制可独立修改的“当前值”。未治理样稿中的所有数字会替换为稳定槽位，之后只能从当前项目 Fact/MetricValue 填充。
