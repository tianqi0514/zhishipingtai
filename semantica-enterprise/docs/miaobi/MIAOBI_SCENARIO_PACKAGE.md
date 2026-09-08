# 妙笔场景包

场景包把输入字段、本体映射、规则、公式、工具、章节、输出和人工确认节点固化为版本化契约。它不是大模型提示词附件。

## 契约组成

- `input_schema`：受支持 JSON Schema 子集、必填字段和数值范围。
- `ontology_mapping`：输入字段到业务对象和属性的映射。
- `rules` 与 `criteria`：Semantica 规则及确定性判据。
- `chapter_template` 与 `output_schema`：目录和输出结构。
- `decision_gates`：正式发布前必须确认的业务决定。
- `comparison_dimensions`：方案比较口径。
- `config`：`minimum_plan_count`、`default_plan_count`、超时和限制。

版本激活前校验必填项、字段引用、人工闸门唯一性、章节和方案数量，保存内容 Checksum。项目一旦绑定版本，不会随场景包后续编辑漂移。

## 七类灾种状态

仓库提供地震、洪涝、火灾、地质灾害、冰雪灾害、疫情和反恐维稳七个独立 JSON 契约。地震已经按客户材料和确定性 Ground Truth 深度验证，状态为 `accepted`。其余六类具备独立输入 Schema、章节、确认节点和最小数据契约，但缺少客户确认的权威规则与参数，状态保持 `pending_customer_confirmation`；不能将其描述为已完成业务验收。
