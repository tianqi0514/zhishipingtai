"""Offline, resumable OCR of publicly obtained standards; retain page provenance.

Requires local pdftoppm and tesseract with chi_sim. OCR is not verified normative
text: formulas, tables and illustrations always require checking the source PDF.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    from pypdf import PdfReader
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--url", required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    source_hash = hashlib.sha256(args.source.read_bytes()).hexdigest()
    pages = len(PdfReader(args.source).pages)
    cache = args.output.parent / (".ocr-" + source_hash[:16])
    cache.mkdir(parents=True, exist_ok=True)

    def page(number):
        text_path = cache / f"page-{number:04d}.txt"
        if text_path.exists():
            return number, text_path.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="standard-ocr-") as work:
            image = Path(work) / "page"
            subprocess.run(["pdftoppm", "-f", str(number), "-l", str(number), "-singlefile", "-r", "170", "-png", str(args.source), str(image)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=90)
            env = dict(os.environ, OMP_THREAD_LIMIT="1")
            result = subprocess.run(["tesseract", str(image.with_suffix(".png")), "stdout", "-l", "chi_sim+eng", "--psm", "3"], check=True, capture_output=True, text=True, env=env, timeout=120)
            text_path.write_text(result.stdout, encoding="utf-8")
            return number, result.stdout

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, min(6, args.workers))) as pool:
        for future in as_completed([pool.submit(page, number) for number in range(1, pages + 1)]):
            number, value = future.result()
            results[number] = value
            if len(results) % 10 == 0 or len(results) == pages:
                print(f"OCR {args.source.name}: {len(results)}/{pages} pages", flush=True)
    output = [f"# {args.source.stem}", "", f"来源：{args.url}", f"原文件 SHA-256：{source_hash}", "",
              "> 历史版本资料。本文由真实 OCR 转换，尚未逐条人工核验；公式、表格、插图及工程条文以原 PDF 为准，不可直接作为现行设计结论。", ""]
    for number in range(1, pages + 1):
        output += [f"## 原文第 {number} 页", "", results[number].strip() or "[本页未识别到文字，请核对原 PDF]", ""]
    args.output.write_text("\n".join(output), encoding="utf-8")
    audit = {"source": args.source.name, "source_url": args.url, "source_sha256": source_hash,
             "pages": pages, "ocr_pages": len(results), "engine": "tesseract chi_sim+eng", "dpi": 170,
             "verification_status": "unverified_ocr", "output": args.output.name,
             "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}
    args.output.with_suffix(".ocr.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
