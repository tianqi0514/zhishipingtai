"""
Analytics routes for graph metrics and validation.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..dependencies import get_session
from ..schemas import AnalyticsResponse, ValidationIssue, ValidationReportResponse
from ..session import GraphSession

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


@router.get("", response_model=AnalyticsResponse)
async def get_analytics(
    response: Response,
    metrics: Optional[str] = Query(
        None,
        description="Comma-separated metrics to compute: centrality,community,connectivity",
    ),
    session: GraphSession = Depends(get_session),
):
    requested = set((metrics or "centrality,community,connectivity").split(","))
    graph_dict = await asyncio.to_thread(session.build_graph_dict)
    result: dict = {}
    attempted = 0
    failed = 0

    if "centrality" in requested and session.centrality is not None:
        attempted += 1
        try:
            result["centrality"] = await asyncio.to_thread(
                session.centrality.calculate_degree_centrality,
                graph_dict,
            )
        except Exception as exc:
            result["centrality"] = {"error": str(exc)}
            failed += 1

    if "community" in requested and session.community is not None:
        attempted += 1
        try:
            result["community"] = await asyncio.to_thread(
                session.community.detect_communities,
                graph_dict,
            )
        except Exception as exc:
            result["community"] = {"error": str(exc)}
            failed += 1

    if "connectivity" in requested and session.connectivity is not None:
        attempted += 1
        try:
            result["connectivity"] = await asyncio.to_thread(
                session.connectivity.analyze_connectivity,
                graph_dict,
            )
        except Exception as exc:
            result["connectivity"] = {"error": str(exc)}
            failed += 1

    if attempted and failed == attempted:
        # Every requested metric raised: a plain 2xx (even 207) reads as
        # success to callers that only check `response.ok`, so surface this
        # as a hard failure rather than a body full of {"error": ...}.
        raise HTTPException(status_code=500, detail="All requested analytics metrics failed to compute")

    if failed:
        response.status_code = 207

    return AnalyticsResponse(**result)


@router.get("/validation", response_model=ValidationReportResponse)
async def validate_graph(
    session: GraphSession = Depends(get_session),
):
    validator = session.validator
    if validator is None:
        return ValidationReportResponse(valid=True, error_count=0, warning_count=0, issues=[])

    graph_dict = await asyncio.to_thread(session.build_graph_dict)
    try:
        report = await asyncio.to_thread(validator.validate, graph_dict)
    except Exception as exc:
        return ValidationReportResponse(
            valid=False,
            error_count=1,
            issues=[ValidationIssue(severity="error", message=str(exc))],
        )

    if isinstance(report, dict):
        valid = report.get("valid", True)
        errors = report.get("errors", [])
        warnings = report.get("warnings", [])
    else:
        valid = getattr(report, "valid", True)
        errors = getattr(report, "errors", [])
        warnings = getattr(report, "warnings", [])

    issues = [ValidationIssue(severity="error", message=str(error)) for error in (errors or [])]
    issues.extend(ValidationIssue(severity="warning", message=str(warning)) for warning in (warnings or []))

    return ValidationReportResponse(
        valid=valid,
        error_count=len(errors or []),
        warning_count=len(warnings or []),
        issues=issues,
    )
