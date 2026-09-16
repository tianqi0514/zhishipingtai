# 文章 Chunk 绑定

Plate 正文不是一个大 HTML 字符串。标题、段落、列表项和表格都具有稳定 `chunk_id/block_id`，版本保存时投影为不可变 WritingChunk，并通过 WritingChunkDependency 记录依赖。

一个正文 Chunk 可以绑定多个 Fact、Evidence、Relation、ComputationRun 和公开参考；同一 Fact 也可以影响摘要、正文、表格和附件中的多个 Chunk。

服务端保存绑定时校验：

1. block ID、类型、内容和内容哈希与当前文稿一致；
2. ProjectFact 和 ComputationRun 属于当前项目；
3. 写作图谱对象属于本文固定的 WritingGraphRelease；
4. 知识引用来自本文锁定范围内真实 QueryRun；
5. 精确权威数字存在对应事实或测算依赖。

本次验收将 Agent 返回的任务清单规范化为 5 行 × 6 列原生 Plate 表格。服务端拒绝嵌套对象和字符串化表格，导出的 Word 表格仍可编辑。

