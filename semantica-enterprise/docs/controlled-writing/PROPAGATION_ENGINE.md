# 传播引擎

传播闭包使用有向依赖图和受限广度遍历，记录直接/间接影响、关系、动作、确定性和完整路径。

| 关系 | 动作 |
|---|---|
| DERIVES_FROM | recompute |
| DEPENDS_ON | rejudge |
| SUPPORTS | restate_claim |
| CAUSES | rewrite_argument |
| CONTRADICTS | block |
| ASSUMES | revalidate |
| RESTATES | sync_text |
| COMPARES_WITH | recheck_choice |
| REFERENCES | recheck_validity |
| CONTEXT_OF | relayout |

遍历包含环检测、最大深度 12 和最大节点 2000 的默认保护。确定依赖可自动重算；需要语言调整的 Chunk 生成建议；只有语义相似但未绑定的文本仅列为疑似影响，不自动修改。
