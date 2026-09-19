# 可控推演写作部署记录

## 代码

- 传神智库分支：`codex/controlled-writing-bidirectional-diff`
- 本地最终功能提交：`da423d4c`
- DeepSeek Work 分支：`codex/controlled-writing-plugin`
- DeepSeek Work 插件提交：`6202eec`
- 两个分支均已推送到各自 GitHub 远端。

服务器补丁提交因服务器仓库已有部署留痕而具有不同 SHA，最终工作树提交为 `d1deb9626`；功能内容与本地分支等价，GitHub 分支是源代码权威基线。

## 服务器

- 主机：`10.5.113.232`
- 传神智库目录：`/home/tianqi/zhishipingtai-controlled-writing-v2`
- DeepSeek Work 工作目录：`/home/tianqi/dshwork`
- 验收产物：`/home/tianqi/controlled-writing-validation`

所有应用与中间件均在服务器运行；本次部署不依赖开发机服务，没有删除任何 Volume。

## 镜像与服务

- 应用镜像：`semantica-enterprise:0.10.0`
- 镜像摘要：`sha256:1c621555877715bcb01099281a5e1f197e56d20eda4460735d5127e89487df9a`
- API、Worker、Scheduler、MCP Server 已切换到同一镜像并通过健康检查。
- Agent Runtime、妙笔协同、PostgreSQL、Redis、RabbitMQ、MinIO、OpenSearch、Qdrant、FalkorDB 和 ASR Runtime 均健康。
- `workdsh-preview.service` 为 active。
- 最近应用与 DeepSeek Work 服务日志未发现 Traceback、Unhandled、Fatal 或未处理 Exception。

## 插件

- 沿用现有传神插件，扩展到 46 个唯一工具，没有安装第二套重复插件。
- 安装前备份：`/home/tianqi/backups/workdsh-plugin-chuanshen-20260919164441`
- 插件通过 DeepSeek Work 正式 Runtime 安装，不修改 `node_modules` 作为源码。
- 实测模型只有服务端真实标识 `deepseek-v4-flash-0731`；最小请求、严格 JSON 和工具调用均通过。

## 升级和回退原则

- 升级保留服务器现有 `.env`、Secret 和全部 Volume。
- 数据库迁移可重复，服务重复启动不会重复插入版本对象。
- 应用回退使用前一镜像和已有不可变数据库版本；不要执行 `docker compose down -v`。
- 插件回退使用部署前备份包，并重新执行插件清单与工具数量检查。

## 最终检查

服务器真实验收 JSON 状态为 `passed`，应急资源与科研楼可研两个场景均通过。DeepSeek Work 新会话能使用当前模型调用新插件，浏览器 Console Error 为 0；未授权的妙笔入口仍要求登录。
