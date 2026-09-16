# 写作图谱可视化

写作图谱与原知识图谱使用独立发布版本和查询接口，不改变现有全文、向量和图谱检索排序。

## 两种视图

- 业务关系：`Entity — Relation → Entity`，适合查看组织、资源、事件和职责关系。
- 事实依据：`Evidence → Claim → Fact → Entity / Relation`，适合核验一条写作事实如何从原文形成。

节点颜色固定为：Entity 蓝、Evidence 紫、Claim 橙、Fact 绿、指标/计算金、推演事实红紫、历史失效灰；Relation Assertion 仅在依据视图以青色展开。实线表示已确认，虚线表示候选或推演，灰色点线表示历史失效。

界面支持版本、类型、状态、来源筛选，节点搜索、上下游展开和打开真实片段。前端不得自行拼造图谱；节点和边均来自 `/writing-graph/releases/{release_id}/graph?view=business|evidence`。

