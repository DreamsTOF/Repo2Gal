#!/usr/bin/env python3
"""校验请求文件，并把结论输出成可直接贴进 PR 评论的 markdown。

    python3 tools/validate_requests.py requests/a.yml requests/b.yml
    python3 tools/validate_requests.py --all
    python3 tools/validate_requests.py --all --no-repo-check     # 离线：不查 GitHub

退出码 0 = 全部通过；1 = 有问题（错误写进报告）。只依赖标准库 + PyYAML。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from request_schema import (  # noqa: E402
    ASSET_PACKS,
    MODES,
    PROFILES,
    Request,
    RequestError,
    display_path,
    filter_request_files,
    iter_request_files,
    parse_request,
)

ROOT = Path(__file__).resolve().parent.parent
MARKER = "<!-- repo2gal-validate -->"
API = "https://api.github.com"

for _stream in (sys.stdout, sys.stderr):  # Windows 控制台默认 gbk，报告里有 ✅/❌ 会炸
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def probe_repo(repo: str, token: str) -> tuple[str, str]:
    """返回 (等级, 说明)：ok / warn。等级只用于展示，不是校验结论。"""
    request = urllib.request.Request(
        f"{API}/repos/{repo}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "repo2gal-requests-validate",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "missing", "GitHub 上找不到，或它不是公开仓库"
        if exc.code in (403, 429):
            return "warn", f"GitHub API 限流（{exc.code}），本次跳过仓库存在性检查"
        return "warn", f"GitHub API 返回 {exc.code}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return "warn", f"网络异常：{exc}"

    facts = [
        data.get("language") or "未知语言",
        f"★{data.get('stargazers_count', 0)}",
        f"{data.get('created_at', '')[:10]} 创建",
        "私有" if data.get("private") else "公开",
    ]
    return "ok", " · ".join(facts)


def render(requests: list[Request], errors: list[str], notes: list[str]) -> str:
    ok = not errors
    lines = [MARKER, f"## repo2gal 请求校验：{'✅ 全部通过' if ok else f'❌ 有 {len(errors)} 处问题'}", ""]

    if requests:
        lines += [
            "| 请求 | 目标仓库 | 模式 | 风格 | 素材包 | 合并后站点 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for item in requests:
            lines.append(
                f"| `{display_path(item.path)}` | `{item.repo}` | {item.mode} | {item.profile} "
                f"| {item.asset_pack} | [{item.slug}]({item.site_url}) |"
            )
        lines.append("")
        lines.append(f"合并到 `main` 后会自动生成并部署，链接形如 `{requests[0].site_url}`。")
        lines.append("")

    if errors:
        lines += ["### 需要修的问题", ""]
        lines += [f"- {message}" for message in errors]
        lines += [
            "",
            "字段说明见 [`requests/README.md`](../blob/main/requests/README.md)；"
            f"可选值：mode ∈ {' / '.join(MODES)}，profile ∈ {' / '.join(PROFILES)}，"
            f"asset_pack ∈ {' / '.join(ASSET_PACKS)}。",
            "",
        ]

    if notes:
        lines += ["### 提示", ""]
        lines += [f"- {message}" for message in notes]
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 repo2gal 请求文件")
    parser.add_argument("files", nargs="*", help="要校验的请求文件（相对仓库根）")
    parser.add_argument("--all", action="store_true", help="校验 requests/ 下全部请求")
    parser.add_argument("--no-repo-check", action="store_true", help="不访问 GitHub 检查仓库是否存在")
    args = parser.parse_args(argv)

    if args.all:
        paths = iter_request_files(ROOT)
    else:
        paths = filter_request_files([Path(p) if Path(p).is_absolute() else ROOT / p for p in args.files])

    if not paths:
        print(render([], [], ["本次改动里没有请求文件（`requests/*.yml`）"]))
        return 0

    requests: list[Request] = []
    errors: list[str] = []
    notes: list[str] = []
    token = os.environ.get("GITHUB_TOKEN", "")

    for path in paths:
        try:
            requests.append(parse_request(path))
        except RequestError as exc:
            errors.append(str(exc))

    if requests and not args.no_repo_check:
        for item in requests:
            level, detail = probe_repo(item.repo, token)
            if level == "missing":
                errors.append(
                    f"`{display_path(item.path)}`：目标仓库 `{item.repo}` 不存在或不是公开仓库"
                )
            elif level == "warn":
                notes.append(f"`{item.repo}`：{detail}")
            else:
                notes.append(f"`{item.repo}`：{detail}")

    print(render(requests, errors, notes))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())