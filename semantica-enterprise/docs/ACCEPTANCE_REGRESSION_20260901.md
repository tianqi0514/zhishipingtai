# 清空重建与全链路验收报告（2026-09-01）

## 验收范围

本轮经用户明确授权，先备份并清理平台业务数据，再建立一套可重复验证的测试数据集。管理员、角色、模型配置、解析/切片/抽取/治理策略和加密 Secret 保留；未删除 Docker Volume，也未改写 Semantica 或 DeepSeek Harness 核心代码。

| 项目 | 结果 |
|---|---|
| 业务数据备份 | `/Users/tianqi/Documents/828semantic/artifacts/clean-slate-20260901/`；PostgreSQL、MinIO、Harness Session 与 Provenance 均已备份并校验 Hash |
| 应用基线 | `c7a856b50240bc92aa2ea31777f9c5c7e1df8128`，分支 `codex/insights-knowledge-service-ux` |
| Semantica | `cce5ea177cbac29a526effa546219c48f8ec36f4` |
| DeepSeek Harness | `cd5ef8148158c3a752a658978873241fdf8e2bbc` |
| 应用镜像 | `semantica-enterprise:0.10.0` / `sha256:3f7894ab56ee1a4b098957a160fa8078a4ffa94bdcc73b6cab456d447533e843` |
| Harness 镜像 | `chuanshen-agent-runtime:0.1.0-cd5ef81` / `sha256:c0003665b95b8a724c10b9274427f8da7960574c4ae179be3c771e7d34c3efc7` |
| Secret 处理 | 未在源码、命令输出、日志或报告中打印 API Key；浏览器不回显明文 |

## 留在平台中的验收数据集

最终保留 2 个有效知识空间：`集团产品知识验收库` 和 `制度与供应链分析库`。冷启动后通过正式 API 核对为 20 份有效文档、13 个有效数据源、2 个可继续查看的真实问答会话，且运行中任务为 0。

### 文件与媒介

| 类别 | 测试内容 | 结果 |
|---|---|---|
| 文本 | TXT、Markdown | 解析、切片、画像、索引、引用通过 |
| PDF | 普通 PDF、扫描 PDF | 普通解析与 OCR 路径通过，页码可定位 |
| Office | DOCX、PPTX、XLSX | 正文/幻灯片/工作表解析与结构位置通过 |
| 图片 | PNG | OCR 内容进入统一元素、Chunk 与检索 |
| 邮件 | EML 与附件 | 邮件头、正文和附件递归解析通过 |
| 音频 | MP3 | 元数据、时长和时间属性可检索；未配置 ASR 时不伪造转写 |
| 视频 | MP4 | 媒体元数据解析；未配置 ASR/Vision 时真实降级 |
| 压缩包 | ZIP 多文件包 | 安全解压、递归解析、来源追溯通过 |

另有 8 份文档由真实数据源同步生成。内容不变的再次同步返回 `unchanged`；修改本地目录中的一个文件后只重新加工 1 个变化 Chunk，未重复生成未变化版本。

### 数据源

页面保留并可继续测试的 13 类来源为 Web、REST API、RSS、Sitemap、WebDAV、本地目录、MinIO/S3、PostgreSQL、MCP、SFTP、FTP、FTPS 和 IMAP。13 类连接测试全部成功，其中 8 类完成真实同步。

本地协议 Fixture 通过真实 HTTP、WebDAV、MCP、FTP/FTPS、SFTP、IMAP 和 MinIO/PostgreSQL 协议交互，不是 Pydantic Schema 或静态 Mock。最终 `source-fixture` 与 `protocol-fixture` 均为 healthy，重建后再次实测 Web、MCP 连接成功。需企业外部账号的云服务未冒充真实账号测试结果。

## 自动化与真实链路结果

| 测试层 | 结果 |
|---|---|
| Python 单元测试 | 118/118 通过 |
| Semantica/平台合约测试 | 19/19 通过 |
| Semantica Analyze 实时 E2E | 1/1 通过；规则、Datalog、证据、发布/回滚、SPARQL、MCP 均真实执行 |
| 文件与媒介 | 上表 12 类上传处理通过 |
| 数据源能力矩阵 | 29 项适配器/协议检查通过；本轮平台保留 13 类真实可复测来源 |
| DeepSeek Harness 四轮问答 | 4/4 完成；事件数 232/206/302/302，引用数 10/5/5/3，刷新后历史恢复 |
| 取消与重试 | 真正取消后创建新 Turn 重试，620 个事件后完成 |
| REST/MCP/CLI | REST 5 条检索结果；MCP 7 个工具与 128 个事件；CLI search/fragment/chat 通过 |
| 安全边界 | 会话、空间、片段、内部服务认证、Agent Scope、凭据过期/撤销均通过 |
| 检索降级 | 停止 Qdrant 后全文/图谱仍返回结果和 Warning，没有整体 500 |
| 队列降级 | 停止 RabbitMQ 后上传生成可重试失败任务；恢复后重试成功，没有重复业务对象 |

模型实测使用平台加密配置中的 Kimi LLM；Embedding 为 Docker 内真实运行的 `BAAI/bge-small-zh-v1.5`，512 维、不需要 API Key，并非伪造的“测试成功”。Vision、ASR、Reranker 未配置，因此页面和检索轨迹显示明确降级，不宣称相应模型能力已运行。

## 浏览器真实点击

在 1440×900 和 1280×720 下完成登录、密码显示、工作台、数据源选择器与连接测试、文档弹窗取消、白底 3D 图谱、节点/边 CRUD、四类知识分析说明、规则/场景/结果/SPARQL、服务接入手册和三栏智能问答回归。

真实浏览器问答“关键供应商识别规则推导出了什么结论？”由 Harness 运行 118 秒，页面计时随真实 Turn 更新；调用 `knowledge_search`、`knowledge_reason` 和 `knowledge_graph_query`，完成后显示排序依据与引用。页面刷新后回答、事件、轨迹与召回依据恢复。引用弹窗已把内部 `document` 值转换为“文档正文”，音频 JSON 已转换为业务可读的元数据描述。最终页面无非预期整体滚动或横向溢出，控制台错误/警告为 0。

图谱浏览器测试中创建、编辑并删除临时节点和边，最终恢复为 6 个业务节点、6 条关系；测试临时数据未遗留。

## 本轮发现并修复的问题

1. RabbitMQ 仅检查 Erlang 节点存活，API 可能早于 AMQP 监听启动：改为端口连通性健康检查并增加合理超时。
2. Celery 发布失败会让上传请求 500，客户端重试可能产生重复对象：现在保留持久任务并标记 `QUEUE_DISPATCH_FAILED`，返回可重试告警。
3. 文档已解析成功但下游知识加工投递失败会错误地把解析结果整体判失败：现在保留已成功解析结果，只标记下游任务失败。
4. Semantica OpenAI-compatible Provider 没有使用平台模型超时与重试配置：增加薄适配层，通过客户端 `with_options` 应用配置，不复制 Semantica 算法。
5. 数据源同步结果在页面暴露原始 JSON/UUID：改为“内容未变化”“已创建新版本，等待解析”等业务状态。
6. Harness `tool_started` 在完成后仍显示运行中：按 `call_id` 与 `tool_finished` 配对，显示真实耗时和完成/失败状态，并去除重复步骤。
7. 召回依据和片段弹窗显示 `document`、原始音频 JSON：统一转成中文结构位置和可读媒介元数据。
8. 两个验收协议容器健康检查误用了不存在的 8000 端口：仅重建无持久数据的测试容器，按 8088/8095 实际端口检查，现均 healthy。

## 冷启动持久化

最终镜像构建后执行 `docker compose stop` 与 `docker compose up -d --wait`，没有删除 Volume。12/12 平台服务全部恢复 healthy。重启后正式 API 返回 2 个空间、20 份文档、13 个数据源、2 个会话，混合检索返回 5 条排序结果，会话详情可恢复；OpenSearch、Qdrant、FalkorDB、MinIO 与 Harness Session 数据均可继续使用。

## 可重复执行入口

```bash
# 建立文件、图谱、分析与对话验收数据
docker compose run --rm --no-deps -v "$PWD:/workspace" -w /workspace api sh -lc \
  'ADMIN_PASSWORD="$BOOTSTRAP_ADMIN_PASSWORD" API_BASE=http://api:8080/api/v1 python tests/e2e/seed_acceptance_dataset.py'

# 建立数据源验收数据（执行前先启动 tests/fixtures 中的协议服务）
docker compose run --rm --no-deps -v "$PWD:/workspace" -w /workspace api sh -lc \
  'ADMIN_PASSWORD="$BOOTSTRAP_ADMIN_PASSWORD" API_BASE=http://api:8080/api/v1 python tests/e2e/seed_source_acceptance.py'

# 最终单元与合约测试
docker compose run --rm --no-deps -v "$PWD:/workspace" -w /workspace api pytest -q tests/unit
docker compose run --rm --no-deps -v "$PWD:/workspace" -w /workspace api pytest -q tests/contract
```

两个 Seed 脚本使用固定业务编码并在发现同名验收数据时立即停止，避免重复写入；需要重新执行时应通过平台 API 清理对应验收数据，不需要删除 Docker Volume。生产环境不得运行清空步骤；备份恢复方法见部署与故障排查文档。
