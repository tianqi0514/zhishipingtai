#!/usr/bin/env python3
"""Deterministic account-free source protocols for customer demonstrations.

The HTTP endpoints cover Web, REST, RSS and Sitemap ingestion. A tiny local
``git daemon`` serves a real repository on port 9418, so the Git connector is
also exercised without depending on GitHub or an external account.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


BASE = "http://source-fixture:8088"
GIT_PORT = 9418
GIT_REPOSITORY_NAME = "guolian-demo.git"
GIT_FILES = {
    "README.md": """# 国联集团组织级知识底座演示配置

本仓库为演示数据，不代表国联集团真实经营数据。

东方智造供应 NexusOne；NexusOne 用于智慧流程中枢项目。项目由数字科技公司负责，
风险管理部持续跟踪交付延期风险。
""",
    "config/project-dependencies.yaml": """project: 智慧流程中枢项目
owner: 数字科技公司
products:
  - NexusOne
dependencies:
  - 集团数据交换平台
risk_tracking_department: 风险管理部
data_notice: 演示数据，不代表国联集团真实经营数据。
""",
    "rules/risk-routing.json": json.dumps(
        {
            "supplier": "东方智造",
            "risk": "交付延期",
            "affected_product": "NexusOne",
            "affected_project": "智慧流程中枢项目",
            "action": "数字化管理部协调替代方案",
            "data_notice": "演示数据，不代表国联集团真实经营数据。",
        },
        ensure_ascii=False,
        indent=2,
    ),
}


def _run_git(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )


def create_git_fixture(root: Path) -> Path:
    """Create a deterministic, exportable bare repository under ``root``."""

    work = root / "work"
    bare = root / GIT_REPOSITORY_NAME
    work.mkdir(parents=True, exist_ok=True)
    _run_git("init", "--initial-branch=main", cwd=work)
    for relative, content in GIT_FILES.items():
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "传神智库演示",
            "GIT_AUTHOR_EMAIL": "demo@chuanshen.invalid",
            "GIT_COMMITTER_NAME": "传神智库演示",
            "GIT_COMMITTER_EMAIL": "demo@chuanshen.invalid",
            "GIT_AUTHOR_DATE": "2026-09-01T09:00:00+08:00",
            "GIT_COMMITTER_DATE": "2026-09-01T09:00:00+08:00",
        }
    )
    _run_git("add", ".", cwd=work, env=env)
    _run_git("commit", "-m", "prepare deterministic Guolian demo configuration", cwd=work, env=env)
    _run_git("clone", "--bare", str(work), str(bare), cwd=root)
    (bare / "git-daemon-export-ok").touch()
    return bare


def start_git_fixture() -> tuple[Path, subprocess.Popen[bytes]]:
    root = Path(tempfile.mkdtemp(prefix="guolian-source-git-"))
    create_git_fixture(root)
    process = subprocess.Popen(
        [
            "git",
            "daemon",
            "--reuseaddr",
            "--export-all",
            f"--base-path={root}",
            "--listen=0.0.0.0",
            f"--port={GIT_PORT}",
            str(root),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def cleanup() -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        shutil.rmtree(root, ignore_errors=True)

    atexit.register(cleanup)
    for _ in range(50):
        if process.poll() is not None:
            cleanup()
            raise RuntimeError("Git fixture failed to start")
        try:
            with socket.create_connection(("127.0.0.1", GIT_PORT), timeout=0.2):
                return root, process
        except OSError:
            time.sleep(0.05)
    cleanup()
    raise RuntimeError("Git fixture did not become ready")


class Handler(BaseHTTPRequestHandler):
    def respond(self, status: int, content_type: str, body: str | bytes) -> None:
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            return self.respond(200, "application/json", '{"status":"ok"}')
        if self.path == "/robots.txt":
            return self.respond(200, "text/plain; charset=utf-8", "User-agent: *\nAllow: /\n")
        if self.path in {"/", "/portal"}:
            return self.respond(
                200,
                "text/html; charset=utf-8",
                """<!doctype html><html><head><title>智慧流程中枢运行门户</title></head>
                <body><h1>智慧流程中枢运行门户</h1>
                <p>智慧流程中枢一期贯通上会立项、采购申请、合同签订三个业务阶段。</p>
                <p>流程实例 GL-SP-2026-002 当前停留在采购需求审批节点，责任部门为数字赋能中心，已触发黄色超时预警。</p>
                <p>所有关键审批动作应全程留痕，流程归档后形成可追溯的制度、表单、决策和合同证据链。</p>
                </body></html>""",
            )
        if self.path == "/process/manual":
            return self.respond(
                200,
                "text/html; charset=utf-8",
                """<!doctype html><html><head><title>采购流程操作指引</title></head>
                <body><h1>采购流程操作指引</h1>
                <p>采购估算价达到三十万元以上的项目，应按照集团采购管理制度确定采购方式。</p>
                <p>采购申请完成后依次进入需求审批、采购组织、合同签订和履约归档。</p>
                </body></html>""",
            )
        if self.path == "/api/processes":
            return self.respond(
                200,
                "application/json; charset=utf-8",
                json.dumps(
                    {
                        "generated_at": "2026-09-03T10:00:00+08:00",
                        "processes": [
                            {
                                "process_id": "GL-SP-2026-001",
                                "project": "国联集团合规体系数字化项目",
                                "process_type": "上会立项",
                                "current_node": "会议决议归档",
                                "status": "已完成",
                                "owner_department": "集团办公室",
                            },
                            {
                                "process_id": "GL-SP-2026-002",
                                "project": "智慧流程中枢一期",
                                "process_type": "采购申请",
                                "current_node": "采购需求审批",
                                "status": "进行中",
                                "owner_department": "数字赋能中心",
                                "risk_level": "黄色",
                            },
                            {
                                "process_id": "GL-SP-2026-003",
                                "project": "合规管控能力与办公门户管理系统",
                                "process_type": "合同签订",
                                "current_node": "合同归档",
                                "status": "已完成",
                                "owner_department": "国联数字科技有限公司",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
        if self.path == "/feed.xml":
            return self.respond(
                200,
                "application/rss+xml; charset=utf-8",
                f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
                <title>智慧流程风险动态</title><link>{BASE}/portal</link><description>流程预警与制度更新</description>
                <item><title>采购申请 GL-SP-2026-002 触发黄色预警</title><link>{BASE}/portal</link>
                <description>该流程已在采购需求审批节点停留三天，责任部门为数字赋能中心，建议在 2026 年 9 月 5 日前处理。</description></item>
                <item><title>采购制度知识更新</title><link>{BASE}/process/manual</link>
                <description>采购估算价达到三十万元以上的项目，应按照集团采购管理制度确定采购方式。</description></item>
                </channel></rss>""",
            )
        if self.path == "/sitemap.xml":
            return self.respond(
                200,
                "application/xml; charset=utf-8",
                f"""<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                <url><loc>{BASE}/portal</loc></url><url><loc>{BASE}/process/manual</loc></url></urlset>""",
            )
        if self.path == "/guolian/portal":
            return self.respond(
                200,
                "text/html; charset=utf-8",
                """<!doctype html><html><head><title>集团项目运行门户（演示）</title></head>
                <body><h1>智慧流程中枢项目运行状态</h1>
                <p>本页面为演示数据，不代表国联集团真实经营数据。</p>
                <p>智慧流程中枢项目由数字科技公司负责，核心产品 NexusOne 由东方智造供应。</p>
                <p>东方智造发生交付延期，数字化管理部正在协调替代方案，风险管理部持续跟踪。</p>
                </body></html>""",
            )
        if self.path == "/guolian/api/risks":
            return self.respond(
                200,
                "application/json; charset=utf-8",
                json.dumps(
                    {
                        "generated_at": "2026-09-01T15:30:00+08:00",
                        "data_notice": "演示数据，不代表国联集团真实经营数据。",
                        "items": [
                            {
                                "risk_id": "DEMO-RISK-001",
                                "supplier": "东方智造",
                                "risk_type": "交付延期",
                                "risk_level": "高",
                                "product": "NexusOne",
                                "project": "智慧流程中枢项目",
                                "owner_department": "风险管理部",
                                "status": "跟踪中",
                            },
                            {
                                "risk_id": "DEMO-RISK-002",
                                "supplier": "太湖云科",
                                "risk_type": "资料待补充",
                                "risk_level": "低",
                                "project": "集团知识底座项目",
                                "owner_department": "集团采购管理部",
                                "status": "整改中",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
        if self.path == "/guolian/feed.xml":
            return self.respond(
                200,
                "application/rss+xml; charset=utf-8",
                f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
                <title>集团采购与供应商风险动态（演示）</title><link>{BASE}/guolian/portal</link>
                <description>演示数据，不代表国联集团真实经营数据。</description>
                <item><title>东方智造交付延期风险</title><link>{BASE}/guolian/portal</link>
                <description>东方智造供应的 NexusOne 交付延期，可能影响智慧流程中枢项目。</description></item>
                <item><title>采购制度现行版本提示</title><link>{BASE}/guolian/policies</link>
                <description>演示问答应优先使用 2025 演示现行版采购实施细则。</description></item>
                </channel></rss>""",
            )
        if self.path == "/guolian/sitemap.xml":
            return self.respond(
                200,
                "application/xml; charset=utf-8",
                f"""<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                <url><loc>{BASE}/guolian/portal</loc></url>
                <url><loc>{BASE}/guolian/policies</loc></url>
                <url><loc>{BASE}/guolian/projects</loc></url></urlset>""",
            )
        if self.path == "/guolian/policies":
            return self.respond(
                200,
                "text/html; charset=utf-8",
                """<!doctype html><html><head><title>采购制度知识页（演示）</title></head><body>
                <h1>集团本部采购实施细则</h1><p>演示数据，不代表国联集团真实经营数据。</p>
                <p>有效采购金额统计应排除已取消订单；供应商出现重大交付风险时应持续跟踪并制定替代方案。</p>
                </body></html>""",
            )
        if self.path == "/guolian/projects":
            return self.respond(
                200,
                "text/html; charset=utf-8",
                """<!doctype html><html><head><title>项目依赖知识页（演示）</title></head><body>
                <h1>智慧流程中枢项目</h1><p>演示数据，不代表国联集团真实经营数据。</p>
                <p>项目使用 NexusOne，并依赖集团数据交换平台和统一身份组件，由数字科技公司负责。</p>
                </body></html>""",
            )
        return self.respond(404, "text/plain; charset=utf-8", "not found")

    def log_message(self, *_args: object) -> None:
        return


def main() -> None:
    start_git_fixture()
    ThreadingHTTPServer(("0.0.0.0", 8088), Handler).serve_forever()


if __name__ == "__main__":
    main()
