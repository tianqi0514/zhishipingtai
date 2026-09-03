#!/usr/bin/env python3
"""Deterministic Web/REST/RSS/Sitemap fixture for the 智慧流程中枢 demo."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


BASE = "http://source-fixture:8088"


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
        return self.respond(404, "text/plain; charset=utf-8", "not found")

    def log_message(self, *_args: object) -> None:
        return


ThreadingHTTPServer(("0.0.0.0", 8088), Handler).serve_forever()
