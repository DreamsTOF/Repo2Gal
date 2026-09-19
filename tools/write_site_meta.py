#!/usr/bin/env python3
"""把"这份站点是用哪个源提交生成的"写进产物：site-meta.json。

这是"已经处理过的不会再处理"的状态来源：状态跟着产物走，不需要 CI 回写仓库，
因此和分支保护（要求 PR、禁止直推）不冲突。

    python3 tools/write_site_meta.py output/site/site-meta.json \
        --repo owner/name --mode overview --profile chronicle-subtle \
        --asset-pack none --slug my-slug --commit <sha> [--run-url <url>]
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def build_meta(
    *,
    repo: str,
    mode: str,
    profile: str,
    asset_pack: str,
    slug: str,
    commit: str,
    run_url: str = "",
) -> dict:
    return {
        "repo": repo,
        "mode": mode,
        "profile": profile,
        "asset_pack": asset_pack,
        "slug": slug,
        "sourceCommit": commit,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runUrl": run_url
        or f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
        f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="写 site-meta.json")
    parser.add_argument("output", type=Path)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--asset-pack", default="none")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--commit", required=True, help="目标仓库的源提交 sha（空字符串表示未知）")
    parser.add_argument("--run-url", default="")
    args = parser.parse_args(argv)

    meta = build_meta(
        repo=args.repo,
        mode=args.mode,
        profile=args.profile,
        asset_pack=args.asset_pack,
        slug=args.slug,
        commit=args.commit,
        run_url=args.run_url,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())