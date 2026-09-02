# 已知限制

- Google Drive、OneDrive/SharePoint 已通过 OAuth/API 协议兼容 Stub，但没有企业账号，仍需在目标组织租户完成真实授权验收。
- Snowflake、Databricks 连接器代码与配置校验已实现，当前没有企业凭据和测试仓库，尚未完成真实外部账号或协议服务验证。
- Hugging Face Dataset 本轮使用本地 Dataset 加载，没有验证私有 Hub Token。
- 没有配置 ASR/Vision 时，媒体只产生真实元数据/OCR 和明确的 `not_configured` 状态；不会生成转写或视觉描述。
- 本机 16GB 验收环境已真实运行 SenseVoice CPU 与 Kimi K3 云端视觉；本地 Vision 仅完成 OpenAI 兼容接口、安全边界和合约测试，尚未在该机器常驻 1B–2B 视觉权重。目标服务器确认内存/GPU 后需单独做真实模型回归。
- SenseVoice 当前运行时若不返回说话人标签、词级时间或确定的模型 Revision，平台会如实保留普通时间段并显示能力告警，不伪造说话人或模型版本；生产离线包仍需冻结具体模型 Revision 与校验和。
- 没有默认 Reranker 时使用 Semantica SearchRanker 的 RRF，页面会显示降级告警。
- DeepSeek Harness 仍是 Developer Preview。系统已锁定 commit 并把版本影响限制在 adapter/runtime，但升级必须执行完整合约与恢复回归。
- 当前可视化分析规则使用二元 Datalog 谓词；数值聚合、时间窗口、否定和外部函数尚未开放给业务配置，需要后续以白名单 Builtin 扩展并增加资源上限与确定性测试。
- 结构化数据对象的行数优先采用数据库统计估算；源库尚未执行 `ANALYZE` 时，小表估算可能为 0，但实时预览的当前页行数和查询结果仍来自真实数据库。
- 未声明外键的关系只能生成带证据和置信度的映射建议，不会自动激活；没有稳定主键/唯一组合键的表可以预览和实时查询，但默认不允许稳定图谱实体物化。
- 数据库实时查询当前只支持 MySQL 和 PostgreSQL；SQLite 仅用于 Ontology2SQL 上游参考测试，不被宣称为生产方言支持。
- 当前未接统一身份平台，也未启用密级体系；已有租户、知识空间和用户权限隔离不能替代未来的集团统一身份/密级集成。
- 性能测试为单机 Docker Desktop：50 并发检索中位约 7.14s、P95 约 10.08s；生产容量需根据硬件、索引规模和模型配额重新压测。
