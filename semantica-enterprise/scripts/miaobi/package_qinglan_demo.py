"""Package only reviewed synthetic material and verified platform exports."""
from pathlib import Path
from io import BytesIO
import hashlib
import json
import re
from zipfile import ZipFile, ZIP_DEFLATED


MATERIAL_NAMES = (
    "01-安置点设施台账.docx",
    "02-柳溪供水站巡查简报.pdf",
    "03-生活保障职责与工作指引.docx",
    "04-搜救力量需求及到位清单.md",
)
EXPORT_NAMES = {
    "docx": "青岚县安置保障与搜救协调报告.docx",
    "pdf": "青岚县安置保障与搜救协调报告.pdf",
    "evidence_docx": "推演与来源依据.docx",
}
MARKDOWN_LINK = re.compile(r"\[([^\]\n]+)\]\((<[^>\n]+>|[^)\n]+)\)")


def checked_read(base: Path, relative: str) -> bytes:
    """Read only a regular, non-symlink file below the reviewed directory."""
    name = Path(relative)
    if name.is_absolute() or ".." in name.parts or "\\" in relative:
        raise ValueError("打包路径必须位于本轮目录内")
    path = base / name
    if any(parent.is_symlink() for parent in (base, *base.parents)) or any((base / Path(*name.parts[:i])).is_symlink() for i in range(1, len(name.parts) + 1)):
        raise ValueError("打包材料不能使用符号链接")
    if not path.is_file() or not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("打包材料不存在或超出本轮目录")
    return path.read_bytes()


def portable_markdown(text: str, links: dict[str, str]) -> str:
    """Rewrite packaged targets; identify unpackaged local sources without dead links."""
    def replace_link(match: re.Match[str]) -> str:
        label, raw = match.groups()
        target = raw.strip().removeprefix("<").removesuffix(">")
        if re.match(r"^(?:https?://|mailto:|#)", target, re.IGNORECASE):
            return match.group()
        path, separator, anchor = target.partition("#")
        for local, packaged in links.items():
            # Repository-relative suffixes also work when packing on a server
            # while the reviewed guide was authored with an absolute local path.
            if path == local or path.endswith("/" + local):
                return f"[{label}]({packaged}{separator}{anchor})"
        return f"{label}（包外资料，需在原仓库或测试服务器查看）"

    text = MARKDOWN_LINK.sub(replace_link, text)
    return text.replace("demo/miaobi/qinglan-live-20260914/01-上传材料", "01-上传材料")


def build_entries(root: Path) -> dict[str, bytes]:
    """Build a closed allowlist; never recurse through QA or runtime output."""
    source = root / "demo/miaobi/qinglan-live-20260914"
    output = root / ".demo-build/qinglan-live-20260914"
    state = json.loads(checked_read(output, "state.json"))
    if state.get("finalized", {}).get("status") != "completed" or state["finalized"].get("quality_report", {}).get("ok") is not True:
        raise ValueError("只有完成并通过质量门的报告才能打包")
    uploaded = state.get("uploaded")
    if (
        not isinstance(uploaded, list) or len(uploaded) != len(MATERIAL_NAMES)
        or any(not isinstance(item, dict) or not isinstance(item.get("version"), dict) for item in uploaded)
        or {item.get("filename") for item in uploaded} != set(MATERIAL_NAMES)
    ):
        raise ValueError("上传记录必须包含四份指定材料及其版本信息")
    uploaded_hashes = {item["filename"]: item["version"].get("sha256") for item in uploaded}
    exports = json.loads(checked_read(output, "exports.json"))
    if not isinstance(exports, list) or len(exports) != len(EXPORT_NAMES) or {item.get("format") for item in exports} != set(EXPORT_NAMES):
        raise ValueError("必须完成正文DOCX、PDF及来源依据DOCX三种导出")
    versions = {item.get("document_version_id") for item in exports}
    if len(versions) != 1 or None in versions or "" in versions:
        raise ValueError("三种报告导出必须来自同一个已核验文稿版本")
    report = json.loads(checked_read(output, "report_document.json"))
    if (
        not isinstance(report, dict) or not isinstance(report.get("current_version_id"), str)
        or not report["current_version_id"] or not isinstance(report.get("current_version"), dict)
        or report["current_version"].get("id") != report["current_version_id"]
    ):
        raise ValueError("文稿快照必须包含一致的当前版本")
    if versions != {report["current_version_id"]}:
        raise ValueError("三种报告导出必须匹配文稿快照的当前版本")
    if {path.name for path in (source / "01-上传材料").iterdir()} != set(MATERIAL_NAMES):
        raise ValueError("首轮目录必须恰好包含四份指定演练材料")

    entries: dict[str, bytes] = {}
    links: dict[str, str] = {}
    def include(base: Path, relative: str, packaged: str) -> None:
        if packaged in entries:
            raise ValueError("打包文件名重复")
        entries[packaged] = checked_read(base, relative)
        links[(base / relative).relative_to(root).as_posix()] = packaged
        links[packaged] = packaged

    for name in MATERIAL_NAMES:
        include(source, "01-上传材料/" + name, "01-上传材料/" + name)
        if hashlib.sha256(entries["01-上传材料/" + name]).hexdigest() != uploaded_hashes[name]:
            raise ValueError("材料内容与本轮上传记录不匹配：" + name)
    include(source, "02-变更参考/05-人员到位更新.md", "02-变更参考/05-人员到位更新.md")
    for artifact in exports:
        name = EXPORT_NAMES[artifact["format"]]
        if artifact.get("path") != name:
            raise ValueError("报告清单包含非本轮约定的导出文件")
        data = checked_read(output, name)
        if hashlib.sha256(data).hexdigest() != artifact.get("sha256") or len(data) != artifact.get("bytes"):
            raise ValueError("报告导出校验值或长度不匹配")
        if artifact["format"] == "pdf":
            if not data.startswith(b"%PDF-"):
                raise ValueError("报告PDF签名不正确")
        else:
            with ZipFile(BytesIO(data)) as document:
                if document.testzip() is not None or not {"[Content_Types].xml", "word/document.xml"}.issubset(document.namelist()):
                    raise ValueError("报告Word内容不完整")
        packaged = "03-已生成报告/" + name
        entries[packaged] = data
        links[(output / name).relative_to(root).as_posix()] = packaged
        links[packaged] = packaged
    include(source, "source_truth.json", "测试核对-不要上传/source_truth.json")
    links["source_truth.json"] = "测试核对-不要上传/source_truth.json"
    guides = {
        "docs/miaobi/MIAOBI_QINGLAN_LIVE_DEMO.md": "开始这里-演示脚本.md",
        "docs/miaobi/MIAOBI_QINGLAN_LIVE_TEST_REPORT.md": "测试记录.md",
    }
    links.update(guides)
    for local, packaged in guides.items():
        text = portable_markdown(checked_read(root, local).decode("utf-8"), links)
        for match in MARKDOWN_LINK.finditer(text):
            target = match.group(2).strip("<>").partition("#")[0]
            if target and not re.match(r"^(?:https?://|mailto:)", target, re.IGNORECASE) and target not in entries and target not in guides.values():
                raise ValueError("便携文档仍存在未随包提供的本地链接")
        entries[packaged] = text.encode("utf-8")
    # Hash every delivered member except this self-referential checksum file,
    # including rewritten guides and the non-uploadable reference truth.
    entries["文件校验.json"] = json.dumps(
        {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()},
        ensure_ascii=False, indent=2,
    ).encode("utf-8")
    return entries


def main():
    root = Path(__file__).resolve().parents[2]
    output = root / ".demo-build/qinglan-live-20260914"
    entries = build_entries(root)
    bundle = output / "妙笔青岚演练材料与脚本.zip"
    if bundle.is_symlink():
        raise ValueError("输出ZIP不能使用符号链接")
    data = BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    with ZipFile(data) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(entries):
            raise ValueError("便携ZIP校验失败")
    bundle.write_bytes(data.getvalue())
    print(bundle)


if __name__ == "__main__":
    main()
