# 妙笔测试报告

测试日期：2026-09-09（Asia/Shanghai）

测试分支：`codex/miaobi-production`

最终功能验证提交：`2ab3477592cb3fa7d64ecd77eb530c31ed3d3a4a`

内网应用镜像：`semantica-enterprise:0.10.0`（`sha256:3e4148adce41…`，运行用户 `app`）

Plate 锁定提交：`8f65d77f8b4709833436e63661e4d061f709258f`

## 汇总

| 测试层 | 成功 | 失败 | 跳过 | 说明 |
|---|---:|---:|---:|---|
| Python 单元测试 | 530 | 0 | 0 | 当前工作区全部单元测试 |
| Semantica 合约 | 19 | 0 | 0 | 真实 DatalogReasoner、证据、预览、发布/撤回 |
| DeepSeek Harness 合约 | 20 | 0 | 0 | Runtime、Cordis 工具、事件、取消和恢复 |
| 结构化数据库集成 | 11 | 0 | 0 | MySQL/PostgreSQL 实库与 PostgreSQL Fact 幂等 |
| Plate 组件 | 9 | 0 | 0 | 编辑、可信块、引用定位、HTTP 内网协同兼容、1000 流式增量 |
| 协同安全 | 2 | 0 | 0 | Token、房间和权限 |
| 六灾种 API E2E | 6 | 0 | 0 | 独立场景的完整技术链路 |
| 地震 Ground Truth | 14 | 0 | 0 | 事实、公式、局部重算 |

## 真实运行结果

### 地震主场景

- 震级 6.2、人口密度 305.6 人/km²通过确定性判据形成 Semantica 输入事实。
- Semantica Datalog 推导“重大地震灾害（Ⅱ级）”，证明链包含两个前提。
- 搜救人员 500−320=180；输入改为 400 后局部重算为 100，未关联块不受影响。
- 三套方案分别走快速、安全和综合路线，优化目标、权重、路线、时长和风险真实不同。
- 当前知识产品 Release 绑定真实检索记录、文档 Chunk、推演事实和计算运行。
- DSH 写作 Turn 完成 139 个原始 Session Event，服务重启后仍恢复消息、事件和最终回答。

### 六类扩展场景

`tests/e2e/miaobi_multiscenario_live.py` 通过公开 API 逐一创建临时任务，完成输入验证、3 个核验事实、确定性缺口计算、两个可信业务块、文稿审校和 JSON 导出，随后只软删除测试任务：

| 场景 | 确定性结果 | 技术链路 | 业务状态 |
|---|---:|---|---|
| 洪涝 | 舟艇缺口 12 艘 | 通过 | 模板待客户确认 |
| 火灾 | 消防车辆缺口 8 辆 | 通过 | 模板待客户确认 |
| 地质灾害 | 转移车辆缺口 6 辆 | 通过 | 模板待客户确认 |
| 雨雪冰冻 | 除冰车辆缺口 8 辆 | 通过 | 模板待客户确认 |
| 疫情 | 隔离床位缺口 60 张 | 通过 | 模板待客户确认 |
| 反恐维稳 | 巡控小组缺口 4 组 | 通过 | 模板待客户确认 |

### 导出与协同

- DOCX、PDF、JSON、XLSX、GeoJSON 均由真实导出接口生成，下载文件 Checksum 与服务端记录一致。
- DOCX/PDF 使用渲染工具实际转为页面图片检查；两页中文、分页、表格和页码无裁切。
- XLSX 包含“已核验事实”“确定性计算”“备选方案”三个真实工作表。
- GeoJSON 使用选中方案路径与项目坐标生成真实 LineString；缺失坐标时返回错误，不生成假路线。
- 5 个协同客户端连接同一 Hocuspocus 房间并同步不同改动；服务重启后快照恢复。
- 内网仅 HTTP 地址不提供浏览器 Secure Context；启动兼容层只补齐 Plate/Yjs 用于确定性初始状态的 SHA-256 摘要，不替代随机数、鉴权、服务端可信哈希或生产 TLS。真实浏览器在该地址已显示“协同已同步”。

### 内网服务器与重启恢复

- 部署地址：`http://10.5.113.232:9002/`，妙笔入口：`/miaobi/`。
- 服务器：x86_64、32 vCPU、251 GiB 内存、879 GiB 系统盘（验收时可用约 792 GiB）。
- 14 个 Compose 服务全部 `running/healthy`；API、Worker、Scheduler、Agent Runtime、MCP、协同、ASR 和全部中间件均位于服务器，不依赖本机。
- 不删除 Volume 完整停止并重启服务后，严格预检仍为 `ready=true`：Ground Truth 14/14、3 套方案、15 条图谱事实、三路检索命中正常。
- 重启后同一 DSH Session 继续追问“其中搜救人员缺口是如何计算出来的”，真实执行任务资料读取与知识检索，回答 500−320=180，并恢复上一轮消息、事件时间线和已插入修订内容。
- 本机 16 个项目与结构化测试容器全部正常停止且未删除 Volume；本机 `8080` 已不可访问时，内网 `/health/ready` 仍返回 HTTP 200，浏览器中的 Plate、协同和历史会话继续正常工作。
- 服务器没有 NVIDIA GPU，因此未在该主机部署 Qwen3.8-27B-NVFP4；模型推理继续调用已配置且真实可用的独立推理服务。

### 性能

- 10 个并发工作区请求全部成功，中位延迟 1016 ms，P95 1040 ms。
- 100 块文稿保存并审校 31 ms。
- 1000 个流式 `answer_delta` 通过动画帧缓冲一次提交，避免逐 Token 重绘。
- Plate 初始应用包 249.80 kB（gzip 80.10 kB）；完整编辑器延迟块 1,154.02 kB（gzip 341.93 kB）。

## 关键执行命令

```bash
docker run --rm -v "/Users/tianqi/Documents/828semantic:/repo" \
  -w /repo/semantica-enterprise semantica-enterprise:0.10.0 \
  pytest tests/unit -q

docker run --rm --network semantica-enterprise_default \
  -v "/Users/tianqi/Documents/828semantic:/repo" \
  -w /repo/semantica-enterprise semantica-enterprise:0.10.0 \
  pytest tests/contract/test_semantica_adapter.py -q

docker compose exec -T -e MIAOBI_REPO_ROOT=/app \
  -e MIAOBI_DEMO_URL=http://127.0.0.1:8080 api \
  python /tmp/miaobi_multiscenario_live.py

cd apps/miaobi-web && pnpm test && pnpm typecheck && pnpm build
COLLABORATION_CLIENTS=5 node apps/miaobi-collab/tests/live-collaboration.mjs
docker compose exec -T agent-runtime npm test
```

管理员密码、模型密钥、数据库密码和内部服务 Token 均通过运行时环境或 Secret 注入，本报告未记录其值。

## 分级结论

1. 已真实运行验证：地震主闭环、六场景技术闭环、Semantica、DSH、结构化实库、Plate、协同、导出、浏览器和不删卷恢复。
2. 协议级自动化验证：模型 429、服务超时、跨租户与注入防护等故障边界。
3. 尚需外部联调：商业 GIS、实时交通、真实医院/库存系统和客户公文模板。
4. 尚需业务确认：除地震外六类灾种的正式规则、阈值、参数和决策权限。
