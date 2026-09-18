# 语义图谱产物与双向 Diff 规范评审

评审对象：`语义图谱产物存储调用与双向Diff规范.md`

## 结论

该规范对妙笔最有帮助的不是“增加更多产物”，而是明确了三条生产约束：

1. 一个权威事实只有一个有效来源和版本，正文、表格、摘要只是投影。
2. Agent 按章节消费有边界的工作包，而不是读取完整语料后自由发挥。
3. 资料继承变化与正文编辑变化是两种 Diff；两者都只能先形成影响预览，再由用户应用。

这些约束与现有 `WritingGraphRelease`、`WritingProjectMaterial`、`WritingChunkBinding`、`ComputationRun` 和 `ImpactPreview` 可以直接衔接，不需要另建一套知识系统。

## 当前采纳方式

| 规范思想 | 当前实现映射 | 本轮调整 |
|---|---|---|
| 单一权威事实来源 | WritingFact / Evidence / WritingGraphRelease | 项目自动锁定最新已发布写作图谱 |
| 原始切片与章节骨架分离 | Chunk / 文章目录与章节要求 | 每章构造有字符上限的 source pack |
| bounded work package | `writing_get_chapter_source_pack` | 扩大合理上限；标题近义匹配失败时使用受控材料摘要兜底 |
| node mention / 正文绑定 | WritingChunkBinding | Agent 输出必须携带真实 Fact、Evidence 或 Chunk ID |
| 继承 Diff | 知识版本刷新、stale binding | 继续显式刷新，不静默覆盖正文 |
| 编辑 Diff | ImpactPreview / ChangeApplication | 先预览，支持全选或逐项应用 |
| deterministic gate | 生成前后引用和数值校验 | 无效 ID、无来源精确数字和失效来源阻止定稿 |

## 本轮不照搬的内容

- 不把 30 余种 JSONL/JSON 产物全部暴露成产品对象。
- 不为单体阶段引入 Kafka 和多套新的事实存储。
- 不让普通用户理解 inheritance diff、editing diff、skeleton 或 bundle 等技术名词。
- 不把当前项目材料也只压缩成抽象骨架。当前材料仍需保留原始 Chunk，才能逐段引用和核验；历史样稿和模板语料可以只提供结构投影。

## 简化后的正式链路

```text
知识空间当前有效文档
  → 不可变文档版本与 Chunk
  → 已治理 WritingGraphRelease
  → 创建项目时自动锁定材料版本
  → 按章节构造 source pack
  → DSH Agent 检索、写作并返回绑定
  → Plate 正文 Chunk
  → 来源或事实变化时生成 ImpactPreview
  → 用户选择后形成新文章版本
```

## 验收重点

1. 新建项目后立即能看到空间内有效材料，不需要再次逐份选择。
2. 每章 source pack 中能找到真实 Chunk ID、文档 ID 和版本 ID。
3. Agent 生成的关键段落绑定实际来源，而不是只在提示词中声称“参考了材料”。
4. 修改事实后，应用前正文不变；应用后只改变用户选择的已登记依赖。
5. 历史文章版本和历史来源版本仍可核验。

