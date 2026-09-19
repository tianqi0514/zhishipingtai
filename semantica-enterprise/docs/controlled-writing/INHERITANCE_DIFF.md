# 继承 Diff

继承 Diff 比较历史语料结构与当前项目事实，绝不复制历史项目值。

匹配顺序：标准字段 ID 精确匹配；术语归一匹配；量纲兼容的有限语义候选。第三层永远要求人工确认。

输出决策：

- `use_current_project_value`：采用当前事实版本。
- `convert_or_reconfirm`：量纲或单位需换算/确认。
- `semantic_match_requires_confirmation`：语义候选，不能直接应用。
- `recompute`：输入齐全，由公式重新计算。
- `missing_current_fact`：保留 UNDEFINED，必要时阻断相关章节。
- `not_applicable`：剪枝，不生成依赖内容。

预览保存事实版本、语料包校验和和目录指纹。任一基础对象变化后旧预览失效，必须重新计算。
