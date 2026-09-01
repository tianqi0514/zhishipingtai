# Ontology2SQL 参考与适配说明

参考仓库锁定提交：`ece05d1cc988d9bce602a7a9e1b73cd5767a860a`。2026-09-01 基线：`192 passed, 8 xfailed, 0 failed`。

本项目参考了 Mapping Manifest、Plan、IR、确定性编译和 benchmark 问题矩阵的架构思想；生产实现结合传神智库已有 Ontology/OntologyTerm、租户权限、模型配置、审计和 DSH 协议独立编写。没有引入 BIRD Gold SQL、Oracle Evidence 或评测专用逻辑，也没有把 SQLite 编译器宣称为 MySQL/PostgreSQL 支持。

MySQL/PostgreSQL 编译、只读事务、参数绑定、服务端脱敏、Schema 漂移、QueryRun、Agent Token 和 UI 均为本项目实现。当前提交未直接复制 Ontology2SQL 源文件；第三方许可说明见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。若未来直接复用 Apache-2.0 代码，必须保留原文件版权头、LICENSE、NOTICE 和锁定 commit。
