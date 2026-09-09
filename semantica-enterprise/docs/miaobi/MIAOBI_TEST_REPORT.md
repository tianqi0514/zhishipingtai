# 妙笔简洁写作闭环测试报告

测试日期：2026-09-10（Asia/Shanghai）

测试分支：`codex/miaobi-simple-writing-flow`

Plate 能力基线：`8f65d77f8b4709833436e63661e4d061f709258f`

## 验收结论

本轮把旧版“模块很多、操作像配置后台”的妙笔收敛为两个一级入口和三步业务流程：

`方案任务 → 报告编辑`

`输入确认 → 分析计算 → 报告编辑`

客户样本盲测已达到 100/100。系统从 7 份隔离客户材料、587 个真实知识片段开始，由 DeepSeek Harness 生成九章报告；正式正文共 8,985 个可见字符、70 个 Plate 顶层节点、111 个正文引用标记、4 个确定性测算块和 1 个 Semantica 推演块。没有将最终样稿、下发件或推演依据成品作为检索来源。

## 自动化测试

| 测试层 | 成功 | 失败 | 跳过 | 真实验证内容 |
|---|---:|---:|---:|---|
| Python 单元测试 | 550 | 0 | 0 | 写作流程、版本、权限、导出、计算、影响更新及知识底座回归 |
| Semantica 合约 | 19 | 0 | 0 | DatalogReasoner、证据链、预览、发布和撤回 |
| MySQL/PostgreSQL 实库集成 | 10 | 0 | 0 | Schema、实时预览、只读限制、参数化查询和漂移 |
| API E2E | 3 | 0 | 0 | 知识分析、业务推演、结构化语义查询；临时数据已清理 |
| DeepSeek Harness 合约 | 22 | 0 | 0 | 插件、工具策略、多步骤、事件、取消与 Session 恢复 |
| Plate 组件 | 14 | 0 | 0 | 完整编辑器、可信节点、引用、协同值替换和 1000 个增量 |
| TypeScript / 生产构建 | 2 | 0 | 0 | `tsc -b` 与 Vite production build |
| 客户样本质量门禁 | 19 | 0 | 0 | 100/100；长度、章节、重复、引用、可信块与 A4 全通过 |

全量 Python 单元与 Semantica 合约共 569 项一次通过。结构化数据库 E2E 首次被私网访问策略拒绝；给本机隔离 fixture 显式加载测试白名单后通过，生产默认私网策略未放宽。

## 客户样本盲测

| 指标 | 结果 |
|---|---:|
| 隔离输入材料 | 7 份 |
| 知识片段 | 587 个 |
| DSH 真实生成耗时 | 171.62 秒 |
| 正文章节 | 9/9 |
| 可见字符 | 8,985 |
| 客户参考稿字符 | 10,849 |
| 长度比 | 82.8% |
| Plate 顶层节点 | 70 |
| 正文引用标记 | 111 |
| 引用证据行 | 46 |
| 证据来源文档 | 6 份 |
| 确定性测算块 | 4 个 |
| Semantica 推演块 | 1 个 |
| 重复长句 | 0 |
| 工作草稿/平台提示泄漏 | 0 |
| 最终评分 | 100/100 |

正式结论来自三条不同能力链：叙述由 DSH 基于当前知识产品 Release 撰写；资源缺口由确定性计算生成；灾害等级由 Semantica 规则推演生成。模型不能把自由文本直接标记成测算值或正式推演结论。

## 输入变化与影响更新

真实浏览器连续执行：

1. 可用搜救人员 `320 → 400`。
2. 预览显示搜救人员缺口 `180 → 100`，并定位 1 个正文可信内容。
3. 点击“应用更新”后，Plate 正文变为 100，协同状态为“已同步”。
4. 再执行 `400 → 320`，预览显示 `100 → 180`，正文恢复为 180。
5. 页面和 API/Worker/协同/Agent 服务重启后，输入版本 9 的 320、计算结果版本 4 的 180、70 个正文节点和协同内容继续存在。

该测试发现并修复三类生产问题：历史计算运行错误覆盖当前事实、旧计算绑定无法定位同一逻辑指标、服务端权威更新没有通过 Slate 操作发布到 Yjs。回归测试新增了多次往返修改和旧绑定场景。

## 浏览器验证

真实浏览器完成：

- 首次进入默认看到“输入确认”，不是技术配置页。
- 9 项关键输入、来源、版本和确认状态可见。
- 分析计算显示输入、公式、输出和 Semantica 推演依据。
- 报告编辑使用 Plate 53.3.11，`/` 指令、工具栏、右侧助手、引用依据、计算与推演均为真实入口。
- 正文中只使用轻量 `[n]`、`测算`、`推演` 标记，详细依据在右侧展示。
- 重启后刷新恢复当前任务、文稿、引用、320 人与 180 人缺口。
- 1280×720 无整体横向溢出，正文和右栏独立滚动。

当前浏览器控制工具未提供独立 Console 日志接口，因此本轮只记录“页面无可见错误、服务日志无 Traceback/Unhandled/CRITICAL/FATAL”。不将无法直接读取的 Console 结果写成 0 error。

## 导出验收

最终 V16 产物：

- 正式报告 DOCX：7 页渲染检查。
- 生成依据 DOCX：2 页渲染检查，输入为 320、结果为 180。
- PDF：8 页、A4、未加密，实际渲染检查。
- JSON：事实、推演、计算、方案与正文均可机器核验。
- XLSX：`已核验事实`、`确定性计算`、`备选方案` 3 个工作表。

DOCX 和 XLSX 均通过 ZIP 完整性检查；PDF 经 `pdfinfo` 验证为 A4。旧的 V14 产物包含修复前的过期测算记录，不作为交付物。

## Docker 与日志

本机必需服务均为 `running/healthy`：API、Worker、Scheduler、Agent Runtime、MCP、妙笔协同、PostgreSQL、Redis、RabbitMQ、MinIO、OpenSearch、Qdrant 和 FalkorDB。迁移表当前到 `0027_miaobi_agent_session_purpose`。

在不删除 Volume 的前提下重启 API、Worker、Scheduler、Agent Runtime、MCP 与妙笔协同后，项目、事实、当前文稿版本与协同状态恢复。最终服务日志未发现 Traceback、Unhandled、CRITICAL、FATAL 或 ERROR。

## 验证命令

```bash
# Python 单元与 Semantica 合约
docker run --rm -v "$(cd .. && pwd):/workspace" \
  -w /workspace/semantica-enterprise -e PYTHONPATH=/workspace/semantica-enterprise \
  --entrypoint pytest semantica-enterprise:0.10.0 -q tests/unit tests/contract

# MySQL/PostgreSQL 隔离 fixture
docker run --rm --network semantica-enterprise_default \
  -v "$(cd .. && pwd):/workspace" -w /workspace/semantica-enterprise \
  -e PYTHONPATH=/workspace/semantica-enterprise -e RUN_STRUCTURED_DB_TESTS=1 \
  -e SOURCE_PRIVATE_HOST_ALLOWLIST=structured-postgres,structured-mysql,postgres \
  --entrypoint pytest semantica-enterprise:0.10.0 \
  -q tests/integration/test_structured_databases.py

# 完整 Plate 前端
cd apps/miaobi-web
npm test -- --run
npm run typecheck
npm run build

# DSH 插件
docker compose exec -T agent-runtime npm test
```

管理员密码、模型密钥、数据库密码和内部服务 Token 只通过运行环境或 Secret 注入，本报告不记录其值。

## 分级结论

1. 已真实运行验证：客户样本盲测、DSH 写作、Semantica 推演、确定性测算、输入影响更新、Plate/Yjs 同步、MySQL/PostgreSQL、导出与不删卷恢复。
2. 协议级自动化验证：越权、注入、超时、取消、模型 429、服务失败和敏感信息脱敏。
3. 仍需客户确认：正式灾情事实、响应权限、业务公式参数、公文模板和最终发布责任。
4. 非本轮目标：用音视频强行丰富地震报告；本轮未让无业务价值的音视频进入写作主链。
