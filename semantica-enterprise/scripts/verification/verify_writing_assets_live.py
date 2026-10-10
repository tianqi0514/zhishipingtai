"""Read existing encrypted model config, parse a fixture and call the real model.

Never prints secrets or mutates the source configuration database.
Run from semantica-enterprise with PYTHONPATH=. and an authorised DB tunnel.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from dotenv import dotenv_values
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=Path(".local-dev/app.env"))
    parser.add_argument("--database-host", default="127.0.0.1")
    parser.add_argument("--database-port", type=int, default=25432)
    parser.add_argument("--material", type=Path, default=Path("tests/fixtures/writing_assets_history.md"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    private = dotenv_values(args.env)
    if private.get("APP_SECRET_KEY"): os.environ["APP_SECRET_KEY"] = private["APP_SECRET_KEY"]
    from packages.platform.config import get_settings
    get_settings.cache_clear()
    from packages.platform.models import ModelConfig
    from packages.platform.security import decrypt_secret
    from packages.semantica_adapter.parse import parse_document
    from packages.semantica_adapter.writing_extract import WritingEvidenceInput
    from packages.semantica_adapter.writing_assets import extract_assets
    from packages.platform.writing_assets import validate_asset
    from packages.platform.writing_graph import content_hash

    url = make_url(private["DATABASE_URL"]).set(host=args.database_host, port=args.database_port)
    engine = create_engine(url, connect_args={"connect_timeout": 5}, hide_parameters=True)
    with Session(engine) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        config = db.scalar(select(ModelConfig).where(ModelConfig.model_kind == "llm",
            ModelConfig.enabled.is_(True), ModelConfig.is_default.is_(True), ModelConfig.deleted_at.is_(None)))
        if config is None: raise ValueError("缺少可用默认模型")
        model, base_url, parameters = config.model_name, config.base_url, dict(config.config or {})
        secret = decrypt_secret(config.api_key_encrypted)
    elements, summary = parse_document(args.material, version_id="live-assets-fixture-v1", policy={}, supplied_mime="text/markdown")
    inputs = [WritingEvidenceInput(evidence_id="live-" + content_hash({"ordinal": index, "text": element.text})[:24], text=element.text,
             locator={"structural_path": element.structural_path}) for index, element in enumerate(elements) if element.text.strip()]
    started = time.monotonic()
    attempts = []
    try:
        extracted, prompt = extract_assets(inputs, material_role="sample_style", api_key=secret,
            model=model, base_url=base_url, request_parameters=parameters, on_attempt=attempts.append)
    except Exception as exc:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"test_type": "real_model_real_parser", "status": "failed",
            "error_type": type(exc).__name__, "attempts": attempts, "model": model}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    report = {"test_type": "real_model_real_parser", "model": model, "elapsed_seconds": round(time.monotonic()-started, 2),
        "parser_summary": summary, "input": [item.model_dump() for item in inputs], "runtime_prompt": prompt,
        "output": extracted.model_dump(), "attempts": attempts, "validation": [validate_asset(item) for item in extracted.assets]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "model": model, "seconds": report["elapsed_seconds"], "asset_count": len(extracted.assets),
                      "kinds": sorted({item.kind for item in extracted.assets}), "evidence_count": len(inputs)}, ensure_ascii=False))


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        # Provider exception text can contain request credentials: never echo it.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}, ensure_ascii=False))
        raise SystemExit(1)
