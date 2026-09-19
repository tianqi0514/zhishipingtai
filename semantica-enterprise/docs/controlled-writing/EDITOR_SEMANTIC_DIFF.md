# 编辑语义 Diff

编辑保存前依次生成字符事件、结构操作和语义判断。结构操作为 ADD、DEL、MOD、MOVE；语义关系为 RESTATES、REFERENCES、DERIVES_FROM、CONTRADICTS、ASSUMES、SUPPORTS、CAUSES、CONTEXT_OF、NEW、UNBOUND。

确定性规则优先：移动只改变上下文；删除正文不删除事实；已绑定数值修改转入事实变更；未绑定精确数字阻断；已绑定措辞变化不触发重算。模型只对无法确定的新增分析性表达做辅助分类，不能扩大确定传播范围。

应用时校验基础文稿版本和选择项。受控数值不允许走普通 MOD；必须通过事实候选修改、影响预览和选择性应用。
