# 数据源支持矩阵

全部 29 种类型共用 SourceConnector 数据模型和页面：创建、查看、编辑、删除、启停、连接测试、手工同步、定时同步、增量游标、内容 Hash 去重、版本创建、任务百分比、结果、错误和重试。敏感字段使用单独 `secret` 加密保存，不进入 `config`。

| 数据源 | 实现/协议 | 自动化验证 | 外部账号状态 |
|---|---|---|---|
| Web | Semantica Web Ingestor，robots/重试 | 本地 HTTP 真连接与同步 | 不需要 |
| REST API | GET/POST、头/参数、Secret Header | 本地 REST 真连接 | 不需要 |
| RSS/Atom | Feed Ingestor | 本地 RSS 真解析 | 不需要 |
| Sitemap | Sitemap Ingestor、页面抓取 | 本地 Sitemap 真解析 | 不需要 |
| Git | Semantica Git Ingestor | 本地 `git daemon` 真克隆 | 不需要 |
| PostgreSQL | Semantica Database Ingestor | 现有 PostgreSQL 真查询 | 不需要 |
| MySQL | Semantica Database Ingestor | MySQL 8 容器真查询 | 不需要 |
| IMAP/POP3 Email | TLS 邮件协议、正文/附件 | 协议容器 IMAPS/POP3S 真交互 | 不需要 |
| MCP | MCP Resource 读取 | 测试 MCP Server 真会话 | 不需要 |
| MongoDB | PyMongo 读取 | Mongo 容器真查询 | 不需要 |
| Elasticsearch | 官方客户端兼容接口 | 协议 Stub，真实请求/响应 | 未接企业账号 |
| OpenSearch | OpenSearch HTTP | 现有 OpenSearch + Stub 真查询 | 不需要 |
| DuckDB | DuckDB 查询 | 本地真实文件查询 | 不需要 |
| Parquet | PyArrow | 本地真实文件 | 不需要 |
| Arrow | PyArrow IPC | 本地真实文件 | 不需要 |
| Hugging Face Dataset | datasets | 本地 JSON Dataset 真加载 | 未访问私有 Hub |
| RabbitMQ Stream | pika 消费 | 现有 RabbitMQ 真消息 | 不需要 |
| Snowflake | 官方 Connector | 配置/实现/单元验证 | 缺企业账号，未做真实外部验证 |
| Databricks | SQL Connector | 配置/实现/单元验证 | 缺企业账号，未做真实外部验证 |
| 本地挂载目录 | 安全目录遍历/递归 | 应用 Volume 内 26 文件真同步 | 不需要 |
| S3/MinIO | S3 兼容 API | 现有 MinIO 真上传/列举/下载 | 不需要 |
| 通用对象前缀 | 与 S3 共用实现 | S3 同代码路径/合约测试 | 未接公有云账号 |
| SFTP | Paramiko | 临时 SFTP Server 真交互 | 不需要 |
| FTP | ftplib | 临时 FTP Server 真交互 | 不需要 |
| FTPS | FTP_TLS | 临时 FTPS Server 真交互 | 不需要 |
| WebDAV | PROPFIND/GET | 本地 WebDAV 真交互 | 不需要 |
| SMB/CIFS | smbprotocol | Samba 容器真交互 | 不需要 |
| Google Drive | OAuth、刷新、Drive API | 官方协议兼容 Stub | 缺真实 Google 企业账号 |
| OneDrive/SharePoint | OAuth、刷新、Graph API | Microsoft Graph 兼容 Stub | 缺真实 Microsoft 企业账号 |

`run_source_matrix.sh` 本轮通过三组结果：基础/本地 14 个执行项、协议 10 个执行项、云协议 5 个执行项。Snowflake 与 Databricks 不能在没有企业凭据时声称完成外部实测，当前明确保留为“代码已实现、配置与单元验证通过、真实账号待验”。

所有网络数据源执行 SSRF 检查，默认拒绝私网、回环、链路本地地址；测试环境只通过进程级 allowlist 放行明确 Fixture 主机。URL 不允许内嵌账号密码，认证头和 Token 必须走加密 Secret。
