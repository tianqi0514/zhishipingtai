"""Resolve exact GB files on Commons and download only explicitly PD assets.

Not an authenticity or current-validity certificate. Missing editions are retained
as gaps; no commercial atlases, paywalls or authenticated sites are bypassed.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

STANDARDS = [
    ("GB 50153-2008", "工程结构可靠性设计统一标准"),
    ("GB 50068-2018", "建筑结构可靠性设计统一标准"),
    ("GB 50223-2008", "建筑工程抗震设防分类标准"),
    ("GB 50009-2012", "建筑结构荷载规范"),
    ("GB 50011-2010", "建筑抗震设计规范"),
    ("GB 50010-2010", "混凝土结构设计规范"),
    ("GB 50003-2011", "砌体结构设计规范"),
    ("GB 50017-2003", "钢结构设计规范"),
    ("GB 50007-2011", "建筑地基基础设计规范"),
    ("GB 50108-2008", "地下工程防水技术规范"),
    ("GB 50016-2014", "建筑设计防火规范"),
    ("GB 50205-2001", "钢结构工程施工质量验收规范"),
    ("GB 50666-2011", "混凝土结构工程施工规范"),
    ("GB 50204-2015", "混凝土结构工程施工质量验收规范"),
]


def get(url):
    with urlopen(Request(url, headers={"User-Agent": "NexusOne-SourceAudit/1.0 (document import)"}), timeout=45) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    def download(item):
        code, title = item
        row = {"code": code, "title": title, "status": "unavailable", "edition_verified": False}
        try:
            query = urlencode({"action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|extmetadata", "titles": f"File:{code} {title}.pdf"})
            data = json.loads(get("https://commons.wikimedia.org/w/api.php?" + query))
            page = next(iter(data["query"]["pages"].values()))
            info = page.get("imageinfo", [{}])[0]
            metadata = info.get("extmetadata", {})
            row["metadata"] = metadata
            if metadata.get("Copyrighted", {}).get("value", "").lower() != "false" or metadata.get("License", {}).get("value") != "pd":
                row["reason"] = "No exact publicly licensed fulltext file found"
                return row
            row.update(source_url=info["url"], source_page=info["descriptionurl"], license="Public domain (Commons declaration)")
            target = args.output_dir / (code.replace(" ", "") + ".pdf")
            if not target.exists():
                contents = get(info["url"])
                if not contents.startswith(b"%PDF-"):
                    raise ValueError("Not a PDF")
                target.write_bytes(contents)
            row.update(status="downloaded_pending_conversion_and_edition_check", filename=target.name)
        except Exception as exc:
            # Network errors contain no secret; preserve failure, never fake success.
            row["reason"] = str(exc)[:300]
        print(f"{code}: {row['status']}", flush=True)
        return row

    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(download, STANDARDS))
    (args.output_dir / "commons-source-audit.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
