from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Application,
    ComputationDefinition,
    ComputationDefinitionVersion,
    ScenarioPackage,
    ScenarioPackageVersion,
)
from .writing import BUILTIN_FORMULAS, content_hash, validate_scenario_contract


SCENARIO_ROOT = Path(__file__).resolve().parents[2] / "demo" / "miaobi" / "scenarios"


def bootstrap_writing(db: Session, *, tenant_id: str, actor_id: str) -> None:
    """Idempotently install reviewed built-in contracts without changing user versions."""
    application = db.scalar(
        select(Application).where(
            Application.tenant_id == tenant_id,
            Application.code == "miaobi-emergency",
            Application.deleted_at.is_(None),
        )
    )
    if application is None:
        db.add(
            Application(
                tenant_id=tenant_id,
                code="miaobi-emergency",
                name="妙笔·应急方案生成",
                description="基于组织知识、确定性计算和规则推演生成可核验的专业方案。",
                app_type="web",
                environment="production",
                owner_id=actor_id,
                status="active",
                config={"launch_url": "/miaobi/", "product": "miaobi"},
                enabled=True,
            )
        )
    for operation, spec in BUILTIN_FORMULAS.items():
        definition = db.scalar(
            select(ComputationDefinition).where(
                ComputationDefinition.tenant_id == tenant_id,
                ComputationDefinition.code == operation,
                ComputationDefinition.deleted_at.is_(None),
            )
        )
        if definition is None:
            definition = ComputationDefinition(
                tenant_id=tenant_id,
                code=operation,
                name=spec["name"],
                description="妙笔内置确定性公式；只执行受控操作，不解释任意表达式。",
                enabled=True,
            )
            db.add(definition)
            db.flush()
        if definition.current_version_id:
            continue
        manifest = {
            "operation": operation,
            "expression": spec["expression"],
            "rounding": {"mode": "half_up", "digits": 0},
        }
        version = ComputationDefinitionVersion(
            tenant_id=tenant_id,
            definition_id=definition.id,
            version=1,
            operation=operation,
            expression=spec["expression"],
            input_schema={"type": "object"},
            output_schema={"type": "number"},
            unit=spec["unit"],
            rounding=manifest["rounding"],
            default_parameters={},
            tests=[],
            checksum=content_hash(manifest),
            status="active",
            created_by=actor_id,
        )
        db.add(version)
        db.flush()
        definition.current_version_id = version.id

    if not SCENARIO_ROOT.exists():
        return
    for path in sorted(SCENARIO_ROOT.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_scenario_contract(payload)
        package = db.scalar(
            select(ScenarioPackage).where(
                ScenarioPackage.tenant_id == tenant_id,
                ScenarioPackage.code == payload["code"],
                ScenarioPackage.deleted_at.is_(None),
            )
        )
        if package is None:
            package = ScenarioPackage(
                tenant_id=tenant_id,
                code=payload["code"],
                name=payload["name"],
                disaster_type=payload["disaster_type"],
                description=payload.get("description", ""),
                status="active",
                enabled=True,
            )
            db.add(package)
            db.flush()
        if package.current_version_id:
            continue
        contract = {
            key: payload.get(key, [] if key.endswith("_ids") else {})
            for key in (
                "input_schema",
                "ontology_mapping",
                "rule_set_ids",
                "formula_ids",
                "tool_ids",
                "chapter_template",
                "output_schema",
                "review_rules",
                "decision_gates",
                "comparison_dimensions",
                "config",
            )
        }
        version = ScenarioPackageVersion(
            tenant_id=tenant_id,
            scenario_package_id=package.id,
            version=1,
            checksum=content_hash(contract),
            status="active",
            created_by=actor_id,
            **contract,
        )
        db.add(version)
        db.flush()
        package.current_version_id = version.id
