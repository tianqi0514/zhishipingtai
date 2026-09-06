# 国联集团演示准备工具

这组工具只操作编码为 `guolian-enterprise-demo` 的独立演示空间。所有演示材料均声明：**演示数据，不代表国联集团真实经营数据。**

服务器首先使用三层 Compose 和 `demo` profile 启动完整环境：

```bash
export GUOLIAN_DEMO_ENABLED=1
scripts/deploy_server.sh up
```

以下准备命令优先在 API 容器内运行；这样可以复用镜像中的项目依赖、Docker 内网 DNS 和共享数据卷，宿主机无需另行 `pip install`。

## 1. 查看准备计划（零网络、零写入）

```bash
docker compose exec -T \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/prepare_guolian_demo.py --dry-run
```

## 2. 执行幂等准备

凭据只通过环境变量传入；脚本不会把凭据写入文件或输出。

```bash
export GUOLIAN_DEMO_ADMIN_PASSWORD='REPLACE_WITH_DEMO_ADMIN_SECRET'
export GUOLIAN_DEMO_USER_PASSWORD='REPLACE_WITH_DEMO_USER_SECRET'
export GUOLIAN_DEMO_DATABASE_PASSWORD='REPLACE_WITH_DEMO_DB_SECRET'
# 全新数据库尚无在线千问时才需要；只通过本次进程环境注入
export GUOLIAN_DEMO_QWEN_API_KEY='REPLACE_WITH_DASHSCOPE_API_KEY'
docker compose exec -T \
  -e GUOLIAN_DEMO_ADMIN_PASSWORD -e GUOLIAN_DEMO_USER_PASSWORD \
  -e GUOLIAN_DEMO_DATABASE_PASSWORD -e GUOLIAN_DEMO_QWEN_API_KEY \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/prepare_guolian_demo.py
```

全新环境在提供 `GUOLIAN_DEMO_QWEN_API_KEY` 时，会通过真实模型配置 API 创建或更新 LLM 与 Vision 配置，连接测试成功后才设为默认并绑定演示路由；Key 由平台加密保存，不进入报告。已有可用配置时可以不提供该变量。执行完成后应 `unset GUOLIAN_DEMO_QWEN_API_KEY`。

数据库密码也可使用现有的 `STRUCTURED_FIXTURE_PASSWORD`。API 地址默认是 `http://127.0.0.1:8080/api/v1`，可通过 `GUOLIAN_DEMO_API_URL` 覆盖。

阶段可独立重跑：

```bash
docker compose exec -T \
  -e GUOLIAN_DEMO_ADMIN_PASSWORD -e GUOLIAN_DEMO_USER_PASSWORD \
  -e GUOLIAN_DEMO_DATABASE_PASSWORD -e GUOLIAN_DEMO_QWEN_API_KEY \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/prepare_guolian_demo.py \
  --phase documents --phase graph --phase analysis
```

`documents` 阶段只处理显式演示白名单：同名文件的 SHA-256 未变化时复用当前版本，内容变化时在原文档下上传并加工新版本，不会创建第二个同名文档。例如修订扫描件后应看到 `new-version`，再次执行则为 `existing`。

人工治理演示使用独立的安全脚本。默认会真实执行画像、OCR、实体组合等治理发布，验证结果后立即按批次回滚；重复运行复用已有审计历史，不会不断制造治理记录：

```bash
docker compose exec -T -e GUOLIAN_DEMO_ADMIN_PASSWORD \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/prepare_guolian_governance.py --dry-run
docker compose exec -T -e GUOLIAN_DEMO_ADMIN_PASSWORD \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/prepare_guolian_governance.py --verify-only
docker compose exec -T -e GUOLIAN_DEMO_ADMIN_PASSWORD \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/prepare_guolian_governance.py
```

跨文档完全重复/近重复治理现已具备确定性扫描和业务待办闭环：可以比较两个当前版本内容，保留两份来源，或选择主文档并屏蔽副本文档当前版本的 Chunk/Fact；屏蔽会真实重发全文、向量和图谱投影，合并批次可以回滚并重新打开待办。该能力不会物理删除 Document 或历史版本；“保留两份”当前没有独立撤销入口。制度谱系、条款级差异、过期知识自动下线仍会明确返回 `gap` 或 `partial`。完整实测边界见 `docs/demo/GUOLIAN_GOVERNANCE_CAPABILITY_AUDIT.md`。

## 3. 演示前只读预检

```bash
scripts/demo/preflight_guolian_demo.sh
```

预检会真实检查模型、文档加工、治理画像、数据库数据源、语义映射、本体、图谱、分析任务以及全文/向量/图谱检索。未配置重排模型属于明确的可选降级，主演示所需检查失败时退出码非零。

## 4. 查看重置范围

默认只输出真实清理清单，不删除任何对象：

```bash
docker compose exec -T -e GUOLIAN_DEMO_ADMIN_PASSWORD \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/reset_guolian_demo.py
```

执行重置必须同时提供两个显式参数：

```bash
docker compose exec -T -e GUOLIAN_DEMO_ADMIN_PASSWORD \
  -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1 \
  api python /app/scripts/demo/reset_guolian_demo.py \
  --execute \
  --confirm guolian-enterprise-demo
```

重置会在发现该空间仍有运行中任务时拒绝继续。它不会删除 Docker Volume、其他知识空间、共享模型配置、演示角色或演示用户。
