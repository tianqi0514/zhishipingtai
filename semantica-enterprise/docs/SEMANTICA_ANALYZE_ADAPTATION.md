# Semantica Analyze 适配边界

## 职责划分

平台负责身份、租户、空间权限、有效事实投影、业务规则版本、任务、证据持久化、发布和回滚。Semantica 的 `DatalogReasoner` 负责规则解析验证、半朴素不动点计算和多步规则闭包。

适配入口：

- `compile_rule()`：把中文业务关系映射为稳定谓词 Token，并调用 `DatalogReasoner.add_rule()` 验证最终规则。
- `run_graph_inference()`：把授权范围内的有效事实加入 Semantica，调用 `derive_all()`，再把结果映射回平台实体与业务关系。
- `run_readonly_sparql()`：在授权知识投影上执行有界只读 SPARQL。

## 业务化扩展

本轮新增的数据准备、词表、模板、匹配预览、零结果诊断和运行比较属于平台编排层，不复制推理算法。匹配预览同样真实调用 Semantica，只是不持久化结果。

## 证据

适配器将已有 Fact 的 ID、来源 Chunk、空间和置信度放入 provenance 索引。Semantica 完成推演后，平台根据变量绑定恢复每条前提；多步推演通过 `source_result_key` 关联上一步推导结论。

## 限制

- 当前业务规则为二元关系。
- 条件连接为 AND。
- 不开放任意 Builtin 或代码执行。
- 规则中的结论变量必须在条件中出现。
- 推演最多接收 50,000 条已有关系、500 条规则和 10,000 条返回结果。
