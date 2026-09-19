# Semantica 集成

正式规则推演继续由 Semantica Analyze / DatalogReasoner 执行。平台把已确认 Fact 和确定性判据事实映射为 Datalog 输入，保存规则版本、InferenceRun、InferredFact、前提和 Provenance。

数值比较不伪装为 Datalog 内建能力：由确定性判据服务先产生诸如“震级区间满足重大灾害条件”的可验证事实，再由 Semantica 组合关系规则。输入变化后创建新的推演运行；旧结论标记失效但不删除。Python 条件只能生成判据输入，不能冒充正式 Semantica 推导。
