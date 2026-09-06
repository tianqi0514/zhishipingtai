# 国联集团完整演示预检指南

> **演示数据，不代表国联集团真实经营数据。** 预检只读，不以静态文件存在代替平台状态验证。

## 1. 何时执行

- 正式演示前一天：执行完整 `prepare`、自动化验收和一次全流程彩排。
- 演示前 60 分钟：启动服务、模型预热、执行严格 `verify`。
- 演示前 10 分钟：执行 `preflight`，打开目标页面和三条关键引用。
- 切换到测试环境后：停止本机相关服务，再运行同一套预检，证明远端独立运行。

## 2. 环境变量

凭据只从安全环境变量或现有 Secret 注入，不写入命令历史、脚本、文档或报告。

```bash
export GUOLIAN_DEMO_API_URL='http://127.0.0.1:8080/api/v1'
export GUOLIAN_DEMO_ADMIN_USERNAME='admin'
export GUOLIAN_DEMO_ADMIN_PASSWORD='REPLACE_WITH_DEMO_ADMIN_SECRET'
export GUOLIAN_DEMO_USER_PASSWORD='REPLACE_WITH_DEMO_USER_SECRET'
export GUOLIAN_DEMO_DATABASE_PASSWORD='REPLACE_WITH_DEMO_DB_SECRET'
export GUOLIAN_DEMO_MCP_URL='http://127.0.0.1:8091/mcp'
# 仅全新环境创建在线千问配置时临时提供
export GUOLIAN_DEMO_QWEN_API_KEY='REPLACE_WITH_DASHSCOPE_API_KEY'
```

避免用 `set -x`；截图前清理终端历史和环境变量展示。

服务器上先以同一组 Compose 启动平台、两个演示数据库和
`source-fixture`，这一步会同时把严格的演示内网白名单注入 API/Worker：

```bash
export GUOLIAN_DEMO_ENABLED=1
scripts/deploy_server.sh up
```

不要单独执行 `docker compose -f compose.guolian-demo.yaml up`；那样会丢失基础服务定义，也不能保证已运行的 API 获得演示白名单。

## 3. 首次准备

生产镜像已包含已提交的演示脚本和素材。优先在 API 容器中执行，以复用已安装的 Python 依赖、Docker 内网名称和共享应用数据卷。先查看零写入计划：

```bash
docker compose exec -T \
  -e GUOLIAN_DEMO_ADMIN_USERNAME -e GUOLIAN_DEMO_ADMIN_PASSWORD \
  -e GUOLIAN_DEMO_USER_PASSWORD -e GUOLIAN_DEMO_DATABASE_PASSWORD \
  -e GUOLIAN_DEMO_QWEN_API_KEY \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/prepare_guolian_demo.py --dry-run
```

计划只能包含：三个演示角色/用户、指定演示空间、媒体策略、本体、白名单素材、独立 MySQL/PostgreSQL、映射、图谱和三项分析任务。确认后执行：

```bash
docker compose exec -T \
  -e GUOLIAN_DEMO_ADMIN_USERNAME -e GUOLIAN_DEMO_ADMIN_PASSWORD \
  -e GUOLIAN_DEMO_USER_PASSWORD -e GUOLIAN_DEMO_DATABASE_PASSWORD \
  -e GUOLIAN_DEMO_QWEN_API_KEY \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/prepare_guolian_demo.py
```

脚本是幂等的；已发布文档应复用，内容未变化不得重复生成版本。失败重跑时只重跑受影响阶段，例如：

全新平台没有在线千问配置时，`models` 阶段只在显式收到 `GUOLIAN_DEMO_QWEN_API_KEY` 后通过真实模型配置 API 创建 Qwen LLM/Vision；真实连接测试成功后才设为默认并绑定路由。凭据由平台加密保存，不进入输出。准备结束后执行 `unset GUOLIAN_DEMO_QWEN_API_KEY`。未提供且平台也没有已验证 Qwen 时，严格预检会真实失败，不会用 Kimi 或假成功代替。

```bash
docker compose exec -T \
  -e GUOLIAN_DEMO_ADMIN_PASSWORD -e GUOLIAN_DEMO_USER_PASSWORD \
  -e GUOLIAN_DEMO_DATABASE_PASSWORD -e GUOLIAN_DEMO_QWEN_API_KEY \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/prepare_guolian_demo.py \
  --phase documents --phase graph --phase analysis
```

## 4. 一键只读预检

```bash
scripts/demo/preflight_guolian_demo.sh
```

脚本调用 `verify_guolian_demo.py` 并返回：主演示模型、文档加工、治理画像、音频时间戳、视频帧/时间线、数据库连接、语义映射、本体、图谱、分析任务、六种检索模式、失败任务，以及 REST、DeepSeek Harness、MCP、CLI 的真实服务链。宿主 Python 已安装项目依赖时直接运行；否则包装脚本会自动在运行中的 API 容器执行。任何必需项失败都应返回非零退出码。

六种检索模式为：仅全文、仅向量、仅图谱、全文＋向量、全文＋向量＋图谱、三路检索＋请求重排。最后一种在没有可用重排模型时必须返回明确的降级 Warning 并保留 RRF 结果；没有 Warning 或伪报“已重排”都会使预检失败。

服务链验收不是静态契约检查：它会真实执行 REST 检索/片段/画像/图谱/严格 Plan/IR 结构化查询，完成一个两轮 DSH 会话并核验事件和引用，调用主演示 MCP 工具，并通过 CLI 执行空间、检索、片段和问答命令。20 条自然语言结构化 Ground Truth 由总预检单独执行；服务链复用激活映射中的语义 ID 验证 REST 与 MCP 的确定性执行，避免为同一预检重复触发一次模型规划。验收创建的临时会话会在结束时清理。

需要保存证据时：

```bash
mkdir -p .demo-build
scripts/demo/preflight_guolian_demo.sh > .demo-build/guolian-preflight.json
```

`.demo-build/` 只存本地报告，禁止提交缓存、Secret 或模型文件。

## 5. 人工预检清单

| 检查 | 通过条件 | 证据 |
|---|---|---|
| 页面 | `http://localhost:8080/` 可登录，刷新不掉登录 | 浏览器截图/Network |
| 空间 | 全局选择器为国联演示空间，其他空间不受影响 | 空间 ID |
| 文档 | 主演示 12 种素材均完成，失败任务为 0 | 文档详情、Job ID |
| MD 路由 | Markdown 解析器为文本链，无 OCR 阶段 | 解析摘要 |
| 音频 | transcript 带 time_start/time_end | 片段详情 |
| 视频 | 54 秒，帧和时间线可打开，视觉描述真实存在 | 帧 ID |
| 治理 | 版本、别名、OCR、关系缺失等案例可操作 | Case ID |
| 本体/图谱 | 节点、边、版本、来源可打开 | Graph release |
| Semantica | 三个任务可预览，至少目标规则有证明链 | run/fact ID |
| 数据库 | 两个独立库连接成功，Schema/映射为 active | source/mapping ID |
| 数值 | 20 条结构化 Ground Truth 逐项一致 | QueryRun IDs |
| 检索 | 六种模式均有真实召回；无重排模型时明确降级 | Query IDs、Warning |
| Agent | DSH 两轮上下文、流式事件、工具调用、引用和刷新恢复通过 | harness session ID、event sequence |
| 开放能力 | REST、MCP、CLI 各有一次真实调用，MCP 拒绝原始 SQL | 审计记录、机器报告 |
| Console | 无 error、未处理 Promise、非预期 4xx/5xx | Console/Network |
| Secret | 页面、日志、事件和报告无明文 Key/密码 | 脱敏扫描 |

## 6. 关键问法快速检查

1. “《集团本部采购实施细则（2025演示现行版）》主要适用于哪些单位？”
2. “2026 年演示数据中，各单位采购金额分别是多少？”
3. “东方智造的交付延期会影响哪些项目和责任部门？请说明完整路径。”
4. “项目例会中提到的风险是什么，哪个部门采取行动？”
5. “国联集团明年的实际利润目标是多少？”——必须明确证据不足。

每个回答都需打开至少一条真实引用；数值问题必须出现数据引用和 QueryRun。

## 7. 模型预热

- 在线千问：最小对话、JSON、工具调用各 1 次。
- BGE：生成向量并检查维度、Qdrant 点数。
- Vision：固定架构图真实识别 1 次。
- SenseVoice：短音频转写 1 次。
- Kimi：若作为显式备用则最小请求 1 次。
- 内网 Qwen 不可达时保持停用，不触发测试风暴。

## 8. 重置

先只看范围：

```bash
docker compose exec -T \
  -e GUOLIAN_DEMO_ADMIN_USERNAME -e GUOLIAN_DEMO_ADMIN_PASSWORD \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/reset_guolian_demo.py
```

只有明确需要重新搭建时执行：

```bash
docker compose exec -T \
  -e GUOLIAN_DEMO_ADMIN_USERNAME -e GUOLIAN_DEMO_ADMIN_PASSWORD \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/reset_guolian_demo.py \
  --execute --confirm guolian-enterprise-demo
```

该动作只能删除演示空间范围对象，不得删除 Docker Volume、其他空间或共享模型配置。正式演示当天不建议重置。

## 9. 放行判断

只有 `verify` 必需项全部通过、浏览器第三轮彩排无阻塞、Ground Truth 数值和引用全部一致，才标记“可以演示”。测试环境当前未验证；恢复后必须重复以上流程，且关闭本机后远端仍可独立通过。
