# 妙笔协同编辑

协同服务使用自建 Hocuspocus 3.4.4 + Yjs 13.6.32，运行在独立 Docker 服务 `miaobi-collab`。浏览器从 FastAPI 获取短期文稿级 JWT，再连接 WebSocket；服务器保存原子 Yjs 快照到独立持久卷。

## 安全边界

- JWT 固定 `aud=miaobi-collaboration`，仅允许 HS256。
- 房间名必须为 `writing-<tenant_uuid>-<document_uuid>`，并与 Token 中 `room` 完全相同。
- Token 包含用户、租户、项目、文稿和角色；跨文稿复用、过期或伪造 Token 被拒绝。
- viewer/commenter 连接为只读；编辑、审校、发布和所有者角色按业务权限签发。
- 服务以非 root 用户运行，单消息最大 4 MiB，快照使用临时文件后原子重命名。

## 持久化职责

Yjs 保存实时编辑状态，IndexedDB 保存浏览器短期离线更新；PostgreSQL 保存评论、权限、业务元数据和不可变文稿版本；MinIO/应用数据卷保存导出文件。Yjs 不能替代正式版本。

## 部署

本机默认 `ws://<API 主机>:8092`。内网部署可设置：

```dotenv
COLLABORATION_BIND_ADDRESS=0.0.0.0
COLLABORATION_PUBLISHED_PORT=9003
COLLABORATION_PUBLIC_URL=ws://10.5.113.232:9003
```

单元安全测试 2/2 和双客户端实时同步已通过；最终部署需要再次执行“写入—重启服务—读取”恢复测试。
