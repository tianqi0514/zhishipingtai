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
| Python 单元/合约 | 204/204 | 写作图谱、治理、查询、Semantica、DSH契约、绑定、影响和质量门禁 |
| Plate 前端 | 40/40 | 完整编辑器、抽取工作台、章节依据、生成和影响弹窗 |
| TypeScript | 通过 | `tsc -b` |
| Vite production build | 通过 | 保留已知大包告警，未提高阈值掩盖 |
| API 文稿审校 | 通过 | V12，0 issues |
| DOCX/PDF | 通过 | 真实接口生成并逐页渲染 |
| 浏览器 | 通过 | 真实登录、Plate、原生表格、影响入口、双数据通道、两档视口、Console 0 |

测试发现并修复：模型表格包装被字符串化、Agent 漏填精确数值依赖、同一逻辑指标跨版本无法定位、部分接受后依赖块长期 stale、测试环境协同端口只绑定回环地址。

