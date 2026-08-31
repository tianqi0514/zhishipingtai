# 故障排查手册

## 登录后再次跳回登录页

检查 `docker compose logs api`、浏览器 Network 中 `/auth/me` 状态、`APP_SECRET_KEY` 是否在容器重建时变化。401 由前端统一清理 Token 并跳转；不要循环重试旧 Token。

## Worker 任务不动

```bash
docker compose ps worker rabbitmq redis
docker compose logs --since=10m worker rabbitmq
docker compose exec -T worker celery -A apps.worker.celery_app:celery_app inspect ping
```

Worker 停机期间已入 RabbitMQ 的 queued 任务会在恢复后继续。不要直接改数据库任务状态。

## MinIO `XMinioStorageFull`

```bash
docker exec semantica-enterprise-minio-1 df -h /data
docker system df
```

先清理无引用的 Docker build cache/旧镜像，不要删除 `minio-data` 或其他业务 Volume。MinIO 默认在磁盘低于安全阈值时拒绝写入。

## 某个检索通道不可用

页面“检索轨迹”会显示全文/向量/图谱告警。检查：

```bash
curl -fsS http://127.0.0.1:9200/_cluster/health
curl -fsS http://127.0.0.1:6333/collections
redis-cli -p 6380 ping
```

只要其他通道可用，API 会返回降级结果而不是 500。恢复服务后重试；发布失败时 PostgreSQL 当前版本过滤仍会阻止旧索引泄露。

## Harness 对话失败或重启后丢历史

```bash
docker compose logs --since=10m agent-runtime api
docker compose exec -T agent-runtime node -e "fetch('http://localhost:8090/health/ready').then(r=>r.text()).then(console.log)"
python3 tests/integration/restart_recovery.py
```

确认 `harness-data` Volume 存在、锁定 commit 正确、`agent_service_secret` 两端一致。不要手工编辑 Session JSONL。

## 模型 429/超时

模型配置中设置合理的超时、重试和并发。系统对 429/5xx 做有限退避，不无限重试；某次模型治理失败不会删除已解析文档，可在文档画像中重试。

## 解析失败

在文档任务详情查看阶段、百分比、错误码和 Worker 日志。扫描 PDF 需要 Docling/OCR；旧 Office 需要 LibreOffice；音视频需要 ffmpeg；转写/视觉语义还需要对应模型配置。扩展名、MIME、Magic 不一致会被安全拒绝，这是预期行为。
