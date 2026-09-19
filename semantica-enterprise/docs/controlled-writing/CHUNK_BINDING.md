# Chunk 绑定

每个 Plate 顶层正文块有稳定 `chunk_id`。不可变文稿版本保存 Plate 节点快照，`WritingChunkDependency` 保存 Fact、Evidence、Relation、Rule、InferenceRun、Metric、ComputationRun 和公开引用的稳定 ID 与版本。

一个 Chunk 可以绑定多个来源；同一 Fact 可以影响摘要、正文、表格和附件中的多个 Chunk。正文保存当时渲染值以保证历史可重现，但当前权威值来自 Fact/MetricValue。

生成后必须校验：绑定对象存在且有权限、事实版本和适用范围正确、精确数字能回到 Fact/ComputationRun、推演结论能回到 InferenceRun 和证明链。没有绑定的精确数字标记 unsupported 并阻断定稿。
