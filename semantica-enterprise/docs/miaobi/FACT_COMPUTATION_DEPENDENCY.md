# 事实、计算与正文依赖

## 1. 依赖边

依赖边保存稳定 ID，不依赖文字匹配：

- Claim `derived_from` Evidence；
- Fact `accepted_from` Claim；
- Relation `projects` Fact；
- ComputationRun `reads` Fact/MetricValue；
- DerivedFact `computed_by` ComputationRun；
- WritingChunk `binds` Fact/Evidence/Relation/ComputationRun；
- ArticleVersion `contains` WritingChunk。

## 2. 影响遍历

事实候选变更时，从旧 Fact 版本开始沿显式出边广度遍历：

1. 找到直接读取该 Fact 的计算；
2. 使用版本化公式重算；
3. 比较输出并生成新的候选计算运行；
4. 继续遍历读取这些输出的计算和推演结论；
5. 找到绑定任一受影响对象的正文 Chunk；
6. 对可结构化替换的 Chunk 生成确定修改建议；
7. 对只有语义相似性的段落生成疑似影响，不自动修改。

## 3. 预览一致性

ImpactPreview 指纹至少包含：基础 Fact 版本、依赖对象版本、文章当前版本、公式版本和候选值。如果任一对象在应用前变化，旧预览返回 409，要求重新计算。

## 4. 应用语义

- 全选和逐项选择共享同一事务边界。
- 应用先创建新 Fact/ComputationRun，再派生新文章版本。
- 选择项使用建议内容；未选择的确定影响项保持原文并标记 stale。
- 定稿文章派生新草稿，不原地修改。
- manual_override 内容只标记影响，不自动替换。
- `preview_id + selection_hash` 作为幂等键。
- 撤销通过反向 ChangeApplication 产生新版本，不删除历史。

## 5. 验收示例

```text
需求人数 fact:demand-v1 = 500 人
可用人数 fact:available-v1 = 320 人
缺口 computation:gap-v1 = 500 - 320 = 180 人
```

将可用人数候选改为 400 后，预览应得到缺口 100，并列出绑定摘要、基本情况、资源现状、缺口章节、统计表、增援建议和任务清单的 Chunk。应用前版本保持 320/180；部分应用后未选 Chunk 保持原文并显示 stale；历史版本始终保持 320/180。
