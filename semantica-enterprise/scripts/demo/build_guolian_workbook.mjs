import fs from "node:fs/promises";
import path from "node:path";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const repoRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const outputDir = path.join(repoRoot, "demo/guolian");
const buildDir = path.join(repoRoot, ".demo-build/xlsx");
const outputPath = path.join(outputDir, "供应商风险台账（演示版）.xlsx");

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(buildDir, { recursive: true });

const workbook = Workbook.create();
const risks = workbook.worksheets.add("供应商风险台账");
const rules = workbook.worksheets.add("评分与治理说明");
const summary = workbook.worksheets.add("风险概览");

const notice = "演示数据，不代表国联集团真实经营数据。";
const headers = [
  "风险编号", "供应商名称", "统一编码", "风险类型", "风险等级", "发现日期",
  "涉及产品", "涉及项目", "责任部门", "处置状态", "风险评分", "建议措施", "数据声明",
];
const rows = [
  ["RISK-2026-001", "东方智造", "SUP-001", "交付延期", "高", new Date("2026-08-28"), "NexusOne", "智慧流程中枢项目", "数字化管理部", "跟踪中", null, "协调替代供货与部署窗口", notice],
  ["RISK-2026-002", "东方智造有限公司", "SUP-001", "名称重复", "中", new Date("2026-08-29"), "NexusOne", "智慧流程中枢项目", "集团采购管理部", "待治理", null, "依据统一编码确认主体别名", notice],
  ["RISK-2026-003", "江南信息", "SUP-002", "安全整改", "中", new Date("2026-07-16"), "统一身份组件", "集团知识底座项目", "风险管理部", "整改中", null, "完成漏洞复核后关闭风险", notice],
  ["RISK-2026-004", "太湖云科", "SUP-003", "服务连续性", "低", new Date("2026-06-11"), "集团数据交换平台", "采购协同平台升级项目", "数字化管理部", "已缓解", null, "保留应急切换演练记录", notice],
  ["RISK-2026-005", "华东系统集成", "SUP-004", "人员变更", "低", new Date("2026-08-05"), "智慧流程引擎", "智慧流程中枢项目", "项目管理办公室", "观察中", null, "补齐关键岗位备份人员", notice],
  ["RISK-2026-006", "新城数据服务", "SUP-005", "材料缺失", "中", new Date("2026-08-19"), null, "集团知识底座项目", "集团采购管理部", "待补充", null, "补交资质文件并完成复核", notice],
];

risks.getRange("A1:M1").values = [headers];
risks.getRange(`A2:M${rows.length + 1}`).values = rows;
risks.getRange("K2").formulas = [["=IF(E2=\"高\",5,IF(E2=\"中\",3,1))"]];
risks.getRange(`K2:K${rows.length + 1}`).fillDown();
risks.freezePanes.freezeRows(1);
risks.showGridLines = false;
risks.getRange("A1:M1").format = {
  fill: "#155EEF",
  font: { color: "#FFFFFF", bold: true },
  verticalAlignment: "center",
  horizontalAlignment: "center",
  wrapText: true,
  rowHeight: 34,
};
risks.getRange(`A2:M${rows.length + 1}`).format = {
  font: { color: "#27364B" },
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: "#D9E2F1" },
  rowHeight: 42,
};
risks.getRange(`F2:F${rows.length + 1}`).setNumberFormat("yyyy-mm-dd");
risks.getRange(`K2:K${rows.length + 1}`).setNumberFormat("0");
risks.getRange(`E2:E${rows.length + 1}`).dataValidation = {
  rule: { type: "list", values: ["高", "中", "低"] },
};
risks.getRange(`J2:J${rows.length + 1}`).dataValidation = {
  rule: { type: "list", values: ["待治理", "跟踪中", "整改中", "观察中", "待补充", "已缓解", "已关闭"] },
};
risks.getRange(`K2:K${rows.length + 1}`).conditionalFormats.add("colorScale", {
  colors: ["#E8F8EF", "#FFF3CD", "#FDECEC"],
  thresholds: ["min", "50%", "max"],
});

const widths = [18, 20, 14, 16, 12, 14, 20, 24, 20, 14, 12, 34, 30];
for (let i = 0; i < widths.length; i += 1) {
  risks.getRangeByIndexes(0, i, rows.length + 1, 1).format.columnWidth = widths[i];
}

rules.getRange("A1:D1").values = [["等级", "默认评分", "治理含义", "演示说明"]];
rules.getRange("A2:D4").values = [
  ["高", 5, "需要立即跟踪并明确责任部门", notice],
  ["中", 3, "需要补充证据或制定整改计划", notice],
  ["低", 1, "纳入例行观察，保留处置记录", notice],
];
rules.getRange("A6:D6").values = [["治理问题", "系统建议", "人工动作", "可回滚"]];
rules.getRange("A7:D10").values = [
  ["供应商别名", "依据名称与统一编码提出合并建议", "确认、拆分或设置标准名称", "是"],
  ["风险证据不足", "提示缺少来源或低置信度内容", "补充来源片段并重新加工", "是"],
  ["文档版本冲突", "比较生效日期和条款差异", "选择当前有效版本", "是"],
  ["敏感字段", "服务端禁止或脱敏", "配置预览策略", "是"],
];
rules.showGridLines = false;
for (const rangeName of ["A1:D1", "A6:D6"]) {
  rules.getRange(rangeName).format = {
    fill: "#155EEF", font: { color: "#FFFFFF", bold: true },
    horizontalAlignment: "center", verticalAlignment: "center", rowHeight: 32,
  };
}
rules.getRange("A2:D4").format = { borders: { preset: "all", style: "thin", color: "#D9E2F1" }, wrapText: true, rowHeight: 36 };
rules.getRange("A7:D10").format = { borders: { preset: "all", style: "thin", color: "#D9E2F1" }, wrapText: true, rowHeight: 42 };
rules.getRange("A:D").format.columnWidth = 24;

summary.getRange("A1:F1").merge();
summary.getRange("A1").values = [["供应商风险概览（演示数据）"]];
summary.getRange("A1:F1").format = {
  fill: "#0F3D75", font: { color: "#FFFFFF", bold: true, size: 20 },
  horizontalAlignment: "center", verticalAlignment: "center", rowHeight: 46,
};
summary.getRange("A3:B7").values = [
  ["指标", "结果"],
  ["风险记录数", null],
  ["高风险数量", null],
  ["待治理数量", null],
  ["涉及项目记录数", null],
];
summary.getRange("B4").formulas = [[`=COUNTA('供应商风险台账'!A2:A${rows.length + 1})`]];
summary.getRange("B5").formulas = [[`=COUNTIF('供应商风险台账'!E2:E${rows.length + 1},\"高\")`]];
summary.getRange("B6").formulas = [[`=COUNTIF('供应商风险台账'!J2:J${rows.length + 1},\"待治理\")`]];
summary.getRange("B7").formulas = [[`=COUNTA('供应商风险台账'!H2:H${rows.length + 1})`]];
summary.getRange("A3:B3").format = { fill: "#155EEF", font: { color: "#FFFFFF", bold: true }, horizontalAlignment: "center" };
summary.getRange("A4:B7").format = { fill: "#F7F9FC", borders: { preset: "all", style: "thin", color: "#D9E2F1" }, rowHeight: 34 };
summary.getRange("A9:F10").merge();
summary.getRange("A9").values = [[notice + " 本工作簿用于验证表格解析、自动分类、实体别名、风险治理和检索，不包含真实经营数据。"]];
summary.getRange("A9:F10").format = { fill: "#FFF8E6", font: { color: "#7A4D00" }, wrapText: true, verticalAlignment: "center", rowHeight: 32 };
summary.getRange("A:F").format.columnWidth = 22;
summary.showGridLines = false;

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

for (const sheetName of ["供应商风险台账", "风险概览"]) {
  const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(buildDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}
const inspection = await workbook.inspect({ kind: "workbook,sheet,formula", maxChars: 12000, options: { maxResults: 100 } });
await fs.writeFile(path.join(buildDir, "inspection.ndjson"), inspection.ndjson, "utf8");
console.log(JSON.stringify({ outputPath, sheets: 3, rows: rows.length }));
