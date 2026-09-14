# 青岚县地震演练独立演示材料

所有地名、单位及业务记录均属虚构，不代表真实灾情，不构成调度命令。此目录不含客户原件或真实个人信息。

首轮仅上传 `01-上传材料` 中的4份文件。该目录特意把设施依赖、设施运行状态、保障职责及人员统计拆在不同来源中，供跨来源核验使用。

`02-变更参考/05-人员到位更新.md` 只供第二轮输入修正参考，不能混入首轮。本轮只实测影响预览和取消，未将该文件上传或纳入固定知识产品版本。`source_truth.json` 是测试核对用的事实与预期结果，不应上传为业务来源。生成器及 `qa` 目录同样不属于上传材料。

`03-管理员预置配置` 是妙笔应用配置模板，不是用户业务材料。已在测试服务器建立独立场景，用户复演直接选择，无需上传配置。操作步骤和实测结果在 `docs/miaobi/MIAOBI_QINGLAN_LIVE_DEMO.md`、`MIAOBI_QINGLAN_LIVE_TEST_REPORT.md`。

## 重跑

从任意目录运行以下命令。生成器只重建本独立目录中的同名文件，使用与本次生成相同的捆绑运行时。

```bash
/Users/tianqi/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /Users/tianqi/Documents/828semantic/semantica-enterprise/demo/miaobi/qinglan-live-20260914/generate_fixture.py
```

DOCX 采用正式备忘录样式，Letter 纵向、黑色标题、宋体正文。PDF 嵌入支持中文的字体。修改生成器内容后须重新渲染并逐页检查 DOCX 和 PDF，不应仅用文本抽取代替版式核验。
