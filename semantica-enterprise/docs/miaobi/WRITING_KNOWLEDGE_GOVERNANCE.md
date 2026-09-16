# 写作知识治理

## 目标

写作图谱候选不能直接成为正式事实。治理工作台负责把模型抽取的 Entity、Claim、Fact 和 Relation 与平台确定性生成的 Evidence 对照后，形成可发布对象。

## 操作边界

| 对象 | 自动阶段 | 人工阶段 | 发布约束 |
|---|---|---|---|
| Evidence | 从文档版本、ContentElement、Chunk 和结构位置确定性生成 | 核对定位与有效版本 | 原文和内容哈希不可由模型改写 |
| Entity | 模型提及候选、程序去重 | 接受、改名、合并、拆分、设置别名 | 只有已确认对象进入发布版本 |
| Claim | 联合抽取且强制引用 Evidence | 修正主体、谓词、客体、时间和范围 | Claim 不等于事实 |
| Fact | Claim 校验后形成候选 | 接受、拒绝、设为当前或历史 | 模型不能将 Fact 标记为 verified |
| Relation | 从关系型 Fact 投影 | 核对两端实体和关系 | 必须能回溯 Fact、Claim、Evidence |

接口位于 `/api/v1/writing-graph/governance/*`。每次决定保存原值、新值、理由、操作者和时间；重新加工只能产生新候选，不能覆盖人工结果。

## 当前验收

“写作图谱与妙笔联动验收空间”真实发布版本包含 25 个 Evidence、78 个 Entity、58 个 Claim、93 个 Fact 和 13 个 Relation。候选通过治理后才进入不可变 `WritingGraphRelease`。

