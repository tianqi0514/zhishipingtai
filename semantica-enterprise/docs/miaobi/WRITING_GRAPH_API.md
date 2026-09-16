# 写作图谱与妙笔 API

所有路径以 `/api/v1` 为前缀，并执行租户、知识空间、项目和文稿权限校验。

## 写作图谱

- `GET /writing-graph/governance/summary`
- `GET /writing-graph/governance/items`
- `GET /writing-graph/governance/items/{type}/{id}`
- `POST /writing-graph/governance/items/{type}/{id}/decide`
- `GET|POST /writing-graph/releases`
- `GET /writing-graph/releases/{id}`
- `GET /writing-graph/releases/{id}/graph`

## 妙笔消费与绑定

- `POST /writing/projects/{id}/facts/adopt-writing-graph`
- `POST /writing/projects/{id}/generate-report`
- `GET /writing/generation-runs/{id}`
- `GET|POST /writing/documents/{id}/versions`
- `GET /writing/documents/{id}/chunks`
- `GET|POST /writing/documents/{id}/bindings`
- `POST /writing/documents/{id}/validate`

## 事实变更

- `POST /writing/projects/{id}/input-changes/preview`
- `POST /writing/projects/{id}/input-changes/apply`
- `POST /writing/projects/{id}/input-changes/{preview_id}/cancel`

预览不会修改正式事实和正文。应用请求必须携带未过期的预览和所选提案；重复应用返回冲突，不会重复生成版本。

## 导出

- `POST /writing/documents/{id}/exports`
- `GET /writing/documents/{id}/exports`
- `GET /writing/exports/{job_id}/download`

DOCX/PDF 在导出前再次执行正文绑定、来源版本、精确数字和业务确认门禁。

