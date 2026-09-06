#!/usr/bin/env python3
"""Build the normal and scanned PDF fixtures for the Guolian demo."""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "demo" / "guolian"
NORMAL_PDF = OUT / "智慧流程中枢项目建设方案（演示版）.pdf"
SCANNED_PDF = OUT / "供应商现场评估记录（演示版）-扫描件.pdf"
APPROVAL_SCAN_IMAGE = OUT / "扫描采购审批单（演示版）.jpg"
# Backwards-compatible alias for callers that imported the former constant.
SCANNED_IMAGE = APPROVAL_SCAN_IMAGE
NOTICE = "演示数据，不代表国联集团真实经营数据。"
FONT_FILE = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")


def _register_font() -> str:
    family = "GuolianDemoCJK"
    if family not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(family, str(FONT_FILE)))
    return family


def _page(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont("GuolianDemoCJK", 8.5)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawRightString(A4[0] - 18 * mm, A4[1] - 12 * mm, "传神智库 · 国联集团组织级知识底座演示")
    canvas.setFillColor(colors.HexColor("#B45309"))
    canvas.drawCentredString(A4[0] / 2, 10 * mm, NOTICE)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawString(18 * mm, 10 * mm, f"第 {document.page} 页")
    canvas.restoreState()


def build_normal_pdf() -> Path:
    font = _register_font()
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "DemoTitle", parent=styles["Title"], fontName=font, fontSize=24,
        leading=32, textColor=colors.HexColor("#173E75"), alignment=TA_CENTER,
        spaceAfter=14,
    )
    subtitle = ParagraphStyle(
        "DemoSubtitle", parent=styles["Normal"], fontName=font, fontSize=11,
        leading=18, textColor=colors.HexColor("#475569"), alignment=TA_CENTER,
        spaceAfter=10,
    )
    heading = ParagraphStyle(
        "DemoHeading", parent=styles["Heading2"], fontName=font, fontSize=16,
        leading=22, textColor=colors.HexColor("#173E75"), spaceBefore=12, spaceAfter=8,
    )
    body = ParagraphStyle(
        "DemoBody", parent=styles["BodyText"], fontName=font, fontSize=10.5,
        leading=18, textColor=colors.HexColor("#1F2937"), alignment=TA_LEFT,
        spaceAfter=7,
    )
    warning = ParagraphStyle(
        "DemoWarning", parent=body, textColor=colors.HexColor("#B45309"), alignment=TA_CENTER,
    )
    document = SimpleDocTemplate(
        str(NORMAL_PDF), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=22 * mm, bottomMargin=18 * mm,
        title="智慧流程中枢项目建设方案（演示版）",
        author="传神智库演示数据生成器",
    )
    story = [
        Spacer(1, 12 * mm),
        Paragraph("智慧流程中枢项目建设方案（演示版）", title),
        Paragraph("版本 V1.0 · 2026 年 3 月", subtitle),
        Paragraph(NOTICE, warning),
        Spacer(1, 10 * mm),
        Paragraph("一、项目定位", heading),
        Paragraph("智慧流程中枢项目面向集团采购、合同、用印和项目管理等跨组织流程，统一提供流程编排、知识服务、风险识别和可追溯执行能力。", body),
        Paragraph("NexusOne 在本项目中承担语义建模、知识检索、知识图谱和智能问答能力，为流程节点提供制度依据、历史案例和关联对象。", body),
        Paragraph("项目由数字科技公司（演示）牵头，项目管理办公室负责交付统筹，数字化管理部负责技术集成，风险管理部跟踪供应链与系统依赖风险。", body),
        Paragraph("二、建设目标", heading),
        Paragraph("1. 建立统一流程资产目录；2. 将制度要求映射到流程控制节点；3. 对接采购和合同数据库；4. 将项目、产品、供应商和责任组织形成可追溯关系；5. 对关键风险形成可解释的影响链。", body),
        Paragraph("三、范围边界", heading),
        Paragraph("本期覆盖集团采购申请、采购审批、合同签订、用印和项目督办。实时金额与数量由结构化数据库查询获得，制度解释与责任依据来自当前有效文档，跨对象影响关系来自知识图谱与规则推演。", body),
        PageBreak(),
        Paragraph("四、总体架构", heading),
    ]
    architecture = [
        ["业务入口", "员工门户 / 智能体 / 流程应用"],
        ["流程中枢", "流程编排 · 任务督办 · 风险事件 · 审批轨迹"],
        ["知识服务", "文档检索 · 向量检索 · 知识图谱 · 数据库语义查询"],
        ["核心组件", "NexusOne · 智慧流程引擎 · 集团数据交换平台 · 统一身份组件"],
        ["数据基础", "制度文档 · 采购数据库 · 邮件 · 图片 · 音频 · 视频"],
    ]
    table = Table(architecture, colWidths=[40 * mm, 125 * mm], rowHeights=[15 * mm] * len(architecture))
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), font, 10),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#2456A6")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
        ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#F4F7FB")),
        ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#1F2937")),
        ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([
        table,
        Spacer(1, 8 * mm),
        Paragraph("关键依赖关系", heading),
        Paragraph("智慧流程中枢 → 依赖 → 集团数据交换平台；集团数据交换平台 → 使用 → 统一身份组件；智慧流程中枢项目 → 使用 → NexusOne；智慧流程中枢项目 → 由 → 数字科技公司（演示）负责。", body),
        Paragraph("上述关系分布在项目方案、周报、邮件和会议材料中，适合展示图谱在跨文档、多跳关联方面对向量检索的补充。", body),
        PageBreak(),
        Paragraph("五、实施计划与里程碑", heading),
    ])
    milestones = [
        ["阶段", "计划时间", "主要交付物", "责任部门", "状态"],
        ["需求与制度梳理", "2026-01—03", "流程清单、制度映射", "集团采购管理部", "完成"],
        ["平台配置与集成", "2026-03—05", "流程、接口、NexusOne 集成", "数字化管理部", "进行中"],
        ["试运行", "2026-06—07", "试点流程与问题闭环", "项目管理办公室", "待开始"],
        ["推广", "2026-08—12", "子企业推广与运营机制", "数字科技公司", "待开始"],
    ]
    milestone_table = Table(milestones, colWidths=[32 * mm, 32 * mm, 52 * mm, 36 * mm, 20 * mm])
    milestone_table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), font, 8.8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2456A6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([
        milestone_table,
        Spacer(1, 7 * mm),
        Paragraph("六、供应链风险", heading),
        Paragraph("东方智造（演示）供应 NexusOne 相关组件。2026 年 4 月 28 日，供应商发出交付延期通知，预计延后十个工作日。该事项可能影响平台配置与集成阶段，需要数字化管理部牵头评估替代方案。", body),
        Paragraph("风险管理部负责每周跟踪；项目管理办公室负责确认里程碑影响；集团采购管理部负责依据当前采购制度处理供应商履约与审批记录。", body),
        PageBreak(),
        Paragraph("七、验收口径", heading),
        Paragraph("文档知识：当前有效版本能够检索、引用并追溯页码；结构化数据：采购金额按有效状态统计并排除 cancelled 与 draft；图谱知识：关系路径能够追溯到来源片段；规则推演：每条结论保留规则、前提与来源证据。", body),
        Paragraph("八、演示验证问题", heading),
        Paragraph("1. NexusOne 在智慧流程中枢项目中承担什么作用？", body),
        Paragraph("2. 东方智造交付延期会影响哪些产品、项目和责任部门？", body),
        Paragraph("3. 智慧流程中枢依赖哪些系统组件？", body),
        Paragraph("4. 项目当前处于哪个建设阶段，下一里程碑是什么？", body),
        Paragraph("九、数据声明", heading),
        Paragraph("本文件中的组织名称、金额、日期、供应商、项目状态和风险事件均为演示用确定性合成数据，不代表国联集团真实经营情况，不得用于业务决策。", body),
    ])
    document.build(story, onFirstPage=_page, onLaterPages=_page)
    return NORMAL_PDF


def _pil_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_FILE), size)


def _apply_scan_effect(
    image: Image.Image,
    *,
    seed: int,
    rotation: float,
    noise_points: int = 18000,
    blur_radius: float = 0.35,
) -> Image.Image:
    """Apply repeatable scan artefacts without changing the source content."""

    randomizer = random.Random(seed)
    pixels = image.load()
    for _ in range(noise_points):
        x = randomizer.randrange(image.width)
        y = randomizer.randrange(image.height)
        base = pixels[x, y]
        delta = randomizer.choice((-16, -10, 10, 16))
        pixels[x, y] = tuple(max(0, min(255, channel + delta)) for channel in base)
    image = image.rotate(
        rotation,
        resample=Image.Resampling.BICUBIC,
        expand=False,
        fillcolor="#eeeae0",
    )
    return image.filter(ImageFilter.GaussianBlur(radius=blur_radius))


def build_supplier_assessment_scan() -> Path:
    """Build the scanned supplier assessment PDF.

    This content must never be reused as the procurement approval JPG: the two
    fixtures represent different business documents and have separate ground
    truth.
    """

    width, height = 1654, 2339
    image = Image.new("RGB", (width, height), "#f4f1e8")
    draw = ImageDraw.Draw(image)
    title_font = _pil_font(54)
    heading_font = _pil_font(34)
    body_font = _pil_font(30)
    small_font = _pil_font(23)
    draw.text((250, 100), "供应商现场评估记录（演示版）", fill="#202020", font=title_font)
    draw.text((440, 175), NOTICE, fill="#8a3b12", font=small_font)
    fields = [
        ("评估编号", "DEMO-SUP-2026-0428"),
        ("供应商", "东方智造（演示）"),
        ("评估日期", "2026 年 4 月 28 日"),
        ("评估产品", "NexusOne 部署组件"),
        ("风险等级", "高"),
    ]
    y = 290
    for label, value in fields:
        draw.rectangle((150, y, 1500, y + 88), outline="#555555", width=2)
        draw.line((500, y, 500, y + 88), fill="#555555", width=2)
        draw.text((180, y + 22), label, fill="#222222", font=body_font)
        draw.text((540, y + 22), value, fill="#222222", font=body_font)
        y += 88
    draw.text((150, y + 50), "一、现场结论", fill="#172554", font=heading_font)
    lines = [
        "1. 关键组件当前库存只能满足首批部署，后续批次预计延期十个工作日。",
        "2. 供应商尚未提交完整的分批交付承诺，项目里程碑存在延期风险。",
        "3. 建议数字化管理部在 2026 年 5 月 15 日前完成替代方案评估。",
        "4. 风险管理部每周跟踪；项目管理办公室核验对试运行阶段的影响。",
    ]
    y += 120
    for line in lines:
        draw.text((175, y), line, fill="#222222", font=body_font)
        y += 90
    draw.text((150, y + 30), "二、低置信度核验项", fill="#172554", font=heading_font)
    y += 105
    draw.text((175, y), "手写金额：壹拾贰万元（¥120,000.00）", fill="#3f3f46", font=body_font)
    draw.text((175, y + 80), "模糊字符样例：交付批次『B-0I』，需人工确认 I/1。", fill="#3f3f46", font=body_font)
    draw.text((150, y + 210), "三、评估意见", fill="#172554", font=heading_font)
    draw.rectangle((150, y + 270, 1500, y + 650), outline="#555555", width=2)
    draw.text((180, y + 305), "结论：有条件通过。关键风险不得在低置信度 OCR 未确认时自动发布。", fill="#222222", font=body_font)
    draw.text((180, y + 390), "责任部门：数字化管理部；协同部门：风险管理部、项目管理办公室。", fill="#222222", font=body_font)
    draw.text((180, y + 475), "来源关系：东方智造 → 供应 → NexusOne；东方智造 → 存在风险 → 交付延期。", fill="#222222", font=small_font)
    draw.ellipse((1160, 1940, 1480, 2260), outline="#a11", width=9)
    draw.text((1205, 2050), "演示专用章", fill="#a11", font=body_font)
    draw.text((150, 2260), "扫描质量：刻意加入轻微噪声、旋转和模糊，用于 OCR 与人工治理验收。", fill="#6b7280", font=small_font)
    image = _apply_scan_effect(image, seed=20260906, rotation=0.35)
    image.save(SCANNED_PDF, "PDF", resolution=150.0)
    return SCANNED_PDF


def build_procurement_approval_scan() -> Path:
    """Build an actual procurement approval form aligned with demo order 1001."""

    width, height = 1654, 2339
    image = Image.new("RGB", (width, height), "#f5f2e9")
    draw = ImageDraw.Draw(image)
    title_font = _pil_font(52)
    heading_font = _pil_font(32)
    body_font = _pil_font(28)
    small_font = _pil_font(22)
    tiny_font = _pil_font(19)
    navy = "#172554"
    ink = "#202020"
    grid = "#555555"
    demo_red = "#9f241f"

    draw.text((285, 80), "采购申请审批单（演示版）", fill=ink, font=title_font)
    draw.text((455, 150), NOTICE, fill="#8a3b12", font=small_font)
    draw.text((125, 215), "单据编号：DEMO-APPROVAL-2026-001", fill=ink, font=small_font)
    draw.text((1120, 215), "币种：人民币", fill=ink, font=small_font)

    left, right, top = 120, 1534, 275
    label_width = 260
    row_height = 78
    fields = (
        ("采购订单", "PO-DEMO-2026-001"),
        ("申请单位", "数字科技公司（演示）"),
        ("申请部门", "数字化管理部"),
        ("申请日期", "2026 年 1 月 12 日"),
        ("所属项目", "智慧流程中枢项目"),
        ("拟定供应商", "东方智造（演示）"),
        ("采购内容", "智慧流程中枢首批组件（含 NexusOne）"),
        ("申请金额", "¥360,000.00（人民币叁拾陆万元整）"),
        ("采购方式", "演示比选"),
    )
    y = top
    for label, value in fields:
        draw.rectangle((left, y, right, y + row_height), outline=grid, width=2)
        draw.line((left + label_width, y, left + label_width, y + row_height), fill=grid, width=2)
        draw.text((left + 24, y + 20), label, fill=ink, font=body_font)
        draw.text((left + label_width + 26, y + 20), value, fill=ink, font=body_font)
        y += row_height

    draw.text((left, y + 40), "一、采购申请说明", fill=navy, font=heading_font)
    description_top = y + 95
    description_bottom = description_top + 230
    draw.rectangle((left, description_top, right, description_bottom), outline=grid, width=2)
    descriptions = (
        "用于智慧流程中枢项目首批平台配置与集成，采购范围包含 NexusOne 相关组件。",
        "预算来源：智慧流程中枢项目演示预算；供应商、金额和日期均为确定性演示数据。",
        "验收要求：完成部署清单、接口验证和知识服务能力测试，并保留全过程记录。",
    )
    text_y = description_top + 30
    for line in descriptions:
        draw.text((left + 30, text_y), line, fill=ink, font=body_font)
        text_y += 62

    draw.text((left, description_bottom + 42), "二、审批记录", fill=navy, font=heading_font)
    approval_top = description_bottom + 98
    columns = (left, 465, 1220, right)
    header_height = 65
    approval_row_height = 112
    headers = ("审批环节", "审批意见", "日期")
    for index, label in enumerate(headers):
        draw.rectangle(
            (columns[index], approval_top, columns[index + 1], approval_top + header_height),
            outline=grid,
            width=2,
        )
        draw.text((columns[index] + 18, approval_top + 16), label, fill=ink, font=body_font)
    approvals = (
        ("申请部门", "材料齐全，提交项目采购申请。", "2026-01-12"),
        ("项目管理办公室", "符合项目建设计划和预算安排，同意。", "2026-01-13"),
        ("集团采购管理部", "审批通过，按制度完成合同与验收留痕。", "2026-01-14"),
    )
    y = approval_top + header_height
    for stage, opinion, date in approvals:
        values = (stage, opinion, date)
        for index, value in enumerate(values):
            draw.rectangle(
                (columns[index], y, columns[index + 1], y + approval_row_height),
                outline=grid,
                width=2,
            )
            font = small_font if index == 1 else body_font
            draw.text((columns[index] + 18, y + 35), value, fill=ink, font=font)
        y += approval_row_height

    result_top = y + 50
    draw.rectangle((left, result_top, right, result_top + 240), outline=grid, width=2)
    draw.text((left + 30, result_top + 28), "审批结论：通过", fill=navy, font=heading_font)
    draw.text(
        (left + 30, result_top + 82),
        "审批金额：人民币 360000 元；关联采购订单：PO-DEMO-2026-001。",
        fill=ink,
        font=small_font,
    )
    draw.text(
        (left + 30, result_top + 128),
        "数据库核验：purchase_orders.id=1001；approval_records.decision=approved。",
        fill=ink,
        font=tiny_font,
    )
    draw.text(
        (left + 30, result_top + 174),
        "本扫描件仅用于 OCR、视觉理解、文档与数据库交叉核验演示。",
        fill=ink,
        font=small_font,
    )

    stamp_left, stamp_top = 1125, 1940
    draw.ellipse((stamp_left, stamp_top, stamp_left + 310, stamp_top + 310), outline=demo_red, width=9)
    draw.text((stamp_left + 66, stamp_top + 118), "演示审批章", fill=demo_red, font=body_font)
    draw.text((125, 2200), "演示数据 · 合成扫描件 · 不作为真实审批凭证", fill=demo_red, font=small_font)
    draw.text(
        (125, 2250),
        "扫描质量：加入轻微噪声、旋转和模糊，用于验证图片 OCR 与视觉理解。",
        fill="#6b7280",
        font=tiny_font,
    )

    image = _apply_scan_effect(
        image,
        seed=20260907,
        rotation=-0.12,
        noise_points=8000,
        blur_radius=0.18,
    )
    image.save(APPROVAL_SCAN_IMAGE, "JPEG", quality=84, optimize=True, progressive=False)
    return APPROVAL_SCAN_IMAGE


def build_scanned_pdf() -> tuple[Path, Path]:
    """Backward-compatible entry point that now builds two distinct fixtures."""

    return build_supplier_assessment_scan(), build_procurement_approval_scan()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    normal = build_normal_pdf()
    scanned, image = build_scanned_pdf()
    print(normal)
    print(scanned)
    print(image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
