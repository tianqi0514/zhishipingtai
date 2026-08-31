# 传神智库 0.10.0 测试报告

## 构建信息

| 项目 | 值 |
|---|---|
| 测试日期 | 2026-08-30（Asia/Shanghai） |
| 应用镜像 | `semantica-enterprise:0.10.0` / `sha256:81e4fa7d41a28a7a414d5deb5ca4d0166cb8c1fcbc836f1ab1bbf187e787a403` |
| Harness 镜像 | `chuanshen-agent-runtime:0.1.0-cd5ef81` / `sha256:0cac7a469e2142f1b09c9d0eef0b022604bb82d175b9958b21cf5d9f2c40c32c` |
| OpenSearch 镜像 | `chuanshen-opensearch:3.6.0` / `sha256:c844e27f12affee7b66537c3a05ae5a7017e21367c776af2c1477316a56f4435`；移除未使用且产生启动错误的 PA/Security Analytics 插件 |
| 应用 Git | 顶层仓库为 unborn HEAD，尚无应用提交；未伪造 commit |
| Semantica commit | `cce5ea177cbac29a526effa546219c48f8ec36f4` |
| DeepSeek Harness commit | `cd5ef8148158c3a752a658978873241fdf8e2bbc` |

## 自动化汇总

| 层次 | 数量 | 成功 | 失败 | 跳过 |
|---|---:|---:|---:|---:|
| Python 单元测试 | 86 | 86 | 0 | 0 |
| Semantica/平台合约测试 | 19 | 19 | 0 | 0 |
| 需要运行时环境的 Pytest E2E | 1 | 0 | 0 | 1 |
| Harness 插件/恢复合约 | 6 | 6 | 0 | 0 |
| 知识分析 REST/MCP/CLI 实时场景 | 1 | 1 | 0 | 0 |
| Docker 多模态真实文件 | 26 | 26 | 0 | 0 |
| 数据源协议执行项 | 29 | 29 | 0 | 0 |
| 模型协议类型 | 5 | 5 | 0 | 0 |
| 50 并发检索 | 50 | 50 | 0 | 0 |
| 5 并发 Agent 会话 | 5 | 5 | 0 | 0 |

核心 Pytest 最终结果为 `105 passed, 1 skipped`；跳过项是需要单独注入实时 E2E 环境的知识分析用例，该场景已按下方独立命令真实执行通过；Node Harness 为 `6 passed`。外部企业账号没有被伪装成 skip，单独列在“待外部账号验证”。

## 关键执行命令

```bash
docker compose build api worker scheduler mcp-server agent-runtime
docker run --rm --network semantica-enterprise_default -v "$PWD:/app" -w /app \
  semantica-enterprise:0.10.0 python -m pytest -q
docker compose exec -T agent-runtime npm test -- --runInBand
bash tests/e2e/api_crud_smoke.sh
bash tests/e2e/graph_crud_smoke.sh
bash tests/e2e/m10_platform_smoke.sh
python3 tests/e2e/source_incremental.py
docker run --rm -v "$PWD:/workspace" -w /workspace \
  semantica-enterprise:0.10.0 python tests/integration/multimodal_live.py
bash tests/integration/run_source_matrix.sh
python3 tests/e2e/conversation_agent_quality.py
python3 tests/e2e/conversation_cancel_retry.py
docker compose run --rm --no-deps \
  -e E2E_BASE_URL=http://api:8080/api/v1 \
  -e E2E_MCP_URL=http://mcp-server:8091/mcp \
  -v "$PWD/tests:/app/tests:ro" api \
  python -m pytest tests/e2e/test_analysis_api_live.py -q -s
python3 tests/e2e/security_boundaries.py
python3 tests/integration/restart_recovery.py
python3 tests/integration/cold_start_persistence.py
python3 tests/performance/live_load.py
```

## 三轮回归

### 第一轮：功能开发回归

- 105 项 Python 单元/合约测试与 6 项 Harness 测试通过；1 项需实时环境的 Pytest E2E 由独立 Docker 命令验证。
- 组织、角色、用户、空间、授权、29 类数据源 Schema、五类模型、解析/切片/抽取/治理、本体/词条 CRUD 通过。
- 图谱节点、边、级联删除与新 FalkorDB 发布通过。
- 三路搜索实测：全文 5、向量 8、图谱 11；未配置 Reranker 时真实降级到 RRF。
- 增量同步两版：`unchanged=true`；稳定 Chunk 复用 1，变化 Chunk/模型抽取 1；删除后发布图谱 42/索引 20，当前检索排除删除文档，历史引用仍可追溯。

### 第二轮：集成与浏览器回归

- 26 种真实文件在 Docker 中解析；扫描 PDF 走 Docling/OCR，旧 Office 走 LibreOffice，邮件附件/ZIP 递归通过。
- 数据源 Fixture 执行 14 + 10 + 5 项；MinIO、PostgreSQL/MySQL、Mongo、OpenSearch、RabbitMQ、Git、FTP/FTPS/SFTP、SMB、WebDAV、邮件、MCP 均有真实协议交互。
- REST 5 个检索结果、MCP 7 工具、CLI search/chat/fragment/reason/sparql 通过。
- 知识分析实时 E2E 完成规则集/规则版本/场景/保存查询 CRUD、Semantica Datalog 推理、两条证据、发布、内部 Harness 工具、MCP、CLI、SPARQL 与回滚；临时数据自动清理。
- 四轮 Harness 事件数 190/116/396/253，引用 8/8/8/3；取消后重试收到 752 个事件并完成。
- 分别停止 OpenSearch、Qdrant、FalkorDB，其他通道继续返回结果和明确 Warning。
- 浏览器逐项验证登录/密码显示、品牌、文档上传与百分比、治理画像、版本弹窗取消、数据源 CRUD/测试/同步/unchanged、白底 3D 图谱及节点边 CRUD、对话、停止/重试、轨迹、排序、引用与刷新恢复。

### 第三轮：冷启动与持久化回归

- `docker compose stop` 后 `docker compose start`，未删除 Volume，12/12 服务恢复 healthy。
- 既有文档 6、迁移记录 4、会话与 Harness Session 保持；最终镜像组合冷启动前后两轮事件 116/92（上一轮 164/135），追问正确继承 NexusOne。
- Worker 停机 queued 任务恢复后 succeeded；Harness 单独重启后同一 Session 持久化 2 个 Turn。
- 冷启动前完成 105 项 Python 全量测试；冷启动后重新执行知识分析 REST/MCP/CLI 关键 E2E、数据持久化核对和服务健康检查，全部通过。
- 最终浏览器截图为白底图谱 R44；真实对话的检索轨迹显示全文 5、向量 8、图谱 11、24→8 去重、RRF、414ms，总排名与分通道分数可见；引用打开真实 `m10-knowledge.txt`。浏览器错误/警告日志为空。
- 最终 12 个服务均为 healthy；应用/Worker/Harness/MCP 无 Traceback、Unhandled、Fatal。OpenSearch 精简镜像重启后也无 Error，并再次通过全文/向量/图谱检索。

## 全局 UI/UX 重构专项回归

本轮在最终镜像上执行了真实浏览器操作和后台接口复验：

| 场景 | 结果 |
| --- | --- |
| 八个业务主模块 | 工作台、知识资产、数据接入、知识服务、知识洞察、运营中心、配置中心、系统管理全部点击进入，URL Hash、标题与面包屑一致 |
| 配置中心 | 五个上下文页签无横向溢出；解析策略完成新增、编辑、删除；测试数据已清理 |
| 通用弹窗 | 连续两次 CRUD 时保存按钮会重新启用；保存中防重复提交；取消不触发校验 |
| 文档上传 | 未选择文件直接取消，弹窗正常关闭且没有“请选择文件”提示 |
| 检索调试 | 真实请求返回 8 条排序结果；全文 1、向量 8、图谱 0，展示融合分/重排分；第一条引用打开真实 `m10-knowledge.txt` 片段 |
| 智能问答布局 | 消息区、会话栏均为独立纵向滚动；输入区固定可见；根文档无非预期滚动或内容截断 |
| 3D 知识图谱 | 白色画布、浅色检查器；缩放、适配、旋转、搜索定位通过；节点和关系分别完成新增、编辑、删除并发布新图谱，测试数据已清理 |
| 响应式 | 1280×800 与 1920×1080 下关键模块均无非预期横向滚动，模块页签无溢出 |
| 浏览器控制台 | 最终资源版本 `enterprise-ux-4` 的本轮应用错误/警告为 0 |
| 冷重启持久化 | 不删除 Volume 重启全部服务；文档 23、会话 57、空间记录 32、数据源记录 23，重启前后计数一致；新页面保持登录并恢复工作台 |

冷重启过程中 FalkorDB 容器首次健康检查受到 Docker Desktop `setns` 运行时错误影响；保留命名卷仅重建该容器后恢复 healthy，图谱 API CRUD 再次通过。Worker 与 Scheduler 在 RabbitMQ 启动前出现的短暂连接拒绝按重试策略自动恢复，最终日志窗口没有未处理异常。

## 性能与故障注入

| 场景 | 结果 |
|---|---|
| 50 并发检索 | 50/50；中位 7140ms，P95 10078ms |
| 5 并发 Agent | 5/5；最大 51.16s |
| 模型 429 | 首次 429、第二次成功，重试 2 次 |
| 模型超时 | 0.05s 配置触发真实超时，未无限重试 |
| Worker 重启 | queued → succeeded |
| Harness 重启 | Session ID、上下文与 JSONL Turn 保持 |
| 单通道故障 | 三个通道分别注入，均降级而非 500 |

## 安全验证

通过租户/空间/会话/Chunk 越权、伪造服务 Secret、伪造 Agent JWT、错误会话/空间 Scope、会话删除后凭据撤销、Secret 加密脱敏、SSRF、路径穿越、ZIP Bomb、恶意文件名、MIME 伪装、请求头注入、XSS 输出转义和引用编号校验。文档内容在 Agent 提示中明确为不可信证据，不能覆盖系统指令。

## 待外部账号验证

- Google Drive、OneDrive、SharePoint：代码与协议 Stub 通过，缺企业 OAuth 租户。
- Snowflake、Databricks：实现与配置/单元验证通过，缺企业实例。
- Hugging Face：本地 Dataset 实测，未验证私有 Hub。

这些项目不计入“真实外部账号已通过”。
