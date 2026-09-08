from __future__ import annotations

import argparse

from demo_client import DemoClient, PROJECT_CODE


def main() -> None:
    parser = argparse.ArgumentParser(description="只清理妙笔地震验收任务，不触碰知识空间、知识产品或客户材料")
    parser.add_argument("--confirm", help=f"必须明确输入 {PROJECT_CODE}")
    args = parser.parse_args()
    if args.confirm != PROJECT_CODE:
        raise SystemExit(f"未执行。请使用 --confirm {PROJECT_CODE} 明确清理范围")
    api = DemoClient()
    project = api.one("/writing/projects", "code", PROJECT_CODE)
    if not project:
        print("妙笔地震验收任务不存在，无需清理")
        return
    api.delete(f"/writing/projects/{project['id']}")
    print(f"已软删除演示任务 {PROJECT_CODE}；知识空间、知识产品、资料和历史数据库均未删除")


if __name__ == "__main__":
    main()
