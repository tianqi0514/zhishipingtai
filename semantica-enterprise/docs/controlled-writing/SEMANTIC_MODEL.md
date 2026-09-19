# 语义模型

核心链路为 `Evidence → Claim → Fact → Relation`：

- Evidence：解析产生的真实、不可变、可定位原文，模型不能伪造。
- Entity：稳定业务对象，模型只给候选，平台分配 ID。
- Claim：材料声称了什么，必须引用 Evidence，但不自动等于事实。
- Fact：经程序或人工确认后当前采用的事实，按版本保存。
- Relation：关系型 Fact 的图谱投影，必须反向回到 Fact、Claim、Evidence。

写作补充对象：MetricDefinition 定义公式和单位；ComputationRun 保存确定性计算；Rule/InferenceRun 保存 Semantica 规则和证明；WritingChunkBinding 将正文投影绑定到上述权威对象。

模型负责候选抽取和语义辅助分类。服务器负责 ID、引用合法性、单位/时间归一、冲突、状态和版本。人工负责关键事实、冲突和低置信度候选的最终确认。
