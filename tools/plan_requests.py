#!/usr/bin/env python3
"""挑出"这次要生成哪些请求"，产出 GitHub Actions 能直接吃的 matrix。

    python3 tools/plan_requests.py --all
    python3 tools/plan_requests.py --diff <before-sha> <head-sha>

stdout 只输出 `key=value`（给 `>> "$GITHUB_OUTPUT"`），人类可读日志走 stderr。
matrix 里每一项都会作为一次 generate-galgame 调用的输入。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from request_schema import (  # noqa: E402
    RequestError,
    display_path,
    filter_request_files,
    iter_request_files,
    parse_request,
)

ROOT = Path(__file__).resolve().parent.parent
NULL_SHA = "0" * 40

for _stream in (sys.stdout, sys.stderr):  # Windows 控制台默认 gbk，日志里有 → 之类字符会炸
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def changed_files(before: str, head: str) -> list[Path] | None:
    """返回本次 push 改动的 requests/ 文件；无法判定（如首推/强推）时返回 None。"""
    if not before or set(before) == {"0"} or before == NULL_SHA:
        return None
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=d", before, head, "--", "requests/"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        print(f"[plan] git diff 失败，回退为全量：{completed.stderr.strip()[:200]}", file=sys.stderr)
        return None
    names = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return filter_request_files([ROOT / name for name in names])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="产出 repo2gal 生成计划")
    parser.add_argument("--all", action="store_true", help="requests/ 下全部请求")
    parser.add_argument("--diff", nargs=2, metavar=("BEFORE", "HEAD"), help="只挑这次 push 改动到的请求")
    args = parser.parse_args(argv)

    if not args.all and not args.diff:
        parser.error("要么 --all，要么 --diff BEFORE HEAD")

    reason = "全量"
    paths: list[Path] | None = None
    if args.diff:
        paths = changed_files(*args.diff)
        if paths is not None:
            reason = f"本次 push 改动（{args.diff[0][:8]}..{args.diff[1][:8]}）"
    if paths is None:
        paths = iter_request_files(ROOT)

    if not paths:
        print(f"[plan] {reason}：没有要生成的请求", file=sys.stderr)
        print("count=0")
        print("slugs=")
        print('matrix={"include":[]}')
        return 0

    rows: list[dict] = []
    for path in paths:
        try:
            request = parse_request(path)
        except RequestError as exc:
            print(f"[plan] 请求文件不合法，先修再合并：{exc}", file=sys.stderr)
            return 1
        rows.append(request.as_matrix_row())
        print(
            f"[plan] {display_path(path)} → {request.repo}（{request.mode}）→ {request.site_url}",
            file=sys.stderr,
        )

    slugs = ",".join(row["slug"] for row in rows)
    print(f"[plan] {reason}：{len(rows)} 个请求", file=sys.stderr)
    print(f"count={len(rows)}")
    print(f"slugs={slugs}")
    print("matrix=" + json.dumps({"include": rows}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())