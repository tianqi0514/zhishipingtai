# 智能问答 UI 与 DeepSeek Harness 事件模型

## 业务布局

智能问答在桌面端使用三栏工作区：左栏管理会话，中栏展示消息和固定输入框，右栏集中呈现“执行过程、检索轨迹、召回依据”。三栏分别滚动，长对话不会撑高整个页面；较窄屏幕将右栏变成可打开的抽屉。

检索高级选项默认展开，知识范围统一跟随页面顶部“当前知识空间”；用户可直接控制全文、向量、图谱、重排和 Top K。切换空间后，会话列表、检索轨迹和召回依据一起重载，不再在问答区重复展示一套空间选择器。平台不允许关闭全部基础召回通道；向量或重排模型不可用时，后端返回真实降级警告并继续使用可用通道。

## 可核验执行过程

右栏的“执行过程”来自 DeepSeek Harness Session Event，不是模型私有 Chain-of-Thought，也没有用固定延时伪造阶段。计时从服务端 `turn_started` 时间戳开始，在 `turn_completed`、`turn_failed` 或 `turn_cancelled` 时停止；刷新页面后使用持久化事件恢复。

| DSH/平台事件 | 用户看到的阶段 | 可展示内容 |
|---|---|---|
| `turn_started` | 正在分析问题 | Turn 开始时间、是否存在历史轮次 |
| `step_started` | 正在理解上下文 | Step 序号、简洁状态 |
| `retrieval_started` | 正在检索知识 | 实际查询词、授权知识空间、启用通道 |
| `tool_started` | 正在调用知识工具 | 工具名称、开始时间 |
| `tool_finished` | 已取得工具结果 | 成功/失败、结果数、真实耗时 |
| `retrieval_ranked` | 正在整理引用 | 召回数量、融合/重排、最终排名、警告 |
| `answer_delta` | 正在生成回答 | 仅用于平滑追加可见答案文本 |
| `citation` | 正在整理引用 | 已验证引用与召回项的关联 |
| `warning` | 降级或证据不足 | 可理解的告警，不暴露内部凭据 |
| `turn_completed` | 已完成 | 服务端真实总耗时 |
| `turn_failed` | 生成失败 | 标准化错误和重试入口 |
| `turn_cancelled` | 已停止 | 取消状态和重新生成入口 |

不展示模型隐式推理 Token、系统提示词全文、Harness 内部 Token、API Key 或服务凭据。文档中的提示注入内容始终作为不可信知识数据处理。

## 流式与状态恢复

- FastAPI 是浏览器唯一入口，使用 SSE 转发 Harness 流。
- `answer_delta` 通过 `requestAnimationFrame` 小批次刷新，避免大量小片段造成闪烁。
- 只有用户位于消息底部附近时自动跟随；离开底部后出现“返回最新回答”。
- 切换会话会终止旧流、旧计时器和待处理渲染队列，避免串流。
- Session Event 带服务端时间戳、序号和 Harness 序号；投影层按消息、事件类型和 Harness 序号去重。
- Harness append-only Session 是 Agent 上下文权威来源；PostgreSQL 保存面向前端的消息、轨迹和引用只读投影。

## 检索轨迹和召回依据

检索轨迹来自真实 `QueryRun`、`RetrievalTrace` 和工具事件，展示原始问题、上下文改写、知识空间、各通道召回数、去重、RRF、重排、耗时和警告。召回依据严格按最终排名排列，保留各通道分数、融合分、重排分、页码/时间区间和结构位置。

回答中的引用编号由后端校验。点击引用会切换到右侧召回依据并定位相应卡片；打开片段时 FastAPI 再次执行知识空间权限检查。Harness 仍只通过短期受限凭据调用 Knowledge Tool API，不直连 PostgreSQL、OpenSearch、Qdrant 或 FalkorDB。
