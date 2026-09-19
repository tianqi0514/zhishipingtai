# 受控写作 API

新增接口位于现有 `/api/v1/writing` 路由：

- `POST /projects/{id}/corpus-packages`
- `GET /corpus-packages/{id}`
- `GET /corpus-packages/{id}/artifacts`
- `POST /projects/{id}/inheritance/preview`
- `GET /projects/{id}/inheritance/{alignment_id}`
- `POST /projects/{id}/inheritance/apply`
- `POST /documents/{id}/changesets/preview`
- `GET /documents/{id}/changesets/{changeset_id}`
- `POST /documents/{id}/changesets/{changeset_id}/apply`
- `POST /projects/{id}/input-changes/{preview_id}/rollback`

现有事实变更 preview/apply、写作生成、验证和导出接口继续复用。写接口要求 editor 权限；只读接口至少要求项目可见。请求模型继承严格 `extra=forbid`。冲突、版本过期和重复应用分别返回可理解的 409/422，不能用 HTTP 200 伪装失败。
