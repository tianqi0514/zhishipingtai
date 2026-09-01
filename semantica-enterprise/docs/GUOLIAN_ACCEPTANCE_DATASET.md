# 国联集团全链路验收数据集设计

## 数据隔离

所有对象使用 `guolian-acceptance` 业务前缀或“国联集团验收”名称，内容全部为合成数据。Seed 必须幂等：存在相同业务编码时更新或停止，不重复生成版本；清理只能调用公开 API 删除本轮测试对象，不删除 Volume。

## 组织与权限

- 组织：集团总部 5 部门、数字科技 3 部门、供应链 2 部门、产业投资 2 部门。
- 角色：系统管理员、集团知识管理员、子企业知识管理员、部门维护、普通员工、应用开发、只读审计、应用服务账号。
- 空间：集团制度、NexusOne 产品、供应商采购、经营数据、子企业私有测试空间。

## 文档集合

1. 八份集团战略/制度，包含正式版、发布 PDF、扫描盖章版、宣贯 PPT 和至少一组修订前后版本。
2. NexusOne 产品手册、参数表、介绍 PPT、FAQ、售后说明、架构图、培训音频、演示视频、销售邮件和 ZIP 资料包。
3. 供应商准入、合同、框架协议、评分、风险报告、纪要、邮件附件、扫描资质和目录。
4. 文档间设置明确引用、重复、冲突、生效/废止日期和权威版本。

## 经营数据库

MySQL/PostgreSQL 具有同一事实集，包含 companies、departments、suppliers、products、customers、contracts、orders、order_items、sales_targets、risk_events、projects、project_members、indicator_definitions 和无主键日志表。包含 2025/2026、取消订单、NULL、JSON、大文本和必须在服务端脱敏的字段。

## 多源数据

真实协议范围：本地目录、Web、REST、RSS、Sitemap、Git、PostgreSQL、MySQL、MinIO、WebDAV、FTP、SFTP、IMAP/EML、MCP。Google Drive、OneDrive/SharePoint、Snowflake 不计入已完成能力。

## 可重复生成

计划新增：

- `tests/fixtures/generate_guolian_acceptance.py`：生成多格式合成业务文件。
- `tests/e2e/seed_guolian_acceptance.py`：通过公开 API 创建组织、用户、空间、上传文件和分析事实。
- `tests/e2e/validate_business_standard_answers.py`：对检索、结构化查询、图谱、推理、权限和应用调用作精确断言。

Secret 只从环境变量读取，不写入 fixture、日志或报告。
