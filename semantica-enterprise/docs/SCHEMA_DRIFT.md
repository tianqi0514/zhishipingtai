# Schema 漂移检测与恢复

结构发现对规范化 Catalog 计算 SHA-256 指纹，并与当前版本生成 Diff：新增/删除对象、字段、兼容/不兼容类型变化、主键、外键、索引、注释和改名候选。

处理规则：

- 新增无关字段：保留活动映射，页面提示变化。
- 删除映射字段或不兼容类型变化：映射变为 `stale`。
- 主键或 Join 相关外键变化：映射变为 `stale`。
- `stale` 映射的 Plan/IR 校验、编译、执行和 DSH 工具统一返回 409。
- 源库恢复到历史相同结构时仍创建新的单调递增 Schema Version；迁移 `0018_schema_fingerprint_history` 移除了 `(source_id, schema_fingerprint)` 唯一约束，保留完整漂移时间线。
- 恢复结构不会自动重新激活旧映射；管理员必须基于当前 Schema 创建/验证/激活新版本，避免静默恢复带来的权限和口径风险。

真实 E2E 会把 Fixture `orders.status` 临时改名为 `status_drift`，验证 stale/409 后在 `finally` 恢复字段并激活新版本。Fixture 使用 tmpfs，且测试无论成功失败都尝试恢复源结构。
