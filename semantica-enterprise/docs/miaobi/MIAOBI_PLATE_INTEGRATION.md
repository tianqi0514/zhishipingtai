# Plate 集成说明

妙笔前端位于 `apps/miaobi-web`，是独立 React 19 + TypeScript 应用。Plate 参考仓库固定在提交 `8f65d77f8b4709833436e63661e4d061f709258f`；项目使用精确依赖版本和已提交的 `pnpm-lock.yaml`，没有引用 Plate Plus 商业代码，也没有修改 `node_modules`。

## 已集成能力

- `platejs` 核心、基础块、标题、列表、缩进、对齐、表格、列、链接、目录、脚注、日期、数学公式、代码块、媒体、题注、提及、Slash Command、拖放和块选择。
- Markdown、HTML、CSV、DOCX 粘贴及导入导出插件。
- AI、Copilot、Suggestion、Comment。
- Yjs、Hocuspocus Provider、IndexedDB 离线缓存与远程光标。
- 自建业务节点：`knowledge_citation`、`verified_fact`、`computed_metric`、`inference_conclusion`、`manual_assumption`、`decision_gate`、`alternative_plan`、`action_task`、`data_table`、`geo_route`、`risk_warning`。

## 数据边界

Plate Value 是文稿内容；`WritingDocumentVersion` 保存不可变业务快照；Yjs 只承载实时协同更新。计算值和推演结论默认不可直接改数值，更新必须回到事实、公式或规则；人工覆盖会保留原值、理由和审计记录。

## 构建与验证

```bash
cd apps/miaobi-web
pnpm install --frozen-lockfile
pnpm test
pnpm exec tsc --noEmit
pnpm build
```

2026-09-09 验证为 Vitest 3/3、TypeScript 通过、Vite 生产构建通过。完整插件组合产物约 1.4 MB（gzip 约 421 KB），Vite 给出大 Chunk 警告；功能可用，但下一版本应按编辑器工具组动态分包，不能通过调大警告阈值掩盖问题。

## 许可证边界

仅使用 Plate 官方开源 npm 包和开源 API。项目自行实现应用布局、工具栏、评论面板、可信业务块、协同 UI 和导出编排；Plate Plus 中的商业模板或 UI 代码未复制进仓库。
