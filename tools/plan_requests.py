#!/usr/bin/env python3
"""挑出"这次要生成哪些请求"，产出 GitHub Actions 能直接吃的 matrix。

    python3 tools/plan_requests.py --all
    python3 tools/plan_requests.py --diff <before-sha> <head-sha>
    python3 tools/plan_requests.py --all --ignore-state     # 忽略"已生成过"判断，全部重做

去重分两层：
  1. 仓库内：同一个（仓库, 模式, 风格, 素材包）只允许一个 slug（见 find_duplicates）；
  2. 已生成过：站点根目录的 site-meta.json 里记着上次用的源提交，源仓库没有新提交就跳过，
     不用回写仓库、也不怕分支保护（状态跟着产物走）。要强制重做就写 `force: true`。

stdout 只输出 `key=value`（给 `>> "$GITHUB_OUTPUT"`），人类可读日志走 stderr。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from request_schema import (  # noqa: E402
    Request,
    RequestError,
    display_path,
    filter_request_files,
    find_duplicates,
    iter_request_files,
    parse_request,
)

ROOT = Path(__file__).resolve().parent.parent
NULL_SHA = "0" * 40
META_PATH = "site-meta.json"

for _stream in (sys.stdout, sys.stderr):  # Windows 控制台默认 gbk，日志里有 → 之类字符会炸
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def _get_json(url: str, token: str = "", timeout: int = 20) -> tuple[object | None, str]:
    """返回 (数据, 错误说明)。响应不是 JSON（Pages 对未知路径会回 index.html）时数据为 None、无错误。"""
    headers = {"Accept": "application/json", "User-Agent": "repo2gal-requests-plan"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        return None, "" if exc.code == 404 else f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return None, str(exc)[:120]
    try:
        return json.loads(body.decode("utf-8", errors="replace")), ""
    except ValueError:
        return None, ""


def source_head(repo: str, token: str) -> tuple[str, str]:
    """目标仓库默认分支的最新提交；查不到时返回 ("", 原因)。"""
    data, error = _get_json(f"https://api.github.com/repos/{repo}/commits?per_page=1", token)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return str(data[0].get("sha") or ""), ""
    return "", error or "响应结构异常"


def site_meta(slug: str) -> tuple[dict | None, str]:
    """站点上的 site-meta.json（公开可读，不需要任何密钥）。"""
    data, error = _get_json(f"https://{slug}.repo2gal-gallery.pages.dev/{META_PATH}")
    if isinstance(data, dict):
        return data, ""
    return None, error


def already_generated(request: Request, head_sha: str, meta: dict | None) -> str:
    """返回跳过原因；空串表示需要生成。"""
    if request.force:
        return ""
    if not head_sha:
        return ""
    if not meta:
        return ""
    same_target = (
        str(meta.get("repo", "")).lower() == request.repo.lower()
        and meta.get("mode") == request.mode
        and meta.get("profile") == request.profile
        and meta.get("asset_pack") == request.asset_pack
    )
    if same_target and str(meta.get("sourceCommit", "")) == head_sha:
        generated = str(meta.get("generatedAt", ""))[:19]
        return f"已生成过（源提交 {head_sha[:8]}，{generated}），源仓库没有新提交"
    return ""


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
    parser.add_argument("--ignore-state", action="store_true", help="忽略已生成判断，全部重做")
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
        print("skipped=0")
        print("slugs=")
        print('matrix={"include":[]}')
        return 0

    requests: list[Request] = []
    for path in paths:
        try:
            requests.append(parse_request(path))
        except RequestError as exc:
            print(f"[plan] 请求文件不合法，先修再合并：{exc}", file=sys.stderr)
            return 1

    duplicates = find_duplicates(requests)
    if duplicates:
        for message in duplicates:
            print(f"[plan] {message}", file=sys.stderr)
        return 1

    token = os.environ.get("GITHUB_TOKEN", "")
    rows: list[dict] = []
    skipped: list[str] = []
    for request in requests:
        head_sha, head_error = source_head(request.repo, token)
        if head_error:
            print(f"[plan] {request.repo} 取源提交失败（{head_error}）：按需要生成处理", file=sys.stderr)
        meta, meta_error = site_meta(request.slug)
        if meta_error:
            print(f"[plan] {request.slug} 读站点状态失败（{meta_error}）：按需要生成处理", file=sys.stderr)

        if not args.ignore_state:
            why = already_generated(request, head_sha, meta)
            if why:
                skipped.append(f"{request.slug}：{why}")
                print(f"[plan] 跳过 {display_path(request.path)} → {request.slug}：{why}", file=sys.stderr)
                continue

        rows.append(request.as_matrix_row(head_sha))
        print(
            f"[plan] {display_path(request.path)} → {request.repo}（{request.mode}）"
            f"@{(head_sha or '未知')[:8]} → {request.site_url}",
            file=sys.stderr,
        )

    slugs = ",".join(row["slug"] for row in rows)
    print(f"[plan] {reason}：{len(rows)} 个要生成，{len(skipped)} 个跳过", file=sys.stderr)
    for line in skipped:
        print(f"[plan]   跳过 {line}", file=sys.stderr)
    print(f"count={len(rows)}")
    print(f"skipped={len(skipped)}")
    print(f"slugs={slugs}")
    print("skipped_detail=" + " | ".join(skipped))
    print("matrix=" + json.dumps({"include": rows}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())