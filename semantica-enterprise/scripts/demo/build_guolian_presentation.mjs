import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const repoRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const outputDir = path.join(repoRoot, "demo/guolian");
const buildDir = path.join(repoRoot, ".demo-build/pptx");
const skillDir = process.env.SKILL_DIR;
const runtimePython = process.env.RUNTIME_PYTHON;
if (!skillDir || !runtimePython) throw new Error("SKILL_DIR and RUNTIME_PYTHON are required");
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(buildDir, { recursive: true });

const { resolvePresentationFont, finalizePresentation } = await import(
  pathToFileURL(path.join(skillDir, "container_tools/artifact_tool_utils.mjs")).href,
);
const family = resolvePresentationFont();
const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });
const notice = "演示数据，不代表国联集团真实经营数据。";
const colors = {
  ink: "#17243A", muted: "#5A6B82", blue: "#155EEF", blueDark: "#0F3D75",
  blueLight: "#EAF1FF", cyan: "#0E7490", green: "#138A5B", greenLight: "#E9F8F0",
  amber: "#B45309", amberLight: "#FFF5E8", red: "#B42318", redLight: "#FDECEC",
  border: "#D9E2F1", surface: "#F7F9FC", white: "#FFFFFF",
};

function addText(slide, text, position, style = {}, options = {}) {
  const box = slide.shapes.add({
    geometry: options.geometry ?? "textbox",
    position,
    fill: options.fill ?? "none",
    line: { style: "solid", fill: options.line ?? "none", width: options.lineWidth ?? 0 },
    borderRadius: options.radius,
    shadow: options.shadow,
  });
  box.text = text;
  box.text.style = {
    typeface: family,
    fontSize: style.fontSize ?? 22,
    bold: style.bold ?? false,
    color: style.color ?? colors.ink,
    alignment: style.alignment ?? "left",
    verticalAlignment: style.verticalAlignment ?? "middle",
    autoFit: "shrinkText",
  };
  return box;
}

function addHeader(slide, eyebrow, title, subtitle = "") {
  addText(slide, eyebrow, { left: 68, top: 42, width: 360, height: 28 }, { fontSize: 15, bold: true, color: colors.blue });
  addText(slide, title, { left: 68, top: 78, width: 1144, height: 62 }, { fontSize: 34, bold: true });
  if (subtitle) addText(slide, subtitle, { left: 68, top: 140, width: 1110, height: 36 }, { fontSize: 18, color: colors.muted });
  addText(slide, notice, { left: 68, top: 674, width: 760, height: 22 }, { fontSize: 12, color: colors.muted });
}

function addCard(slide, x, y, w, h, title, body, accent = colors.blue, fill = colors.white) {
  const card = addText(slide, "", { left: x, top: y, width: w, height: h }, {}, {
    geometry: "roundRect", fill, line: colors.border, lineWidth: 1, radius: "rounded-xl", shadow: "shadow-sm",
  });
  addText(slide, title, { left: x + 22, top: y + 18, width: w - 44, height: 32 }, { fontSize: 20, bold: true, color: accent });
  addText(slide, body, { left: x + 22, top: y + 56, width: w - 44, height: h - 72 }, { fontSize: 16, color: colors.muted });
  return card;
}

// 1 — cover
{
  const slide = presentation.slides.add();
  slide.background.fill = colors.white;
  addText(slide, "传神智库 · 国联集团演示", { left: 74, top: 72, width: 410, height: 38 }, { fontSize: 18, bold: true, color: colors.blue });
  addText(slide, "集团知识，从分散材料\n走向统一、可信、可调用", { left: 74, top: 152, width: 790, height: 150 }, { fontSize: 48, bold: true });
  addText(slide, "多源接入 · 自动与人工治理 · 语义建模 · 图谱推演 · 数据库问答 · Agent 服务", { left: 76, top: 330, width: 940, height: 48 }, { fontSize: 20, color: colors.muted });
  addText(slide, "演示范围", { left: 76, top: 442, width: 140, height: 30 }, { fontSize: 17, bold: true, color: colors.blue });
  addText(slide, "集团采购制度执行、供应商风险传导与智慧流程中枢项目建设", { left: 76, top: 480, width: 760, height: 52 }, { fontSize: 24, bold: true });
  addText(slide, "DEMO", { left: 932, top: 146, width: 250, height: 250 }, { fontSize: 44, bold: true, color: colors.white, alignment: "center" }, { geometry: "ellipse", fill: colors.blue, line: colors.blue, lineWidth: 0, shadow: "shadow-lg" });
  addText(slide, notice, { left: 76, top: 654, width: 760, height: 24 }, { fontSize: 13, color: colors.muted });
  slide.speakerNotes.textFrame.setText(`${notice} 本页用于客户演示开场。`);
}

// 2 — pain points
{
  const slide = presentation.slides.add(); slide.background.fill = colors.white;
  addHeader(slide, "01 业务背景", "集团知识真正难的不是“存起来”，而是“用起来”", "同一个业务判断往往横跨制度、项目材料、数据库和会议记录。 ");
  addCard(slide, 68, 206, 258, 330, "知识分散", "制度在文档中\n项目关系在图表中\n风险在邮件与会议中\n金额在业务数据库中", colors.blue, colors.blueLight);
  addCard(slide, 350, 206, 258, 330, "质量不一", "新旧版本并存\n组织与供应商存在别名\n扫描件文字置信度不稳定\n字段可能包含敏感信息", colors.amber, colors.amberLight);
  addCard(slide, 632, 206, 258, 330, "关系难发现", "单段文本不能直接说明\n供应商 → 产品 → 项目\n→ 责任组织的完整影响链", colors.red, colors.redLight);
  addCard(slide, 914, 206, 298, 330, "应用难复用", "每个应用重复接数据\n数据库实时值与文档口径割裂\n答案缺少统一引用与审计", colors.green, colors.greenLight);
  slide.speakerNotes.textFrame.setText(`${notice} 先讲业务问题，再进入技术能力。`);
}

// 3 — platform flow
{
  const slide = presentation.slides.add(); slide.background.fill = colors.white;
  addHeader(slide, "02 平台定位", "不是一次性 RAG，而是组织级知识供给链", "同一套知识经过治理、建模和发布后，同时服务人、模型与智能体。 ");
  const cards = [
    ["多源接入", "文档 / 表格 / 数据库\n图片 / 音频 / 视频", colors.blue],
    ["知识加工", "解析 / OCR / ASR\n切片 / 向量化 / 抽取", colors.cyan],
    ["治理校准", "版本 / 重复 / 别名\n冲突 / 质量 / 人工纠错", colors.amber],
    ["语义建模", "本体 / 实体 / 关系\n来源 / 规则 / 图谱", colors.green],
    ["统一服务", "检索 / 问答 / 推演\nREST / MCP / CLI", colors.blueDark],
  ];
  for (let i = 0; i < cards.length; i += 1) {
    const x = 56 + i * 244;
    addCard(slide, x, 232, 204, 236, cards[i][0], cards[i][1], cards[i][2], colors.white);
    if (i < cards.length - 1) addText(slide, "→", { left: x + 207, top: 314, width: 38, height: 44 }, { fontSize: 30, bold: true, color: colors.blue });
  }
  addText(slide, "每一步都有真实任务、版本、权限与来源记录", { left: 278, top: 510, width: 724, height: 54 }, { fontSize: 24, bold: true, color: colors.blue, alignment: "center" }, { geometry: "roundRect", fill: colors.blueLight, line: "none", radius: "rounded-full" });
  slide.speakerNotes.textFrame.setText(`${notice} 说明平台从原始素材到统一知识服务的完整链路。`);
}

// 4 — risk chain
{
  const slide = presentation.slides.add(); slide.background.fill = colors.white;
  addHeader(slide, "03 演示主线", "从一封延期邮件，追溯到受影响项目与责任部门", "事实分散在不同来源，图谱和规则把它们连接成可核验的业务结论。 ");
  const chain = [
    ["东方智造", "供应商", colors.redLight, colors.red],
    ["NexusOne", "核心产品", colors.blueLight, colors.blue],
    ["智慧流程中枢", "建设项目", colors.greenLight, colors.green],
    ["数字科技公司", "责任组织", colors.amberLight, colors.amber],
  ];
  for (let i = 0; i < chain.length; i += 1) {
    const x = 66 + i * 300;
    addText(slide, `${chain[i][0]}\n${chain[i][1]}`, { left: x, top: 250, width: 225, height: 112 }, { fontSize: 21, bold: true, color: chain[i][3], alignment: "center" }, { geometry: "roundRect", fill: chain[i][2], line: chain[i][3], lineWidth: 1, radius: "rounded-xl" });
    if (i < chain.length - 1) addText(slide, i === 0 ? "供应 →" : i === 1 ? "用于 →" : "负责 →", { left: x + 224, top: 280, width: 78, height: 46 }, { fontSize: 15, bold: true, color: colors.muted, alignment: "center" });
  }
  addText(slide, "延期风险", { left: 68, top: 402, width: 224, height: 70 }, { fontSize: 20, bold: true, color: colors.red, alignment: "center" }, { geometry: "roundRect", fill: colors.redLight, line: colors.red, lineWidth: 1, radius: "rounded-xl" });
  addText(slide, "规则推演：供应商风险会沿产品与项目关系形成“受到影响”结论", { left: 332, top: 404, width: 880, height: 68 }, { fontSize: 21, bold: true, color: colors.blue, alignment: "center" }, { geometry: "roundRect", fill: colors.blueLight, line: colors.blue, lineWidth: 1, radius: "rounded-xl" });
  addText(slide, "来源：交付延期邮件 + 产品说明 + 项目周报 + 会议录音 + 数据库映射", { left: 224, top: 518, width: 840, height: 38 }, { fontSize: 18, color: colors.muted, alignment: "center" });
  slide.speakerNotes.textFrame.setText(`${notice} 本页所有主体和金额均为演示构造。`);
}

// 5 — choosing the right capability
{
  const slide = presentation.slides.add(); slide.background.fill = colors.white;
  addHeader(slide, "04 能力协同", "不同问题使用不同知识能力，最后由 Agent 统一编排", "系统不让一种技术承担所有问题。 ");
  const data = [
    ["制度与说明", "全文 + 向量", "找到原文、语义相近内容与页码", colors.blue],
    ["多跳影响链", "知识图谱", "沿实体关系扩展并解释每一跳", colors.green],
    ["金额与排名", "结构化语义查询", "本体映射 → Query IR → 参数化 SQL", colors.amber],
    ["明确业务规则", "Semantica 推演", "把已有事实推导成有证据的新结论", colors.red],
    ["会议与架构图", "ASR + 视觉理解", "定位时间点、关键帧与图形关系", colors.cyan],
  ];
  for (let i = 0; i < data.length; i += 1) {
    const y = 198 + i * 82;
    addText(slide, data[i][0], { left: 68, top: y, width: 208, height: 58 }, { fontSize: 18, bold: true, color: data[i][3], alignment: "center" }, { geometry: "roundRect", fill: colors.surface, line: colors.border, lineWidth: 1, radius: "rounded-lg" });
    addText(slide, data[i][1], { left: 298, top: y, width: 270, height: 58 }, { fontSize: 18, bold: true, color: colors.ink, alignment: "center" }, { geometry: "roundRect", fill: colors.white, line: colors.border, lineWidth: 1, radius: "rounded-lg" });
    addText(slide, data[i][2], { left: 590, top: y, width: 622, height: 58 }, { fontSize: 17, color: colors.muted }, { geometry: "roundRect", fill: colors.white, line: colors.border, lineWidth: 1, radius: "rounded-lg" });
  }
  slide.speakerNotes.textFrame.setText(`${notice} DeepSeek Harness 负责工具选择与多轮会话，业务数据与权限仍由平台管理。`);
}

// 6 — value
{
  const slide = presentation.slides.add(); slide.background.fill = colors.white;
  addHeader(slide, "05 演示结论", "知识底座最终要让集团知识“可信、可算、可推演、可调用”", "从一次演示，延伸到集团各类业务应用。 ");
  addCard(slide, 68, 220, 260, 250, "可信", "版本、来源、权限、治理操作和引用都可追溯。", colors.blue, colors.blueLight);
  addCard(slide, 350, 220, 260, 250, "可算", "实时金额、数量、排名由受控数据库查询得到。", colors.amber, colors.amberLight);
  addCard(slide, 632, 220, 260, 250, "可推演", "专家规则形成可重复执行、可撤回的新知识。", colors.red, colors.redLight);
  addCard(slide, 914, 220, 298, 250, "可调用", "通过 REST、MCP 与 CLI 服务集团应用和智能体。", colors.green, colors.greenLight);
  addText(slide, "下一步：围绕采购、风控、项目管理和制度问答建设具体应用场景", { left: 162, top: 526, width: 956, height: 62 }, { fontSize: 24, bold: true, color: colors.white, alignment: "center" }, { geometry: "roundRect", fill: colors.blue, line: colors.blue, lineWidth: 0, radius: "rounded-full", shadow: "shadow-md" });
  slide.speakerNotes.textFrame.setText(`${notice} 结束时强调统一知识底座对应用场景的支撑作用。`);
}

const finalPath = path.join(outputDir, "集团知识底座建设汇报（演示版）.pptx");
const candidatePath = path.join(buildDir, "candidate.pptx");
await (await PresentationFile.exportPptx(presentation)).save(candidatePath);
const requirements = {
  explicitTotalSlideCount: 6,
  requiredNativeTableOwnerSlides: [],
  requiredNativeChartOwnerSlides: [],
};
const result = await finalizePresentation({
  ...requirements,
  workspaceDir: repoRoot,
  candidatePath,
  finalPath,
  pythonExecutable: runtimePython,
  integrityValidatorPath: path.join(skillDir, "container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath: path.join(skillDir, "container_tools/inspect_presentation_layout_geometry.py"),
  layoutArgs: ["--expected-slide-size-emu", "12192000,6858000", "--validate-heading-fit"],
  requiredNativeTableOwnerSlides: [],
  fontPolicy: { basis: "design", families: [family] },
  verifyArtifactToolImport: true,
  receiptPath: path.join(buildDir, "presentation.validation.json"),
});
for (let i = 0; i < presentation.slides.items.length; i += 1) {
  const slide = presentation.slides.items[i];
  const preview = await presentation.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(path.join(buildDir, `slide-${i + 1}.png`), new Uint8Array(await preview.arrayBuffer()));
  const layout = await slide.export({ format: "layout" });
  await fs.writeFile(path.join(buildDir, `slide-${i + 1}.layout.json`), await layout.text(), "utf8");
}
console.log(JSON.stringify({ finalPath, slides: 6, font: family, validation: result?.status ?? "completed" }));
