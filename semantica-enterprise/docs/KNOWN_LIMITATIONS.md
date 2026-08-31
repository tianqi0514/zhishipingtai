# 已知限制

- Google Drive、OneDrive/SharePoint 已通过 OAuth/API 协议兼容 Stub，但没有企业账号，仍需在目标组织租户完成真实授权验收。
- Snowflake、Databricks 连接器代码与配置校验已实现，当前没有企业凭据和测试仓库，尚未完成真实外部账号或协议服务验证。
- Hugging Face Dataset 本轮使用本地 Dataset 加载，没有验证私有 Hub Token。
- 没有配置 ASR/Vision 时，媒体只产生真实元数据/OCR 和明确的 `not_configured` 状态；不会生成转写或视觉描述。
- 没有默认 Reranker 时使用 Semantica SearchRanker 的 RRF，页面会显示降级告警。
- DeepSeek Harness 仍是 Developer Preview。系统已锁定 commit 并把版本影响限制在 adapter/runtime，但升级必须执行完整合约与恢复回归。
- 当前可视化分析规则使用二元 Datalog 谓词；数值聚合、时间窗口、否定和外部函数尚未开放给业务配置，需要后续以白名单 Builtin 扩展并增加资源上限与确定性测试。
- 应用工作区的顶层 Git 仓库当前是 unborn HEAD（尚无首个应用提交）；交付镜像记录了 Semantica upstream commit。正式发布前应由仓库负责人创建受控基线提交和版本 Tag。
- 当前未接统一身份平台，也未启用密级体系；已有租户、知识空间和用户权限隔离不能替代未来的集团统一身份/密级集成。
- 性能测试为单机 Docker Desktop：50 并发检索中位约 7.14s、P95 约 10.08s；生产容量需根据硬件、索引规模和模型配额重新压测。
