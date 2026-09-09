from __future__ import annotations

from scripts.miaobi.acceptance_upload_to_report import assess, improvement_recommendations


def _material_state() -> dict:
    return {
        "missing": [],
        "document_count": 3,
        "ready_count": 3,
        "channel_counts": {"vector": 3, "graph": 3},
    }


def _knowledge_context() -> dict:
    return {
        "snapshot_locked": True,
        "release": {"version": 2},
        "spaces": [{"graph_available": True, "vector_available": True}],
    }


def _assistant() -> dict:
    return {
        "status": "completed",
        "content": (
            "# 灾情研判\n"
            "本次地震震级为6.2级，人口密度305.6人/km²，判定为重大地震灾害（Ⅱ级）[1]。\n"
            "# 组织体系\n"
            "建立统一指挥和跨部门协同机制，并以现场核验信息动态调整任务。[2]\n"
            "# 应急保障\n"
            "搜救人员缺口180人，县域床位缺口220张、全域床位缺口80张，帐篷缺口1800顶。"
            "推荐综合平衡方案，说明路线、时长与风险取舍，并持续核验未解决资源缺口。[3]\n"
            "# 附则\n"
            + "方案依据随资料版本更新，发布前必须完成业务确认。" * 24
        ),
        "citations": [
            {"citation_number": 1},
            {"citation_number": 2},
            {"citation_number": 3},
        ],
    }


def _events() -> list[dict]:
    return [
        {
            "event_type": "tool_finished",
            "payload": {"name": "writing_get_project_context", "success": True},
        },
        {
            "event_type": "tool_finished",
            "payload": {"name": "knowledge_search", "success": True},
        },
    ]


def test_upload_to_report_quality_accepts_complete_traceable_report() -> None:
    result = assess(
        material_state=_material_state(),
        knowledge_context=_knowledge_context(),
        assistant=_assistant(),
        events=_events(),
    )

    assert result["score"] == 100
    assert result["verdict"] == "通过"
    assert result["failed_checks"] == []
    assert improvement_recommendations(result) == []


def test_upload_to_report_quality_maps_failures_back_to_platform_work() -> None:
    assistant = _assistant()
    assistant["content"] = "这是一个没有依据的短答，其中泄露了 knowledge_search。"
    assistant["citations"] = []
    result = assess(
        material_state=_material_state(),
        knowledge_context=_knowledge_context(),
        assistant=assistant,
        events=[],
    )

    assert result["score"] < 70
    assert "关键事实与确定性计算正确" in result["failed_checks"]
    assert "引用编号与服务端证据投影一致" in result["failed_checks"]
    assert "真实调用知识与写作工具" in result["failed_checks"]
    assert "正文没有内部实现、凭据或 UUID 泄露" in result["failed_checks"]
    recommendations = improvement_recommendations(result)
    assert {item["area"] for item in recommendations} >= {
        "事实进入写作链路",
        "引用绑定",
        "智库与妙笔联动",
        "输出安全",
    }
