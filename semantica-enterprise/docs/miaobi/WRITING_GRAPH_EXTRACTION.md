# 写作图谱抽取

## 1. 处理阶段

### 阶段 A：确定性 Evidence

输入为已解析 DocumentVersion、ContentElement 和 Chunk。服务端生成 Evidence ID，保存定位、原文、前后文和内容哈希。此阶段不调用模型。

### 阶段 B：联合抽取

按可控 Token 大小将相邻 Evidence 组成批次，一次模型调用返回：

- entity_mentions；
- claims；
- metric_mentions；
- relation_hints；
- time_scope；
- applicable_scope；
- 每个结果引用的 evidence_ids。

输出使用 Pydantic 严格模型并设置 `extra="forbid"`。提示词只能引用本批次签发的 Evidence ID；返回其他 ID 时整项拒绝。

### 阶段 C：确定性归一和候选生成

服务端完成：

- 字符和空白归一；
- 实体名称、别名和类型候选去重；
- 单位标准化；
- 时间范围解析；
- Claim 主体和客体引用校验；
- 数值 Claim 转 Fact 候选；
- 关系型 Fact 转 Relation 候选；
- 相同范围事实去重；
- 冲突识别；
- 来源权限和文档版本校验。

### 阶段 D：疑难补抽

只对主体/客体不明确、实体重名、缺失单位、时间不清、Claim 无法结构化或关系歧义的项目补抽。补抽结果仍经过相同 Schema 和 Evidence 校验。

## 2. 材料用途约束

| 用途 | Evidence | Entity/Claim/Fact/Relation |
|---|---|---|
| 本次业务资料 | 完整 | 完整候选 |
| 政策与制度依据 | 完整 | 完整候选，保留有效期和适用范围 |
| 参考材料 | 完整 | 候选默认待核验 |
| 样稿与格式 | 完整 | 只抽文体、目录、章节职责、表格和版式，不产生本次业务 Fact |
| 报告附件 | 完整 | 默认待核验，按用户选择决定是否进入候选 |

## 3. 模型边界

模型可以：识别实体提及、陈述、数值提及、关系线索和范围。

模型不能：生成 Evidence、分配正式实体 ID、把 Fact 标为 verified、决定当前有效版本、执行正式计算、发布图谱或修改正文。

## 4. Semantica 复用

继续使用 Semantica 的 Parser、ContentElement、Splitter、LLM Provider、Normalize、Provenance 和图谱适配边界。写作五层是平台侧语义契约，不修改 Semantica 核心抽取和 Datalog 算法。

## 5. 幂等与重试

幂等键包含：文档版本、Evidence 批次哈希、抽取策略版本、模型配置 ID 和 Prompt Schema 版本。只有超时、限流和临时服务错误可以重试；Schema 错误进入失败详情，不进行无条件模型循环。

## 6. 可观察性

任务阶段显示真实状态：生成来源依据、联合抽取、确定性校验、冲突识别、等待治理。记录批次数、模型调用数、Token、耗时、候选数、拒绝数、补抽数和错误类型，不记录 API Key 或完整敏感原文。
