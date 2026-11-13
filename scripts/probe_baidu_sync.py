"""Read-only Baidu API feasibility probe; never uploads, deletes or prints tokens."""
from __future__ import annotations

import argparse
import json
import os

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-dir", required=True, help="单独创建的测试目录，不使用正式库")
    args = parser.parse_args()
    token = os.getenv("TORCH_BAIDU_ACCESS_TOKEN", "")
    if not token:
        print(json.dumps({"status": "pending_authorization", "network_called": False,
                          "note": "从安全的本机授权流程配置 TORCH_BAIDU_ACCESS_TOKEN 后再验证；不要把令牌放入命令行或报告。"}, ensure_ascii=False))
        return 2
    if not args.remote_dir.startswith("/apps/") or ".." in args.remote_dir.split("/"):
        parser.error("请选择 /apps/ 下的独立测试目录")
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.get("https://pan.baidu.com/rest/2.0/xpan/file", params={
                "method": "list", "dir": args.remote_dir, "start": 0, "limit": 1,
                "access_token": token})
        data = response.json() if response.status_code == 200 else {}
        print(json.dumps({"http_status": response.status_code, "errno": data.get("errno"),
                          "returned_items": len(data.get("list", [])), "writes_performed": False,
                          "scope": "仅验证授权和单页列举，不代表上传/分页/冲突/安卓已通过"}, ensure_ascii=False))
        return 0 if response.status_code == 200 and data.get("errno") == 0 else 1
    except (httpx.HTTPError, ValueError):
        print('{"status":"network_or_response_error","writes_performed":false}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
