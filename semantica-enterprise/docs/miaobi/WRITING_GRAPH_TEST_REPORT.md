# 写作图谱与妙笔联动测试报告

测试日期：2026-09-17。分支：`codex/writing-graph-miaobi-validation`。

## 真实数据

- 验收空间：`writing-graph-validation`。
- 已发布写作图谱：Evidence 25、Entity 78、Claim 58、Fact 93、Relation 13。
- 文章：15 章、7,302 个中文字符、90 个引用、4 项计算。
- Ground Truth：搜救缺口 180、县域床位缺口 220、全域床位缺口 80、帐篷缺口 1800。

## 本轮回归

| 层 | 结果 | 内容 |
|---|---:|---|
| Python 单元/合约 | 215/215 | 写作图谱、治理、查询、Semantica、绑定、影响、部署和质量门禁 |
| DSH Runtime 合约 | 32/32 | 正式运行镜像内工具注册、写作证据策略、多 Step、取消恢复和事件投影 |
| Plate 前端 | 40/40 | 完整编辑器、抽取工作台、章节依据、生成和影响弹窗 |
| TypeScript | 通过 | `tsc -b` |
| Vite production build | 通过 | 保留已知大包告警，未提高阈值掩盖 |
| API 文稿审校 | 通过 | V12，0 issues |
| DOCX/PDF | 通过 | 真实接口生成并逐页渲染 |
| 浏览器 | 通过 | 真实登录、Plate、原生表格、影响入口、双数据通道、两档视口、Console 0 |

测试发现并修复：模型表格包装被字符串化、Agent 漏填精确数值依赖、同一逻辑指标跨版本无法定位、部分接受后依赖块长期 stale、测试环境协同端口只绑定回环地址。

## 重启与镜像持久化

- 最终代码层镜像：`sha256:9333236572f6288d2ad209f0eef6adb9b15c6d3b5fd2f4e85129eebbda026f97`。
- API、Worker、Scheduler、MCP Server 使用同一镜像；14 个必需服务均为 healthy。
- 不删除 Volume 重建应用服务后，R1 写作图谱、12 个文章版本、197 个 Chunk、146 条绑定和 V12 DOCX/PDF 均可读。
- 当前正文保留 400/100，历史版本保留 320/180；0 stale，质量校验 0 issues。
- 生产 `Secure` Cookie 策略保持不变，命令行验收客户端使用登录响应中的 Bearer Token。
