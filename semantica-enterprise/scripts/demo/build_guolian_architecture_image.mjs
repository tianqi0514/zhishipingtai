import fs from "node:fs/promises";
import path from "node:path";
import { Presentation } from "@oai/artifact-tool";

const repoRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const outputPath = path.join(repoRoot, "demo/guolian/项目总体架构图（演示版）.png");
const family = "Helvetica Neue";
const p = Presentation.create({ slideSize: { width: 1600, height: 900 } });
const slide = p.slides.add();
slide.background.fill = "#FFFFFF";

function shape(text, x, y, w, h, fill, line, fontSize = 23, color = "#17243A", bold = false, geometry = "roundRect") {
  const s = slide.shapes.add({
    geometry,
    position: { left: x, top: y, width: w, height: h },
    fill,
    line: { style: "solid", fill: line, width: line === "none" ? 0 : 2 },
    borderRadius: "rounded-xl",
    shadow: geometry === "roundRect" ? "shadow-sm" : undefined,
  });
  s.text = text;
  s.text.style = {
    typeface: family,
    fontSize,
    color,
    bold,
    alignment: "center",
    verticalAlignment: "middle",
    autoFit: "shrinkText",
  };
  return s;
}

function label(text, x, y, w, h, fontSize = 23, color = "#17243A", bold = false, align = "left") {
  const s = slide.shapes.add({
    geometry: "textbox",
    position: { left: x, top: y, width: w, height: h },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  s.text = text;
  s.text.style = { typeface: family, fontSize, color, bold, alignment: align, verticalAlignment: "middle", autoFit: "shrinkText" };
  return s;
}

label("智慧流程中枢项目总体架构（演示版）", 74, 50, 1020, 62, 42, "#17243A", true);
label("业务入口、知识服务、流程能力与集团基础平台形成可追溯的依赖关系", 76, 112, 1200, 38, 21, "#60718A");

const columns = [280, 670, 1060];
label("业务入口", 82, 195, 150, 38, 22, "#155EEF", true);
const entry = [
  shape("采购协同门户\n制度查询 · 采购申请", columns[0], 170, 280, 108, "#EAF1FF", "#155EEF", 23, "#155EEF", true),
  shape("项目管理门户\n里程碑 · 风险跟踪", columns[1], 170, 280, 108, "#E9F8F0", "#138A5B", 23, "#138A5B", true),
  shape("智能体应用\n问答 · 推演 · 任务协同", columns[2], 170, 280, 108, "#FFF5E8", "#B45309", 23, "#B45309", true),
];

label("核心能力", 82, 388, 150, 38, 22, "#155EEF", true);
const core = [
  shape("智慧流程引擎\n编排审批与业务规则", columns[0], 350, 280, 125, "#EEF3FF", "#155EEF", 23, "#155EEF", true),
  shape("NexusOne 知识服务\n检索 · 图谱 · 数据问答", columns[1], 350, 280, 125, "#E7F7FB", "#0E7490", 23, "#0E7490", true),
  shape("风险联动中心\n供应商风险与项目影响链", columns[2], 350, 280, 125, "#FDECEC", "#B42318", 23, "#B42318", true),
];

label("集团基础平台", 82, 586, 170, 38, 22, "#155EEF", true);
const foundation = [
  shape("集团数据交换平台\n业务数据与主数据交换", columns[0], 550, 280, 125, "#F7F9FC", "#9BAAC0", 22, "#27364B", true),
  shape("统一身份组件\n用户、组织与访问控制", columns[1], 550, 280, 125, "#F7F9FC", "#9BAAC0", 22, "#27364B", true),
  shape("经营数据库\n订单 · 合同 · 审批 · 风险", columns[2], 550, 280, 125, "#F7F9FC", "#9BAAC0", 22, "#27364B", true),
];

for (let i = 0; i < 3; i += 1) {
  slide.shapes.connect(entry[i], core[i], {
    kind: "straight", fromSide: "bottom", toSide: "top",
    line: { style: "solid", fill: "#667A96", width: 3 },
    head: { type: "triangle", width: "sm", length: "sm" },
  });
  slide.shapes.connect(core[i], foundation[i], {
    kind: "straight", fromSide: "bottom", toSide: "top",
    line: { style: "solid", fill: "#667A96", width: 3 },
    head: { type: "triangle", width: "sm", length: "sm" },
  });
}
slide.shapes.connect(core[0], core[1], {
  kind: "straight", fromSide: "right", toSide: "left",
  line: { style: "solid", fill: "#667A96", width: 3 }, head: { type: "triangle", width: "sm", length: "sm" },
});
slide.shapes.connect(core[1], core[2], {
  kind: "straight", fromSide: "right", toSide: "left",
  line: { style: "solid", fill: "#667A96", width: 3 }, head: { type: "triangle", width: "sm", length: "sm" },
});
label("演示数据，不代表国联集团真实经营数据。图中关系用于验证 OCR、视觉理解、图谱抽取与多跳检索。", 76, 816, 1300, 30, 16, "#60718A");

const blob = await p.export({ slide, format: "png", scale: 1 });
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(outputPath, new Uint8Array(await blob.arrayBuffer()));
console.log(JSON.stringify({ outputPath, width: 1600, height: 900 }));
